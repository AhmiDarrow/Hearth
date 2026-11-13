"""Local control plane + peer HTTP API."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, update
from .crypto import hmac_hex
from .discovery import BEACON_PORT, Discovery
from .files import guess_type, known_folders, list_dir, list_local_dirs, resolve_share_path, resolve_user_path
from .netutil import local_ipv4s, parse_host
from .state import Store

UI_DIR = Path(__file__).parent / "ui" / "static"
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
    text: str
    to: str | None = None

class ThemeIn(BaseModel):
    theme: str

class ManualHostIn(BaseModel):
    host: str

class SettingsIn(BaseModel):
    device_name: str | None = None
    bridge: bool | None = None

class UpdatesCheckIn(BaseModel):
    install: bool = False

class HandshakeIn(BaseModel):
    device_id: str
    name: str
    group_id: str
    port: int
    ips: list[str] = []


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

    def auth_ok(request: Request) -> bool:
        key = store.group_key()
        if not key:
            return False
        token = request.headers.get(PEER_HEADER, "")
        expected = hmac_hex(key, store.group["id"].encode())
        return token == expected

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
            "shares": store.data.get("shares", []),
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
            "shares": store.data.get("shares", []),
            "chat": store.data.get("chat", [])[-200:],
            "nearby": [
                offer for offer in nearby.values()
                if now - (offer.get("last_seen") or 0) < 30
            ],
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
            try:
                result = await asyncio.to_thread(update.check_and_maybe_install, auto=True)
                if result.get("available") or result.get("installed"):
                    await broadcast_ui("update", result)
                if result.get("installed"):
                    update.request_process_exit(delay=2.0)
                    return
            except Exception:
                pass
            await asyncio.sleep(update.CHECK_INTERVAL_SEC)

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
                for msg in r.json().get("chat") or []:
                    if msg.get("id") and msg["id"] not in existing:
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
    app.state.start_discovery = start_discovery
    app.state.start_maintenance = start_maintenance
    app.state.broadcast_ui = broadcast_ui
    app.state.online = online
    app.state.nearby = nearby
    app.state.on_peer = on_peer
    app.state.show_ui_callbacks = show_ui_callbacks
    if UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = UI_DIR / "index.html"
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

    @app.post("/api/ui/show")
    async def api_show_ui(request: Request) -> dict[str, Any]:
        require_local(request)
        for cb in list(show_ui_callbacks):
            try:
                cb()
            except Exception:
                continue
        return {"ok": True}

    @app.post("/api/updates/check")
    async def api_updates_check(request: Request, body: UpdatesCheckIn | None = None) -> dict[str, Any]:
        require_local(request)
        result = await asyncio.to_thread(
            update.check_and_maybe_install,
            install=bool(body and body.install),
            auto=False,
        )
        if result.get("installed"):
            update.request_process_exit(delay=2.0)
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
        if body.device_name:
            store.device_name = body.device_name
        if body.bridge is not None:
            store.data["bridge"] = body.bridge
            store.save()
        start_discovery()
        await broadcast_ui("settings")
        return snapshot()

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
        if not path.exists() or not path.is_dir():
            raise HTTPException(400, "Folder does not exist on this PC")
        store.add_share(str(path), body.label.strip() or path.name, body.mode)
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
            return {"shares": store.data.get("shares", [])}
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
            return {"share": share, "path": path, "items": list_dir(target)}
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        r = await peer_request("GET", peer, f"/peer/files/{share_id}", params={"path": path}, headers=peer_headers(), timeout=12.0)
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        return r.json()

    @app.get("/api/download/{device_id}/{share_id}")
    async def api_download(device_id: str, share_id: str, path: str) -> FileResponse:
        if device_id == store.identity.device_id:
            _, target = share_or_http(share_id, path)
            if not target.is_file():
                raise HTTPException(404, "Not a file")
            return FileResponse(target, filename=target.name, media_type=guess_type(target))
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        dest = store.root / "inbox"
        dest.mkdir(exist_ok=True)
        out = dest / Path(path).name
        last_error = "Download failed"
        for url in peer_urls(peer, f"/peer/download/{share_id}"):
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", url, params={"path": path}, headers=peer_headers()) as r:
                        if r.status_code != 200:
                            last_error = "Download failed"
                            continue
                        with out.open("wb") as fh:
                            async for chunk in r.aiter_bytes():
                                fh.write(chunk)
                return FileResponse(out, filename=out.name)
            except httpx.RequestError:
                continue
        raise HTTPException(502, last_error)

    @app.post("/api/upload/{device_id}/{share_id}")
    async def api_upload(device_id: str, share_id: str, path: str = "", file: UploadFile = File(...)) -> dict[str, Any]:
        data = await file.read()
        if device_id == store.identity.device_id:
            share, target_dir = share_or_http(share_id, path)
            if share.get("mode") != "readwrite":
                raise HTTPException(403, "Share is read-only")
            dest = target_dir / (file.filename or "upload.bin")
            dest.write_bytes(data)
            return {"ok": True, "name": dest.name}
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        r = await peer_request(
            "POST",
            peer,
            f"/peer/upload/{share_id}",
            params={"path": path},
            headers=peer_headers(),
            files={"file": (file.filename, data, file.content_type or "application/octet-stream")},
            timeout=None,
        )
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
        for peer in list(online.values()):
            try:
                await peer_request("POST", peer, "/peer/chat", headers=peer_headers(), json=msg, timeout=5.0)
            except Exception:
                continue

    @app.websocket("/ws")
    async def ws_ui(ws: WebSocket) -> None:
        await ws.accept()
        sockets.add(ws)
        await ws.send_json({"event": "hello", "state": snapshot()})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
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
        return {"shares": [{"id": s["id"], "label": s["label"], "mode": s["mode"]} for s in store.data.get("shares", [])]}

    @app.get("/peer/files/{share_id}")
    async def peer_files(share_id: str, request: Request, path: str = "") -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        share, target = share_or_http(share_id, path)
        return {"share": {"id": share["id"], "label": share["label"], "mode": share["mode"]}, "path": path, "items": list_dir(target)}

    @app.get("/peer/download/{share_id}")
    async def peer_download(share_id: str, request: Request, path: str) -> FileResponse:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        _, target = share_or_http(share_id, path)
        if not target.is_file():
            raise HTTPException(404, "Not a file")
        return FileResponse(target, filename=target.name, media_type=guess_type(target))

    @app.post("/peer/upload/{share_id}")
    async def peer_upload(share_id: str, request: Request, path: str = "", file: UploadFile = File(...)) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        share, target_dir = share_or_http(share_id, path)
        if share.get("mode") != "readwrite":
            raise HTTPException(403, "Share is read-only")
        dest = target_dir / (file.filename or "upload.bin")
        dest.write_bytes(await file.read())
        return {"ok": True, "name": dest.name}

    @app.post("/peer/chat")
    async def peer_chat(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        msg = await request.json()
        existing = {m["id"] for m in store.data.get("chat", [])}
        if msg.get("id") not in existing:
            store.append_chat(msg)
            await broadcast_ui("chat", msg)
        return {"ok": True}

    @app.get("/peer/chat")
    async def peer_chat_list(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return {"chat": store.data.get("chat", [])[-200:]}

    @app.get("/peer/ping")
    async def peer_ping(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return {"ok": True, "name": store.device_name, "id": store.identity.device_id, "port": peer_port, "ips": local_ipv4s()}

    return app


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
