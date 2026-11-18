from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from hearth.access import csrf_origin_ok, host_header_allowed, is_loopback_address, parse_host_header
from hearth.app import (
    PEER_HEADER,
    accept_synced_chat,
    chat_fanout_peers,
    create_app,
    normalize_peer_chat,
)
from hearth.crypto import hmac_hex
from hearth.files import safe_filename, sanitize_relpath
from hearth.state import Store


class _HeaderReq:
    def __init__(self, host: str | None = None, origin: str | None = None, referer: str | None = None, site: str | None = None) -> None:
        headers: dict[str, str] = {}
        if host is not None:
            headers["host"] = host
        if origin is not None:
            headers["origin"] = origin
        if referer is not None:
            headers["referer"] = referer
        if site is not None:
            headers["sec-fetch-site"] = site
        self.headers = headers
        self.client = type("C", (), {"host": "127.0.0.1"})()


def test_parse_host_header() -> None:
    assert parse_host_header("127.0.0.1:7474") == "127.0.0.1"
    assert parse_host_header("[::1]:7474") == "[::1]"
    assert parse_host_header("LOCALHOST") == "localhost"
    assert parse_host_header("evil.example") == "evil.example"


def test_loopback_and_host_checks() -> None:
    assert is_loopback_address("127.0.0.1")
    assert is_loopback_address("::1")
    assert is_loopback_address("::ffff:127.0.0.1")
    assert not is_loopback_address("192.168.1.20")
    assert host_header_allowed(_HeaderReq(host="127.0.0.1:7474"))
    assert host_header_allowed(_HeaderReq(host="localhost"))
    assert not host_header_allowed(_HeaderReq(host="evil.example"))
    assert not host_header_allowed(_HeaderReq(host=""))


def test_csrf_origin_checks() -> None:
    assert csrf_origin_ok(_HeaderReq())
    assert csrf_origin_ok(_HeaderReq(origin="http://127.0.0.1:7474"))
    assert csrf_origin_ok(_HeaderReq(origin="http://localhost:7474"))
    assert not csrf_origin_ok(_HeaderReq(origin="http://evil.example"))
    assert not csrf_origin_ok(_HeaderReq(origin="null"))
    assert not csrf_origin_ok(_HeaderReq(referer="https://evil.example/attack"))
    assert not csrf_origin_ok(_HeaderReq(origin="http://127.0.0.1", site="cross-site"))
    assert csrf_origin_ok(_HeaderReq(referer="http://127.0.0.1:7474/"))


def test_sanitize_relpath_blocks_escapes() -> None:
    assert sanitize_relpath("") == ""
    assert sanitize_relpath("photos/img.jpg") == "photos/img.jpg"
    assert sanitize_relpath("folder/with space/café.txt") == "folder/with space/café.txt"
    with pytest.raises(PermissionError):
        sanitize_relpath("../secret.txt")
    with pytest.raises(PermissionError):
        sanitize_relpath("a/../../secret")
    with pytest.raises(PermissionError):
        sanitize_relpath(r"C:\Windows\win.ini")
    with pytest.raises(PermissionError):
        sanitize_relpath("/etc/passwd")
    with pytest.raises(PermissionError):
        sanitize_relpath(r"\\server\share\file")
    with pytest.raises(PermissionError):
        sanitize_relpath("//server/share/file")
    with pytest.raises(PermissionError):
        sanitize_relpath("notes.txt:secret")
    with pytest.raises(PermissionError):
        sanitize_relpath("CON")
    with pytest.raises(PermissionError):
        sanitize_relpath("aux.txt")
    with pytest.raises(PermissionError):
        sanitize_relpath("foo\x00bar")
    with pytest.raises(PermissionError, match="dot or a space"):
        sanitize_relpath("photos/file.txt.")
    with pytest.raises(PermissionError, match="dot or a space"):
        sanitize_relpath("photos/file.txt ")
    with pytest.raises(PermissionError, match="escapes"):
        sanitize_relpath("../secret")


def test_safe_filename_strips_traversal_and_ads() -> None:
    assert safe_filename("../escape.txt") == "escape.txt"
    assert safe_filename(r"C:\Windows\win.ini") == "win.ini"
    assert safe_filename("notes.txt:stream") == "notes.txt"
    assert safe_filename("CON") == "upload.bin"
    assert safe_filename("..") == "upload.bin"
    assert safe_filename("file.txt.") == "upload.bin"
    assert safe_filename("file.txt ") == "upload.bin"
    assert safe_filename("café notes.txt") == "café notes.txt"
    assert safe_filename("my file.txt") == "my file.txt"


def test_resolve_share_rejects_windows_and_symlink_escapes(tmp_path: Path) -> None:
    from hearth.files import resolve_share_path

    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "ok.txt").write_text("ok")
    (tmp_path / "secret.txt").write_text("nope")
    shares = [{"id": "abc", "path": str(shared), "label": "Shared", "mode": "read"}]
    _, target = resolve_share_path(shares, "abc", "ok.txt")
    assert target == (shared / "ok.txt").resolve()
    with pytest.raises(PermissionError):
        resolve_share_path(shares, "abc", "../secret.txt")
    with pytest.raises(PermissionError):
        resolve_share_path(shares, "abc", r"C:\Windows\win.ini")
    with pytest.raises(PermissionError):
        resolve_share_path(shares, "abc", str(tmp_path / "secret.txt"))

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.txt").write_text("hidden")
    link = shared / "link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available on this system")
    with pytest.raises(PermissionError):
        resolve_share_path(shares, "abc", "link/hidden.txt")


def test_corrupt_state_and_device_key_recover(tmp_path: Path) -> None:
    root = tmp_path / "hearth-data"
    root.mkdir()
    (root / "state.json").write_text("{not json", encoding="utf-8")
    (root / "device.key").write_bytes(b"not-a-key")
    store = Store(root)
    assert store.group is None
    assert store.device_name
    assert (root / "state.json.corrupt").is_file()
    assert (root / "device.key.corrupt").is_file()
    assert (root / "device.key").is_file()
    store.create_group("Home", "pass-word")
    assert store.group and store.group["name"] == "Home"

    # A later load of a truncated / wrong-typed file also recovers.
    store.path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    again = Store(root)
    assert again.group is None
    assert (root / "state.json.corrupt").is_file()


def test_incomplete_group_is_quarantined(tmp_path: Path) -> None:
    root = tmp_path / "hearth-data"
    root.mkdir()
    (root / "state.json").write_text(json.dumps({"group": {"id": "x"}}), encoding="utf-8")
    store = Store(root)
    assert store.group is None
    assert (root / "state.json.corrupt").is_file()


def _local(app):
    return httpx.ASGITransport(app=app, client=("127.0.0.1", 123))


def _remote(app, host="192.168.1.50"):
    return httpx.ASGITransport(app=app, client=(host, 123))


def test_control_plane_rejects_lan_and_csrf(store: Store) -> None:
    app = create_app(store, 17474)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=_remote(app), base_url="http://192.168.1.50:7474") as client:
            res = await client.get("/api/state")
            assert res.status_code == 403
            res = await client.post("/api/group/create", json={"name": "Stolen"})
            assert res.status_code == 403
        async with httpx.AsyncClient(transport=_local(app), base_url="http://127.0.0.1") as client:
            ok = await client.get("/api/state")
            assert ok.status_code == 200
            assert "password" in (ok.json().get("group") or {}) or ok.json()["group"] is None
            bad_host = await client.get("/api/state", headers={"host": "evil.example"})
            assert bad_host.status_code == 403
            bad_origin = await client.get("/api/state", headers={"origin": "http://evil.example"})
            assert bad_origin.status_code == 403
            # UI itself is also locked to this PC.
            page = await client.get("/")
            assert page.status_code == 200

        async with httpx.AsyncClient(transport=_remote(app), base_url="http://192.168.1.50") as client:
            page = await client.get("/")
            assert page.status_code == 403

    asyncio.run(_run())


def test_peer_routes_still_work_from_lan(store: Store, tmp_path: Path) -> None:
    store.create_group("Home", "join-pass")
    shared = tmp_path / "share"
    shared.mkdir()
    (shared / "a.txt").write_text("hello")
    store.add_share(str(shared), "Share", "read")
    app = create_app(store, 17474)
    token = hmac_hex(store.group_key(), store.group["id"].encode())
    share_id = store.data["shares"][0]["id"]

    async def _run() -> None:
        async with httpx.AsyncClient(transport=_remote(app), base_url="http://192.168.1.50") as client:
            denied = await client.get("/peer/shares")
            assert denied.status_code == 401
            ok = await client.get("/peer/shares", headers={PEER_HEADER: token})
            assert ok.status_code == 200
            assert ok.json()["shares"][0]["id"] == share_id
            trav = await client.get(
                f"/peer/files/{share_id}",
                params={"path": "../secret.txt"},
                headers={PEER_HEADER: token},
            )
            assert trav.status_code == 403
            listing = await client.get(
                f"/peer/files/{share_id}",
                headers={PEER_HEADER: token},
            )
            assert listing.status_code == 200
            names = {item["name"] for item in listing.json()["items"]}
            assert "a.txt" in names

    asyncio.run(_run())


def test_upload_sanitizes_name_and_enforces_size(store: Store, tmp_path: Path, monkeypatch) -> None:
    store.create_group("Home", "join-pass")
    shared = tmp_path / "share"
    shared.mkdir()
    store.add_share(str(shared), "Share", "readwrite")
    app = create_app(store, 17474)
    did = store.identity.device_id
    sid = store.data["shares"][0]["id"]

    async def _run() -> None:
        async with httpx.AsyncClient(transport=_local(app), base_url="http://127.0.0.1") as client:
            res = await client.post(
                f"/api/upload/{did}/{sid}",
                files={"file": ("../escape.txt", b"inside", "text/plain")},
            )
            assert res.status_code == 200, res.text
            assert (shared / "escape.txt").read_bytes() == b"inside"
            assert not (tmp_path / "escape.txt").exists()

            spaced = await client.post(
                f"/api/upload/{did}/{sid}",
                files={"file": ("my notes.txt", b"spaced", "text/plain")},
            )
            assert spaced.status_code == 200
            assert (shared / "my notes.txt").read_bytes() == b"spaced"

            monkeypatch.setattr("hearth.app.MAX_UPLOAD_BYTES", 16)
            monkeypatch.setattr("hearth.files.MAX_UPLOAD_BYTES", 16)
            too_big = await client.post(
                f"/api/upload/{did}/{sid}",
                files={"file": ("big.bin", b"x" * 64, "application/octet-stream")},
            )
            assert too_big.status_code == 413
            assert not (shared / "big.bin").exists()
            assert not list(shared.glob(".big.bin.*.part"))

    asyncio.run(_run())


def test_upload_rejects_trailing_dot_or_space_and_missing_parent(store: Store, tmp_path: Path) -> None:
    from hearth.files import save_upload

    store.create_group("Home", "join-pass")
    shared = tmp_path / "share"
    shared.mkdir()
    store.add_share(str(shared), "Share", "readwrite")
    app = create_app(store, 17474)
    did = store.identity.device_id
    sid = store.data["shares"][0]["id"]

    class _Empty:
        async def read(self, _n: int = -1) -> bytes:
            return b""

    missing = tmp_path / "no-such-folder" / "a.txt"

    async def _run() -> None:
        async with httpx.AsyncClient(transport=_local(app), base_url="http://127.0.0.1") as client:
            dotted = await client.post(
                f"/api/upload/{did}/{sid}",
                files={"file": ("file.txt.", b"nope", "text/plain")},
            )
            assert dotted.status_code == 400
            assert "dot or a space" in dotted.text
            spaced = await client.post(
                f"/api/upload/{did}/{sid}",
                files={"file": ("file.txt ", b"nope", "text/plain")},
            )
            assert spaced.status_code == 400
            gone = await client.post(
                f"/api/upload/{did}/{sid}",
                params={"path": "missing-folder"},
                files={"file": ("note.txt", b"x", "text/plain")},
            )
            assert gone.status_code == 404
            assert "not on this PC" in gone.text
        with pytest.raises(FileNotFoundError):
            await save_upload(missing, _Empty())

    asyncio.run(_run())
    assert not (shared / "file.txt.").exists()
    assert not (shared / "upload.bin").exists()
    assert not (shared / "missing-folder").exists()
    assert not missing.parent.exists()


def test_chat_rejects_oversized_and_garbage(store: Store) -> None:
    store.create_group("Home", "join-pass")
    app = create_app(store, 17474)
    token = hmac_hex(store.group_key(), store.group["id"].encode())

    async def _run() -> None:
        async with httpx.AsyncClient(transport=_local(app), base_url="http://127.0.0.1") as client:
            huge = await client.post("/api/chat", json={"text": "a" * 5000})
            assert huge.status_code == 422
            ok = await client.post("/api/chat", json={"text": "<script>alert(1)</script>"})
            assert ok.status_code == 200
            assert ok.json()["text"] == "<script>alert(1)</script>"
        async with httpx.AsyncClient(transport=_remote(app), base_url="http://192.168.1.50") as client:
            garbage = await client.post("/peer/chat", headers={PEER_HEADER: token}, json=["nope"])
            assert garbage.status_code == 400
            long_msg = await client.post(
                "/peer/chat",
                headers={PEER_HEADER: token},
                json={"id": "x" * 8, "text": "b" * 5001, "from_name": "Eve"},
            )
            assert long_msg.status_code == 400
            good = await client.post(
                "/peer/chat",
                headers={PEER_HEADER: token},
                json={"id": "abc123", "text": "hello", "from_name": "Eve", "from_id": "eve"},
            )
            assert good.status_code == 200

    asyncio.run(_run())
    assert normalize_peer_chat({"id": "1", "text": "ok", "from_name": "A"})["text"] == "ok"
    assert normalize_peer_chat({"text": "missing id"}) is None


def test_private_chat_is_not_broadcast_to_the_group(store: Store) -> None:
    store.create_group("Home", "join-pass")
    app = create_app(store, 17474)
    token = hmac_hex(store.group_key(), store.group["id"].encode())
    me = store.identity.device_id

    async def _run() -> None:
        async with httpx.AsyncClient(transport=_local(app), base_url="http://127.0.0.1") as client:
            private = await client.post("/api/chat", json={"text": "secret note", "to": "other-device"})
            assert private.status_code == 200, private.text
            public = await client.post("/api/chat", json={"text": "hello room"})
            assert public.status_code == 200, public.text
            state = (await client.get("/api/state")).json()
            secret = next(msg for msg in state["chat"] if msg["text"] == "secret note")
            assert secret["from_id"] == me
            assert any(msg["text"] == "hello room" for msg in state["chat"])
        async with httpx.AsyncClient(transport=_remote(app), base_url="http://192.168.1.50") as client:
            feed = await client.get("/peer/chat", headers={PEER_HEADER: token})
            assert feed.status_code == 200, feed.text
            texts = [msg["text"] for msg in feed.json()["chat"]]
            assert "hello room" in texts
            assert "secret note" not in texts
            leaked = await client.post(
                "/peer/chat",
                headers={PEER_HEADER: token},
                json={"id": "priv-other", "text": "not for you", "from_name": "Eve", "from_id": "eve", "to": "someone-else"},
            )
            assert leaked.status_code == 200, leaked.text
            directed = await client.post(
                "/peer/chat",
                headers={PEER_HEADER: token},
                json={"id": "priv-me", "text": "for this pc", "from_name": "Eve", "from_id": "eve", "to": me},
            )
            assert directed.status_code == 200, directed.text
            feed = await client.get("/peer/chat", headers={PEER_HEADER: token})
            texts = [msg["text"] for msg in feed.json()["chat"]]
            assert "for this pc" not in texts
            assert "not for you" not in texts
        async with httpx.AsyncClient(transport=_local(app), base_url="http://127.0.0.1") as client:
            state = (await client.get("/api/state")).json()
            texts = [msg["text"] for msg in state["chat"]]
            assert "for this pc" in texts
            assert "not for you" not in texts

    asyncio.run(_run())
    assert accept_synced_chat({"id": "a", "text": "hi", "from_name": "A", "to": "x"}) is None
    assert accept_synced_chat({"id": "b", "text": "hi", "from_name": "A"})["text"] == "hi"
    peers = [{"device_id": "a"}, {"device_id": "b"}]
    assert [peer["device_id"] for peer in chat_fanout_peers({"text": "x"}, peers)] == ["a", "b"]
    assert [peer["device_id"] for peer in chat_fanout_peers({"to": "b", "text": "x"}, peers)] == ["b"]
    assert chat_fanout_peers({"to": "", "text": "x"}, peers) == peers


def test_auth_ok_uses_compare_digest(store: Store) -> None:
    store.create_group("Home", "join-pass")
    app = create_app(store, 17474)
    token = hmac_hex(store.group_key(), store.group["id"].encode())

    async def _run() -> None:
        async with httpx.AsyncClient(transport=_remote(app), base_url="http://192.168.1.50") as client:
            # Wrong length / wrong token must be a clean 401, not a 500.
            bad = await client.get("/peer/ping", headers={PEER_HEADER: "deadbeef"})
            assert bad.status_code == 401
            empty = await client.get("/peer/ping", headers={PEER_HEADER: ""})
            assert empty.status_code == 401
            ok = await client.get("/peer/ping", headers={PEER_HEADER: token})
            assert ok.status_code == 200

    asyncio.run(_run())
