"""User preferences helpers: default Downloads folder, unique names, headers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .files import _known_folder_paths, safe_filename
from .state import Store, default_data_dir


def default_download_folder(store: Store | None = None) -> Path:
    custom = ""
    if store is not None:
        custom = str(store.data.get("download_folder") or "").strip()
    if custom:
        path = Path(custom).expanduser()
        try:
            if path.is_dir():
                return path
        except OSError:
            pass
    for path in _known_folder_paths():
        if path.name.lower() == "downloads":
            return path
    inbox = (store.root if store is not None else default_data_dir()) / "inbox"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return inbox


def unique_dest(folder: Path, name: str) -> Path:
    """Return folder/name, adding ' (n)' before the suffix if the name is taken."""
    folder.mkdir(parents=True, exist_ok=True)
    base = safe_filename(name)
    dest = folder / base
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    for index in range(1, 1000):
        candidate = folder / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem}-{os.urandom(3).hex()}{suffix}"


def content_disposition(filename: str) -> str:
    """RFC 5987 Content-Disposition that never trips latin-1 header encoding."""
    raw = safe_filename(filename)
    try:
        raw.encode("ascii")
        ascii_name = raw.replace('"', "'")
    except UnicodeEncodeError:
        ascii_name = safe_filename(raw.encode("ascii", "ignore").decode("ascii") or "download.bin")
        ascii_name = ascii_name.replace('"', "'")
    starred = quote(raw, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{starred}'


def user_facing_error(exc: BaseException) -> str:
    """Short, non-secret explanation suitable for a toast or JSON detail."""
    if isinstance(exc, PermissionError):
        return "This PC could not read or write that file. It may be locked or in a folder Hearth cannot use."
    if isinstance(exc, FileNotFoundError):
        return "That file is no longer available."
    if isinstance(exc, TimeoutError):
        return "The other PC did not respond in time."
    text = str(exc).strip() or exc.__class__.__name__
    if len(text) > 240:
        text = text[:237] + "..."
    return f"Could not finish that transfer: {text}"


def settings_snapshot(store: Store, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import update
    from .startup import start_at_login_enabled

    payload = {
        "device_name": store.device_name,
        "theme": store.theme,
        "start_at_login": start_at_login_enabled(),
        "download_folder": str(default_download_folder(store)),
        "auto_check_updates": bool(store.data.get("auto_check_updates", True)),
        "download_limit_bps": int(store.data.get("download_limit_bps") or 0),
        "upload_limit_bps": int(store.data.get("upload_limit_bps") or 0),
        "last_update_check": store.data.get("last_update_check"),
        "github": f"https://github.com/{update.GITHUB_REPO}",
        "license": "MIT",
        "cert_thumbprint": update.CERT_THUMBPRINT,
        "cert_thumbprint_sha256": "2087A5805C97A523A49CD4000549421665798CFE38EFAA3DE619BBCC53F9F54B",
    }
    if extra:
        payload.update(extra)
    return payload


SETTINGS_DEFAULTS = {
    "theme": "system",
    "auto_check_updates": True,
    "download_folder": "",
    "download_limit_bps": 0,
    "upload_limit_bps": 0,
    "bridge": False,
    "start_at_login": False,
}


def reset_settings(store: Store) -> None:
    """Restore preference defaults. Does not leave a group or drop shares."""
    from .startup import set_start_at_login

    store.theme = SETTINGS_DEFAULTS["theme"]
    store.data["auto_check_updates"] = SETTINGS_DEFAULTS["auto_check_updates"]
    store.data["download_folder"] = SETTINGS_DEFAULTS["download_folder"]
    store.data["download_limit_bps"] = SETTINGS_DEFAULTS["download_limit_bps"]
    store.data["upload_limit_bps"] = SETTINGS_DEFAULTS["upload_limit_bps"]
    store.data["bridge"] = SETTINGS_DEFAULTS["bridge"]
    store.save()
    set_start_at_login(SETTINGS_DEFAULTS["start_at_login"])
