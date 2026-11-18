from __future__ import annotations

import json

import asyncio
import httpx
from fastapi.testclient import TestClient

from hearth.app import create_app
from hearth.discovery import Discovery, datagram_bind_kwargs, open_beacon_transport
from hearth.state import Store


def test_datagram_bind_kwargs_skips_reuse_port_without_so_reuseport(monkeypatch) -> None:
    import socket

    monkeypatch.delattr(socket, "SO_REUSEPORT", raising=False)
    kwargs = datagram_bind_kwargs(45454)
    assert "reuse_port" not in kwargs
    assert kwargs["allow_broadcast"] is True
    assert kwargs["local_addr"] == ("0.0.0.0", 45454)


def test_datagram_bind_kwargs_sets_reuse_port_when_supported(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(socket, "SO_REUSEPORT", 15, raising=False)
    assert datagram_bind_kwargs(1)["reuse_port"] is True


def test_open_beacon_retries_without_reuse_port_on_value_error(monkeypatch) -> None:
    import socket

    monkeypatch.setattr(socket, "SO_REUSEPORT", 15, raising=False)
    calls: list[dict] = []

    class FakeLoop:
        async def create_datagram_endpoint(self, factory, **kwargs):
            calls.append(kwargs)
            if "reuse_port" in kwargs:
                raise ValueError("reuse_port not supported by socket module")

            class Transport:
                def get_extra_info(self, _name):
                    return None

            return Transport(), None

    transport, _protocol = asyncio.run(open_beacon_transport(FakeLoop(), lambda: None))
    assert transport is not None
    assert calls[0]["reuse_port"] is True
    assert "reuse_port" not in calls[1]
    assert calls[1]["local_addr"] == ("0.0.0.0", 45454)


def test_open_beacon_falls_back_to_ephemeral_port_on_oserror() -> None:
    calls: list[dict] = []

    class FakeLoop:
        async def create_datagram_endpoint(self, factory, **kwargs):
            calls.append(dict(kwargs))
            if kwargs["local_addr"][1] != 0:
                raise OSError("address in use")

            class Transport:
                pass

            return Transport(), None

    transport, _protocol = asyncio.run(open_beacon_transport(FakeLoop(), lambda: None))
    assert transport is not None
    assert calls[-1]["local_addr"] == ("0.0.0.0", 0)
    assert "reuse_port" not in calls[-1]


def test_beacon_carries_group_name_and_salt() -> None:
    seen: list[dict] = []

    def on_peer(peer: dict) -> None:
        seen.append(peer)

    d = Discovery(
        device_id="self",
        device_name="Alpha",
        peer_port=7474,
        group_id=None,
        group_key=None,
        on_peer=on_peer,
        group_name="Home",
        group_salt="abcd",
    )
    # Simulate a foreign beacon with group metadata.
    other = Discovery(
        device_id="other",
        device_name="Office-PC",
        peer_port=7474,
        group_id="gid123",
        group_key=b"k" * 32,
        on_peer=lambda p: None,
        group_name="Home",
        group_salt="deadbeef",
    )
    peer = d._accept(other._payload(), ("192.168.1.20", 45454))
    assert peer is not None
    assert peer["group_id"] == "gid123"
    assert peer["group_name"] == "Home"
    assert peer["salt"] == "deadbeef"
    assert peer["name"] == "Office-PC"


def test_ungrouped_peer_is_listed_as_nearby(store: Store) -> None:
    import gc
    import warnings

    app = create_app(store, 17474)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.state.on_peer({
            "device_id": "other",
            "name": "Office-PC",
            "group_id": "gid123",
            "group_name": "Home",
            "salt": "deadbeef",
            "address": "192.168.1.20",
            "port": 7474,
            "ips": ["192.168.1.20"],
            "via": "beacon",
        })
        gc.collect()
    assert not any("never awaited" in str(item.message) for item in caught)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
    state = client.get("/api/state").json()
    assert state["group"] is None
    assert state["theme"] == "system"
    assert len(state["nearby"]) == 1
    offer = state["nearby"][0]
    assert offer["group_name"] == "Home"
    assert offer["salt"] == "deadbeef"
    assert offer["name"] == "Office-PC"


def test_ui_show_is_local_only(store: Store) -> None:
    app = create_app(store, 17474)
    called = {"n": 0}
    app.state.show_ui_callbacks.append(lambda: called.__setitem__("n", called["n"] + 1))

    async def _run() -> None:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            res = await client.post("/api/ui/show")
            assert res.status_code == 200, res.text

    asyncio.run(_run())
    assert called["n"] == 1


def test_join_from_nearby(store: Store, tmp_path) -> None:
    other = Store(tmp_path / "other")
    other.device_name = "Office-PC"
    group = other.create_group("Home", "join-pass")
    host_app = create_app(other, 17501)
    join_app = create_app(store, 17502)
    store.device_name = "Bedroom"
    with TestClient(host_app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as host, TestClient(join_app, base_url="http://127.0.0.1", client=("127.0.0.1", 12346)) as joiner:
        # Host already has a group from store; ensure state is served.
        assert host.get("/api/state").json()["group"]["id"] == group["id"]
        body = {
            "group_id": group["id"],
            "name": "Home",
            "password": "join-pass",
            "salt": group["salt"],
            "device_name": "Bedroom",
            "host": "127.0.0.1:17501",
        }
        res = joiner.post("/api/group/join", json=body)
        assert res.status_code == 200, res.text
        assert res.json()["group"]["id"] == group["id"]
