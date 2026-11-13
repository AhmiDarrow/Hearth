"""Native Windows drag-out (CF_HDROP) so files can be dropped onto Explorer.

Implemented with ctypes / OLE only — no shell, no PowerShell. On any
platform other than Windows the helpers return False and the UI falls back
to Chromium DownloadURL when that is available.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["drag_files", "reveal_path", "shell_open"]


def drag_files(paths: list[str] | list[Path]) -> bool:
    existing = [str(Path(p)) for p in paths if p and Path(p).exists()]
    if not existing:
        return False
    if sys.platform != "win32":
        return False
    return _win_drag_files(existing)


def reveal_path(path: str | Path) -> bool:
    target = Path(path)
    if not target.exists():
        return False
    if sys.platform != "win32":
        return False
    # Opening the parent folder is enough; select-item would need explorer.exe.
    folder = target if target.is_dir() else target.parent
    return shell_open(folder)


def shell_open(path: str | Path) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
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
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    rc = shell32.ShellExecuteW(None, "open", str(path), None, None, 1)
    return int(rc) > 32


def _win_drag_files(paths: list[str]) -> bool:
    import ctypes
    from ctypes import HRESULT, POINTER, Structure, byref, c_uint, c_void_p, c_wchar, sizeof, wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.OleInitialize(None)

    CF_HDROP = 15
    TYMED_HGLOBAL = 1
    DVASPECT_CONTENT = 1
    DROPEFFECT_COPY = 1
    DRAGDROP_S_DROP = 0x00040100
    DRAGDROP_S_CANCEL = 0x00040101
    DATA_S_SAMEFORMATETC = 0x00040130
    E_NOTIMPL = 0x80004001
    E_NOINTERFACE = 0x80004002
    E_UNEXPECTED = 0x8000FFFF
    S_OK = 0
    S_FALSE = 1
    MK_LBUTTON = 0x0001

    class GUID(Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(text: str) -> GUID:
        parts = text.split("-")
        data4 = bytes.fromhex(parts[3] + parts[4])
        g = GUID(int(parts[0], 16), int(parts[1], 16), int(parts[2], 16))
        for i, b in enumerate(data4):
            g.Data4[i] = b
        return g

    IID_IUnknown = guid("00000000-0000-0000-C000-000000000046")
    IID_IDropSource = guid("00000121-0000-0000-C000-000000000046")
    IID_IDataObject = guid("0000010E-0000-0000-C000-000000000046")

    class POINTL(Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class DROPFILES(Structure):
        _fields_ = [
            ("pFiles", wintypes.DWORD),
            ("pt", POINTL),
            ("fNC", wintypes.BOOL),
            ("fWide", wintypes.BOOL),
        ]

    class FORMATETC(Structure):
        _fields_ = [
            ("cfFormat", wintypes.WORD),
            ("ptd", c_void_p),
            ("dwAspect", wintypes.DWORD),
            ("lindex", wintypes.LONG),
            ("tymed", wintypes.DWORD),
        ]

    class STGMEDIUM(Structure):
        _fields_ = [
            ("tymed", wintypes.DWORD),
            ("hGlobal", wintypes.HGLOBAL),
            ("pUnkForRelease", c_void_p),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    GMEM_MOVEABLE = 0x0002
    GMEM_ZEROINIT = 0x0040

    def make_hdrop(items: list[str]) -> wintypes.HGLOBAL:
        payload = ("\0".join(items) + "\0\0").encode("utf-16le")
        size = sizeof(DROPFILES) + len(payload)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, size)
        if not handle:
            raise OSError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(handle)
        try:
            drop = DROPFILES.from_address(ptr)
            drop.pFiles = sizeof(DROPFILES)
            drop.pt.x = 0
            drop.pt.y = 0
            drop.fNC = False
            drop.fWide = True
            ctypes.memmove(ptr + sizeof(DROPFILES), payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)
        return handle

    # COM object helpers -------------------------------------------------
    class COMObject:
        def __init__(self, iids: list[GUID]):
            self._ref = 1
            self._iids = iids
            self._keep = []  # keep callbacks alive

        def QueryInterface(self, this, riid, ppv):
            iid = GUID.from_address(riid)
            wanted = bytes(iid)
            for supported in self._iids:
                if bytes(supported) == wanted:
                    ctypes.cast(ppv, POINTER(c_void_p))[0] = this
                    self._ref += 1
                    return S_OK
            ctypes.cast(ppv, POINTER(c_void_p))[0] = None
            return E_NOINTERFACE

        def AddRef(self, this):
            self._ref += 1
            return self._ref

        def Release(self, this):
            self._ref -= 1
            return self._ref

    def make_vtable(obj: COMObject, methods: list) -> tuple[c_void_p, list]:
        """Build an IUnknown-style object pointer: [vtbl_ptr] where vtbl is an array of function pointers."""
        keep = []
        slots = []
        for proto, fn in methods:
            cb = proto(fn)
            keep.append(cb)
            slots.append(ctypes.cast(cb, c_void_p))
        arr_t = c_void_p * len(slots)
        vtbl = arr_t(*slots)
        keep.append(vtbl)
        holder = (c_void_p * 1)(ctypes.addressof(vtbl))
        keep.append(holder)
        obj._keep.extend(keep)
        return ctypes.cast(holder, c_void_p), keep

    drop_source = COMObject([IID_IUnknown, IID_IDropSource])

    def ds_query_continue(this, escape, key_state):
        if escape:
            return DRAGDROP_S_CANCEL
        if not (key_state & MK_LBUTTON):
            return DRAGDROP_S_DROP
        return S_OK

    def ds_give_feedback(this, effect):
        return DATA_S_SAMEFORMATETC  # use default cursor (actually DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102)

    DRAGDROP_S_USEDEFAULTCURSORS = 0x00040102

    def ds_give_feedback2(this, effect):
        return DRAGDROP_S_USEDEFAULTCURSORS

    QI = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
    ADDREF = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
    RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
    QCONTINUE = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.BOOL, wintypes.DWORD)
    FEEDBACK = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD)

    ds_ptr, _ds_keep = make_vtable(
        drop_source,
        [
            (QI, drop_source.QueryInterface),
            (ADDREF, drop_source.AddRef),
            (RELEASE, drop_source.Release),
            (QCONTINUE, ds_query_continue),
            (FEEDBACK, ds_give_feedback2),
        ],
    )

    data_object = COMObject([IID_IUnknown, IID_IDataObject])
    hdrop = make_hdrop(paths)

    def matches(fmt: FORMATETC) -> bool:
        return fmt.cfFormat == CF_HDROP and (fmt.tymed & TYMED_HGLOBAL) and fmt.dwAspect == DVASPECT_CONTENT

    def do_get_data(this, pformatetc, pmedium):
        fmt = FORMATETC.from_address(int(pformatetc) if not isinstance(pformatetc, int) else pformatetc)
        # pformatetc is a pointer
        fmt = ctypes.cast(pformatetc, POINTER(FORMATETC)).contents
        if not matches(fmt):
            return 0x80040064  # DV_E_FORMATETC
        # Clone the HGLOBAL so the drop target can take ownership.
        locked = kernel32.GlobalLock(hdrop)
        # We don't know the size easily; use GlobalSize
        kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalSize.restype = ctypes.c_size_t
        size = kernel32.GlobalSize(hdrop)
        clone = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, size)
        dest = kernel32.GlobalLock(clone)
        ctypes.memmove(dest, locked, size)
        kernel32.GlobalUnlock(hdrop)
        kernel32.GlobalUnlock(clone)
        medium = ctypes.cast(pmedium, POINTER(STGMEDIUM)).contents
        medium.tymed = TYMED_HGLOBAL
        medium.hGlobal = clone
        medium.pUnkForRelease = None
        return S_OK

    def do_get_data_here(this, pformatetc, pmedium):
        return E_NOTIMPL

    def do_query_get_data(this, pformatetc):
        fmt = ctypes.cast(pformatetc, POINTER(FORMATETC)).contents
        return S_OK if matches(fmt) else 0x80040064

    def do_get_canonical(this, pin, pout):
        return DATA_S_SAMEFORMATETC

    def do_set_data(this, pformatetc, pmedium, release):
        return E_NOTIMPL

    def do_enum_format(this, direction, ppenum):
        return E_NOTIMPL

    def do_dadvise(this, pformatetc, advf, padvise, pdw):
        return E_NOTIMPL

    def do_undadvise(this, dw):
        return E_NOTIMPL

    def do_enum_advise(this, ppenum):
        return E_NOTIMPL

    GETDATA = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, c_void_p)
    GETDATAHERE = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, c_void_p)
    QUERYGET = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p)
    GETCANON = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, c_void_p)
    SETDATA = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, c_void_p, wintypes.BOOL)
    ENUMFMT = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD, c_void_p)
    DADVISE = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, wintypes.DWORD, c_void_p, c_void_p)
    UNADVISE = ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.DWORD)
    ENUMADV = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p)

    do_ptr, _do_keep = make_vtable(
        data_object,
        [
            (QI, data_object.QueryInterface),
            (ADDREF, data_object.AddRef),
            (RELEASE, data_object.Release),
            (GETDATA, do_get_data),
            (GETDATAHERE, do_get_data_here),
            (QUERYGET, do_query_get_data),
            (GETCANON, do_get_canonical),
            (SETDATA, do_set_data),
            (ENUMFMT, do_enum_format),
            (DADVISE, do_dadvise),
            (UNADVISE, do_undadvise),
            (ENUMADV, do_enum_advise),
        ],
    )

    ole32.DoDragDrop.argtypes = [c_void_p, c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD)]
    ole32.DoDragDrop.restype = HRESULT
    effect = wintypes.DWORD(0)
    try:
        hr = ole32.DoDragDrop(do_ptr, ds_ptr, DROPEFFECT_COPY, byref(effect))
        return hr in {S_OK, DRAGDROP_S_DROP, DRAGDROP_S_CANCEL} or hr >= 0
    except Exception:
        return False
    finally:
        try:
            kernel32.GlobalFree(hdrop)
        except Exception:
            pass
