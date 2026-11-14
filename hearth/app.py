"""Local control plane + peer HTTP API."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, update
from .access import local_ui_allowed
from .crypto import hmac_hex, verify_hmac
from .discovery import BEACON_PORT, Discovery
from .files import (
    MAX_UPLOAD_BYTES,
    UploadTooLarge,
    canonical_stored_path,
    default_share_label,
    display_share,
    folder_is_available,
    guess_type,
    known_folders,
    list_dir,
    list_local_dirs,
    resolve_share_path,
    resolve_user_path,
    safe_filename,
    save_upload,
)
from .netutil import local_ipv4s, parse_host
from .paths import static_ui_dir
from .prefs import content_disposition, default_download_folder, reset_settings, settings_snapshot, user_facing_error
from .state import Store
from .throttle import TRANSFER_CHUNK, RateLimits
from .transfers import TransferHub, open_peer_download

log = logging.getLogger("hearth")

PEER_HEADER = "x-hearth-auth"


class CreateGroupIn(BaseModel):
    name: str = "Home"
    password: str | None = None
    device_name: str | None = None

class JoinGroupIn(BaseModel):
    group_id: str
    name: str = "Home"
    password: str
    salt: str
    device_name: str | None = None
    host: str | None = None

class JoinByInviteIn(BaseModel):
    invite: str
    device_name: str | None = None

class ShareIn(BaseModel):
    path: str
    label: str = ""
    mode: str = Field(default="read", pattern="^(read|readwrite)$")

class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    to: str | None = None

class ThemeIn(BaseModel):
    theme: str

class ManualHostIn(BaseModel):
    host: str

class SettingsIn(BaseModel):
    device_name: str | None = None
    bridge: bool | None = None
    theme: str | None = None
    start_at_login: bool | None = None
    download_folder: str | None = None
    auto_check_updates: bool | None = None
    download_limit_bps: int | None = None
    upload_limit_bps: int | None = None

class SharePatchIn(BaseModel):
    mode: str | None = Field(default=None, pattern="^(read|readwrite)$")
    label: str | None = None
    path: str | None = None

class PathValidateIn(BaseModel):
    path: str


class TransferDownloadIn(BaseModel):
    device_id: str
    share_id: str
    path: str = ""
    paths: list[str] | None = None

class UpdatesCheckIn(BaseModel):
    install: bool = False

class HandshakeIn(BaseModel):
    device_id: str
    name: str
    group_id: str
    port: int
    ips: list[str] = []


class HandoffIn(BaseModel):
    token: str
    argv: list[str] = []
    show: bool = True


def create_app(store: Store, peer_port: int) -> FastAPI:
    lifecycle: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        start = lifecycle.get("start")
        if start:
            start()
        yield
        stop = lifecycle.get("stop")
        if stop:
            stop()

    app = FastAPI(title="Hearth", version=__version__, lifespan=lifespan)
    sockets: set[WebSocket] = set()
    online: dict[str, dict[str, Any]] = {}
    discovery: dict[str, Discovery] = {}
    bg_tasks: set[asyncio.Task[Any]] = set()
    maintenance_started = {"on": False}
    updates_started = {"on": False}
    nearby: dict[str, dict[str, Any]] = {}
    show_ui_callbacks: list[Any] = []
    pending_open: dict[str, Any] = {"argv": []}
    transfers = TransferHub(RateLimits(), store.root / "transfers.json")
    last_update: dict[str, Any] = {"result": None}

    def apply_rate_limits() -> None:
        transfers.limits.configure(
            int(store.data.get("download_limit_bps") or 0),
            int(store.data.get("upload_limit_bps") or 0),
        )

    apply_rate_limits()

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        if isinstance(exc, StarletteHTTPException):
            from fastapi.exception_handlers import http_exception_handler

            return await http_exception_handler(request, exc)
        if isinstance(exc, RequestValidationError):
            from fastapi.exception_handlers import request_validation_exception_handler

            return await request_validation_exception_handler(request, exc)
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": user_facing_error(exc)}, status_code=500)

    def auth_ok(request: Request) -> bool:
        key = store.group_key()
        if not key or not store.group:
            return False
        token = request.headers.get(PEER_HEADER, "")
        return verify_hmac(key, store.group["id"].encode(), token)

    def peer_headers() -> dict[str, str]:
        key = store.group_key()
        if not key or not store.group:
            return {}
        return {PEER_HEADER: hmac_hex(key, store.group["id"].encode())}

    def schedule(coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(coro)
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

    def snapshot() -> dict[str, Any]:
        now = time.time()
        members = [{
            "device_id": store.identity.device_id,
            "name": store.device_name,
            "self": True,
            "online": True,
            "ips": local_ipv4s(),
            "port": peer_port,
            "shares": [display_share(s) for s in store.data.get("shares", [])],
        }]
        seen_ids = {store.identity.device_id}
        merged: dict[str, dict[str, Any]] = {}
        for pid, peer in {**store.data.get("known_peers", {}), **online}.items():
            if pid == store.identity.device_id:
                continue
            merged[pid] = {**merged.get(pid, {}), **peer, "device_id": pid}
        # Hide leftover seed-* ghosts once the real device at that address is known.
        real_addrs = {
            (p.get("address"), int(p.get("port") or peer_port))
            for pid, p in merged.items()
            if not str(pid).startswith("seed-")
        }
        for pid, peer in merged.items():
            if pid in seen_ids:
                continue
            addr = (peer.get("address"), int(peer.get("port") or peer_port))
            if str(pid).startswith("seed-") and addr in real_addrs:
                continue
            seen_ids.add(pid)
            last = peer.get("last_seen") or 0
            members.append({
                "device_id": pid,
                "name": peer.get("name") or "PC",
                "self": False,
                "online": pid in online and now - last < 30,
                "ips": peer.get("ips") or [peer.get("address")],
                "address": peer.get("address"),
                "port": peer.get("port") or peer_port,
                "bridge": peer.get("bridge", False),
                "last_seen": last,
            })
        members.sort(key=lambda m: (not m["self"], not m["online"], m["name"].lower()))
        return {
            "version": __version__,
            "just_updated": update.read_just_updated(store.root),
            "frozen": update.is_frozen(),
            "device": {"id": store.identity.device_id, "name": store.device_name, "ips": local_ipv4s(), "peer_port": peer_port, "beacon_port": BEACON_PORT},
            "theme": store.theme,
            "bridge": bool(store.data.get("bridge")),
            "manual_hosts": store.data.get("manual_hosts", []),
            "known_folders": known_folders(),
            "group": None if not store.group else {
                "id": store.group["id"], "name": store.group["name"], "password": store.group["password"],
                "salt": store.group["salt"], "invite": make_invite(store, peer_port),
            },
            "members": members,
            "shares": [display_share(s) for s in store.data.get("shares", [])],
            "chat": visible_local_chat(store.data.get("chat", []), store.identity.device_id),
            "nearby": [
                offer for offer in nearby.values()
                if now - (offer.get("last_seen") or 0) < 30
            ],
            "settings": settings_snapshot(store, {
                "peer_port": peer_port,
                "beacon_port": BEACON_PORT,
            }),
            "update": last_update.get("result") or {},
            "transfers": transfers.list(),
            "pending_open": list(pending_open.get("argv") or []),
        }

    async def broadcast_ui(event: str, payload: Any | None = None) -> None:
        dead: list[WebSocket] = []
        message = {"event": event, "state": snapshot(), "payload": payload}
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            sockets.discard(ws)

    def forget_seed_for(peer: dict[str, Any]) -> None:
        address = peer.get("address")
        port = int(peer.get("port") or peer_port)
        drop = []
        for pid, existing in list(online.items()):
            if not str(pid).startswith("seed-"):
                continue
            if existing.get("address") == address and int(existing.get("port") or peer_port) == port:
                drop.append(pid)
        for pid in drop:
            online.pop(pid, None)
            store.data.get("known_peers", {}).pop(pid, None)
        if drop:
            store.save()

    def on_peer(peer: dict[str, Any]) -> None:
        if not store.group:
            gid = peer.get("group_id")
            if not gid:
                return
            nearby[gid] = {**peer, "last_seen": time.time()}
            schedule(broadcast_ui("nearby"))
            return
        online[peer["device_id"]] = {**peer, "last_seen": time.time()}
        forget_seed_for(peer)
        store.remember_peer(peer)
        schedule(broadcast_ui("peer"))

    def start_discovery() -> None:
        old = discovery.pop("d", None)
        if old:
            old.stop()
        g = store.group
        d = Discovery(
            device_id=store.identity.device_id,
            device_name=store.device_name,
            peer_port=peer_port,
            group_id=g["id"] if g else None,
            group_key=store.group_key(),
            on_peer=on_peer,
            manual_hosts=store.data.get("manual_hosts", []),
            bridge=bool(store.data.get("bridge")),
            group_name=g["name"] if g else None,
            group_salt=g["salt"] if g else None,
        )
        discovery["d"] = d
        schedule(d.start())

    def start_maintenance() -> None:
        if maintenance_started["on"]:
            return
        maintenance_started["on"] = True
        schedule(maintenance_loop())
        start_updates()

    def start_updates() -> None:
        if update.updates_disabled() or updates_started["on"]:
            return
        updates_started["on"] = True
        schedule(updates_loop())

    async def updates_loop() -> None:
        await asyncio.sleep(20)
        while True:
            wait = update.CHECK_INTERVAL_SEC
            try:
                if store.data.get("auto_check_updates", True):
                    result = await asyncio.to_thread(update.check_and_maybe_install, auto=True)
                    if result.get("deferred"):
                        wait = min(wait, max(30.0, update.failure_backoff_sec()))
                    elif not result.get("in_flight"):
                        store.data["last_update_check"] = time.time()
                        store.save()
                        last_update["result"] = result
                        if result.get("available") or result.get("opened_installer") or result.get("failed"):
                            await broadcast_ui("update", result)
            except Exception as exc:
                log.exception("Update check failed")
                failed = update.fallback_after_failure(
                    current=__version__,
                    exc=exc,
                    open_page=False,
                )
                last_update["result"] = failed
                try:
                    await broadcast_ui("update", failed)
                except Exception:
                    pass
            await asyncio.sleep(wait)

    async def maintenance_loop() -> None:
        while True:
            try:
                if store.group:
                    await handshake_all_hosts()
                    await sync_chat_from_peers()
            except Exception:
                pass
            await asyncio.sleep(5)

    async def handshake_all_hosts() -> None:
        hosts = list(store.data.get("manual_hosts", []))
        for peer in list(online.values()):
            address = peer.get("address")
            port = peer.get("port") or peer_port
            if address:
                host = f"{address}:{port}"
                if host not in hosts:
                    hosts.append(host)
        for host in hosts:
            await handshake_with_host(host)

    async def handshake_with_host(host: str) -> dict[str, Any] | None:
        if not store.group:
            return None
        try:
            ip, port = parse_host(host)
        except ValueError:
            return None
        # Do not handshake with ourselves.
        if port == peer_port and (ip in set(local_ipv4s()) | {"127.0.0.1", "localhost"}):
            return None
        payload = {
            "device_id": store.identity.device_id,
            "name": store.device_name,
            "group_id": store.group["id"],
            "port": peer_port,
            "ips": local_ipv4s() or ["127.0.0.1"],
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(
                    f"http://{ip}:{port}/peer/handshake",
                    headers=peer_headers(),
                    json=payload,
                )
            if r.status_code != 200:
                return None
            info = r.json()
            if info.get("device_id") == store.identity.device_id:
                return None
            peer = {
                "device_id": info["device_id"],
                "name": info.get("name") or ip,
                "port": int(info.get("port") or port),
                "ips": info.get("ips") or [ip],
                "address": ip,
                "via": "handshake",
            }
            already = online.get(peer["device_id"])
            if already and already.get("name") == peer["name"] and already.get("address") == ip:
                already["last_seen"] = time.time()
                return already
            on_peer(peer)
            return peer
        except Exception:
            return None

    async def sync_chat_from_peers() -> None:
        if not store.group:
            return
        existing = {m["id"] for m in store.data.get("chat", [])}
        added = False
        for peer in list(online.values()):
            if str(peer.get("device_id", "")).startswith("seed-"):
                # Still try seed hosts so we can pull chat before handshake completes.
                pass
            try:
                r = await peer_request("GET", peer, "/peer/chat", headers=peer_headers(), timeout=5.0)
                if r.status_code != 200:
                    continue
                for raw in r.json().get("chat") or []:
                    msg = accept_synced_chat(raw)
                    if msg and msg["id"] not in existing:
                        store.append_chat(msg)
                        existing.add(msg["id"])
                        added = True
            except Exception:
                continue
        if added:
            await broadcast_ui("chat")

    def share_or_http(share_id: str, path: str = "") -> tuple[dict[str, Any], Path]:
        try:
            return resolve_share_path(store.data.get("shares", []), share_id, path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    def stop_background() -> None:
        old = discovery.pop("d", None)
        if old:
            old.stop()

    lifecycle["start"] = lambda: (start_discovery(), start_maintenance())
    lifecycle["stop"] = stop_background

    def _flush_for_update() -> None:
        try:
            store.save()
        except Exception:
            pass
        try:
            stop_background()
        except Exception:
            pass

    update.clear_pre_install_hooks()
    update.register_pre_install(_flush_for_update)
    app.state.start_discovery = start_discovery
    app.state.start_maintenance = start_maintenance
    app.state.broadcast_ui = broadcast_ui
    app.state.online = online
    app.state.nearby = nearby
    app.state.on_peer = on_peer
    app.state.show_ui_callbacks = show_ui_callbacks
    if static_ui_dir().exists():
        app.mount("/static", StaticFiles(directory=static_ui_dir()), name="static")

    @app.middleware("http")
    async def protect_local_ui(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith("/peer/"):
            return await call_next(request)
        if not local_ui_allowed(request):
            return JSONResponse({"detail": "This page is only available on this PC"}, status_code=403)
        return await call_next(request)

    def open_readable_file(target: Path, download_name: str | None = None):
        if not target.is_file():
            raise HTTPException(404, "Not a file")
        try:
            with target.open("rb"):
                pass
        except OSError as exc:
            raise HTTPException(403, "File is locked or unreadable") from exc
        headers = {"Content-Disposition": content_disposition(download_name or target.name)}
        if transfers.limits.upload.rate > 0:
            async def chunks():
                with target.open("rb") as handle:
                    while True:
                        piece = handle.read(TRANSFER_CHUNK)
                        if not piece:
                            break
                        await transfers.limits.upload.take(len(piece))
                        yield piece

            return StreamingResponse(chunks(), media_type=guess_type(target), headers=headers)
        # Unlimited: FileResponse lets the server use sendfile / zero-copy.
        response = FileResponse(target, media_type=guess_type(target))
        response.headers.update(headers)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = static_ui_dir() / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>Hearth UI missing</h1>", status_code=500)
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.get("/api/state")
    async def api_state() -> dict[str, Any]:
        return snapshot()

    def require_local(request: Request) -> None:
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(403, "Local only")

    def apply_open_args(argv: list[str] | None = None, *, show: bool = True) -> dict[str, Any]:
        cleaned = [str(item) for item in (argv or []) if str(item).strip()]
        if cleaned:
            pending_open["argv"] = cleaned
            schedule(broadcast_ui("open", {"argv": cleaned}))
        shown = False
        if show:
            for cb in list(show_ui_callbacks):
                try:
                    cb()
                    shown = True
                except Exception:
                    continue
        return {"ok": True, "shown": shown, "argv": list(pending_open.get("argv") or [])}

    @app.post("/api/ui/show")
    async def api_show_ui(request: Request) -> dict[str, Any]:
        require_local(request)
        result = apply_open_args(show=True)
        result["port"] = peer_port
        return result

    @app.post("/api/instance/handoff")
    async def api_instance_handoff(request: Request, body: HandoffIn) -> dict[str, Any]:
        require_local(request)
        expected = getattr(app.state, "instance_token", None)
        if not expected or body.token != expected:
            raise HTTPException(403, "Bad token")
        result = apply_open_args(body.argv, show=body.show)
        result["port"] = peer_port
        return result

    app.state.instance_token = None
    app.state.apply_open_args = apply_open_args
    app.state.pending_open = pending_open

    @app.post("/api/updates/check")
    async def api_updates_check(request: Request, body: UpdatesCheckIn | None = None) -> dict[str, Any]:
        require_local(request)
        loop = asyncio.get_running_loop()

        def on_progress(info: dict[str, Any]) -> None:
            current = dict(last_update.get("result") or {})
            current.update(info)
            last_update["result"] = current

            def publish() -> None:
                schedule(broadcast_ui("update", current))

            try:
                loop.call_soon_threadsafe(publish)
            except Exception:
                pass

        try:
            result = await asyncio.to_thread(
                update.check_and_maybe_install,
                install=bool(body and body.install),
                auto=False,
                progress=on_progress,
                commit=False,
            )
        except Exception as exc:
            log.exception("Update check failed")
            result = update.fallback_after_failure(
                current=__version__,
                exc=exc,
                open_page=True,
            )
        store.data["last_update_check"] = time.time()
        store.save()
        last_update["result"] = result
        try:
            await broadcast_ui("update", result)
        except Exception:
            pass

        def _commit_hidden_install() -> None:
            setup = result.get("setup")
            if not result.get("commit_pending") or not setup:
                return
            try:
                update.commit_hidden_install(Path(setup))
            except Exception:
                log.exception("Failed to start the hidden installer after the update response")

        if result.get("commit_pending"):
            return JSONResponse(result, background=BackgroundTask(_commit_hidden_install))
        return result

    @app.post("/api/updates/ack")
    async def api_updates_ack(request: Request) -> dict[str, Any]:
        require_local(request)
        update.clear_just_updated(store.root)
        return snapshot()

    @app.get("/api/known-folders")
    async def api_known_folders() -> dict[str, Any]:
        return {"folders": known_folders()}

    @app.get("/api/local-folders")
    async def api_local_folders(path: str = "") -> dict[str, Any]:
        try:
            return list_local_dirs(path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @app.post("/api/path/validate")
    async def api_validate_path(body: PathValidateIn) -> dict[str, Any]:
        raw = (body.path or "").strip()
        if not raw:
            raise HTTPException(400, "Choose a folder first")
        try:
            path = resolve_user_path(raw)
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc) or "That folder does not exist on this PC") from exc
        if not folder_is_available(path):
            raise HTTPException(400, "That folder does not exist on this PC")
        stored = canonical_stored_path(str(path))
        return {"ok": True, "path": stored, "name": default_share_label(stored, "")}

    @app.post("/api/group/create")
    async def api_create(body: CreateGroupIn) -> dict[str, Any]:
        if body.device_name:
            store.device_name = body.device_name
        store.create_group(body.name, body.password)
        nearby.clear()
        start_discovery()
        await broadcast_ui("group")
        return snapshot()

    @app.post("/api/group/join")
    async def api_join(body: JoinGroupIn) -> dict[str, Any]:
        if body.device_name:
            store.device_name = body.device_name
        store.join_group(body.group_id, body.name, body.password, body.salt)
        nearby.clear()
        if body.host:
            _remember_host(store, body.host)
        start_discovery()
        if body.host:
            await handshake_with_host(body.host)
        await broadcast_ui("group")
        return snapshot()

    @app.post("/api/group/invite")
    async def api_join_invite(body: JoinByInviteIn) -> dict[str, Any]:
        parsed = parse_invite(body.invite)
        if body.device_name:
            store.device_name = body.device_name
        store.join_group(parsed["group_id"], parsed["name"], parsed["password"], parsed["salt"])
        nearby.clear()
        for host in parsed.get("hosts", []):
            _remember_host(store, host)
        store.save()
        start_discovery()
        for host in parsed.get("hosts", []):
            if not await handshake_with_host(host):
                seed_invite_peer(store, online, host)
        await broadcast_ui("group")
        return snapshot()

    @app.post("/api/group/leave")
    async def api_leave() -> dict[str, Any]:
        store.leave_group()
        online.clear()
        nearby.clear()
        start_discovery()
        await broadcast_ui("group")
        return snapshot()

    @app.post("/api/settings")
    async def api_settings(body: SettingsIn) -> dict[str, Any]:
        if body.device_name is not None and body.device_name.strip():
            store.device_name = body.device_name
        if body.bridge is not None:
            store.data["bridge"] = body.bridge
            store.save()
        if body.theme:
            store.theme = body.theme
        if body.start_at_login is not None:
            from .startup import set_start_at_login

            set_start_at_login(body.start_at_login)
        if body.download_folder is not None:
            raw = body.download_folder.strip()
            if raw:
                path = Path(raw).expanduser()
                if not path.is_dir():
                    raise HTTPException(400, "That download folder does not exist on this PC")
                store.data["download_folder"] = str(path)
            else:
                store.data["download_folder"] = ""
            store.save()
        if body.auto_check_updates is not None:
            store.data["auto_check_updates"] = body.auto_check_updates
            store.save()
        if body.download_limit_bps is not None:
            store.data["download_limit_bps"] = max(0, int(body.download_limit_bps))
            store.save()
        if body.upload_limit_bps is not None:
            store.data["upload_limit_bps"] = max(0, int(body.upload_limit_bps))
            store.save()
        apply_rate_limits()
        if body.device_name is not None or body.bridge is not None:
            start_discovery()
        await broadcast_ui("settings")
        return snapshot()

    @app.post("/api/settings/reset")
    async def api_reset_settings() -> dict[str, Any]:
        reset_settings(store)
        apply_rate_limits()
        start_discovery()
        await broadcast_ui("settings")
        return snapshot()

    @app.patch("/api/shares/{share_id}")
    async def api_patch_share(share_id: str, body: SharePatchIn) -> dict[str, Any]:
        share = next((s for s in store.data.get("shares", []) if s["id"] == share_id), None)
        if not share:
            raise HTTPException(404, "Unknown share")
        new_path = None
        if body.path is not None and body.path.strip():
            try:
                path = resolve_user_path(body.path.strip())
            except FileNotFoundError as exc:
                raise HTTPException(400, str(exc) or "That folder does not exist on this PC") from exc
            if not folder_is_available(path):
                raise HTTPException(400, "That folder does not exist on this PC")
            new_path = canonical_stored_path(str(path))
        updated = store.update_share(share_id, label=body.label, mode=body.mode, path=new_path)
        if not updated:
            raise HTTPException(404, "Unknown share")
        await broadcast_ui("shares")
        return snapshot()

    @app.get("/api/local-path/{share_id}")
    async def api_local_path(share_id: str, path: str = "") -> dict[str, Any]:
        _, target = share_or_http(share_id, path)
        return {"path": str(target), "dir": target.is_dir()}

    @app.post("/api/quit")
    async def api_quit(request: Request) -> dict[str, Any]:
        require_local(request)

        def _exit() -> None:
            os._exit(0)

        try:
            asyncio.get_running_loop().call_later(0.25, _exit)
        except RuntimeError:
            _exit()
        return {"ok": True}

    @app.get("/api/transfers")
    async def api_list_transfers() -> dict[str, Any]:
        return {"transfers": transfers.list()}

    @app.post("/api/transfers/clear")
    async def api_clear_transfers() -> dict[str, Any]:
        transfers.clear_finished()
        await broadcast_ui("transfers", transfers.list())
        return {"transfers": transfers.list()}

    @app.post("/api/transfers/{tid}/dismiss")
    async def api_dismiss_transfer(tid: str) -> dict[str, Any]:
        job = transfers.dismiss(tid)
        if not job:
            raise HTTPException(409, "Only finished transfers can be removed from the list")
        await broadcast_ui("transfers", transfers.list())
        return {"transfer": job, "transfers": transfers.list()}

    @app.post("/api/transfers/{tid}/cancel")
    async def api_cancel_transfer(tid: str) -> dict[str, Any]:
        job = transfers.cancel(tid)
        if not job:
            raise HTTPException(404, "Unknown transfer")
        await broadcast_ui("transfers", transfers.list())
        return {"transfer": job, "transfers": transfers.list()}

    @app.post("/api/transfers/download")
    async def api_transfer_download(body: TransferDownloadIn) -> dict[str, Any]:
        rels = [item for item in [body.path, *(body.paths or [])] if item]
        # de-dup while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for rel in rels:
            if rel not in seen:
                seen.add(rel)
                unique.append(rel)
        if not unique:
            raise HTTPException(400, "Nothing to download")
        folder = default_download_folder(store)
        started: list[dict[str, Any]] = []
        for rel in unique:
            name = safe_filename(Path(rel).name)
            if body.device_id == store.identity.device_id:
                _, target = share_or_http(body.share_id, rel)

                async def local_source(path: Path = target) -> Path:
                    return path

                job = await transfers.download_to_folder(
                    name=name,
                    folder=folder,
                    source=local_source,
                    broadcast=broadcast_ui,
                )
            else:
                peer = online.get(body.device_id) or store.data.get("known_peers", {}).get(body.device_id)
                if not peer:
                    raise HTTPException(404, "That PC is offline")

                async def remote_source(
                    current: dict[str, Any] = peer,
                    share: str = body.share_id,
                    item: str = rel,
                ) -> tuple[httpx.Response, httpx.AsyncClient]:
                    return await open_peer_download(
                        peer_urls(current, f"/peer/download/{share}"),
                        params={"path": item},
                        headers=peer_headers(),
                    )

                job = await transfers.download_to_folder(
                    name=name,
                    folder=folder,
                    source=remote_source,
                    broadcast=broadcast_ui,
                )
            started.append(job)
        return {"transfers": transfers.list(), "started": started}

    @app.post("/api/theme")
    async def api_theme(body: ThemeIn) -> dict[str, Any]:
        store.theme = body.theme
        await broadcast_ui("theme")
        return snapshot()

    @app.post("/api/hosts")
    async def api_add_host(body: ManualHostIn) -> dict[str, Any]:
        host = body.host.strip()
        if not host:
            raise HTTPException(400, "Host required")
        _remember_host(store, host)
        start_discovery()
        if not await handshake_with_host(host):
            seed_invite_peer(store, online, host)
        await broadcast_ui("hosts")
        return snapshot()

    @app.delete("/api/hosts/{host}")
    async def api_del_host(host: str) -> dict[str, Any]:
        store.data["manual_hosts"] = [h for h in store.data.get("manual_hosts", []) if h != host]
        store.save()
        start_discovery()
        await broadcast_ui("hosts")
        return snapshot()

    @app.post("/api/shares")
    async def api_add_share(body: ShareIn) -> dict[str, Any]:
        path = resolve_user_path(body.path)
        if not folder_is_available(path):
            raise HTTPException(400, "Folder does not exist on this PC")
        stored = canonical_stored_path(str(path))
        store.add_share(stored, default_share_label(stored, body.label), body.mode)
        await broadcast_ui("shares")
        return snapshot()

    @app.delete("/api/shares/{share_id}")
    async def api_del_share(share_id: str) -> dict[str, Any]:
        store.remove_share(share_id)
        await broadcast_ui("shares")
        return snapshot()

    @app.get("/api/remote-shares/{device_id}")
    async def api_remote_shares(device_id: str) -> dict[str, Any]:
        if device_id == store.identity.device_id:
            return {"shares": [display_share(s) for s in store.data.get("shares", [])]}
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        r = await peer_request("GET", peer, "/peer/shares", headers=peer_headers(), timeout=8.0)
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        return r.json()

    @app.get("/api/files/{device_id}/{share_id}")
    async def api_list_files(device_id: str, share_id: str, path: str = "") -> dict[str, Any]:
        if device_id == store.identity.device_id:
            share, target = share_or_http(share_id, path)
            return {"share": display_share(share), "path": path, "items": items_or_http(target)}
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        r = await peer_request("GET", peer, f"/peer/files/{share_id}", params={"path": path}, headers=peer_headers(), timeout=12.0)
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        return r.json()

    @app.get("/api/download/{device_id}/{share_id}")
    async def api_download(device_id: str, share_id: str, path: str):
        """Stream a file as an attachment.

        The in-app Download button does not navigate here — it uses
        /api/transfers/download so the window cannot be replaced by an error
        page. This route stays as a fallback and for tests. Remote files are
        proxied directly (no inbox spool) so a locked or unwritable inbox
        cannot turn into a bare 500.
        """
        try:
            if device_id == store.identity.device_id:
                _, target = share_or_http(share_id, path)
                return open_readable_file(target)
            peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
            if not peer:
                raise HTTPException(404, "That PC is offline")
            response, client = await open_peer_download(
                peer_urls(peer, f"/peer/download/{share_id}"),
                params={"path": path},
                headers=peer_headers(),
            )

            async def proxy():
                written = 0
                try:
                    async for chunk in response.aiter_bytes(TRANSFER_CHUNK):
                        written += len(chunk)
                        if written > MAX_UPLOAD_BYTES:
                            break
                        await transfers.limits.download.take(len(chunk))
                        yield chunk
                finally:
                    try:
                        await response.aclose()
                    except Exception:
                        pass
                    try:
                        await client.aclose()
                    except Exception:
                        pass

            name = safe_filename(Path(path).name)
            return StreamingResponse(
                proxy(),
                media_type=response.headers.get("content-type") or "application/octet-stream",
                headers={"Content-Disposition": content_disposition(name)},
            )
        except HTTPException:
            raise
        except UploadTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        except OSError as exc:
            log.exception("Download I/O error for %s", path)
            raise HTTPException(503, user_facing_error(exc)) from exc
        except Exception as exc:
            log.exception("Download failed for %s", path)
            raise HTTPException(502, user_facing_error(exc)) from exc

    async def persist_upload(share_id: str, path: str, file: UploadFile) -> dict[str, Any]:
        share, target_dir = share_or_http(share_id, path)
        if share.get("mode") != "readwrite":
            raise HTTPException(403, "Share is read-only")
        dest = target_dir / safe_filename(file.filename)
        try:
            await save_upload(dest, file)
        except UploadTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        return {"ok": True, "name": dest.name}

    @app.post("/api/upload/{device_id}/{share_id}")
    async def api_upload(device_id: str, share_id: str, path: str = "", file: UploadFile = File(...)) -> dict[str, Any]:
        if device_id == store.identity.device_id:
            return await persist_upload(share_id, path, file)
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        spool_dir = store.root / "spool"
        spool_dir.mkdir(exist_ok=True)
        spool = spool_dir / safe_filename(file.filename, fallback="upload.bin")
        spool = spool.with_name(f".{spool.name}.{os.urandom(4).hex()}.part")
        try:
            try:
                await save_upload(spool, file)
            except UploadTooLarge as exc:
                raise HTTPException(413, str(exc)) from exc

            class _ThrottledReader:
                def __init__(self, handle: Any) -> None:
                    self._handle = handle

                def read(self, size: int = -1) -> bytes:
                    data = self._handle.read(size)
                    if data:
                        transfers.limits.upload.take_sync(len(data))
                    return data

                def seek(self, *args: Any, **kwargs: Any):
                    return self._handle.seek(*args, **kwargs)

                def tell(self) -> int:
                    return self._handle.tell()

            with spool.open("rb") as fh:
                r = await peer_request(
                    "POST",
                    peer,
                    f"/peer/upload/{share_id}",
                    params={"path": path},
                    headers=peer_headers(),
                    files={"file": (safe_filename(file.filename), _ThrottledReader(fh), file.content_type or "application/octet-stream")},
                    timeout=None,
                )
        finally:
            try:
                spool.unlink(missing_ok=True)
            except OSError:
                pass
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        return r.json()

    @app.post("/api/chat")
    async def api_chat(body: ChatIn) -> dict[str, Any]:
        if not store.group:
            raise HTTPException(400, "Join a hearth first")
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "Empty message")
        msg = {"id": uuid.uuid4().hex, "from_id": store.identity.device_id, "from_name": store.device_name, "to": body.to, "text": text, "ts": time.time()}
        store.append_chat(msg)
        await fanout_chat(msg)
        await broadcast_ui("chat", msg)
        return msg

    async def fanout_chat(msg: dict[str, Any]) -> None:
        for peer in chat_fanout_peers(msg, list(online.values())):
            try:
                await peer_request("POST", peer, "/peer/chat", headers=peer_headers(), json=msg, timeout=5.0)
            except Exception:
                continue

    @app.websocket("/ws")
    async def ws_ui(ws: WebSocket) -> None:
        if not local_ui_allowed(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        sockets.add(ws)
        try:
            await ws.send_json({"event": "hello", "state": snapshot()})
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            sockets.discard(ws)

    @app.post("/peer/handshake")
    async def peer_handshake(body: HandshakeIn, request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        address = request.client.host if request.client else (body.ips[0] if body.ips else "127.0.0.1")
        on_peer({
            "device_id": body.device_id,
            "name": body.name,
            "port": body.port,
            "ips": body.ips or [address],
            "address": address,
            "via": "handshake",
        })
        return {
            "ok": True,
            "device_id": store.identity.device_id,
            "name": store.device_name,
            "port": peer_port,
            "ips": local_ipv4s() or ["127.0.0.1"],
        }

    @app.get("/peer/shares")
    async def peer_shares(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return {"shares": [display_share(s, include_path=False) for s in store.data.get("shares", [])]}

    @app.get("/peer/files/{share_id}")
    async def peer_files(share_id: str, request: Request, path: str = "") -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        share, target = share_or_http(share_id, path)
        return {"share": display_share(share, include_path=False), "path": path, "items": items_or_http(target)}

    @app.get("/peer/download/{share_id}")
    async def peer_download(share_id: str, request: Request, path: str) -> FileResponse:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        _, target = share_or_http(share_id, path)
        return open_readable_file(target)

    @app.post("/peer/upload/{share_id}")
    async def peer_upload(share_id: str, request: Request, path: str = "", file: UploadFile = File(...)) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return await persist_upload(share_id, path, file)

    @app.post("/peer/chat")
    async def peer_chat(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(400, "Chat payload must be JSON") from exc
        msg = normalize_peer_chat(raw)
        if not msg:
            raise HTTPException(400, "Invalid chat message")
        target = msg.get("to")
        if target and target != store.identity.device_id:
            return {"ok": True}
        existing = {m["id"] for m in store.data.get("chat", [])}
        if msg["id"] not in existing:
            store.append_chat(msg)
            await broadcast_ui("chat", msg)
        return {"ok": True}

    @app.get("/peer/chat")
    async def peer_chat_list(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return {"chat": public_peer_chat(store.data.get("chat", []))}

    @app.get("/peer/ping")
    async def peer_ping(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return {"ok": True, "name": store.device_name, "id": store.identity.device_id, "port": peer_port, "ips": local_ipv4s()}

    return app

def items_or_http(target: Path) -> list[dict[str, Any]]:
    try:
        return list_dir(target)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Folder does not exist") from exc
    except PermissionError as exc:
        raise HTTPException(403, "That folder cannot be browsed") from exc


def visible_local_chat(messages: list[Any], device_id: str) -> list[dict[str, Any]]:
    """Messages this PC should show. Filter first, then keep the latest 200."""
    visible: list[dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        target = msg.get("to")
        if not target or target == device_id or msg.get("from_id") == device_id:
            visible.append(msg)
    return visible[-200:]


def public_peer_chat(messages: list[Any]) -> list[dict[str, Any]]:
    """Drop anything addressed with `to`. A group token cannot name the reader."""
    public = [msg for msg in (messages or []) if isinstance(msg, dict) and not msg.get("to")]
    return public[-200:]


def accept_synced_chat(raw: Any) -> dict[str, Any] | None:
    msg = normalize_peer_chat(raw)
    if not msg or msg.get("to"):
        return None
    return msg


def chat_fanout_peers(msg: dict[str, Any], peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = (msg or {}).get("to")
    if not target:
        return list(peers)
    return [peer for peer in peers if peer.get("device_id") == target]


def normalize_peer_chat(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    mid = raw.get("id")
    text = raw.get("text")
    if not isinstance(mid, str) or not mid or len(mid) > 80:
        return None
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > 4000:
        return None
    from_name = raw.get("from_name") or "PC"
    if not isinstance(from_name, str) or len(from_name) > 128:
        return None
    from_id = raw.get("from_id") or ""
    if not isinstance(from_id, str) or len(from_id) > 80:
        return None
    to = raw.get("to")
    if to is not None and (not isinstance(to, str) or len(to) > 80):
        return None
    ts = raw.get("ts") or time.time()
    if not isinstance(ts, (int, float)):
        ts = time.time()
    return {
        "id": mid,
        "from_id": from_id,
        "from_name": from_name.strip() or "PC",
        "to": to,
        "text": text,
        "ts": float(ts),
    }


def peer_candidates(peer: dict[str, Any]) -> list[tuple[str, int]]:
    port = int(peer.get("port") or 7474)
    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int]] = []
    for host in [peer.get("address"), *(peer.get("ips") or [])]:
        if not host:
            continue
        raw = str(host).strip()
        try:
            if ":" in raw and raw.count(":") == 1 and not raw.startswith("["):
                host_ip, host_port = parse_host(raw)
                item = (host_ip, host_port)
            else:
                item = (raw.split(":")[0], port)
        except ValueError:
            continue
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def peer_urls(peer: dict[str, Any], path: str) -> list[str]:
    return [f"http://{host}:{port}{path}" for host, port in peer_candidates(peer)]


async def peer_request(method: str, peer: dict[str, Any], path: str, **kwargs: Any) -> httpx.Response:
    timeout = kwargs.pop("timeout", 8.0)
    last_error: Exception | None = None
    last_response: httpx.Response | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in peer_urls(peer, path):
            try:
                response = await client.request(method, url, **kwargs)
                last_response = response
                if response.status_code < 500:
                    return response
            except httpx.RequestError as exc:
                last_error = exc
                continue
    if last_response is not None:
        return last_response
    raise last_error or RuntimeError("Peer is unreachable")


def _remember_host(store: Store, host: str) -> None:
    host = host.strip()
    if not host:
        return
    hosts = store.data.setdefault("manual_hosts", [])
    if host not in hosts:
        hosts.append(host)
        store.save()


def seed_invite_peer(store: Store, online: dict[str, Any], host: str) -> None:
    raw = host.strip()
    if not raw:
        return
    try:
        ip, port = parse_host(raw)
    except ValueError:
        return
    pid = f"seed-{ip}-{port}"
    if any(p.get("address") == ip and int(p.get("port") or 7474) == port and not str(k).startswith("seed-") for k, p in online.items()):
        return
    peer = {"device_id": pid, "name": ip, "address": ip, "ips": [ip], "port": port, "via": "invite", "last_seen": time.time()}
    online[pid] = peer
    store.remember_peer(peer)


def make_invite(store: Store, peer_port: int) -> str:
    g = store.group
    if not g:
        return ""
    hosts = ",".join(f"{ip}:{peer_port}" for ip in local_ipv4s())
    return f"hearth|{g['id']}|{g['salt']}|{g['password']}|{g['name']}|{hosts}"


def parse_invite(invite: str) -> dict[str, Any]:
    text = invite.strip()
    if text.startswith("hearth|"):
        parts = text.split("|")
        if len(parts) < 5:
            raise HTTPException(400, "Invite code is incomplete")
        hosts = parts[5].split(",") if len(parts) > 5 else []
        return {"group_id": parts[1], "salt": parts[2], "password": parts[3], "name": parts[4], "hosts": [h for h in hosts if h]}
    if text.startswith("hearth:"):
        body = text[len("hearth:"):]
        if "|" in body:
            parts = body.split("|")
            if len(parts) < 4:
                raise HTTPException(400, "Invite code is incomplete")
            hosts = parts[4].split(",") if len(parts) > 4 else []
            return {"group_id": parts[0], "salt": parts[1], "password": parts[2], "name": parts[3], "hosts": [h for h in hosts if h]}
        bits = body.split(":", 4)
        if len(bits) < 4:
            raise HTTPException(400, "Invite code is incomplete")
        hosts = bits[4].split(",") if len(bits) > 4 else []
        return {"group_id": bits[0], "salt": bits[1], "password": bits[2], "name": bits[3], "hosts": [h for h in hosts if h]}
    raise HTTPException(400, "Not a Hearth invite. Ask a member to copy theirs.")
