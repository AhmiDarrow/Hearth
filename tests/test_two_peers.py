from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from hearth.app import create_app
from hearth.state import Store


class Server(threading.Thread):
    def __init__(self, store: Store, port: int) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.app = create_app(store, port)
        self._server = uvicorn.Server(uvicorn.Config(self.app, host="0.0.0.0", port=port, log_level="warning"))

    def run(self) -> None:
        self._server.run()

    def start_background(self) -> None:
        self.start()
        deadline = time.time() + 8
        while time.time() < deadline:
            if self._server.started:
                return
            time.sleep(0.05)
        raise RuntimeError(f"Server on {self.port} did not start")

    def stop(self) -> None:
        self._server.should_exit = True
        self.join(timeout=5)


@pytest.fixture()
def peers(tmp_path: Path):
    store_a = Store(tmp_path / "a")
    store_a.device_name = "Alpha"
    store_b = Store(tmp_path / "b")
    store_b.device_name = "Bravo"
    a = Server(store_a, 17474)
    b = Server(store_b, 17475)
    a.start_background()
    b.start_background()
    try:
        yield {"a": a, "b": b, "store_a": store_a, "store_b": store_b, "share": tmp_path / "share"}
    finally:
        a.stop()
        b.stop()


def test_create_join_share_chat_and_files(peers, tmp_path: Path) -> None:
    share_dir = tmp_path / "share"
    share_dir.mkdir()
    (share_dir / "hello.txt").write_text("from alpha", encoding="utf-8")
    drop = tmp_path / "drop"
    drop.mkdir()

    with httpx.Client(timeout=10.0) as client:
        created = client.post("http://127.0.0.1:17474/api/group/create", json={"name": "Home", "password": "test-pass", "device_name": "Alpha"}).json()
        assert created["group"]["name"] == "Home"
        invite = created["group"]["invite"]
        # Invites advertise LAN IPs only. Append loopback so handshake works in this test.
        if "127.0.0.1:17474" not in invite:
            invite = invite + (",127.0.0.1:17474" if invite.count("|") >= 5 else "|127.0.0.1:17474")
        joined = client.post("http://127.0.0.1:17475/api/group/invite", json={"invite": invite, "device_name": "Bravo"})
        assert joined.status_code == 200, joined.text
        members = joined.json()["members"]
        if not any((m["name"] == "Alpha" or m.get("address") == "127.0.0.1") and not m.get("self") for m in members):
            added = client.post("http://127.0.0.1:17475/api/hosts", json={"host": "127.0.0.1:17474"})
            assert added.status_code == 200, added.text
            members = added.json()["members"]

        # Bravo should see Alpha after HTTP handshake, even when UDP beacons collide.
        deadline = time.time() + 8
        names = []
        while time.time() < deadline:
            state_b = client.get("http://127.0.0.1:17475/api/state").json()
            state_a = client.get("http://127.0.0.1:17474/api/state").json()
            names = (
                [m["name"] for m in state_b["members"] if m.get("online")],
                [m["name"] for m in state_a["members"] if m.get("online")],
            )
            if "Alpha" in names[0] and "Bravo" in names[1]:
                break
            time.sleep(0.25)
        assert "Alpha" in names[0], f"Bravo did not discover Alpha: {names}"
        assert "Bravo" in names[1], f"Alpha did not discover Bravo: {names}"

        shared = client.post("http://127.0.0.1:17474/api/shares", json={"path": str(share_dir), "label": "Notes", "mode": "read"})
        assert shared.status_code == 200, shared.text
        drop_share = client.post("http://127.0.0.1:17474/api/shares", json={"path": str(drop), "label": "Drop", "mode": "readwrite"})
        assert drop_share.status_code == 200

        alpha = client.get("http://127.0.0.1:17474/api/state").json()
        alpha_id = alpha["device"]["id"]
        notes = next(s for s in alpha["shares"] if s["label"] == "Notes")
        drop_info = next(s for s in alpha["shares"] if s["label"] == "Drop")

        remote = client.get(f"http://127.0.0.1:17475/api/remote-shares/{alpha_id}")
        assert remote.status_code == 200, remote.text
        labels = {s["label"] for s in remote.json()["shares"]}
        assert labels == {"Notes", "Drop"}

        listing = client.get(f"http://127.0.0.1:17475/api/files/{alpha_id}/{notes['id']}")
        assert listing.status_code == 200, listing.text
        names_in_share = {item["name"] for item in listing.json()["items"]}
        assert "hello.txt" in names_in_share

        download = client.get(f"http://127.0.0.1:17475/api/download/{alpha_id}/{notes['id']}", params={"path": "hello.txt"})
        assert download.status_code == 200
        assert b"from alpha" in download.content

        upload = client.post(
            f"http://127.0.0.1:17475/api/upload/{alpha_id}/{drop_info['id']}",
            files={"file": ("from-bravo.txt", b"hello from bravo", "text/plain")},
        )
        assert upload.status_code == 200, upload.text
        assert (drop / "from-bravo.txt").read_text(encoding="utf-8") == "hello from bravo"

        chat = client.post("http://127.0.0.1:17475/api/chat", json={"text": "Anyone home?"})
        assert chat.status_code == 200, chat.text
        deadline = time.time() + 8
        seen = []
        while time.time() < deadline:
            seen = [m["text"] for m in client.get("http://127.0.0.1:17474/api/state").json()["chat"]]
            if "Anyone home?" in seen:
                break
            time.sleep(0.25)
        assert "Anyone home?" in seen, f"Alpha did not receive chat: {seen}"
