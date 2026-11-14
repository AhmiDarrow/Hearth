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

# Win32 GetDriveTypeW. Missing and unknown letters must not be probed further:
# exists() and GetVolumeInformationW can hang on a disconnected network drive.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

_GENERIC_LABELS = {
    "",
    "shared folder",
    "shared drive",
    "shared",
    "folder",
    "share",
    "drive",
    "local disk",
    "network",
    "removable disk",
    "cd drive",
    "ram disk",
}

_DRIVE_ROOT_RE = re.compile(r"^([A-Za-z]):[\\/]*$")
_DRIVE_REL_RE = re.compile(r"^([A-Za-z]):([^\\/].*)$")
_UNC_ROOT_RE = re.compile(r"^[\\/]{2}([^\\/]+)[\\/]([^\\/]+)[\\/]*$")
_DRIVE_FACT_CACHE: dict[str, tuple[int, str, str]] = {}


def _is_unc(raw: str) -> bool:
    return raw.startswith("\\\\") or raw.startswith("//")


def _drive_key(path: str | Path) -> str:
    raw = str(path).strip()
    match = _DRIVE_ROOT_RE.fullmatch(raw) or re.match(r"^([A-Za-z]):", raw)
    if not match or _is_unc(raw):
        return ""
    return f"{match.group(1).upper()}:\\"


def canonical_stored_path(path: str | Path) -> str:
    """Store drive roots as ``C:\\`` and UNC as ``\\\\server\\share`` without resolving them.

    ``Path('C:').resolve()`` is the process cwd, and resolving a disconnected
    network drive can hang. Ordinary folders still go through expanduser/resolve.
    """
    raw = str(path).strip()
    if not raw:
        return ""
    root = _DRIVE_ROOT_RE.fullmatch(raw)
    if root:
        return f"{root.group(1).upper()}:\\"
    relative = _DRIVE_REL_RE.fullmatch(raw)
    if relative:
        rest = relative.group(2).replace("/", "\\")
        return f"{relative.group(1).upper()}:\\{rest}"
    if _is_unc(raw):
        parts: list[str] = []
        for part in raw.replace("/", "\\").split("\\"):
            if part in {"", "."}:
                continue
            if part == "..":
                if len(parts) > 2:
                    parts.pop()
                continue
            parts.append(part)
        return "\\\\" + "\\".join(parts)
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return str(Path(raw).expanduser())


def lookup_drive_type(root: str) -> int:
    if os.name != "nt":
        return DRIVE_UNKNOWN
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel.GetDriveTypeW.restype = ctypes.c_uint
        return int(kernel.GetDriveTypeW(root))
    except Exception:
        return DRIVE_UNKNOWN


def lookup_volume(root: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.GetVolumeInformationW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        kernel.GetVolumeInformationW.restype = ctypes.c_int
        buf = ctypes.create_unicode_buffer(261)
        ok = kernel.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0)
        if ok and buf.value.strip():
            return buf.value.strip()
    except Exception:
        return ""
    return ""


def lookup_unc(local_name: str) -> str:
    """Map ``Z:`` to ``\\\\server\\share``. This lookup does not touch the server."""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        mpr = ctypes.windll.mpr
        mpr.WNetGetConnectionW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        mpr.WNetGetConnectionW.restype = ctypes.c_ulong
        size = ctypes.c_ulong(260)
        buf = ctypes.create_unicode_buffer(size.value)
        rc = mpr.WNetGetConnectionW(local_name, buf, ctypes.byref(size))
        if rc == 234:
            buf = ctypes.create_unicode_buffer(size.value)
            rc = mpr.WNetGetConnectionW(local_name, buf, ctypes.byref(size))
        if rc == 0 and buf.value:
            return buf.value
    except Exception:
        return ""
    return ""


def drive_facts(path: str | Path) -> tuple[int, str, str]:
    """Return ``(drive_type, volume_label, unc_target)`` for a drive letter.

    The drive type is read every call. Only a mounted drive is cached, and the
    cache is used only while the type still matches, so a later-plugged USB
    drive is not stuck as missing. Volume names are not queried for remote,
    missing, or unknown drives.
    """
    if os.name != "nt":
        return (DRIVE_UNKNOWN, "", "")
    key = _drive_key(path)
    if not key:
        return (DRIVE_UNKNOWN, "", "")
    kind = lookup_drive_type(key)
    if kind < DRIVE_REMOVABLE:
        return (kind, "", "")
    cached = _DRIVE_FACT_CACHE.get(key)
    if cached and cached[0] == kind:
        return cached
    volume = ""
    unc = ""
    if kind == DRIVE_REMOTE:
        unc = lookup_unc(key[:2])
    else:
        volume = lookup_volume(key)
    facts = (kind, volume, unc)
    _DRIVE_FACT_CACHE[key] = facts
    return facts


def volume_label(path: Path) -> str:
    """Return a mounted local volume name. UNC and missing drives are not probed."""
    raw = str(path).strip()
    if not raw or _is_unc(raw):
        return ""
    key = _drive_key(raw)
    if not key:
        return ""
    kind, volume, _unc = drive_facts(key)
    if kind < DRIVE_REMOVABLE or kind == DRIVE_REMOTE:
        return ""
    return volume


def _unc_host_share(path: Path) -> tuple[str, str] | None:
    raw = str(path).replace("/", "\\")
    if raw.startswith("\\\\"):
        parts = [part for part in raw.split("\\") if part]
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None


def _format_drive(letter: str, volume: str, kind: int, unc: str) -> str:
    shown = letter.upper()
    if not shown.endswith(":"):
        shown = f"{shown}:"
    if volume:
        return f"{volume} ({shown})"
    if kind == DRIVE_REMOTE:
        parts = [part for part in (unc or "").replace("/", "\\").split("\\") if part]
        if parts:
            return f"{parts[-1]} ({shown})"
        return f"Network ({shown})"
    if kind == DRIVE_REMOVABLE:
        return f"Removable Disk ({shown})"
    if kind == DRIVE_CDROM:
        return f"CD Drive ({shown})"
    if kind == DRIVE_RAMDISK:
        return f"RAM Disk ({shown})"
    return f"Local Disk ({shown})"


def _bare_drive_label(explicit: str, raw: str) -> bool:
    match = _DRIVE_ROOT_RE.fullmatch(raw.strip())
    if not match:
        return False
    letter = f"{match.group(1).upper()}:"
    text = explicit.strip().replace("/", "\\").rstrip("\\")
    return text.upper() in {letter, match.group(1).upper()}


def _placeholder_label(explicit: str, raw: str) -> bool:
    text = explicit.strip()
    if text.casefold() in _GENERIC_LABELS:
        return True
    return _bare_drive_label(text, raw)


def default_share_label(
    path: str | Path,
    explicit: str = "",
    volume: str | None = None,
    *,
    drive_type: int | None = None,
    unc_target: str | None = None,
) -> str:
    """Name to show for a share or drive.

    A typed name wins. Placeholders such as "Shared folder", a bare ``C:``,
    and a generated fallback ("Local Disk (C:)", "games (Z:)") are replaced
    with the Explorer-style name when a better one is known. Drive and UNC
    roots are recognized from the path string so labels still resolve off Windows.
    """
    raw = str(path).strip()

    def computed_label(vol_arg: str | None, type_arg: int | None, unc_arg: str | None) -> str:
        drive = _DRIVE_ROOT_RE.fullmatch(raw)
        if drive:
            letter = f"{drive.group(1).upper()}:"
            vol = vol_arg
            kind = type_arg
            unc = unc_arg
            if vol:
                return f"{vol} ({letter})"
            need_facts = vol is None or kind is None or (kind == DRIVE_REMOTE and unc is None)
            if need_facts:
                facts_kind, facts_vol, facts_unc = drive_facts(raw)
                if kind is None:
                    kind = facts_kind
                if vol is None:
                    vol = facts_vol
                if unc is None:
                    unc = facts_unc
            if vol:
                return f"{vol} ({letter})"
            return _format_drive(letter, "", DRIVE_UNKNOWN if kind is None else kind, unc or "")
        unc_root = _UNC_ROOT_RE.fullmatch(raw)
        if unc_root:
            label = f"{unc_root.group(1)}:{unc_root.group(2)}"
            return f"{vol_arg} ({label})" if vol_arg else label
        named = raw
        if not _is_unc(named) and not _DRIVE_ROOT_RE.fullmatch(named) and not _DRIVE_REL_RE.fullmatch(named):
            try:
                named = str(Path(named).expanduser())
            except OSError:
                pass
        parts = [part for part in named.replace("\\", "/").split("/") if part not in {"", ".", ".."}]
        if parts:
            return parts[-1]
        if vol_arg:
            return vol_arg
        anchor = (Path(named).anchor or named or "/").rstrip("\\/") or "/"
        return anchor if anchor != "\\" else "/"

    computed = computed_label(volume, drive_type, unc_target)
    explicit_s = str(explicit).strip() if explicit else ""
    if _placeholder_label(explicit_s, raw):
        return computed
    if explicit_s == computed:
        return explicit_s
    fallback = computed_label("", drive_type, unc_target)
    if explicit_s == fallback and computed != fallback:
        return computed
    return explicit_s


def share_label_needs_repair(path: str | Path, label: str) -> bool:
    stored = str(label or "").strip()
    return default_share_label(path, stored) != stored


def display_share(share: dict[str, Any], *, include_path: bool = True) -> dict[str, Any]:
    """Return a share dict whose label is never a placeholder when the path is known."""
    out = dict(share)
    out["label"] = default_share_label(share.get("path") or "", share.get("label") or "")
    if not include_path:
        return {key: out[key] for key in ("id", "label", "mode") if key in out}
    return out


def folder_is_available(path: str | Path) -> bool:
    """True when a folder can be shared. Drive roots use GetDriveTypeW, not exists()."""
    raw = str(path).strip()
    if _DRIVE_ROOT_RE.fullmatch(raw):
        kind, _volume, _unc = drive_facts(canonical_stored_path(raw))
        return kind >= DRIVE_REMOVABLE
    try:
        folder = Path(raw).expanduser()
        return folder.exists() and folder.is_dir()
    except OSError:
        return False


def resolve_user_path(path: str) -> Path:
    """Expand a typed or well-known folder path (e.g. 'Documents')."""
    raw = (path or "").strip()
    if not raw:
        raise FileNotFoundError("Folder path required")
    if _DRIVE_ROOT_RE.fullmatch(raw) or _DRIVE_REL_RE.fullmatch(raw) or _is_unc(raw):
        return Path(canonical_stored_path(raw))
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


def iter_drive_roots() -> list[str]:
    """Drive letters reported by Windows. Does not call exists()."""
    if os.name != "nt":
        return []
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.GetLogicalDriveStringsW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
        kernel.GetLogicalDriveStringsW.restype = ctypes.c_uint
        size = 256
        for _ in range(3):
            buf = ctypes.create_unicode_buffer(size)
            copied = int(kernel.GetLogicalDriveStringsW(size, buf))
            if copied == 0:
                break
            if copied > size:
                size = copied + 2
                continue
            # buf.value stops at the first null, so join the returned characters.
            raw = "".join(buf[i] for i in range(copied))
            roots = [part for part in raw.split("\x00") if part]
            if roots:
                return roots
            break
    except Exception:
        pass
    return [f"{letter}:\\" for letter in string.ascii_uppercase]


def iter_mounted_drives(roots: list[str] | None = None) -> list[tuple[str, int, str, str]]:
    """Mounted drives as ``(root, type, volume, unc)``. Missing letters are omitted."""
    if roots is None:
        roots = iter_drive_roots()
    mounted: list[tuple[str, int, str, str]] = []
    for root in roots:
        key = _drive_key(root) or canonical_stored_path(root)
        kind, volume, unc = drive_facts(key)
        if kind >= DRIVE_REMOVABLE:
            mounted.append((key, kind, volume, unc))
    return mounted


def _dedup_key(path_str: str) -> str:
    raw = str(path_str).strip()
    if _DRIVE_ROOT_RE.fullmatch(raw) or _is_unc(raw):
        return canonical_stored_path(raw)
    try:
        return str(Path(raw).resolve())
    except OSError:
        return raw


def list_local_dirs(rel: str = "") -> dict[str, Any]:
    """List subfolders for the in-app folder picker. Stays on this PC."""
    if not (rel or "").strip():
        roots: list[dict[str, Any]] = []
        home = Path.home()
        roots.append({"name": "Home", "path": str(home), "dir": True})
        for item in known_folders():
            roots.append({"name": item["name"], "path": item["path"], "dir": True})
        if os.name == "nt":
            for root, kind, volume, unc in iter_mounted_drives():
                label = default_share_label(root, "", volume=volume, drive_type=kind, unc_target=unc)
                roots.append({"name": label, "path": root, "dir": True})
        # Dedup by canonical path. Drive roots are not resolve()'d; that can hang.
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in roots:
            key = _dedup_key(item["path"])
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return {"path": "", "parent": None, "items": deduped}

    raw = rel.strip()
    if _DRIVE_ROOT_RE.fullmatch(raw) or _DRIVE_REL_RE.fullmatch(raw) or _is_unc(raw):
        canon = canonical_stored_path(raw)
        path = Path(canon)
        if _DRIVE_ROOT_RE.fullmatch(raw):
            if not folder_is_available(canon):
                raise FileNotFoundError("Folder does not exist on this PC")
        else:
            try:
                if not path.exists() or not path.is_dir():
                    raise FileNotFoundError("Folder does not exist on this PC")
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise FileNotFoundError("Folder does not exist on this PC") from exc
    else:
        path = Path(raw).expanduser()
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
    root_s = canonical_stored_path(share["path"])
    root = Path(root_s)
    if not folder_is_available(root_s):
        raise FileNotFoundError("Share folder is missing on this PC")
    rel = sanitize_relpath(rel or "")
    if _DRIVE_ROOT_RE.fullmatch(root_s):
        if rel:
            target = Path(root_s.rstrip("\\/") + "\\" + rel.replace("/", "\\"))
        else:
            target = root
        return share, target
    target = (root / rel).resolve() if rel else root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Path escapes the shared folder") from exc
    return share, target


def list_dir(path: Path) -> list[dict[str, Any]]:
    try:
        is_dir = path.is_dir()
    except OSError as exc:
        raise PermissionError("That folder cannot be browsed") from exc
    if not is_dir:
        raise FileNotFoundError("Folder does not exist")
    try:
        children = list(path.iterdir())
    except OSError as exc:
        raise PermissionError("That folder cannot be browsed") from exc
    rows: list[tuple[bool, str, dict[str, Any]]] = []
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            stat = child.stat()
            child_dir = child.is_dir()
        except OSError:
            continue
        rows.append(
            (
                not child_dir,
                child.name.lower(),
                {
                    "name": child.name,
                    "dir": child_dir,
                    "size": 0 if child_dir else stat.st_size,
                    "mtime": stat.st_mtime,
                },
            )
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in rows]


def guess_type(path: Path) -> str:
    typ, _ = mimetypes.guess_type(str(path))
    return typ or "application/octet-stream"
