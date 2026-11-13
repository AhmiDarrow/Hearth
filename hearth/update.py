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
SETUP_ASSET = "HearthSetup.exe"
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


def http_get(url: str, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
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
    raw = get(RELEASES_URL, 30.0)
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
    info = authenticode_info(path)
    if info is None:
        raise ValueError("Could not read the Authenticode signature")
    status = info["status"]
    # Self-signed installers often report NotTrusted/UnknownError until the
    # publisher certificate is trusted. Still require a matching thumbprint.
    if status in {"NotSigned", "HashMismatch"}:
        raise ValueError(f"Installer signature is not valid ({status})")
    got = info["thumbprint"]
    want = normalize_thumbprint(expected_thumbprint)
    if not got:
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



def open_installer(setup: Path) -> None:
    """Open the verified installer with a visible wizard. No cmd or silent flags."""
    path = Path(setup)
    if not path.is_file():
        raise FileNotFoundError(f"Installer not found: {path}")
    if sys.platform != "win32":
        raise RuntimeError("The installer can only be launched on Windows")
    from ctypes import wintypes

    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_ssize_t
    # SW_SHOWNORMAL = 1. Return values <= 32 are errors.
    result = int(shell32.ShellExecuteW(None, "open", str(path), None, str(path.parent), 1))
    if result <= 32:
        raise RuntimeError(f"Could not open the installer (ShellExecute error {result})")


def download_and_verify(
    update: dict[str, Any],
    dest_dir: Path | None = None,
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
) -> Path:
    if not update.get("setup_url"):
        raise RuntimeError("Release is missing HearthSetup.exe")
    if not update.get("sums_url"):
        raise RuntimeError("Release is missing SHA256SUMS.txt; refusing to install")
    get = getter or http_get
    sums = parse_sha256sums(get(update["sums_url"], 30.0).decode("utf-8", errors="replace"))
    expected = sums.get(SETUP_ASSET)
    if not expected:
        raise RuntimeError("SHA256SUMS.txt does not list HearthSetup.exe")
    dest = Path(dest_dir or tempfile.mkdtemp(prefix="hearth-setup-"))
    dest.mkdir(parents=True, exist_ok=True)
    setup = dest / SETUP_ASSET
    setup.write_bytes(get(update["setup_url"], 120.0))
    verify_installer(setup, expected, require_authenticode=require_authenticode)
    return setup


def apply_update(
    update: dict[str, Any],
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
    relaunch: Path | None = None,
    dest_dir: Path | None = None,
) -> dict[str, Any]:
    """Download and verify the installer, then open it for the user to review."""
    del relaunch  # Kept for call-site compatibility; we no longer relaunch ourselves.
    setup = download_and_verify(
        update,
        dest_dir=dest_dir,
        getter=getter,
        require_authenticode=require_authenticode,
    )
    open_installer(setup)
    return {
        "installed": False,
        "opened_installer": True,
        "setup": str(setup),
        "version": update["version"],
    }


def check_and_maybe_install(
    *,
    current: str | None = None,
    install: bool = False,
    auto: bool = False,
    getter: HttpGet | None = None,
    require_authenticode: bool | None = None,
) -> dict[str, Any]:
    if updates_disabled() and getter is None:
        return {
            "current": current or __version__,
            "available": False,
            "installed": False,
            "frozen": is_frozen(),
            "message": "Updates are disabled.",
        }
    result = check_for_update(current=current, getter=getter)
    result["frozen"] = is_frozen()
    result["installed"] = False
    result["release_page"] = RELEASE_PAGE
    if not result.get("available"):
        return result
    result["opened_installer"] = False
    # Never install unattended. Periodic checks only notify; a click is required
    # to download, verify, and open the setup wizard.
    if not install:
        if result.get("available"):
            if is_frozen() and sys.platform == "win32":
                result["message"] = (
                    f"Version {result['version']} is available. "
                    "Click Install update to download it, verify the signature, and open the installer."
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
    applied = apply_update(result, getter=getter, require_authenticode=require_authenticode)
    result.update(applied)
    result["message"] = (
        f"The Hearth {result['version']} installer is opening. "
        "Finish the wizard to update; Hearth may close itself when Setup asks."
    )
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


def request_process_exit(delay: float = 1.0) -> None:
    """Exit the process after a short delay. Unused by the consent-based updater."""

    def _go() -> None:
        time.sleep(delay)
        os._exit(0)

    import threading

    threading.Thread(target=_go, name="hearth-exit-for-update", daemon=True).start()
