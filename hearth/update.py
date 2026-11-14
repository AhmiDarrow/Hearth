"""Check GitHub Releases for a newer Hearth and apply a verified installer."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .authenticode import authenticode_info as read_authenticode_info

# Canonical repository name after the public rename. v0.3.0-beta builds still
# query AhmiDarrow/hearth; GitHub redirects that path to this one.
GITHUB_REPO = "AhmiDarrow/Hearth"
LEGACY_GITHUB_REPO = "AhmiDarrow/hearth"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
LEGACY_RELEASES_URL = f"https://api.github.com/repos/{LEGACY_GITHUB_REPO}/releases"
RELEASE_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_URL = f"{RELEASE_PAGE}/latest"
SETUP_ASSET = "HearthSetup.exe"
LATEST_SETUP_URL = f"{LATEST_RELEASE_URL}/download/{SETUP_ASSET}"
SUMS_ASSET = "SHA256SUMS.txt"
# SHA-1 thumbprint of the public pack/Hearth.cer Authenticode certificate.
# This is not a secret.
CERT_THUMBPRINT = "C7B807D542B3BA7207AABED960F556B4DC85C02F"
USER_AGENT = f"Hearth/{__version__} (+https://github.com/{GITHUB_REPO})"
CHECK_INTERVAL_SEC = 4 * 60 * 60
FAILURE_BACKOFF_BASE_SEC = 15 * 60
JUST_UPDATED_NAME = "just_updated"

# One in-flight download/install at a time. A second check must reuse this
# snapshot instead of starting another download or another installer.
_gate_lock = threading.Lock()
_in_flight: dict[str, Any] | None = None
_last_failure_at = 0.0
_failure_streak = 0


def reset_update_gate() -> None:
    """Test helper: clear in-flight and failure backoff state."""
    global _in_flight, _last_failure_at, _failure_streak
    with _gate_lock:
        _in_flight = None
        _last_failure_at = 0.0
        _failure_streak = 0


def _is_active_update(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    if result.get("failed"):
        return False
    phase = str(result.get("phase") or "")
    return bool(
        phase in {"download", "install"}
        or result.get("in_flight")
        or result.get("commit_pending")
        or result.get("opened_installer")
        or result.get("closed_for_update")
    )


def in_flight_update() -> dict[str, Any] | None:
    with _gate_lock:
        return dict(_in_flight) if _in_flight else None


def _set_in_flight(result: dict[str, Any] | None) -> None:
    global _in_flight
    with _gate_lock:
        _in_flight = dict(result) if result else None


def _note_update_failure() -> None:
    global _last_failure_at, _failure_streak
    with _gate_lock:
        _last_failure_at = time.time()
        _failure_streak = min(_failure_streak + 1, 8)


def _note_update_success() -> None:
    global _last_failure_at, _failure_streak
    with _gate_lock:
        _last_failure_at = 0.0
        _failure_streak = 0


def failure_backoff_sec() -> float:
    with _gate_lock:
        if _failure_streak <= 0:
            return 0.0
        return min(CHECK_INTERVAL_SEC, FAILURE_BACKOFF_BASE_SEC * (2 ** max(_failure_streak - 1, 0)))


def auto_check_deferred(now: float | None = None) -> bool:
    """True when a periodic check should wait after a recent failure."""
    stamp = _last_failure_at
    wait = failure_backoff_sec()
    if stamp <= 0 or wait <= 0:
        return False
    return (now if now is not None else time.time()) < stamp + wait
_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre>[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?$",
    re.IGNORECASE,
)

HttpGet = Callable[[str, float], bytes]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def updates_disabled() -> bool:
    return os.environ.get("HEARTH_NO_UPDATE", "").strip().lower() in {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def releases_url() -> str:
    """GitHub Releases API URL. HEARTH_RELEASES_URL overrides it (CI / tests)."""
    override = os.environ.get("HEARTH_RELEASES_URL", "").strip()
    return override or RELEASES_URL


def skip_open_installer() -> bool:
    """CI can verify a download without launching the installer."""
    return _env_flag("HEARTH_UPDATE_SKIP_OPEN")


def auto_install_allowed() -> bool:
    return is_frozen() and not updates_disabled()


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] | None = None

    @classmethod
    def parse(cls, text: str) -> "Version":
        match = _VERSION_RE.match((text or "").strip())
        if not match:
            raise ValueError(f"unrecognized version: {text!r}")
        pre = match.group("pre")
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            tuple(pre.split(".")) if pre else None,
        )

    @property
    def core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        return text

    def _pre_key(self) -> tuple[Any, ...]:
        # A final release is newer than any prerelease of the same core version.
        if self.prerelease is None:
            return (1,)
        key: list[Any] = [0]
        for part in self.prerelease:
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part.lower()))
        return tuple(key)

    def __lt__(self, other: "Version") -> bool:
        if self.core != other.core:
            return self.core < other.core
        return self._pre_key() < other._pre_key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return (self.core, self.prerelease) == (other.core, other.prerelease)

    def __le__(self, other: "Version") -> bool:
        return self == other or self < other

    def __gt__(self, other: "Version") -> bool:
        return other < self

    def __ge__(self, other: "Version") -> bool:
        return self == other or other < self

    def __hash__(self) -> int:
        return hash((self.core, self.prerelease))


def normalize_thumbprint(value: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", value or "").upper()


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse GNU coreutils / shasum / GitHub-style SHA256SUMS text."""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([0-9A-Fa-f]{64})\s+\*?(.+?)\s*$", line)
        if not match:
            continue
        found[Path(match.group(2).strip()).name] = match.group(1).lower()
    return found


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    got = sha256_file(path)
    want = expected.strip().lower()
    if got != want:
        raise ValueError(f"SHA256 mismatch for {path.name}: expected {want}, got {got}")


def _github_request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )


def http_get(url: str, timeout: float = 30.0) -> bytes:
    try:
        with urllib.request.urlopen(_github_request(url), timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub download failed ({exc.code}) for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach GitHub releases: {exc.reason}") from exc


def http_download(
    url: str,
    dest: Path,
    timeout: float = 120.0,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Stream a file to disk, reporting (bytes_done, bytes_total) as it goes."""
    try:
        with urllib.request.urlopen(_github_request(url), timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total or done)
            if on_progress:
                on_progress(done, total or done)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub download failed ({exc.code}) for {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach GitHub releases: {exc.reason}") from exc


def pick_latest_release(
    releases: list[dict[str, Any]],
    current: str | Version | None = None,
) -> dict[str, Any] | None:
    """Return the newest published release newer than current, or None.

    Drafts are skipped. Prereleases are included because Hearth ships betas.
    """
    current_version = Version.parse(current or __version__)
    best: tuple[Version, dict[str, Any]] | None = None
    for release in releases:
        if release.get("draft"):
            continue
        tag = str(release.get("tag_name") or release.get("name") or "").strip()
        if not tag:
            continue
        try:
            version = Version.parse(tag)
        except ValueError:
            continue
        if version <= current_version:
            continue
        assets = {asset.get("name"): asset for asset in release.get("assets") or [] if asset.get("name")}
        setup = assets.get(SETUP_ASSET)
        if not setup or not setup.get("browser_download_url"):
            continue
        if best is None or version > best[0]:
            best = (version, {
                "version": str(version),
                "tag": tag,
                "name": release.get("name") or tag,
                "prerelease": bool(release.get("prerelease")),
                "html_url": release.get("html_url") or f"{RELEASE_PAGE}/tag/{tag}",
                "setup_url": setup["browser_download_url"],
                "setup_name": SETUP_ASSET,
                "sums_url": (assets.get(SUMS_ASSET) or {}).get("browser_download_url"),
                "published_at": release.get("published_at"),
            })
    return None if best is None else best[1]


def check_for_update(
    current: str | None = None,
    getter: HttpGet | None = None,
) -> dict[str, Any]:
    current_text = current or __version__
    get = getter or http_get
    raw = get(releases_url(), 30.0)
    try:
        releases = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub releases response was not JSON") from exc
    if not isinstance(releases, list):
        raise RuntimeError("GitHub releases response was not a list")
    latest = pick_latest_release(releases, current_text)
    if not latest:
        return {
            "current": current_text,
            "available": False,
            "version": None,
            "message": f"You're up to date (v{current_text}).",
        }
    return {
        "current": current_text,
        "available": True,
        "message": f"Version {latest['version']} is available.",
        **latest,
    }


def authenticode_info(path: Path) -> dict[str, str] | None:
    """Read Authenticode status and signer thumbprint. Windows only. No shell."""
    return read_authenticode_info(path)


def verify_authenticode(path: Path, expected_thumbprint: str = CERT_THUMBPRINT) -> None:
    """Accept a self-signed installer when the publisher thumbprint matches.

    WinVerifyTrust often reports NotTrusted or UnknownError for a self-signed
    cert that is not in the machine's Trusted Root store. A matching SHA-1
    thumbprint plus an intact signature hash is enough. HashMismatch is always
    rejected. If the thumbprint can be read, a misleading NotSigned status
    (untrusted chain misfires) is also accepted — the file is signed by us.
    """
    info = authenticode_info(path)
    if info is None:
        raise ValueError("Could not read the Authenticode signature")
    status = info.get("status") or ""
    got = normalize_thumbprint(info.get("thumbprint") or "")
    want = normalize_thumbprint(expected_thumbprint)
    if status == "HashMismatch" or status.startswith("HashMismatch"):
        raise ValueError(f"Installer signature is not valid ({status})")
    if not got:
        if status in {"NotSigned", "HashMismatch"}:
            raise ValueError(f"Installer signature is not valid ({status})")
        raise ValueError("Installer is not Authenticode-signed")
    if got != want:
        raise ValueError("Installer is not signed by the Hearth publisher certificate")


def verify_installer(
    path: Path,
    expected_sha256: str,
    *,
    require_authenticode: bool | None = None,
    expected_thumbprint: str = CERT_THUMBPRINT,
) -> None:
    verify_sha256(path, expected_sha256)
    if require_authenticode is None:
        require_authenticode = sys.platform == "win32"
    if require_authenticode:
        verify_authenticode(path, expected_thumbprint)



def release_download_page(info: dict[str, Any] | None = None) -> str:
    """Best URL to hand the user when the in-app updater cannot finish."""
    if info:
        url = str(info.get("html_url") or "").strip()
        if url.startswith(("https://", "http://")):
            return url
    return LATEST_RELEASE_URL


# Inno flags for in-app updates: fully hidden, no UI, no UAC (per-user).
INSTALLER_LAUNCH_ARGS = (
    "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART "
    "/CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /CURRENTUSER"
)
RELAUNCH_UI_NAME = "relaunch_ui.json"
RELAUNCH_ARGS_NAME = "relaunch-args.json"
RELAUNCH_CMDLINE_NAME = "relaunch-cmdline.txt"
SW_HIDE = 0
SW_SHOWNORMAL = 1

_pre_install_hooks: list[Callable[[], None]] = []


def register_pre_install(hook: Callable[[], None]) -> None:
    """Run ``hook`` immediately before the installer is launched (flush, stop tray)."""
    _pre_install_hooks.append(hook)


def clear_pre_install_hooks() -> None:
    _pre_install_hooks.clear()


def run_pre_install() -> None:
    for hook in list(_pre_install_hooks):
        try:
            hook()
        except Exception:
            continue


def _is_program_files(path: Path) -> bool:
    parts = {os.path.normcase(part).casefold() for part in Path(path).parts}
    return bool(parts & {"program files", "program files (x86)"})


def _dir_is_user_writable(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        fd, name = tempfile.mkstemp(prefix=".hearth-w-", dir=str(path))
        os.close(fd)
        os.unlink(name)
    except OSError:
        return False
    return True


def default_update_dir(install_dir: str | None = None) -> str:
    """Install location for a silent per-user update (never Program Files)."""
    if install_dir is not None:
        return install_dir.strip()
    override = os.environ.get("HEARTH_UPDATE_DIR", "").strip()
    if override:
        return override
    if is_frozen():
        folder = Path(sys.executable).resolve().parent
        if not _is_program_files(folder) and _dir_is_user_writable(folder):
            return str(folder)
    return ""


def installer_launch_args(install_dir: str | None = None) -> str:
    """Command line for Setup: hidden, per-user, force-close leftovers, optional /DIR."""
    args = INSTALLER_LAUNCH_ARGS
    directory = default_update_dir(install_dir)
    if directory:
        args += f' /DIR="{directory}"'
    return args


def _shell_open(target: str, directory: str | None = None, params: str | None = None, show: int | None = None) -> int:
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_ssize_t
    show_cmd = SW_SHOWNORMAL if show is None else int(show)
    return int(shell32.ShellExecuteW(None, "open", target, params, directory, show_cmd))


def open_release_page(url: str | None = None) -> bool:
    """Open the GitHub release page in the default browser. No shell commands."""
    target = (url or LATEST_RELEASE_URL).strip()
    if not target.startswith(("https://", "http://")):
        return False
    try:
        if sys.platform == "win32":
            return _shell_open(target) > 32
        import webbrowser

        return bool(webbrowser.open(target))
    except Exception:
        return False


def fallback_after_failure(
    *,
    current: str,
    exc: BaseException,
    info: dict[str, Any] | None = None,
    open_page: bool = False,
) -> dict[str, Any]:
    """Describe a failed update check/install and optionally open the download page."""
    page = release_download_page(info)
    opened = False
    if open_page:
        opened = open_release_page(page)
    detail = str(exc).strip() or exc.__class__.__name__
    if opened:
        message = (
            f"The in-app updater could not finish ({detail}). "
            "The release download page is opening so you can install the update yourself."
        )
    else:
        message = (
            f"The in-app updater could not finish ({detail}). "
            f"Download {SETUP_ASSET} from {page}."
        )
    result = {
        "current": current,
        "available": bool(info and info.get("available")),
        "version": (info or {}).get("version"),
        "failed": True,
        "open_release_page": True,
        "opened_release_page": opened,
        "installed": False,
        "opened_installer": False,
        "frozen": is_frozen(),
        "html_url": page,
        "release_page": RELEASE_PAGE,
        "message": message,
    }
    if info:
        for key in ("tag", "name", "setup_url", "sums_url", "published_at"):
            if key in info and key not in result:
                result[key] = info[key]
    return result


def open_installer(setup: Path, params: str | None = None) -> None:
    """Launch the verified installer so it force-closes Hearth and runs unattended."""
    path = Path(setup)
    if not path.is_file():
        raise FileNotFoundError(f"Installer not found: {path}")
    if sys.platform != "win32":
        raise RuntimeError("The installer can only be launched on Windows")
    # SW_SHOWNORMAL = 1. Return values <= 32 are errors.
    result = _shell_open(
        str(path),
        str(path.parent),
        params if params is not None else installer_launch_args(),
        show=SW_HIDE,
    )
    if result <= 32:
        raise RuntimeError(f"Could not open the installer (ShellExecute error {result})")


def _emit(progress: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if progress is None:
        return
    try:
        progress(payload)
    except Exception:
        pass


def download_and_verify(
    update: dict[str, Any],
    dest_dir: Path | None = None,
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    if not update.get("setup_url"):
        raise RuntimeError("Release is missing HearthSetup.exe")
    if not update.get("sums_url"):
        raise RuntimeError("Release is missing SHA256SUMS.txt; refusing to install")
    _emit(progress, phase="download", percent=0, message="Downloading update…")
    get = getter or http_get
    sums = parse_sha256sums(get(update["sums_url"], 30.0).decode("utf-8", errors="replace"))
    expected = sums.get(SETUP_ASSET)
    if not expected:
        raise RuntimeError("SHA256SUMS.txt does not list HearthSetup.exe")
    dest = Path(dest_dir or tempfile.mkdtemp(prefix="hearth-setup-"))
    dest.mkdir(parents=True, exist_ok=True)
    setup = dest / SETUP_ASSET

    last_percent = {"n": -1}

    def on_bytes(done: int, total: int) -> None:
        percent = 100 if total <= 0 else min(99, int(done * 100 / total))
        if percent == last_percent["n"]:
            return
        last_percent["n"] = percent
        _emit(progress, phase="download", percent=percent, message="Downloading update…")

    if getter is not None:
        data = get(update["setup_url"], 120.0)
        setup.write_bytes(data)
        on_bytes(len(data), len(data))
    else:
        http_download(update["setup_url"], setup, timeout=120.0, on_progress=on_bytes)
    _emit(progress, phase="download", percent=100, message="Verifying update…")
    verify_installer(setup, expected, require_authenticode=require_authenticode)
    return setup


def commit_hidden_install(setup: Path) -> None:
    """Flush state, launch the hidden installer, and exit so Setup can replace this build."""
    run_pre_install()
    open_installer(setup)
    request_process_exit(1.0)


def apply_update(
    update: dict[str, Any],
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
    relaunch: Path | None = None,
    dest_dir: Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Download and verify the installer, then launch Setup hidden.

    If commit is False, the caller is expected to send its response first and
    then call commit_hidden_install(setup). That keeps the HTTP server alive
    long enough to answer /api/updates/check instead of resetting the client.
    """
    del relaunch  # The installer relaunches Hearth after a successful install.
    setup = download_and_verify(
        update,
        dest_dir=dest_dir,
        getter=getter,
        require_authenticode=require_authenticode,
        progress=progress,
    )
    if skip_open_installer():
        return {
            "installed": False,
            "opened_installer": False,
            "skipped_open": True,
            "setup": str(setup),
            "version": update["version"],
        }
    _emit(progress, phase="install", percent=100, message="Installing…")
    result = {
        "installed": False,
        "opened_installer": True,
        "closed_for_update": True,
        "setup": str(setup),
        "version": update["version"],
        "phase": "install",
        "percent": 100,
    }
    if commit:
        commit_hidden_install(setup)
    else:
        result["commit_pending"] = True
    return result


def check_and_maybe_install(
    *,
    current: str | None = None,
    install: bool = False,
    auto: bool = False,
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    if updates_disabled() and getter is None:
        return {
            "current": current or __version__,
            "available": False,
            "installed": False,
            "frozen": is_frozen(),
            "message": "Updates are disabled.",
        }
    current_text = current or __version__
    existing = in_flight_update()
    if _is_active_update(existing):
        snapshot = dict(existing)
        snapshot["in_flight"] = True
        snapshot.setdefault("current", current_text)
        snapshot.setdefault("message", "An update is already in progress.")
        return snapshot
    if auto and auto_check_deferred():
        return {
            "current": current_text,
            "available": False,
            "installed": False,
            "frozen": is_frozen(),
            "deferred": True,
            "message": "Waiting to retry a failed update check.",
        }
    try:
        result = check_for_update(current=current, getter=getter)
    except Exception as exc:
        # Background checks stay in the UI; a user click opens the download page
        # so a broken verifier (or a GitHub failure) is never silent.
        if auto:
            _note_update_failure()
        return fallback_after_failure(
            current=current_text,
            exc=exc,
            open_page=not auto,
        )
    result["frozen"] = is_frozen()
    result["installed"] = False
    result["failed"] = False
    result["release_page"] = RELEASE_PAGE
    if not result.get("available"):
        return result
    result["opened_installer"] = False
    # Periodic checks only notify; a click is required to download and apply.
    if not install:
        if result.get("available"):
            if is_frozen() and sys.platform == "win32":
                result["message"] = (
                    f"Version {result['version']} is available. "
                    "Click Install update to download it, verify the signature, and apply it. "
                    "Hearth shows a small progress bar, then closes itself, installs in the "
                    "background with no installer window, and reopens with your data intact."
                )
            else:
                result["message"] = (
                    f"Version {result['version']} is available. "
                    f"Download {SETUP_ASSET} from {result.get('html_url') or RELEASE_PAGE}."
                )
        return result
    if sys.platform != "win32":
        result["message"] = (
            f"Version {result['version']} is available, but the installer can only be opened on Windows. "
            f"See {result.get('html_url') or RELEASE_PAGE}."
        )
        return result
    def _track(info: dict[str, Any]) -> None:
        snapshot = dict(result)
        snapshot.update(info)
        snapshot["in_flight"] = True
        snapshot["available"] = True
        _set_in_flight(snapshot)
        if progress is not None:
            progress(info)

    pending = dict(result)
    pending.update({"phase": "download", "percent": 0, "message": "Downloading update…", "in_flight": True, "available": True})
    _set_in_flight(pending)
    if progress is not None:
        try:
            progress({"phase": "download", "percent": 0, "message": "Downloading update…"})
        except Exception:
            pass
    try:
        applied = apply_update(
            result,
            getter=getter,
            require_authenticode=require_authenticode,
            progress=_track,
            commit=commit,
        )
    except Exception as exc:
        _set_in_flight(None)
        _note_update_failure()
        return fallback_after_failure(
            current=current_text,
            exc=exc,
            info=result,
            open_page=True,
        )
    result.update(applied)
    if result.get("failed"):
        _set_in_flight(None)
        _note_update_failure()
    else:
        _note_update_success()
        if result.get("commit_pending") or result.get("opened_installer") or result.get("closed_for_update"):
            _set_in_flight(dict(result) | {"in_flight": True})
        elif not result.get("skipped_open"):
            _set_in_flight(None)
        else:
            _set_in_flight(None)
    result["message"] = (
        f"Hearth {result['version']} is installing. "
        "Hearth is closing itself now; it will reopen with your settings, shares, "
        "and chat unchanged."
    )
    result.setdefault("phase", "install")
    result.setdefault("percent", 100)
    return result


def just_updated_path(root: Path) -> Path:
    return Path(root) / JUST_UPDATED_NAME


def write_just_updated(root: Path, version: str) -> None:
    path = just_updated_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(version).strip() + "\n", encoding="utf-8")


def read_just_updated(root: Path) -> str | None:
    path = just_updated_path(root)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def clear_just_updated(root: Path) -> None:
    path = just_updated_path(root)
    try:
        path.unlink()
    except OSError:
        pass


def relaunch_ui_path(root: Path) -> Path:
    return Path(root) / RELAUNCH_UI_NAME


def write_relaunch_ui(root: Path, mode: str) -> None:
    """Remember whether the window or tray should come back after an update."""
    value = "tray" if mode == "tray" else "window"
    path = relaunch_ui_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ui": value}), encoding="utf-8")


def read_relaunch_ui(root: Path) -> str | None:
    path = relaunch_ui_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    mode = str(data.get("ui") or "").strip().lower()
    return mode if mode in {"window", "tray"} else None


def clear_relaunch_ui(root: Path) -> None:
    path = relaunch_ui_path(root)
    try:
        path.unlink()
    except OSError:
        pass


def current_install_dir() -> Path | None:
    """Directory that holds Hearth.exe (or HEARTH_UPDATE_DIR in CI)."""
    override = os.environ.get("HEARTH_UPDATE_DIR", "").strip()
    if override:
        return Path(override)
    if is_frozen():
        try:
            return Path(sys.executable).resolve().parent
        except OSError:
            return Path(sys.executable).parent
    return None


def relaunch_args_path(install_dir: Path | str | None = None) -> Path | None:
    folder = Path(install_dir) if install_dir is not None else current_install_dir()
    if folder is None:
        return None
    return folder / RELAUNCH_ARGS_NAME


def drop_updated_args(argv: list[str]) -> list[str]:
    """Strip --updated [version] so a relaunch can add the new version."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--updated" or arg.startswith("--updated="):
            if arg == "--updated" and i + 1 < len(argv) and not str(argv[i + 1]).startswith("-"):
                i += 2
                continue
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def split_command_line(command_line: str) -> list[str]:
    """Split a process CommandLine into argv. Portable enough for Hearth flags."""
    import shlex

    return shlex.split(command_line, posix=False)


def argv_from_command_line(command_line: str, exe: Path | str | None = None) -> list[str]:
    """Return Hearth flags from a Win32 CommandLine, minus the exe and --updated."""
    parts = split_command_line(command_line or "")
    if not parts:
        return []
    first = parts[0].strip('"')
    drop_first = False
    if exe is not None:
        try:
            drop_first = os.path.normcase(os.path.normpath(first)) == os.path.normcase(
                os.path.normpath(str(exe))
            )
        except OSError:
            drop_first = os.path.normcase(first) == os.path.normcase(str(exe))
    if not drop_first and first.lower().endswith("hearth.exe"):
        drop_first = True
    if drop_first:
        parts = parts[1:]
    return drop_updated_args(parts)


def home_from_argv(argv: list[str]) -> str | None:
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--home" and i + 1 < len(argv):
            return str(argv[i + 1])
        if arg.startswith("--home="):
            return arg.split("=", 1)[1]
        i += 1
    return None


def quote_windows_arg(arg: str) -> str:
    """Quote one Windows command-line argument. Leave plain flags alone."""
    text = str(arg)
    if text != "" and not any(ch in text for ch in (" ", "\t", '"')):
        return text
    escaped = text.replace('"', '\\"')
    # A trailing backslash would escape the closing quote.
    if escaped.endswith("\\"):
        escaped += "\\"
    return f'"{escaped}"'


def format_relaunch_cmdline(argv: list[str]) -> str:
    """ANSI command line for Inno [Run], without --updated."""
    return " ".join(quote_windows_arg(part) for part in drop_updated_args([str(part) for part in argv]))


def _cmdline_encoding() -> str:
    # Inno LoadStringFromFile reads the system ANSI code page.
    return "mbcs" if os.name == "nt" else "utf-8"


def write_relaunch_cmdline(folder: Path, argv: list[str]) -> Path:
    path = folder / RELAUNCH_CMDLINE_NAME
    path.write_text(format_relaunch_cmdline(argv), encoding=_cmdline_encoding())
    return path


def argv_for_current_launch(*, home: Path | str | None, port: int | None, host: str | None, no_desktop: bool, no_browser: bool, background: bool = False) -> list[str]:
    """Flags a silent updater must pass so the new process uses this profile."""
    argv: list[str] = []
    if home:
        argv.extend(["--home", str(home)])
    if port is not None:
        argv.extend(["--port", str(int(port))])
    if host:
        argv.extend(["--host", str(host)])
    if no_desktop:
        argv.append("--no-desktop")
    if no_browser:
        argv.append("--no-browser")
    if background:
        argv.append("--background")
    return argv


def write_relaunch_args(install_dir: Path | str, argv: list[str], *, home: Path | str | None = None) -> Path:
    """Persist one instance's launch args next to Hearth.exe for Setup to read."""
    folder = Path(install_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RELAUNCH_ARGS_NAME
    cleaned = drop_updated_args([str(a) for a in argv])
    resolved_home = str(home) if home is not None else home_from_argv(cleaned)
    instances = [{"home": resolved_home or "", "argv": cleaned}]
    # Keep other profiles already recorded so a second --home is not lost.
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        for inst in previous.get("instances") or []:
            if not isinstance(inst, dict):
                continue
            other = [str(a) for a in (inst.get("argv") or [])]
            other_home = str(inst.get("home") or "") or home_from_argv(other) or ""
            if resolved_home and other_home and os.path.normcase(os.path.normpath(other_home)) == os.path.normcase(os.path.normpath(resolved_home)):
                continue
            if other:
                instances.append({"home": other_home, "argv": other})
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, AttributeError):
        pass
    path.write_text(json.dumps({"version": 1, "instances": instances}, indent=2), encoding="utf-8")
    # instances[0] is this process. Inno reads the cmdline file directly so a
    # silent relaunch does not depend on the PowerShell process snapshot.
    write_relaunch_cmdline(folder, instances[0]["argv"])
    return path


def read_relaunch_args(install_dir: Path | str | None = None) -> list[dict[str, Any]]:
    path = relaunch_args_path(install_dir)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    if not isinstance(data, dict):
        return []
    rows: list[dict[str, Any]] = []
    for inst in data.get("instances") or []:
        if not isinstance(inst, dict):
            continue
        argv = drop_updated_args([str(a) for a in (inst.get("argv") or [])])
        if not argv:
            continue
        rows.append({"home": str(inst.get("home") or "") or home_from_argv(argv) or "", "argv": argv})
    return rows


def clear_relaunch_args(install_dir: Path | str | None = None) -> None:
    path = relaunch_args_path(install_dir)
    if path is None:
        return
    folder = path.parent
    for name in (RELAUNCH_ARGS_NAME, RELAUNCH_CMDLINE_NAME):
        try:
            (folder / name).unlink()
        except OSError:
            pass


def _argv_has_option(argv: list[str], option: str) -> bool:
    prefix = option + "="
    return any(a == option or a.startswith(prefix) for a in argv)


def apply_saved_relaunch_args(args: Any, argv: list[str] | None = None, install_dir: Path | str | None = None) -> bool:
    """Fill missing --home/--port/--host/flags from the installer-side persist file."""
    provided = list(argv) if argv is not None else list(sys.argv[1:])
    rows = read_relaunch_args(install_dir)
    if not rows:
        return False
    saved = rows[0]["argv"]
    # Walk saved argv and apply options the new process did not already get.
    i = 0
    changed = False
    while i < len(saved):
        arg = saved[i]
        if arg == "--home" and i + 1 < len(saved):
            if not _argv_has_option(provided, "--home"):
                args.home = saved[i + 1]
                changed = True
            i += 2
            continue
        if arg == "--port" and i + 1 < len(saved):
            if not _argv_has_option(provided, "--port"):
                try:
                    args.port = int(saved[i + 1])
                    changed = True
                except (TypeError, ValueError):
                    pass
            i += 2
            continue
        if arg == "--host" and i + 1 < len(saved):
            if not _argv_has_option(provided, "--host"):
                args.host = saved[i + 1]
                changed = True
            i += 2
            continue
        if arg == "--no-desktop":
            if not _argv_has_option(provided, "--no-desktop"):
                args.no_desktop = True
                changed = True
            i += 1
            continue
        if arg == "--no-browser":
            if not _argv_has_option(provided, "--no-browser"):
                args.no_browser = True
                changed = True
            i += 1
            continue
        if arg == "--background":
            if not _argv_has_option(provided, "--background"):
                args.background = True
                changed = True
            i += 1
            continue
        i += 1
    return changed


def write_current_relaunch_args(args: Any, *, home: Path | str, port: int, install_dir: Path | str | None = None) -> Path | None:
    folder = Path(install_dir) if install_dir is not None else current_install_dir()
    if folder is None:
        return None
    argv = argv_for_current_launch(
        home=home,
        port=port,
        host=getattr(args, "host", None),
        no_desktop=bool(getattr(args, "no_desktop", False)),
        no_browser=bool(getattr(args, "no_browser", False)),
        background=bool(getattr(args, "background", False)),
    )
    return write_relaunch_args(folder, argv, home=home)


_CI_ONLY_FLAGS = ("--no-desktop", "--no-browser")


def strip_ci_only_args(argv: list[str]) -> list[str]:
    """Drop flags that exist so CI stays off the desktop."""
    cleaned: list[str] = []
    for arg in argv:
        if arg in _CI_ONLY_FLAGS or any(arg.startswith(flag + "=") for flag in _CI_ONLY_FLAGS):
            continue
        cleaned.append(arg)
    return cleaned


def normalize_launch_flags(args: Any, *, frozen: bool, ci: bool) -> None:
    """CI stays headless. A frozen end-user install always keeps the tray."""
    if ci:
        args.no_desktop = True
        args.no_browser = True
        args.background = True
        return
    if frozen:
        args.no_desktop = False
        args.no_browser = False


def apply_saved_ui_mode(args: Any, mode: str | None, *, ci: bool) -> str:
    """Choose hidden, tray, or window after an update.

    End users always get a tray. A window comes back when it was open.
    CI stays hidden.
    """
    if ci or bool(getattr(args, "no_desktop", False)):
        return "hidden"
    if mode == "window":
        args.background = False
        return "window"
    if mode == "tray" or bool(getattr(args, "background", False)):
        args.background = True
        return "tray"
    args.background = False
    return "window"


def prepare_visible_takeover(args: Any) -> str:
    """The process that replaces a headless copy opens the window and tray."""
    args.no_desktop = False
    args.no_browser = False
    args.background = False
    return "window"


def second_launch_plan(*, ci: bool, no_desktop: bool, has_shell: bool) -> dict[str, bool]:
    """A shortcut click asks to see Hearth.

    ``--background`` only hides the first window. A headless copy yields so
    the new process can start the tray and the window. CI stays quiet.
    """
    surface = (not ci) and (not no_desktop)
    if not surface:
        return {"show": False, "yield": False, "tray": False, "window": False}
    if has_shell:
        return {"show": True, "yield": False, "tray": True, "window": True}
    return {"show": True, "yield": True, "tray": True, "window": True}


def end_user_relaunch_plan(argv: list[str], ui_mode: str | None) -> dict[str, Any]:
    """End-user update relaunch for a captured command line.

    CI-only flags are removed. The tray stays. A saved window is opened.
    """
    cleaned = strip_ci_only_args(drop_updated_args([str(part) for part in argv]))
    background = "--background" in cleaned
    probe = type("Launch", (), {})()
    probe.no_desktop = False
    probe.no_browser = False
    probe.background = background
    mode = apply_saved_ui_mode(probe, ui_mode, ci=False)
    if mode == "window":
        cleaned = [part for part in cleaned if part != "--background"]
    elif mode == "tray" and "--background" not in cleaned:
        cleaned.append("--background")
    return {
        "argv": cleaned,
        "mode": mode,
        "tray": mode != "hidden",
        "window": mode == "window",
        "background": bool(probe.background),
    }


def request_process_exit(delay: float = 1.0) -> None:
    """Exit the process after a short delay so the installer can replace these files."""

    def _go() -> None:
        time.sleep(delay)
        os._exit(0)

    import threading

    threading.Thread(target=_go, name="hearth-exit-for-update", daemon=True).start()
