"""Local control plane + peer HTTP API."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .crypto import hmac_hex
from .discovery import BEACON_PORT, Discovery
from .files import guess_type, list_dir, resolve_share_path
from .netutil import local_ipv4s
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

class HandshakeIn(BaseModel):
    device_id: str
    name: str
    group_id: str
    port: int
    ips: list[str] = []


def create_app(store: Store, peer_port: int) -> FastAPI:
    app = FastAPI(title="Hearth", version=__version__)
    sockets: set[WebSocket] = set()
    online: dict[str, dict[str, Any]] = {}
    discovery: dict[str, Discovery] = {}

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
        for pid, peer in {**store.data.get("known_peers", {}), **online}.items():
            if pid == store.identity.device_id:
                continue
            last = peer.get("last_seen") or 0
            members.append({
                "device_id": pid,
                "name": peer.get("name") or "PC",
                "self": False,
                "online": pid in online and now - last < 20,
                "ips": peer.get("ips") or [peer.get("address")],
                "address": peer.get("address"),
                "port": peer.get("port") or peer_port,
                "bridge": peer.get("bridge", False),
                "last_seen": last,
            })
        members.sort(key=lambda m: (not m["self"], not m["online"], m["name"].lower()))
        return {
            "version": __version__,
            "device": {"id": store.identity.device_id, "name": store.device_name, "ips": local_ipv4s(), "peer_port": peer_port, "beacon_port": BEACON_PORT},
            "theme": store.theme,
            "bridge": bool(store.data.get("bridge")),
            "manual_hosts": store.data.get("manual_hosts", []),
            "group": None if not store.group else {
                "id": store.group["id"], "name": store.group["name"], "password": store.group["password"],
                "salt": store.group["salt"], "invite": make_invite(store, peer_port),
            },
            "members": members,
            "shares": store.data.get("shares", []),
            "chat": store.data.get("chat", [])[-200:],
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

    def on_peer(peer: dict[str, Any]) -> None:
        online[peer["device_id"]] = {**peer, "last_seen": time.time()}
        store.remember_peer(peer)
        asyncio.get_event_loop().create_task(broadcast_ui("peer"))

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
        )
        discovery["d"] = d
        asyncio.get_event_loop().create_task(d.start())

    app.state.start_discovery = start_discovery
    app.state.broadcast_ui = broadcast_ui
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

    @app.post("/api/group/create")
    async def api_create(body: CreateGroupIn) -> dict[str, Any]:
        if body.device_name:
            store.device_name = body.device_name
        store.create_group(body.name, body.password)
        start_discovery()
        await broadcast_ui("group")
        return snapshot()

    @app.post("/api/group/join")
    async def api_join(body: JoinGroupIn) -> dict[str, Any]:
        if body.device_name:
            store.device_name = body.device_name
        store.join_group(body.group_id, body.name, body.password, body.salt)
        if body.host:
            hosts = store.data.setdefault("manual_hosts", [])
            if body.host not in hosts:
                hosts.append(body.host)
                store.save()
        start_discovery()
        await broadcast_ui("group")
        return snapshot()

    @app.post("/api/group/invite")
    async def api_join_invite(body: JoinByInviteIn) -> dict[str, Any]:
        parsed = parse_invite(body.invite)
        if body.device_name:
            store.device_name = body.device_name
        store.join_group(parsed["group_id"], parsed["name"], parsed["password"], parsed["salt"])
        hosts = store.data.setdefault("manual_hosts", [])
        for host in parsed.get("hosts", []):
            if host not in hosts:
                hosts.append(host)
            seed_invite_peer(store, online, host)
        store.save()
        start_discovery()
        await broadcast_ui("group")
        return snapshot()

    @app.post("/api/group/leave")
    async def api_leave() -> dict[str, Any]:
        store.leave_group()
        online.clear()
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
        hosts = store.data.setdefault("manual_hosts", [])
        if host not in hosts:
            hosts.append(host)
            store.save()
        start_discovery()
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
        path = Path(body.path).expanduser()
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
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(peer_url(peer, "/peer/shares"), headers=peer_headers())
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        return r.json()

    @app.get("/api/files/{device_id}/{share_id}")
    async def api_list_files(device_id: str, share_id: str, path: str = "") -> dict[str, Any]:
        if device_id == store.identity.device_id:
            share, target = resolve_share_path(store.data.get("shares", []), share_id, path)
            return {"share": share, "path": path, "items": list_dir(target)}
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(peer_url(peer, f"/peer/files/{share_id}"), params={"path": path}, headers=peer_headers())
        if r.status_code != 200:
            raise HTTPException(r.status_code, r.text)
        return r.json()

    @app.get("/api/download/{device_id}/{share_id}")
    async def api_download(device_id: str, share_id: str, path: str) -> FileResponse:
        if device_id == store.identity.device_id:
            _, target = resolve_share_path(store.data.get("shares", []), share_id, path)
            if not target.is_file():
                raise HTTPException(404, "Not a file")
            return FileResponse(target, filename=target.name, media_type=guess_type(target))
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        dest = store.root / "inbox"
        dest.mkdir(exist_ok=True)
        out = dest / Path(path).name
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", peer_url(peer, f"/peer/download/{share_id}"), params={"path": path}, headers=peer_headers()) as r:
                if r.status_code != 200:
                    raise HTTPException(r.status_code, "Download failed")
                with out.open("wb") as fh:
                    async for chunk in r.aiter_bytes():
                        fh.write(chunk)
        return FileResponse(out, filename=out.name)

    @app.post("/api/upload/{device_id}/{share_id}")
    async def api_upload(device_id: str, share_id: str, path: str = "", file: UploadFile = File(...)) -> dict[str, Any]:
        data = await file.read()
        if device_id == store.identity.device_id:
            share, target_dir = resolve_share_path(store.data.get("shares", []), share_id, path)
            if share.get("mode") != "readwrite":
                raise HTTPException(403, "Share is read-only")
            dest = target_dir / (file.filename or "upload.bin")
            dest.write_bytes(data)
            return {"ok": True, "name": dest.name}
        peer = online.get(device_id) or store.data.get("known_peers", {}).get(device_id)
        if not peer:
            raise HTTPException(404, "Peer offline")
        async with httpx.AsyncClient(timeout=None) as client:
            r = await client.post(peer_url(peer, f"/peer/upload/{share_id}"), params={"path": path}, headers=peer_headers(), files={"file": (file.filename, data, file.content_type or "application/octet-stream")})
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
        async with httpx.AsyncClient(timeout=5.0) as client:
            for peer in list(online.values()):
                try:
                    await client.post(peer_url(peer, "/peer/chat"), headers=peer_headers(), json=msg)
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
        on_peer({"device_id": body.device_id, "name": body.name, "port": body.port, "ips": body.ips, "address": request.client.host if request.client else body.ips[0], "via": "handshake"})
        return {"ok": True, "device_id": store.identity.device_id, "name": store.device_name}

    @app.get("/peer/shares")
    async def peer_shares(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return {"shares": [{"id": s["id"], "label": s["label"], "mode": s["mode"]} for s in store.data.get("shares", [])]}

    @app.get("/peer/files/{share_id}")
    async def peer_files(share_id: str, request: Request, path: str = "") -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        share, target = resolve_share_path(store.data.get("shares", []), share_id, path)
        return {"share": {"id": share["id"], "label": share["label"], "mode": share["mode"]}, "path": path, "items": list_dir(target)}

    @app.get("/peer/download/{share_id}")
    async def peer_download(share_id: str, request: Request, path: str) -> FileResponse:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        _, target = resolve_share_path(store.data.get("shares", []), share_id, path)
        if not target.is_file():
            raise HTTPException(404, "Not a file")
        return FileResponse(target, filename=target.name, media_type=guess_type(target))

    @app.post("/peer/upload/{share_id}")
    async def peer_upload(share_id: str, request: Request, path: str = "", file: UploadFile = File(...)) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        share, target_dir = resolve_share_path(store.data.get("shares", []), share_id, path)
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

    @app.get("/peer/ping")
    async def peer_ping(request: Request) -> dict[str, Any]:
        if not auth_ok(request):
            raise HTTPException(401, "Bad group token")
        return {"ok": True, "name": store.device_name, "id": store.identity.device_id}

    return app


def peer_url(peer: dict[str, Any], path: str) -> str:
    host = peer.get("address") or (peer.get("ips") or ["127.0.0.1"])[0]
    port = int(peer.get("port") or 7474)
    return f"http://{host}:{port}{path}"


def seed_invite_peer(store: Store, online: dict[str, Any], host: str) -> None:
    raw = host.strip()
    if not raw:
        return
    if raw.count(":") == 1:
        ip, port_s = raw.split(":")
        port = int(port_s)
    else:
        ip, port = raw, 7474
    pid = f"seed-{ip}-{port}"
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
    if text.startswith("hearth|") or text.startswith("hearth:"):
        sep = "|" if text.startswith("hearth|") else ":"
        parts = text.split(sep)
        if len(parts) < 5:
            raise HTTPException(400, "Invite code is incomplete")
        hosts = parts[5].split(",") if len(parts) > 5 else []
        return {"group_id": parts[1], "salt": parts[2], "password": parts[3], "name": parts[4], "hosts": [h for h in hosts if h]}
    raise HTTPException(400, "Not a Hearth invite. Ask a member to copy theirs.")
