"""One Hearth process per user data directory.

A Windows named mutex (or a POSIX file lock) is the source of truth. The
lock is scoped to the resolved profile path, so two peers with separate
`--home` directories can run on the same PC, but a second launch of the
same profile hands its arguments to the process that already holds the
lock and then exits. Named mutexes and flock are released if the process
dies, so a stale lock file cannot block a later start or an update relaunch.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ERROR_ALREADY_EXISTS = 183
ENDPOINT_NAME = "instance.json"
LOCK_NAME = "instance.lock"


def resolve_profile(root: Path | str | None) -> Path:
    from .state import default_data_dir

    path = Path(root) if root else default_data_dir()
    return path.expanduser()


def profile_key(root: Path | str | None) -> str:
    """Stable identifier for a profile: user + normalized data dir."""
    import hashlib

    path = resolve_profile(root)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    normalized = os.path.normcase(os.path.normpath(str(resolved)))
    user = _user_token()
    return hashlib.sha256(f"{user}:{normalized}".encode("utf-8")).hexdigest()[:16]


def mutex_name(root: Path | str | None) -> str:
    """Local (per-logon-session) mutex name for this user profile."""
    return f"Local\\HearthInstance-{profile_key(root)}"


def endpoint_path(root: Path | str | None) -> Path:
    return resolve_profile(root) / ENDPOINT_NAME


def lock_path(root: Path | str | None) -> Path:
    return resolve_profile(root) / LOCK_NAME


def _user_token() -> str:
    if sys.platform == "win32":
        return os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    try:
        return str(os.getuid())
    except AttributeError:
        return os.environ.get("USER") or "user"


class InstanceLock:
    """Exclusive lock for one Hearth profile. Safe to release after a crash."""

    def __init__(self, root: Path | str | None) -> None:
        self.root = resolve_profile(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.name = mutex_name(self.root)
        self._handle: Any = None
        self._fp: Any = None
        self.owned = False

    def acquire(self) -> bool:
        if self.owned:
            return True
        if sys.platform == "win32":
            self.owned = self._acquire_mutex()
        else:
            self.owned = self._acquire_flock()
        return self.owned

    def release(self) -> None:
        handle, self._handle = self._handle, None
        fp, self._fp = self._fp, None
        self.owned = False
        if handle is not None and sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass
        if fp is not None:
            try:
                if sys.platform != "win32":
                    import fcntl

                    fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fp.close()
            except Exception:
                pass

    def _acquire_mutex(self) -> bool:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, True, self.name)
        if not handle:
            return False
        already = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        if already:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def _acquire_flock(self) -> bool:
        path = lock_path(self.root)
        try:
            fp = path.open("a+b")
        except OSError:
            return False
        try:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError, BlockingIOError):
            fp.close()
            return False
        self._fp = fp
        return True

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def write_endpoint(root: Path | str | None, *, port: int, token: str, pid: int | None = None) -> Path:
    profile = resolve_profile(root)
    profile.mkdir(parents=True, exist_ok=True)
    path = endpoint_path(profile)
    payload = {
        "pid": int(pid or os.getpid()),
        "port": int(port),
        "token": str(token),
        "host": "127.0.0.1",
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return path


def read_endpoint(root: Path | str | None) -> dict[str, Any] | None:
    path = endpoint_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        port = int(data.get("port"))
    except (TypeError, ValueError):
        return None
    token = str(data.get("token") or "")
    if port <= 0 or port > 65535 or not token:
        return None
    host = str(data.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return {"pid": pid, "port": port, "token": token, "host": host}


def clear_endpoint(root: Path | str | None) -> None:
    path = endpoint_path(root)
    try:
        path.unlink()
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.unlink()
    except OSError:
        pass


def handoff_to_running(
    root: Path | str | None,
    argv: list[str] | None = None,
    *,
    show: bool = True,
    timeout: float = 3.0,
) -> dict[str, Any] | None:
    """Ask the process that owns this profile to show itself and take argv.

    Retries briefly so a just-started instance can finish writing instance.json
    and bind its port. Returns the running instance's response, or None if
    no handshake succeeded.
    """
    deadline = time.time() + max(0.2, timeout)
    last: dict[str, Any] | None = None
    while time.time() <= deadline:
        last = _handoff_once(root, argv or [], show=show)
        if last and last.get("ok"):
            return last
        time.sleep(0.15)
    return last if last and last.get("ok") else None


def _handoff_once(root: Path | str | None, argv: list[str], *, show: bool) -> dict[str, Any] | None:
    endpoint = read_endpoint(root)
    if not endpoint:
        return None
    payload = json.dumps({
        "token": endpoint["token"],
        "argv": list(argv),
        "show": bool(show),
    }).encode("utf-8")
    url = f"http://{endpoint['host']}:{endpoint['port']}/api/instance/handoff"
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body) if body else {}
        if isinstance(data, dict) and data.get("ok"):
            return data
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    return None
