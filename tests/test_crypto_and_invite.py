from __future__ import annotations

from hearth.app import make_invite, parse_invite
from hearth.crypto import DeviceIdentity, derive_group_key, generate_group_password, hmac_hex, verify_hmac
from hearth.state import Store


def test_password_shape() -> None:
    password = generate_group_password()
    parts = password.split("-")
    assert len(parts) == 3
    assert all(len(p) == 4 for p in parts)


def test_group_key_is_stable(store: Store) -> None:
    group = store.create_group("Home", "test-pass")
    first = store.group_key()
    second = store.group_key()
    assert first == second
    expected = derive_group_key("test-pass", group["id"], bytes.fromhex(group["salt"]))
    assert first == expected


def test_hmac_roundtrip() -> None:
    key = b"x" * 32
    digest = hmac_hex(key, b"hello")
    assert verify_hmac(key, b"hello", digest)
    assert not verify_hmac(key, b"hello", "00" * 32)


def test_device_identity_persists(tmp_path, store: Store) -> None:
    ident = DeviceIdentity.create()
    raw = ident.private_bytes()
    loaded = DeviceIdentity.from_bytes(raw)
    assert loaded.device_id == ident.device_id
    message = b"hearth"
    assert DeviceIdentity.verify(ident.public_b64(), message, ident.sign(message))


def test_invite_roundtrip(store: Store, monkeypatch) -> None:
    store.create_group("Cottage", "alpha-beta")
    monkeypatch.setattr("hearth.app.local_ipv4s", lambda: ["192.168.1.20"])
    invite = make_invite(store, 7474)
    parsed = parse_invite(invite)
    assert parsed["name"] == "Cottage"
    assert parsed["password"] == "alpha-beta"
    assert parsed["hosts"] == ["192.168.1.20:7474"]
    colon = invite.replace("hearth|", "hearth:", 1)
    assert parse_invite(colon)["group_id"] == parsed["group_id"]
