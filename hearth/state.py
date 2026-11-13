"""Persistent local state under ~/.hearth."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any

from .crypto import DeviceIdentity, derive_group_key, generate_group_password


def default_data_dir() -> Path:
    override = os.environ.get("HEARTH_HOME")
    if override:
        return Path(override)
    return Path.home() / ".hearth"


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.json"
        self.data: dict[str, Any] = {
            "device_name": socket.gethostname(),
            "theme": "system",
            "group": None,
            "known_peers": {},
            "shares": [],
            "chat": [],
            "bridge": False,
            "manual_hosts": [],
        }
        self.identity = self._load_identity()
        self.load()

    def _load_identity(self) -> DeviceIdentity:
        key_path = self.root / "device.key"
        if key_path.exists():
            return DeviceIdentity.from_bytes(key_path.read_bytes())
        ident = DeviceIdentity.create()
        key_path.write_bytes(ident.private_bytes())
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        return ident

    def load(self) -> None:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update(loaded)
            except json.JSONDecodeError:
                pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def device_name(self) -> str:
        return self.data.get("device_name") or socket.gethostname()

    @device_name.setter
    def device_name(self, value: str) -> None:
        self.data["device_name"] = value.strip() or socket.gethostname()
        self.save()

    @property
    def theme(self) -> str:
        return self.data.get("theme") or "system"

    @theme.setter
    def theme(self, value: str) -> None:
        self.data["theme"] = value
        self.save()

    @property
    def group(self) -> dict[str, Any] | None:
        return self.data.get("group")

    def create_group(self, name: str, password: str | None = None) -> dict[str, Any]:
        import secrets

        password = password or generate_group_password()
        group_id = secrets.token_hex(8)
        salt = secrets.token_bytes(16)
        key = derive_group_key(password, group_id, salt)
        group = {
            "id": group_id,
            "name": name.strip() or "Home",
            "password": password,
            "salt": salt.hex(),
            "verifier": key.hex()[:32],
            "created": time.time(),
            "creator_id": self.identity.device_id,
        }
        self.data["group"] = group
        self.save()
        return group

    def join_group(self, group_id: str, name: str, password: str, salt_hex: str) -> dict[str, Any]:
        salt = bytes.fromhex(salt_hex)
        key = derive_group_key(password, group_id, salt)
        group = {
            "id": group_id,
            "name": name.strip() or "Home",
            "password": password,
            "salt": salt_hex,
            "verifier": key.hex()[:32],
            "created": time.time(),
            "creator_id": None,
        }
        self.data["group"] = group
        self.save()
        return group

    def leave_group(self) -> None:
        self.data["group"] = None
        self.data["known_peers"] = {}
        self.data["chat"] = []
        self.save()

    def group_key(self) -> bytes | None:
        g = self.group
        if not g:
            self._key_cache = None
            return None
        cache_id = (g["id"], g["password"], g["salt"])
        cached = getattr(self, "_key_cache", None)
        if cached and cached[0] == cache_id:
            return cached[1]
        key = derive_group_key(g["password"], g["id"], bytes.fromhex(g["salt"]))
        self._key_cache = (cache_id, key)
        return key

    def remember_peer(self, peer: dict[str, Any]) -> None:
        peers = self.data.setdefault("known_peers", {})
        peers[peer["device_id"]] = {
            **peers.get(peer["device_id"], {}),
            **peer,
            "last_seen": time.time(),
        }
        self.save()

    def add_share(self, path: str, label: str, mode: str = "read") -> dict[str, Any]:
        share = {
            "id": os.urandom(4).hex(),
            "path": str(Path(path).expanduser().resolve()),
            "label": label,
            "mode": mode,
            "added": time.time(),
        }
        self.data.setdefault("shares", []).append(share)
        self.save()
        return share

    def remove_share(self, share_id: str) -> None:
        self.data["shares"] = [s for s in self.data.get("shares", []) if s["id"] != share_id]
        self.save()

    def append_chat(self, msg: dict[str, Any]) -> dict[str, Any]:
        inbox = self.data.setdefault("chat", [])
        inbox.append(msg)
        if len(inbox) > 500:
            self.data["chat"] = inbox[-500:]
        self.save()
        return msg
