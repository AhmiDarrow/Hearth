"""Start-at-sign-in via a Startup-folder shortcut. File/COM only, no shell."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path


SHORTCUT_NAME = "Hearth.lnk"


def startup_dir() -> Path:
    override = os.environ.get("HEARTH_STARTUP_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / ".config" / "autostart"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def start_at_login_enabled() -> bool:
    return shortcut_path().is_file()


def exe_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.executable)


def set_start_at_login(enabled: bool, *, target: Path | None = None, args: str = "--background") -> bool:
    """Create or remove the Startup-folder shortcut. Returns whether it is enabled."""
    path = shortcut_path()
    if not enabled:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return path.is_file()
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    exe = Path(target or exe_path())
    if sys.platform == "win32" and not os.environ.get("HEARTH_STARTUP_DIR"):
        try:
            _write_windows_shortcut(path, exe, args)
        except Exception:
            path.write_bytes(build_shortcut(exe, args, workdir=exe.parent))
    else:
        # Tests (and non-Windows) record the intended target as a small text file
        # so the toggle can be exercised with ordinary file operations.
        path.write_text(f"{exe}\n{args}\n", encoding="utf-8")
    return path.is_file()


def _write_windows_shortcut(lnk: Path, target: Path, args: str) -> None:
    """Create a .lnk with IShellLink. Falls back to a hand-built Shell Link file."""
    try:
        _com_write_shortcut(lnk, target, args)
        if lnk.is_file() and lnk.stat().st_size > 0:
            return
    except Exception:
        pass
    lnk.write_bytes(build_shortcut(target, args, workdir=target.parent))


def _com_write_shortcut(lnk: Path, target: Path, args: str) -> None:
    import ctypes
    from ctypes import HRESULT, POINTER, byref, c_void_p, c_wchar_p, wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitialize(None)

    clsid = _guid("00021401-0000-0000-C000-000000000046")
    iid_link = _guid("000214F9-0000-0000-C000-000000000046")
    iid_persist = _guid("0000010b-0000-0000-C000-000000000046")

    class VTbl(ctypes.Structure):
        _fields_ = [("fn", ctypes.c_void_p * 32)]

    ole32.CoCreateInstance.argtypes = [c_void_p, c_void_p, wintypes.DWORD, c_void_p, POINTER(c_void_p)]
    ole32.CoCreateInstance.restype = HRESULT
    link = c_void_p()
    hr = ole32.CoCreateInstance(byref(clsid), None, 1, byref(iid_link), byref(link))
    if hr < 0 or not link.value:
        raise OSError(hr)

    vptr = ctypes.cast(link, POINTER(POINTER(VTbl)))
    vtbl = vptr.contents.contents

    def call(index: int, *cargs, restype=HRESULT):
        proto = ctypes.WINFUNCTYPE(restype, c_void_p, *[type(a) for a in cargs])
        return proto(vtbl.fn[index])(link, *cargs)

    # IShellLinkW: SetWorkingDirectory=9, SetArguments=11, SetPath=20
    if call(20, c_wchar_p(str(target))) < 0:
        raise OSError("SetPath failed")
    if call(11, c_wchar_p(args or "")) < 0:
        raise OSError("SetArguments failed")
    call(9, c_wchar_p(str(target.parent)))

    persist = c_void_p()
    qi = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(c_void_p))(vtbl.fn[0])
    hr = qi(link, byref(iid_persist), byref(persist))
    if hr < 0 or not persist.value:
        raise OSError(hr)
    pvptr = ctypes.cast(persist, POINTER(POINTER(VTbl)))
    pvtbl = pvptr.contents.contents
    # IPersistFile.Save is vtable index 6
    save = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_wchar_p, wintypes.BOOL)(pvtbl.fn[6])
    hr = save(persist, str(lnk), True)
    ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(pvtbl.fn[2])(persist)
    ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl.fn[2])(link)
    if hr < 0:
        raise OSError(hr)


def _guid(text: str):
    from ctypes import Structure, c_byte, c_uint, c_ushort

    parts = text.split("-")
    data4 = bytes.fromhex(parts[3] + parts[4])

    class GUID(Structure):
        _fields_ = [
            ("Data1", c_uint),
            ("Data2", c_ushort),
            ("Data3", c_ushort),
            ("Data4", c_byte * 8),
        ]

    g = GUID()
    g.Data1 = int(parts[0], 16)
    g.Data2 = int(parts[1], 16)
    g.Data3 = int(parts[2], 16)
    for i, b in enumerate(data4):
        g.Data4[i] = b
    return g


def build_shortcut(target: Path | str, arguments: str = "", workdir: Path | str | None = None) -> bytes:
    """Build a Shell Link (.lnk) binary for a local target. File-format only."""
    target_s = str(target)
    work_s = str(workdir or Path(target_s).parent)
    flags = 0x02 | 0x10 | 0x80  # HasLinkInfo | HasWorkingDir | IsUnicode
    if arguments:
        flags |= 0x20  # HasArguments

    header = struct.pack("<I", 0x4C)
    header += bytes.fromhex("0114020000000000C000000000000046")
    header += struct.pack("<I", flags)
    header += struct.pack("<I", 0x20)  # FILE_ATTRIBUTE_ARCHIVE
    header += b"\x00" * 24
    header += struct.pack("<IIIH", 0, 0, 1, 0)  # size, icon, SW_SHOWNORMAL, hotkey
    header += b"\x00" * 10

    # LinkInfo with VolumeID + local base path, plus Unicode path extras.
    ansi = target_s.encode("utf-8", "replace") + b"\x00"
    ansi_suffix = b"\x00"
    volume = struct.pack("<IIII", 16, 3, 0, 16)  # size, DRIVE_FIXED, serial, label offset
    # Header size 0x24 includes unicode offsets.
    # Layout: header(0x24) + volume + ansi path + suffix
    header_size = 0x24
    volume_offset = header_size
    local_offset = volume_offset + len(volume)
    suffix_offset = local_offset + len(ansi)
    uni_path = target_s.encode("utf-16le") + b"\x00\x00"
    uni_suffix = b"\x00\x00"
    uni_path_offset = suffix_offset + len(ansi_suffix)
    uni_suffix_offset = uni_path_offset + len(uni_path)
    total = uni_suffix_offset + len(uni_suffix)
    # Pad to a 4-byte boundary
    pad = (4 - (total % 4)) % 4
    total += pad
    link_info = struct.pack(
        "<IIIIIIII",
        total,
        header_size,
        1,  # VolumeIDAndLocalBasePath
        volume_offset,
        local_offset,
        0,
        suffix_offset,
        uni_path_offset,
    )
    # The 8th field above overlaps CommonPathSuffixOffset and we still need
    # CommonPathSuffixOffsetUnicode. Rebuild explicitly.
    link_info = b"".join(
        [
            struct.pack("<I", total),
            struct.pack("<I", header_size),
            struct.pack("<I", 1),
            struct.pack("<I", volume_offset),
            struct.pack("<I", local_offset),
            struct.pack("<I", 0),
            struct.pack("<I", suffix_offset),
            struct.pack("<I", uni_path_offset),
            struct.pack("<I", uni_suffix_offset),
            volume,
            ansi,
            ansi_suffix,
            uni_path,
            uni_suffix,
            b"\x00" * pad,
        ]
    )

    def string_data(text: str) -> bytes:
        encoded = text.encode("utf-16le")
        count = len(encoded) // 2
        return struct.pack("<H", count) + encoded

    extra = string_data(work_s)
    if arguments:
        extra += string_data(arguments)
    return header + link_info + extra + struct.pack("<I", 0)
