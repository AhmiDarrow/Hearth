"""Windows desktop shell: tray icon + an app window (WebView2) with browser fallback."""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    # Frozen: data lives under _MEIPASS/hearth/ui/static or next to the exe.
    candidates = [
        base.joinpath(*parts),
        base / "hearth" / Path(*parts),
        Path(__file__).resolve().parent.joinpath(*parts),
        Path(sys.executable).resolve().parent.joinpath(*parts),
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def icon_path() -> Path:
    for name in ("ui/static/hearth.png", "ui/static/icon.png"):
        path = resource_path(*name.split("/"))
        if path.exists():
            return path
    return resource_path("ui", "static", "hearth.png")


def wait_until_up(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def _start_tray(on_open: Callable[[], None], on_quit: Callable[[], None]) -> Any:
    from PIL import Image, ImageDraw
    from pystray import Icon, Menu, MenuItem

    path = icon_path()
    if path.exists():
        image = Image.open(path).convert("RGBA")
    else:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((4, 4, 60, 60), 16, fill=(227, 155, 90, 255))

    menu = Menu(
        MenuItem("Open Hearth", lambda: on_open(), default=True),
        MenuItem("Quit", lambda: on_quit()),
    )
    icon = Icon("Hearth", image, "Hearth", menu)
    thread = threading.Thread(target=icon.run, name="hearth-tray", daemon=True)
    thread.start()
    return icon


def run_desktop(url: str, *, background: bool = False, on_show: Callable[[Callable[[], None]], None] | None = None) -> None:
    """Show the local UI in a native window and a tray icon. Falls back to a browser."""
    window_holder: dict[str, Any] = {}
    quitting = {"yes": False}

    def show() -> None:
        window = window_holder.get("window")
        if window is not None:
            try:
                window.show()
                restore = getattr(window, "restore", None)
                if callable(restore):
                    restore()
                return
            except Exception:
                pass
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def quit_app(icon: Any | None = None) -> None:
        if quitting["yes"]:
            return
        quitting["yes"] = True
        try:
            if icon is not None:
                icon.stop()
        except Exception:
            pass
        window = window_holder.get("window")
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        # Hard-exit so uvicorn's daemon thread cannot keep the process alive.
        os._exit(0)

    wait_until_up(f"{url}/api/state")
    if on_show:
        on_show(show)

    tray = None
    try:
        tray = _start_tray(show, lambda: quit_app(tray))
    except Exception:
        tray = None

    try:
        import webview
    except Exception:
        if not background:
            webbrowser.open(url)
        if tray is not None:
            try:
                while not quitting["yes"]:
                    time.sleep(0.4)
            except KeyboardInterrupt:
                quit_app(tray)
        return

    window = webview.create_window(
        "Hearth",
        url,
        width=1320,
        height=860,
        min_size=(900, 600),
        background_color="#161310",
        hidden=background,
        confirm_close=False,
    )
    window_holder["window"] = window

    def on_closing() -> bool:
        # Close hides to the tray instead of quitting.
        if quitting["yes"]:
            return True
        try:
            window.hide()
        except Exception:
            return True
        return False

    try:
        window.events.closing += on_closing
    except Exception:
        pass

    def on_closed() -> None:
        if not quitting["yes"]:
            try:
                window_holder.pop("window", None)
            except Exception:
                pass

    try:
        window.events.closed += on_closed
    except Exception:
        pass

    try:
        webview.start()
    except Exception:
        if not background:
            webbrowser.open(url)
        if tray is not None:
            try:
                while not quitting["yes"]:
                    time.sleep(0.4)
            except KeyboardInterrupt:
                pass
    finally:
        quit_app(tray)
