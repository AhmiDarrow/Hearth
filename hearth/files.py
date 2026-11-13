"""Safe path resolution for shared folders and the local folder picker."""

from __future__ import annotations

import mimetypes
import os
import string
from pathlib import Path
from typing import Any

KNOWN_FOLDER_NAMES = ("Documents", "Pictures", "Downloads", "Desktop", "Videos", "Music")

_SKIP_DIR_NAMES = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "system volume information",
    "$recycle.bin",
    "recovery",
    "boot",
    "config.msi",
}


def resolve_user_path(path: str) -> Path:
    """Expand a typed or well-known folder path (e.g. 'Documents')."""
    raw = (path or "").strip()
    if not raw:
        raise FileNotFoundError("Folder path required")
    p = Path(raw).expanduser()
    if p.exists():
        return p
    home = Path.home()
    if not p.is_absolute():
        candidate = home / raw
        if candidate.exists():
            return candidate
        for child in _known_folder_paths():
            if child.name.lower() == raw.lower() or child.name.lower() == Path(raw).name.lower():
                if child.exists():
                    return child
    return p


def _onedrive_roots() -> list[Path]:
    roots: list[Path] = []
    home = Path.home()
    env_keys = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
    for key in env_keys:
        value = os.environ.get(key)
        if value:
            path = Path(value)
            if path.exists() and path not in roots:
                roots.append(path)
    for name in ("OneDrive", "OneDrive - Personal"):
        path = home / name
        if path.exists() and path not in roots:
            roots.append(path)
    return roots


def _known_folder_paths() -> list[Path]:
    home = Path.home()
    found: list[Path] = []
    seen: set[Path] = set()
    for name in KNOWN_FOLDER_NAMES:
        candidates = [home / name]
        for od in _onedrive_roots():
            candidates.append(od / name)
        for path in candidates:
            try:
                resolved = path.resolve() if path.exists() else path
            except OSError:
                continue
            if path.exists() and resolved not in seen:
                seen.add(resolved)
                found.append(path)
    return found


def known_folders() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _known_folder_paths():
        items.append({"name": path.name, "path": str(path), "kind": "known"})
    return items


def list_local_dirs(rel: str = "") -> dict[str, Any]:
    """List subfolders for the in-app folder picker. Stays on this PC."""
    if not rel:
        roots: list[dict[str, Any]] = []
        home = Path.home()
        roots.append({"name": "Home", "path": str(home), "dir": True})
        for item in known_folders():
            roots.append({"name": item["name"], "path": item["path"], "dir": True})
        if os.name == "nt":
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                if drive.exists():
                    roots.append({"name": f"{letter}:", "path": str(drive), "dir": True})
        # Dedup by resolved path, keep first label
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in roots:
            try:
                key = str(Path(item["path"]).resolve())
            except OSError:
                key = item["path"]
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return {"path": "", "parent": None, "items": deduped}

    path = Path(rel).expanduser()
    if not path.is_absolute():
        path = Path.home() / path
    try:
        path = path.resolve()
    except OSError as exc:
        raise FileNotFoundError(str(exc)) from exc
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError("Folder does not exist on this PC")
    if not _is_browsable(path):
        raise PermissionError("That folder cannot be browsed")

    parent = str(path.parent) if path.parent != path else None
    items: list[dict[str, Any]] = []
    try:
        children = list(path.iterdir())
    except OSError:
        children = []
    for child in sorted(children, key=lambda p: p.name.lower()):
        if child.name.startswith(".") or child.name.lower() in _SKIP_DIR_NAMES:
            continue
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        items.append({"name": child.name, "path": str(child), "dir": True})
    return {"path": str(path), "parent": parent, "items": items}


def _is_browsable(path: Path) -> bool:
    name = path.name.lower()
    if name in _SKIP_DIR_NAMES:
        return False
    if os.name == "nt":
        parts = [p.lower() for p in path.parts]
        if len(parts) >= 2 and parts[1] in _SKIP_DIR_NAMES:
            # C:\Windows, C:\Program Files, etc.
            return False
    return True


def resolve_share_path(shares: list[dict[str, Any]], share_id: str, rel: str = "") -> tuple[dict[str, Any], Path]:
    share = next((s for s in shares if s["id"] == share_id), None)
    if not share:
        raise FileNotFoundError("Unknown share")
    root = Path(share["path"]).resolve()
    if not root.exists():
        raise FileNotFoundError("Share folder is missing on this PC")
    target = (root / rel).resolve() if rel else root
    if root != target and root not in target.parents:
        raise PermissionError("Path escapes the shared folder")
    return share, target


def list_dir(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.is_dir():
        return items
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        items.append(
            {
                "name": child.name,
                "dir": child.is_dir(),
                "size": 0 if child.is_dir() else stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return items


def guess_type(path: Path) -> str:
    typ, _ = mimetypes.guess_type(str(path))
    return typ or "application/octet-stream"
