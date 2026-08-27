"""UDP beacons + multi-subnet unicast probes + optional mDNS."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from .crypto import hmac_hex, verify_hmac
from .netutil import candidate_probe_ips, local_ipv4s

BEACON_PORT = 45454
MULTICAST_GROUP = "239.54.54.54"
PROTOCOL = "HEARTH1"


class Discovery:
    def __init__(
        self,
        *,
        device_id: str,
        device_name: str,
        peer_port: int,
        group_id: str | None,
        group_key: bytes | None,
        on_peer: Callable[[dict[str, Any]], None],
        manual_hosts: list[str] | None = None,
        bridge: bool = False,
    ) -> None:
        self.device_id = device_id
        self.device_name = device_name
        self.peer_port = peer_port
        self.group_id = group_id
        self.group_key = group_key
        self.on_peer = on_peer
        self.manual_hosts = manual_hosts or []
        self.bridge = bridge
        self._transport: asyncio.DatagramTransport | None = None
        self._running = False
        self._seen: dict[str, float] = {}

    def update_group(self, group_id: str | None, group_key: bytes | None) -> None:
        self.group_id = group_id
        self.group_key = group_key

    def _payload(self) -> bytes:
        body = {
            "v": PROTOCOL,
            "device_id": self.device_id,
            "name": self.device_name,
            "group_id": self.group_id,
            "port": self.peer_port,
            "ips": local_ipv4s(),
            "bridge": self.bridge,
            "ts": int(time.time()),
        }
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        mac = hmac_hex(self.group_key or b"public", raw) if self.group_key else ""
        envelope = {"body": body, "mac": mac}
        return json.dumps(envelope, separators=(",", ":")).encode()

    def _accept(self, data: bytes, addr: tuple[str, int]) -> dict[str, Any] | None:
        try:
            envelope = json.loads(data.decode("utf-8"))
            body = envelope["body"]
            if body.get("v") != PROTOCOL:
                return None
            if body.get("device_id") == self.device_id:
                return None
            if self.group_id and body.get("group_id") != self.group_id:
                return None
            if self.group_key:
                raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
                if not verify_hmac(self.group_key, raw, envelope.get("mac") or ""):
                    return None
            peer = {
                "device_id": body["device_id"],
                "name": body.get("name") or "PC",
                "port": int(body.get("port") or self.peer_port),
                "ips": body.get("ips") or [addr[0]],
                "address": addr[0],
                "bridge": bool(body.get("bridge")),
                "via": "beacon",
            }
            return peer
        except Exception:
            return None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._running = True

        class Protocol(asyncio.DatagramProtocol):
            def __init__(self, outer: "Discovery") -> None:
                self.outer = outer

            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                peer = self.outer._accept(data, addr)
                if not peer:
                    return
                now = time.time()
                last = self.outer._seen.get(peer["device_id"], 0)
                self.outer._seen[peer["device_id"]] = now
                if now - last > 1:
                    self.outer.on_peer(peer)
                    if self.outer.bridge and self.outer._transport:
                        self.outer._transport.sendto(self.outer._payload(), (addr[0], BEACON_PORT))

        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: Protocol(self),
                local_addr=("0.0.0.0", BEACON_PORT),
                allow_broadcast=True,
                reuse_port=True,
            )
        except TypeError:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: Protocol(self),
                local_addr=("0.0.0.0", BEACON_PORT),
                allow_broadcast=True,
            )
        except OSError:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: Protocol(self),
                local_addr=("0.0.0.0", 0),
                allow_broadcast=True,
            )

        try:
            sock = self._transport.get_extra_info("socket")
            if sock is not None:
                import struct

                mreq = struct.pack("=4sl", __import__("socket").inet_aton(MULTICAST_GROUP), __import__("socket").INADDR_ANY)
                sock.setsockopt(__import__("socket").IPPROTO_IP, __import__("socket").IP_ADD_MEMBERSHIP, mreq)
                sock.setsockopt(__import__("socket").IPPROTO_IP, __import__("socket").IP_MULTICAST_TTL, 8)
        except OSError:
            pass

        asyncio.create_task(self._announce_loop())

    async def _announce_loop(self) -> None:
        while self._running:
            self.announce()
            await asyncio.sleep(3)

    def announce(self) -> None:
        if not self._transport or not self.group_id:
            return
        payload = self._payload()
        dests = {
            ("255.255.255.255", BEACON_PORT),
            (MULTICAST_GROUP, BEACON_PORT),
        }
        for ip in local_ipv4s():
            parts = ip.split(".")
            dests.add((".".join(parts[:3] + ["255"]), BEACON_PORT))
        for ip in candidate_probe_ips(local_ipv4s(), self.manual_hosts):
            dests.add((ip, BEACON_PORT))
        for dest in dests:
            try:
                self._transport.sendto(payload, dest)
            except OSError:
                continue

    def stop(self) -> None:
        self._running = False
        if self._transport:
            self._transport.close()
            self._transport = None
