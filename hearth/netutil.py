"""Local interface and home-subnet helpers."""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Iterable

COMMON_HOME_NETS = [
    "192.168.0.0/24",
    "192.168.1.0/24",
    "192.168.2.0/24",
    "192.168.8.0/24",
    "192.168.10.0/24",
    "192.168.50.0/24",
    "192.168.68.0/24",
    "192.168.86.0/24",
    "10.0.0.0/24",
    "10.0.1.0/24",
    "10.1.1.0/24",
    "172.16.0.0/24",
    "172.16.1.0/24",
]

# Virtual / container adapters that should not appear in invite codes.
_SKIP_IFACE = re.compile(
    r"docker|veth|vethernet|vbox|vmware|virtualbox|hyper-v|wsl|"
    r"loopback|bluetooth|virbr|default switch|hyperv|br-|cni|"
    r"flannel|calico|tun|tap|wsl2|npcap|pseudo-interface",
    re.I,
)


def _is_usable_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    return addr.is_private


def _adapter_ips() -> list[tuple[str, str]]:
    """Return (adapter_name, ipv4) pairs for private addresses."""
    found: list[tuple[str, str]] = []
    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            name = adapter.nice_name or adapter.name or ""
            for ip in adapter.ips:
                raw = ip.ip if isinstance(ip.ip, str) else None
                if raw and _is_usable_ipv4(raw):
                    found.append((name, raw))
    except Exception:
        pass
    return found


def local_ipv4s() -> list[str]:
    """Best-effort LAN addresses for this PC, preferring real home NICs.

    Virtual adapters (WSL, Hyper-V, Docker, VMware) are skipped when a
    physical-looking interface has an address, so invite codes stay useful
    to the rest of the house.
    """
    adapters = _adapter_ips()
    real = [(n, ip) for n, ip in adapters if not _SKIP_IFACE.search(n)]
    chosen = real or adapters
    found = {ip for _, ip in chosen}

    if not found:
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                ip = info[4][0]
                if _is_usable_ipv4(ip):
                    found.add(ip)
        except OSError:
            pass
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("1.1.1.1", 80))
            ip = sock.getsockname()[0]
            sock.close()
            if _is_usable_ipv4(ip):
                found.add(ip)
        except OSError:
            pass
    return sorted(found)


def default_gateway_guess(local_ips: Iterable[str]) -> list[str]:
    guesses: list[str] = []
    for ip in local_ips:
        net = ipaddress.ip_network(ip + "/24", strict=False)
        hosts = list(net.hosts())
        if hosts:
            guesses.append(str(hosts[0]))
            guesses.append(str(hosts[-1]))
    return guesses


def candidate_probe_ips(local_ips: Iterable[str], extra_hosts: Iterable[str] | None = None) -> list[str]:
    """IPs worth unicast-probing when broadcasts cannot cross a sub-router."""
    targets: set[str] = set()
    local_set = set(local_ips)
    for ip in local_ips:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        for host in (1, 2, 100, 101, 254):
            candidate = str(net.network_address + host)
            if candidate not in local_set:
                targets.add(candidate)
    for cidr in COMMON_HOME_NETS:
        net = ipaddress.ip_network(cidr)
        ours = any(ipaddress.ip_address(ip) in net for ip in local_ips)
        targets.add(str(net.network_address + 1))
        targets.add(str(net.network_address + 254))
        if ours:
            continue
    if extra_hosts:
        for host in extra_hosts:
            host = host.strip()
            if host:
                targets.add(host.split(":")[0].strip("[]"))
    targets -= local_set
    return sorted(targets)


def is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def parse_host(host: str) -> tuple[str, int]:
    """Split 'ip', 'ip:port', or 'hostname:port' into (host, port)."""
    raw = (host or "").strip()
    if not raw:
        raise ValueError("Host required")
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            raise ValueError("Bad IPv6 host")
        ip = raw[1:end]
        rest = raw[end + 1 :]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:] else 7474
        return ip, port
    if raw.count(":") == 1:
        ip, port_s = raw.split(":")
        return ip, int(port_s)
    return raw, 7474
