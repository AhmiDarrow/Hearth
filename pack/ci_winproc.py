"""Start CI child processes without a console or foreground window."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

# Windows: do not allocate a new console for the child.
CREATE_NO_WINDOW = 0x08000000


def hidden_popen_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
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
