"""A Hearth profile made and read by the shipped Python app, for the upgrade tests.

    python python_profile.py make <home> <files>   fill <home> as a long-used Hearth 0.4 would
    python python_profile.py dump <home>           print what Hearth 0.4 reads from <home>

<files> is a folder for the shared folders and downloads the profile points at.
Run with the Hearth repository root on PYTHONPATH. Only ever pointed at temporary folders.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HEARTH_NO_UPDATE", "1")

from hearth import update  # noqa: E402
from hearth.state import Store  # noqa: E402
from hearth.transfers import TransferHub  # noqa: E402


def make(home: Path, files: Path) -> None:
    photos = files / "Fotos der Familie"
    docs = files / "Dokumente"
    downloads = files / "Downloads"
    for d in (photos, docs, downloads):
        d.mkdir(parents=True, exist_ok=True)
    store = Store(home)
    store.device_name = "Den PC – Wohnzimmer"
    store.theme = "forest"
    store.data["language"] = "de"
    store.save()
    group = store.create_group("Familie Müller", "ofen kamin zweiundvierzig")
    store.add_share(str(photos), "Fotos", "readwrite")
    store.add_share(str(docs), "", "read")
    store.remember_peer({
        "device_id": "a" * 32,
        "name": "Küche",
        "port": 7474,
        "ips": ["192.0.2.10"],
        "address": "192.0.2.10",
        "via": "beacon",
        "salt": group["salt"],
        "public_key": "b" * 64,
    })
    store.remember_peer({
        "device_id": "c" * 32,
        "name": "Büro",
        "port": 7480,
        "ips": ["192.0.2.11", "198.51.100.4"],
        "address": "192.0.2.11:7480",
        "via": "manual",
    })
    me = store.identity.device_id
    now = time.time()
    store.append_chat({"id": "m1", "from_id": "a" * 32, "from_name": "Küche", "to": None,
                       "text": "Essen ist fertig 🍲", "ts": now - 600})
    store.append_chat({"id": "m2", "from_id": me, "from_name": store.device_name, "to": None,
                       "text": "Komme gleich", "ts": now - 590})
    store.append_chat({"id": "m3", "from_id": me, "from_name": store.device_name, "to": "c" * 32,
                       "text": "Nur für dich", "ts": now - 580})
    store.data["bridge"] = True
    store.data["manual_hosts"] = ["192.0.2.11:7480", "198.51.100.4"]
    store.data["auto_check_updates"] = False
    store.data["download_folder"] = str(downloads)
    store.data["download_limit_bps"] = 5 * 1024 * 1024
    store.data["upload_limit_bps"] = 256 * 1024
    store.data["last_update_check"] = now - 3600
    store.save()

    hub = TransferHub(persist_path=home / "transfers.json")
    for i, (status, message) in enumerate([("done", "Saved"), ("error", "Network error"), ("canceled", "Canceled")]):
        job = hub._new("download", f"Urlaub {i}.jpg", downloads / f"Urlaub {i}.jpg")
        job["bytes"] = 1000 * (i + 1)
        job["total"] = 3000
        job["status"] = status
        job["message"] = message
    hub._persist()

    update.write_relaunch_ui(home, "window")
    (home / "inbox").mkdir(exist_ok=True)
    (home / "inbox" / "received.txt").write_text("from the Küche PC", encoding="utf-8")
    (home / "hearth.log").write_text("Hearth  ·  Den PC\n", encoding="utf-8")


def dump(home: Path) -> None:
    store = Store(home)
    hub = TransferHub(persist_path=home / "transfers.json")
    out = {
        "data": store.data,
        "device_id": store.identity.device_id,
        "device_key": (home / "device.key").read_bytes().hex(),
        "transfers": sorted(hub.list(), key=lambda j: j["id"]),
        "relaunch_ui": update.read_relaunch_ui(home),
    }
    print(json.dumps(out, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    if sys.argv[1] == "make":
        make(Path(sys.argv[2]), Path(sys.argv[3]))
    elif sys.argv[1] == "dump":
        dump(Path(sys.argv[2]))
    else:
        raise SystemExit("make or dump")
