# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH).resolve().parent
import importlib.util
import sys
sys.path.insert(0, str(root))
from hearth import __version__ as HEARTH_VERSION
_helper = Path(SPECPATH) / "write_version_info.py"
_spec = importlib.util.spec_from_file_location("hearth_write_version_info", _helper)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
version_file = _mod.write_version_info(Path(SPECPATH) / "Hearth.fileinfo", HEARTH_VERSION)
datas = []
binaries = []
hiddenimports = [
    "hearth",
    "hearth.app",
    "hearth.desktop",
    "hearth.discovery",
    "hearth.__main__",
    "hearth.update",
    "hearth.paths",
    "hearth.state",
    "hearth.netutil",
    "hearth.files",
    "hearth.crypto",
    "hearth.access",
    "hearth.authenticode",
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

ui_static = root / "hearth" / "ui" / "static"
if ui_static.is_dir():
    datas.append((str(ui_static), str(Path("hearth") / "ui" / "static")))

icon = str(Path(SPECPATH) / "hearth.ico")

a = Analysis(
    [str(root / "pack" / "hearth_launcher.py")],
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
    version=str(version_file),
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
