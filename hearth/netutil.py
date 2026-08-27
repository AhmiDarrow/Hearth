"""Local interface and home-subnet helpers."""

from __future__ import annotations

import ipaddress
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


def local_ipv4s() -> list[str]:
    found: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                found.add(ip)
    except OSError:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        found.add(sock.getsockname()[0])
        sock.close()
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
                targets.add(host.split(":")[0])
    targets -= local_set
    return sorted(targets)


def is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False
