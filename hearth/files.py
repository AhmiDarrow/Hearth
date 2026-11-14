"""Safe path resolution for shared folders and the local folder picker."""

from __future__ import annotations

import mimetypes
import os
import re
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


def volume_label(path: Path) -> str:
    """Return the Windows volume label for a drive or UNC share root, if any."""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        root = Path(path).anchor or str(path)
        root = root.replace("/", "\\")
        if not root.endswith("\\"):
            root += "\\"
        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            root, buf, 261, None, None, None, None, 0
        )
        if ok and buf.value.strip():
            return buf.value.strip()
    except Exception:
        return ""
    return ""


def _unc_host_share(path: Path) -> tuple[str, str] | None:
    raw = str(path).replace("/", "\\")
    if raw.startswith("\\\\"):
        parts = [part for part in raw.split("\\") if part]
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None


def default_share_label(path: str | Path, explicit: str = "", volume: str | None = None) -> str:
    """Name to show for a share or drive.

    Prefers a user-supplied label, then the folder name, then a volume label
    plus drive letter (for example 'Windows (C:)'), then the drive letter,
    then host:share for a UNC root. Drive and UNC roots are recognized from
    the path string so labels still resolve in tests off Windows.
    """
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    raw = str(path).strip()
    try:
        raw = str(Path(raw).expanduser())
    except OSError:
        pass
    vol = volume if volume is not None else volume_label(Path(raw) if raw else Path("."))
    drive_root = re.match(r"^([A-Za-z]:)[\\\/]*$", raw)
    if drive_root:
        letter = drive_root.group(1).upper()
        return f"{vol} ({letter})" if vol else letter
    unc = re.match(r"^[\\/]{2}([^\\/]+)[\\/]([^\\/]+)[\\/]*$", raw)
    if unc:
        label = f"{unc.group(1)}:{unc.group(2)}"
        return f"{vol} ({label})" if vol else label
    p = Path(raw)
    name = p.name.strip()
    if name and name not in {".", ".."}:
        return name
    if vol:
        return vol
    anchor = (p.anchor or raw or "/").rstrip("\\/") or "/"
    return anchor if anchor != "\\" else "/"


def display_share(share: dict[str, Any], *, include_path: bool = True) -> dict[str, Any]:
    """Return a share dict whose label is never empty."""
    out = dict(share)
    out["label"] = default_share_label(share.get("path") or "", share.get("label") or "")
    if not include_path:
        return {key: out[key] for key in ("id", "label", "mode") if key in out}
    return out


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
                    roots.append({"name": default_share_label(drive), "path": str(drive), "dir": True})
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


_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
MAX_UPLOAD_BYTES = int(os.environ.get("HEARTH_MAX_UPLOAD", str(8 * 1024 ** 3)))
UPLOAD_CHUNK = 4 * 1024 * 1024


class UploadTooLarge(Exception):
    """Raised when an upload exceeds MAX_UPLOAD_BYTES."""


def safe_filename(name: str | None, fallback: str = "upload.bin") -> str:
    """Return a single path segment that is safe to write under a shared folder."""
    raw = (name or "").replace("\\", "/")
    if "\x00" in raw:
        return fallback
    raw = raw.split("/")[-1].strip()
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    raw = raw.rstrip(".")
    if not raw or raw in {".", ".."}:
        return fallback
    stem = raw.split(".")[0].upper()
    if stem in _WINDOWS_RESERVED:
        return fallback
    return raw[:255]


def sanitize_relpath(rel: str) -> str:
    """Normalize a share-relative path or raise PermissionError."""
    raw = (rel or "").strip()
    if not raw or raw in {".", "./", ".\\"}:
        return ""
    if "\x00" in raw:
        raise PermissionError("Path contains an illegal character")
    unified = raw.replace("\\", "/")
    if unified.startswith("//"):
        raise PermissionError("UNC paths are not allowed")
    if _DRIVE_RE.match(unified) or unified.startswith("/"):
        raise PermissionError("Absolute paths are not allowed")
    parts: list[str] = []
    for part in Path(unified).parts:
        if part in {"/", "\\"} or (len(part) == 2 and part.endswith(":")):
            raise PermissionError("Absolute paths are not allowed")
        if part in {".", ""}:
            continue
        if part == "..":
            raise PermissionError("Path escapes the shared folder")
        if ":" in part:
            raise PermissionError("Alternate data streams are not allowed")
        stem = part.split(".")[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise PermissionError("That filename is reserved on Windows")
        if "\x00" in part:
            raise PermissionError("Path contains an illegal character")
        parts.append(part)
    return "/".join(parts)


async def save_upload(dest: Path, source: Any, limit: int | None = None, throttle: Any | None = None) -> int:
    """Stream an uploaded file to disk without reading it all into memory."""
    cap = MAX_UPLOAD_BYTES if limit is None else limit
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.urandom(4).hex()}.part")
    written = 0
    try:
        with tmp.open("wb", buffering=UPLOAD_CHUNK) as fh:
            while True:
                chunk = await source.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > cap:
                    raise UploadTooLarge(f"File is larger than the {cap} byte limit")
                if throttle is not None:
                    await throttle.take(len(chunk))
                fh.write(chunk)
        tmp.replace(dest)
        return written
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def resolve_share_path(shares: list[dict[str, Any]], share_id: str, rel: str = "") -> tuple[dict[str, Any], Path]:
    share = next((s for s in shares if s["id"] == share_id), None)
    if not share:
        raise FileNotFoundError("Unknown share")
    root = Path(share["path"]).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError("Share folder is missing on this PC")
    rel = sanitize_relpath(rel or "")
    target = (root / rel).resolve() if rel else root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Path escapes the shared folder") from exc
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
