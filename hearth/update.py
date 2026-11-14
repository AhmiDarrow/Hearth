"""Check GitHub Releases for a newer Hearth and apply a verified installer."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
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
JUST_UPDATED_NAME = "just_updated"
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

    def on_bytes(done: int, total: int) -> None:
        percent = 100 if total <= 0 else min(99, int(done * 100 / total))
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


def apply_update(
    update: dict[str, Any],
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
    relaunch: Path | None = None,
    dest_dir: Path | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Download and verify the installer, shut Hearth down, then launch Setup hidden."""
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
    run_pre_install()
    open_installer(setup)
    request_process_exit(1.0)
    return {
        "installed": False,
        "opened_installer": True,
        "closed_for_update": True,
        "setup": str(setup),
        "version": update["version"],
        "phase": "install",
        "percent": 100,
    }


def check_and_maybe_install(
    *,
    current: str | None = None,
    install: bool = False,
    auto: bool = False,
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
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
    try:
        result = check_for_update(current=current, getter=getter)
    except Exception as exc:
        # Background checks stay in the UI; a user click opens the download page
        # so a broken verifier (or a GitHub failure) is never silent.
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
    try:
        applied = apply_update(
            result,
            getter=getter,
            require_authenticode=require_authenticode,
            progress=progress,
        )
    except Exception as exc:
        return fallback_after_failure(
            current=current_text,
            exc=exc,
            info=result,
            open_page=True,
        )
    result.update(applied)
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


def request_process_exit(delay: float = 1.0) -> None:
    """Exit the process after a short delay so the installer can replace these files."""

    def _go() -> None:
        time.sleep(delay)
        os._exit(0)

    import threading

    threading.Thread(target=_go, name="hearth-exit-for-update", daemon=True).start()
