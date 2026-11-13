"""Local-only access, Host-header, and CSRF origin checks for the UI/control plane."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from .netutil import local_ipv4s

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}
LOCAL_SCHEMES = {"http", "https"}


def parse_host_header(value: str) -> str:
    """Return the hostname from a Host or :authority header, without the port."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            return raw
        return raw[: end + 1]
    if raw.count(":") == 1:
        return raw.split(":", 1)[0]
    return raw


def is_loopback_address(host: str) -> bool:
    raw = (host or "").strip().lower()
    if not raw:
        return False
    if raw in LOOPBACK_NAMES or raw.startswith("127."):
        return True
    if raw.startswith("::ffff:"):
        return is_loopback_address(raw[7:])
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


def client_is_local(request: Any) -> bool:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) or ""
    return is_loopback_address(host)


def _local_ip_set() -> set[str]:
    return {ip.lower() for ip in local_ipv4s()}


def hostname_is_allowed(hostname: str) -> bool:
    host = (hostname or "").strip().lower()
    if not host:
        return False
    if host in LOOPBACK_NAMES or is_loopback_address(host.strip("[]")):
        return True
    return host in _local_ip_set()


def host_header_allowed(request: Any) -> bool:
    """Reject DNS-rebinding by allowing only loopback and this machine's IPs."""
    headers = getattr(request, "headers", {}) or {}
    raw = headers.get("host") or headers.get(":authority") or ""
    return hostname_is_allowed(parse_host_header(raw))


def _url_is_local(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in LOCAL_SCHEMES:
        return False
    return hostname_is_allowed(parsed.hostname or "")


def csrf_origin_ok(request: Any) -> bool:
    """Block a malicious website from driving the local UI (CSRF / confused deputy).

    Browser requests from another origin send Origin (or Referer). Native
    clients and a typed-in address bar usually send neither, and are allowed
    once the client and Host checks have passed.
    """
    headers = getattr(request, "headers", {}) or {}
    if (headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return False
    origin = (headers.get("origin") or "").strip()
    if origin:
        if origin.lower() == "null":
            return False
        return _url_is_local(origin)
    referer = (headers.get("referer") or "").strip()
    if referer:
        return _url_is_local(referer)
    return True


def local_ui_allowed(request: Any) -> bool:
    return client_is_local(request) and host_header_allowed(request) and csrf_origin_ok(request)
