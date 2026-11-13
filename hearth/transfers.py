"""Background file transfers so the UI never navigates or blocks on a download."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .files import MAX_UPLOAD_BYTES, UploadTooLarge, guess_type, safe_filename
from .prefs import default_download_folder, unique_dest, user_facing_error
from .throttle import (
    DOWNLOAD_CONCURRENCY,
    PROGRESS_EVERY,
    TRANSFER_CHUNK,
    RateLimits,
    SpeedMeter,
    socket_options_transport,
)

log = logging.getLogger("hearth.transfers")

Broadcast = Callable[[str, Any | None], Awaitable[None]]


def transfer_client() -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "timeout": None,
        "limits": httpx.Limits(max_keepalive_connections=8, max_connections=16),
    }
    transport = socket_options_transport()
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


FINISHED_STATUSES = frozenset({"done", "error", "canceled"})
ACTIVE_STATUSES = frozenset({"queued", "running"})


class TransferHub:
    def __init__(self, limits: RateLimits | None = None, persist_path: Path | None = None) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self._cancels: dict[str, asyncio.Event] = {}
        self.limits = limits or RateLimits()
        self._dl_gate: asyncio.Semaphore | None = None
        self._persist_path = persist_path
        self._load()

    def _gate(self) -> asyncio.Semaphore:
        if self._dl_gate is None:
            self._dl_gate = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        return self._dl_gate

    def list(self) -> list[dict[str, Any]]:
        return [dict(job) for job in self.jobs.values()]

    def get(self, tid: str) -> dict[str, Any] | None:
        job = self.jobs.get(tid)
        return dict(job) if job else None

    def cancel(self, tid: str) -> dict[str, Any] | None:
        job = self.jobs.get(tid)
        if not job:
            return None
        event = self._cancels.get(tid)
        if event:
            event.set()
        if job.get("status") in ACTIVE_STATUSES:
            job["status"] = "canceled"
            job["message"] = "Canceled"
            job["updated"] = time.time()
            self._persist()
        return dict(job)

    def dismiss(self, tid: str) -> dict[str, Any] | None:
        """Remove a finished entry from the list. Never deletes the file or stops an active transfer."""
        job = self.jobs.get(tid)
        if not job or job.get("status") not in FINISHED_STATUSES:
            return None
        self.jobs.pop(tid, None)
        self._cancels.pop(tid, None)
        self._persist()
        return dict(job)

    def clear_finished(self) -> list[dict[str, Any]]:
        """Drop completed, failed, and canceled rows. Active transfers and files on disk stay."""
        removed: list[dict[str, Any]] = []
        for key, job in list(self.jobs.items()):
            if job.get("status") in FINISHED_STATUSES:
                self.jobs.pop(key, None)
                self._cancels.pop(key, None)
                removed.append(dict(job))
        self._persist()
        return removed

    def _serializable(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job.get("id"),
            "kind": job.get("kind") or "download",
            "name": job.get("name") or "",
            "dest": job.get("dest") or "",
            "bytes": int(job.get("bytes") or 0),
            "total": int(job.get("total") or 0),
            "speed": 0,
            "eta": None,
            "status": job.get("status"),
            "message": job.get("message") or "",
            "started": job.get("started") or 0,
            "updated": job.get("updated") or 0,
        }

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        finished = [self._serializable(job) for job in self.jobs.values() if job.get("status") in FINISHED_STATUSES]
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(json.dumps(finished, indent=2), encoding="utf-8")
        except OSError:
            log.exception("Could not save the transfer list")

    def _load(self) -> None:
        path = self._persist_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read the transfer list")
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            status = item.get("status")
            if status not in FINISHED_STATUSES:
                continue
            job = self._serializable(item)
            self.jobs[str(job["id"])] = job

    def _new(self, kind: str, name: str, dest: Path | None = None, **extra: Any) -> dict[str, Any]:
        tid = uuid.uuid4().hex[:12]
        job = {
            "id": tid,
            "kind": kind,
            "name": name,
            "dest": str(dest) if dest else "",
            "bytes": 0,
            "total": 0,
            "speed": 0,
            "eta": None,
            "status": "queued",
            "message": "",
            "started": time.time(),
            "updated": time.time(),
            **extra,
        }
        self.jobs[tid] = job
        self._cancels[tid] = asyncio.Event()
        if len(self.jobs) > 80:
            drop = [key for key, item in self.jobs.items() if item.get("status") in FINISHED_STATUSES]
            for key in drop[:-40]:
                self.jobs.pop(key, None)
                self._cancels.pop(key, None)
            self._persist()
        return job

    async def download_to_folder(
        self,
        *,
        name: str,
        folder: Path,
        source: Callable[[], Awaitable[tuple[httpx.Response, httpx.AsyncClient] | Path]],
        broadcast: Broadcast | None = None,
        total: int = 0,
    ) -> dict[str, Any]:
        dest = unique_dest(folder, name)
        job = self._new("download", dest.name, dest, total=total)
        if broadcast:
            await broadcast("transfers", self.list())
        event = self._cancels[job["id"]]

        async def emit() -> None:
            if broadcast:
                try:
                    await broadcast("transfers", self.list())
                except Exception:
                    pass

        async def run() -> None:
            tmp = dest.with_name(f".{dest.name}.{os.urandom(4).hex()}.part")
            client: httpx.AsyncClient | None = None
            gate = self._gate()
            await gate.acquire()
            try:
                if event.is_set():
                    raise asyncio.CancelledError()
                job["status"] = "running"
                job["updated"] = time.time()
                await emit()
                origin = await source()
                if isinstance(origin, Path):
                    await _copy_path(origin, tmp, job, event, self.limits.download, emit)
                else:
                    response, client = origin
                    try:
                        if response.status_code != 200:
                            raise RuntimeError(_peer_message(response))
                        job["total"] = int(response.headers.get("content-length") or job.get("total") or 0)
                        meter = SpeedMeter()
                        last_ui = time.monotonic()
                        written = 0

                        def write_chunk(handle: Any, data: bytes) -> None:
                            handle.write(data)

                        with tmp.open("wb", buffering=TRANSFER_CHUNK) as handle:
                            async for chunk in response.aiter_bytes(TRANSFER_CHUNK):
                                if event.is_set():
                                    raise asyncio.CancelledError()
                                await self.limits.download.take(len(chunk))
                                written += len(chunk)
                                if written > MAX_UPLOAD_BYTES:
                                    raise UploadTooLarge("File is too large")
                                await asyncio.to_thread(write_chunk, handle, chunk)
                                meter.add(len(chunk))
                                now = time.monotonic()
                                job["bytes"] = written
                                job["speed"] = meter.speed()
                                leftover = (job["total"] or 0) - written
                                job["eta"] = meter.eta(leftover) if job["total"] else None
                                job["updated"] = time.time()
                                if now - last_ui >= PROGRESS_EVERY:
                                    last_ui = now
                                    await emit()
                    finally:
                        try:
                            await response.aclose()
                        except Exception:
                            pass
                if event.is_set():
                    raise asyncio.CancelledError()
                tmp.replace(dest)
                job["bytes"] = dest.stat().st_size if dest.exists() else job["bytes"]
                job["total"] = job["bytes"] or job["total"]
                job["speed"] = job["speed"] or 0
                job["eta"] = 0
                job["status"] = "done"
                job["message"] = "Saved"
                job["dest"] = str(dest)
            except asyncio.CancelledError:
                job["status"] = "canceled"
                job["message"] = "Canceled"
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            except UploadTooLarge as exc:
                job["status"] = "error"
                job["message"] = str(exc)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            except Exception as exc:
                log.exception("Download failed for %s", job["name"])
                job["status"] = "error"
                job["message"] = user_facing_error(exc)
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            finally:
                job["updated"] = time.time()
                gate.release()
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass
                self._persist()
                await emit()

        asyncio.create_task(run())
        return dict(job)


async def _copy_path(
    src: Path,
    dest: Path,
    job: dict[str, Any],
    event: asyncio.Event,
    bucket: Any,
    emit: Callable[[], Awaitable[None]],
) -> None:
    job["total"] = src.stat().st_size
    meter = SpeedMeter()
    last_ui = time.monotonic()
    written = 0
    use_sendfile = hasattr(os, "sendfile")

    def copy_once() -> int:
        nonlocal written, last_ui
        total = job["total"] or 0
        with src.open("rb") as reader, dest.open("wb", buffering=TRANSFER_CHUNK) as writer:
            if use_sendfile:
                offset = 0
                while offset < total:
                    if event.is_set():
                        raise asyncio.CancelledError()
                    step = min(TRANSFER_CHUNK, total - offset)
                    # sendfile is sync; throttle in the async loop between slices
                    sent = os.sendfile(writer.fileno(), reader.fileno(), offset, step)
                    if sent <= 0:
                        break
                    offset += sent
                    written = offset
                return written
            while True:
                if event.is_set():
                    raise asyncio.CancelledError()
                chunk = reader.read(TRANSFER_CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                written += len(chunk)
        return written

    # Slice the copy so the token bucket and UI can keep up.
    total = job["total"] or 0
    if use_sendfile:
        offset = 0
        with src.open("rb") as reader, dest.open("wb", buffering=TRANSFER_CHUNK) as writer:
            while offset < total:
                if event.is_set():
                    raise asyncio.CancelledError()
                step = min(TRANSFER_CHUNK, total - offset)

                def send_slice(off: int = offset, size: int = step) -> int:
                    return os.sendfile(writer.fileno(), reader.fileno(), off, size)

                sent = await asyncio.to_thread(send_slice)
                if sent <= 0:
                    break
                await bucket.take(sent)
                offset += sent
                written = offset
                meter.add(sent)
                job["bytes"] = written
                job["speed"] = meter.speed()
                job["eta"] = meter.eta(total - written)
                job["updated"] = time.time()
                now = time.monotonic()
                if now - last_ui >= PROGRESS_EVERY:
                    last_ui = now
                    await emit()
    else:
        with src.open("rb") as reader, dest.open("wb", buffering=TRANSFER_CHUNK) as writer:
            while True:
                if event.is_set():
                    raise asyncio.CancelledError()
                chunk = await asyncio.to_thread(reader.read, TRANSFER_CHUNK)
                if not chunk:
                    break
                await bucket.take(len(chunk))
                await asyncio.to_thread(writer.write, chunk)
                written += len(chunk)
                meter.add(len(chunk))
                job["bytes"] = written
                job["speed"] = meter.speed()
                job["eta"] = meter.eta(total - written)
                job["updated"] = time.time()
                now = time.monotonic()
                if now - last_ui >= PROGRESS_EVERY:
                    last_ui = now
                    await emit()
    if event.is_set():
        raise asyncio.CancelledError()
    job["bytes"] = written
    job["speed"] = meter.speed() or int(written / max(0.2, time.time() - job["started"]))
    job["eta"] = 0


def _peer_message(response: httpx.Response) -> str:
    try:
        data = response.json()
        detail = data.get("detail") if isinstance(data, dict) else None
        if detail:
            return str(detail)
    except Exception:
        pass
    text = (response.text or "").strip()
    if text and len(text) < 200 and "<" not in text:
        return text
    return f"The other PC returned HTTP {response.status_code}"


async def open_peer_download(
    urls: list[str],
    *,
    params: dict[str, str],
    headers: dict[str, str],
) -> tuple[httpx.Response, httpx.AsyncClient]:
    """Return an open streaming response and the client that owns it.

    The caller must close both. Tries each peer URL until one returns a
    non-5xx response (200 is required by the transfer runner).
    """
    last_error: Exception | None = None
    for url in urls:
        client = transfer_client()
        try:
            request = client.build_request("GET", url, params=params, headers=headers)
            response = await client.send(request, stream=True)
            if response.status_code == 200:
                return response, client
            body = ""
            try:
                raw = await response.aread()
                body = raw.decode("utf-8", "replace")
            except Exception:
                pass
            await response.aclose()
            await client.aclose()
            if response.status_code < 500:
                raise RuntimeError(body or f"The other PC returned HTTP {response.status_code}")
            last_error = RuntimeError(body or f"The other PC returned HTTP {response.status_code}")
        except httpx.RequestError as exc:
            try:
                await client.aclose()
            except Exception:
                pass
            last_error = exc
            continue
    raise last_error or RuntimeError("The other PC is unreachable")
