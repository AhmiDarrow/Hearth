"""Background file transfers so the UI never navigates or blocks on a download."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .files import MAX_UPLOAD_BYTES, UploadTooLarge, guess_type, safe_filename
from .prefs import default_download_folder, unique_dest, user_facing_error

log = logging.getLogger("hearth.transfers")

Broadcast = Callable[[str, Any | None], Awaitable[None]]


class TransferHub:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self._cancels: dict[str, asyncio.Event] = {}

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
        if job.get("status") in {"queued", "running"}:
            job["status"] = "canceled"
            job["message"] = "Canceled"
        return dict(job)

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
            "status": "queued",
            "message": "",
            "started": time.time(),
            "updated": time.time(),
            **extra,
        }
        self.jobs[tid] = job
        self._cancels[tid] = asyncio.Event()
        # Keep the list bounded
        if len(self.jobs) > 80:
            drop = [key for key, item in self.jobs.items() if item.get("status") in {"done", "error", "canceled"}]
            for key in drop[:-40]:
                self.jobs.pop(key, None)
                self._cancels.pop(key, None)
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

        async def run() -> None:
            tmp = dest.with_name(f".{dest.name}.{os.urandom(4).hex()}.part")
            client: httpx.AsyncClient | None = None
            try:
                job["status"] = "running"
                job["updated"] = time.time()
                origin = await source()
                if isinstance(origin, Path):
                    await _copy_path(origin, tmp, job, event)
                else:
                    response, client = origin
                    try:
                        if response.status_code != 200:
                            raise RuntimeError(_peer_message(response))
                        job["total"] = int(response.headers.get("content-length") or job.get("total") or 0)
                        started = time.time()
                        written = 0

                        def write_chunk(handle: Any, data: bytes) -> None:
                            handle.write(data)

                        with tmp.open("wb") as handle:
                            async for chunk in response.aiter_bytes():
                                if event.is_set():
                                    raise asyncio.CancelledError()
                                written += len(chunk)
                                if written > MAX_UPLOAD_BYTES:
                                    raise UploadTooLarge("File is too large")
                                await asyncio.to_thread(write_chunk, handle, chunk)
                                now = time.time()
                                job["bytes"] = written
                                elapsed = max(0.2, now - started)
                                job["speed"] = int(written / elapsed)
                                job["updated"] = now
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
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass
                if broadcast:
                    try:
                        await broadcast("transfers", self.list())
                    except Exception:
                        pass

        asyncio.create_task(run())
        return dict(job)


async def _copy_path(src: Path, dest: Path, job: dict[str, Any], event: asyncio.Event) -> None:
    job["total"] = src.stat().st_size
    started = time.time()
    written = 0

    def copy() -> None:
        nonlocal written
        with src.open("rb") as reader, dest.open("wb") as writer:
            while True:
                if event.is_set():
                    raise asyncio.CancelledError()
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                written += len(chunk)

    # Incremental copy on a worker so the event loop stays free.
    def write_loop() -> None:
        nonlocal written
        with src.open("rb") as reader, dest.open("wb") as writer:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                written += len(chunk)

    await asyncio.to_thread(write_loop)
    if event.is_set():
        raise asyncio.CancelledError()
    job["bytes"] = written
    job["speed"] = int(written / max(0.2, time.time() - started))


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
        client = httpx.AsyncClient(timeout=None)
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
