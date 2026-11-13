"""Resolve packaged files in a source checkout or a frozen (PyInstaller) build."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def meipass() -> Path | None:
    raw = getattr(sys, "_MEIPASS", None)
    if raw:
        return Path(raw)
    return None


def package_root() -> Path:
    """Directory that contains this package's ui/ and modules."""
    here = Path(__file__).resolve().parent
    index = Path("ui") / "static" / "index.html"
    candidates = [here]
    root = meipass()
    if root is not None:
        candidates.extend(
            [
                root / "hearth",
                root,
                Path(sys.executable).resolve().parent / "hearth",
                Path(sys.executable).resolve().parent,
            ]
        )
    for cand in candidates:
        if (cand / index).exists():
            return cand
    return here


def static_ui_dir() -> Path:
    return package_root() / "ui" / "static"
