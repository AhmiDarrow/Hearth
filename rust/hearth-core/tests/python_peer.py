"""A real Hearth 0.3.x peer for the Rust interop tests.

    python python_peer.py serve <config.json>   run a peer; prints "READY <port>" when up
    python python_peer.py beacon <config.json>  print one beacon datagram (as hex)
    python python_peer.py accept <config.json>  read a beacon (hex) on stdin, print the peer or null

config: {"home", "port", "name", "group": {"id", "name", "password", "salt"} | null,
         "shares": [{"path", "label", "mode"}], "download_folder"}
Run with the Hearth repository root on PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HEARTH_NO_UPDATE", "1")

from hearth.app import create_app  # noqa: E402
from hearth.crypto import derive_group_key  # noqa: E402
from hearth.discovery import Discovery  # noqa: E402
from hearth.state import Store  # noqa: E402


def store_for(cfg: dict) -> Store:
    store = Store(Path(cfg["home"]))
    store.device_name = cfg.get("name", "Python")
    g = cfg.get("group")
    if g:
        store.join_group(g["id"], g["name"], g["password"], g["salt"])
    for s in cfg.get("shares", []):
        store.add_share(s["path"], s.get("label", ""), s.get("mode", "read"))
    if cfg.get("download_folder"):
        store.data["download_folder"] = cfg["download_folder"]
        store.save()
    return store


def discovery_for(cfg: dict, store: Store) -> Discovery:
    g = cfg.get("group")
    key = derive_group_key(g["password"], g["id"], bytes.fromhex(g["salt"])) if g else None
    return Discovery(
        device_id=store.identity.device_id,
        device_name=store.device_name,
        peer_port=cfg.get("port", 7474),
        group_id=g["id"] if g else None,
        group_key=key,
        on_peer=lambda _p: None,
        group_name=g["name"] if g else None,
        group_salt=g["salt"] if g else None,
    )


def main() -> None:
    mode, path = sys.argv[1], sys.argv[2]
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    store = store_for(cfg)
    if mode == "beacon":
        print(discovery_for(cfg, store)._payload().hex())
        return
    if mode == "accept":
        data = bytes.fromhex(sys.stdin.read().strip())
        print(json.dumps(discovery_for(cfg, store)._accept(data, ("192.168.77.7", 45454))))
        return
    import uvicorn

    port = int(cfg["port"])
    server = uvicorn.Server(uvicorn.Config(create_app(store, port), host="0.0.0.0", port=port, log_level="warning"))
    import threading

    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    print(f"READY {port} {store.identity.device_id}", flush=True)
    # Serve until the test closes our stdin.
    sys.stdin.read()
    server.should_exit = True
    t.join(timeout=5)


if __name__ == "__main__":
    main()
