"""Start CI child processes without a console or foreground window."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

# Windows: do not allocate a new console for the child.
CREATE_NO_WINDOW = 0x08000000
SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002
SEM_NOOPENFILEERRORBOX = 0x8000


def suppress_error_dialogs() -> None:
    """Hide WER/critical-error dialogs for this CI process only (no system settings)."""
    if sys.platform != "win32":
        return
    import ctypes

    ctypes.windll.kernel32.SetErrorMode(
        SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
    )


def hidden_popen_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    suppress_error_dialogs()
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": CREATE_NO_WINDOW,
    }


def popen(args: list[str], **kwargs: Any) -> subprocess.Popen:
    extra = hidden_popen_kwargs()
    if "creationflags" in kwargs and "creationflags" in extra:
        extra["creationflags"] = int(kwargs.pop("creationflags") or 0) | int(extra["creationflags"])
    extra.update(kwargs)
    return subprocess.Popen(args, **extra)


def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    extra = hidden_popen_kwargs()
    if "creationflags" in kwargs and "creationflags" in extra:
        extra["creationflags"] = int(kwargs.pop("creationflags") or 0) | int(extra["creationflags"])
    extra.update(kwargs)
    return subprocess.run(args, **extra)
