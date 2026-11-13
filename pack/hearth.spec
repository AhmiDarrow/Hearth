# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH).resolve().parent
datas = []
binaries = []
hiddenimports = [
    "hearth",
    "hearth.app",
    "hearth.desktop",
    "hearth.discovery",
    "hearth.__main__",
    "hearth.update",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "anyio",
    "httpx",
    "multipart",
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "ifaddr",
    "zeroconf",
    "cryptography",
]

for pkg in (
    "hearth",
    "uvicorn",
    "fastapi",
    "starlette",
    "anyio",
    "httpx",
    "pydantic",
    "cryptography",
    "webview",
    "pystray",
    "PIL",
    "ifaddr",
    "zeroconf",
    "websockets",
    "watchfiles",
    "httptools",
    "multipart",
):
    try:
        collected = collect_all(pkg)
        datas += collected[0]
        binaries += collected[1]
        hiddenimports += collected[2]
    except Exception:
        pass

try:
    datas += collect_data_files("hearth")
except Exception:
    pass

icon = str(Path(SPECPATH) / "hearth.ico")

a = Analysis(
    [str(root / "hearth" / "__main__.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["uvloop"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Hearth",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Hearth",
    strip=False,
    upx=False,
)
