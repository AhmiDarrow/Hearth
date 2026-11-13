"""Native folder picker. COM / pywebview only — no shell, no PowerShell."""

from __future__ import annotations

import sys
from pathlib import Path


def pick_folder(directory: str = "", hwnd: int | None = None) -> str | None:
    """Open a native folder dialog and return the chosen path, or None if cancelled."""
    if sys.platform == "win32":
        try:
            chosen = _windows_pick_folder(directory, hwnd)
            if chosen:
                return chosen
        except Exception:
            pass
    return None


def _windows_pick_folder(directory: str, hwnd: int | None) -> str | None:
    import ctypes
    from ctypes import HRESULT, POINTER, byref, c_void_p, c_wchar_p, wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitialize(None)

    CLSID_FileOpenDialog = _guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
    IID_IFileOpenDialog = _guid("D57C7288-D4AD-4768-BE02-9D969532D960")
    IID_IShellItem = _guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")
    FOS_PICKFOLDERS = 0x20
    FOS_FORCEFILESYSTEM = 0x40
    SIGDN_FILESYSPATH = 0x80058000
    CLSCTX_INPROC_SERVER = 1

    class VTbl(ctypes.Structure):
        _fields_ = [("fn", ctypes.c_void_p * 32)]

    ole32.CoCreateInstance.argtypes = [c_void_p, c_void_p, wintypes.DWORD, c_void_p, POINTER(c_void_p)]
    ole32.CoCreateInstance.restype = HRESULT
    dialog = c_void_p()
    hr = ole32.CoCreateInstance(
        byref(CLSID_FileOpenDialog),
        None,
        CLSCTX_INPROC_SERVER,
        byref(IID_IFileOpenDialog),
        byref(dialog),
    )
    if hr < 0 or not dialog.value:
        raise OSError(hr)

    vptr = ctypes.cast(dialog, POINTER(POINTER(VTbl)))
    vtbl = vptr.contents.contents

    def call(index: int, *cargs, restype=HRESULT):
        proto = ctypes.WINFUNCTYPE(restype, c_void_p, *[type(a) for a in cargs])
        return proto(vtbl.fn[index])(dialog, *cargs)

    # IFileDialog.SetOptions = 9, SetTitle = 17, SetFolder = 12, Show = 3, GetResult = 20
    call(9, wintypes.DWORD(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM))
    call(17, c_wchar_p("Choose a folder"))

    start = Path(directory).expanduser() if directory else None
    if start and start.exists():
        try:
            # SHCreateItemFromParsingName
            shell32 = ctypes.WinDLL("shell32", use_last_error=True)
            item = c_void_p()
            shell32.SHCreateItemFromParsingName.argtypes = [c_wchar_p, c_void_p, c_void_p, POINTER(c_void_p)]
            shell32.SHCreateItemFromParsingName.restype = HRESULT
            if shell32.SHCreateItemFromParsingName(str(start), None, byref(IID_IShellItem), byref(item)) >= 0 and item.value:
                call(12, item)
        except Exception:
            pass

    hr = call(3, wintypes.HWND(hwnd or 0))  # Show
    if hr < 0:
        return None  # cancelled (HRESULT_FROM_WIN32(ERROR_CANCELLED) is negative)

    result = c_void_p()
    hr = call(20, byref(result))  # GetResult
    if hr < 0 or not result.value:
        return None

    pvptr = ctypes.cast(result, POINTER(POINTER(VTbl)))
    pvtbl = pvptr.contents.contents
    # IShellItem.GetDisplayName = 5
    name = c_wchar_p()
    get_name = ctypes.WINFUNCTYPE(HRESULT, c_void_p, ctypes.c_ulong, POINTER(c_wchar_p))(pvtbl.fn[5])
    hr = get_name(result, SIGDN_FILESYSPATH, byref(name))
    path = name.value if hr >= 0 else None
    # Release IShellItem
    ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(pvtbl.fn[2])(result)
    ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl.fn[2])(dialog)
    return path


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
