"""Token-bucket rate limits and transfer I/O constants.

A rate of 0 means unlimited. Changing set_rate() applies to in-flight
callers on their next take(). Burst is kept small (~50 ms of traffic) so
short transfers stay within about 10% of the configured rate.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

# 4 MiB chunks cut per-read overhead without holding a whole file in memory.
TRANSFER_CHUNK = 4 * 1024 * 1024
DOWNLOAD_CONCURRENCY = 4
UPLOAD_CONCURRENCY = 4
PROGRESS_EVERY = 0.25
SOCKET_BUFFER = 4 * 1024 * 1024


def tune_tcp_socket(sock: socket.socket) -> None:
    """Enable TCP_NODELAY and enlarge window sizes when the OS allows it."""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, SOCKET_BUFFER)
        except OSError:
            pass


def httpx_socket_options() -> list[tuple[int, int, int]]:
    return [
        (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
        (socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER),
        (socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER),
    ]


class TokenBucket:
    """Virtual-time token bucket. rate_bps is bytes/second; 0 is unlimited.

    Reservations are assigned under a lock, then the caller sleeps until its
    start time so concurrent transfers share one rate without losing tokens
    to a small burst cap.
    """

    def __init__(self, rate_bps: int = 0) -> None:
        self._lock = asyncio.Lock()
        self._rate = 0
        self._ok_at = time.monotonic()
        self.set_rate(rate_bps)

    @property
    def rate(self) -> int:
        return self._rate

    def _burst_seconds(self) -> float:
        if self._rate <= 0:
            return 0.0
        return max(1024, int(self._rate * 0.05)) / self._rate

    def set_rate(self, rate_bps: int) -> None:
        rate = max(0, int(rate_bps or 0))
        now = time.monotonic()
        # Keep any already reserved wait so in-flight transfers stay limited.
        self._ok_at = max(self._ok_at, now) if self._rate and rate else now
        self._rate = rate

    def _reserve(self, nbytes: int) -> float:
        """Return seconds to wait before this reservation may proceed."""
        n = int(nbytes)
        if n <= 0 or self._rate <= 0:
            return 0.0
        now = time.monotonic()
        earliest = now - self._burst_seconds()
        start = max(self._ok_at, earliest)
        self._ok_at = start + (n / self._rate)
        return max(0.0, start - now)

    async def take(self, nbytes: int) -> None:
        n = int(nbytes)
        if n <= 0:
            return
        async with self._lock:
            if self._rate <= 0:
                return
            wait = self._reserve(n)
        deadline = time.monotonic() + wait
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            await asyncio.sleep(min(left, 0.25))

    def take_sync(self, nbytes: int) -> None:
        """Blocking take for sync file wrappers (peer uploads)."""
        n = int(nbytes)
        if n <= 0:
            return
        if self._rate <= 0:
            return
        wait = self._reserve(n)
        deadline = time.monotonic() + wait
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, 0.25))


class RateLimits:
    def __init__(self, download_bps: int = 0, upload_bps: int = 0) -> None:
        self.download = TokenBucket(download_bps)
        self.upload = TokenBucket(upload_bps)

    def configure(self, download_bps: int = 0, upload_bps: int = 0) -> None:
        self.download.set_rate(download_bps)
        self.upload.set_rate(upload_bps)


class SpeedMeter:
    """Windowed throughput and ETA for the transfer list."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._samples: list[tuple[float, int]] = []
        self.total = 0

    def add(self, nbytes: int) -> None:
        self.total += int(nbytes)
        now = time.monotonic()
        self._samples.append((now, self.total))
        cutoff = now - 2.0
        self._samples = [item for item in self._samples if item[0] >= cutoff]

    def speed(self) -> int:
        if len(self._samples) >= 2:
            dt = self._samples[-1][0] - self._samples[0][0]
            db = self._samples[-1][1] - self._samples[0][1]
            if dt > 0:
                return int(db / dt)
        elapsed = max(0.2, time.monotonic() - self._started)
        return int(self.total / elapsed) if self.total else 0

    def eta(self, remaining: int) -> int | None:
        leftover = int(remaining)
        if leftover <= 0:
            return 0
        rate = self.speed()
        if rate <= 0:
            return None
        return int(leftover / rate)


def socket_options_transport() -> Any | None:
    """httpx transport with TCP_NODELAY and larger buffers, or None if unsupported."""
    try:
        import httpx

        return httpx.AsyncHTTPTransport(socket_options=httpx_socket_options())
    except TypeError:
        return None
    except Exception:
        return None
