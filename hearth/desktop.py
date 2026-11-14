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


# Living desktop shell, so the updater can stop the tray and window without
# killing the process before Setup has been launched.
_shell: dict[str, Any] = {}


def stop_shell() -> None:
    """Stop the tray icon and destroy the window. Does not exit the process."""
    icon = _shell.get("tray")
    if icon is not None:
        try:
            icon.stop()
        except Exception:
            pass
        _shell.pop("tray", None)
    window = _shell.get("window")
    if window is not None:
        try:
            window.destroy()
        except Exception:
            pass
        _shell.pop("window", None)


def resource_path(*parts: str) -> Path:
    from .paths import meipass, package_root

    candidates = [
        package_root().joinpath(*parts),
        *(
            [meipass().joinpath(*parts), meipass() / "hearth" / Path(*parts)]
            if meipass() is not None
            else []
        ),
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


class HearthBridge:
    """js_api exposed to the WebView as window.pywebview.api."""

    def __init__(self, window_holder: dict[str, Any], show: Callable[[], None], quit_app: Callable[[], None]):
        self._windows = window_holder
        self._show = show
        self._quit = quit_app

    def _eval(self, script: str) -> None:
        window = self._windows.get("window")
        if window is None:
            return
        try:
            window.evaluate_js(script)
        except Exception:
            pass

    def open_settings(self) -> bool:
        self._show()
        self._eval("window.hearthOpenSettings && window.hearthOpenSettings()")
        return True

    def check_updates(self) -> bool:
        self._show()
        self._eval("window.hearthCheckUpdates && window.hearthCheckUpdates()")
        return True

    def open_about(self) -> bool:
        self._show()
        self._eval("window.hearthOpenAbout && window.hearthOpenAbout()")
        return True

    def quit(self) -> bool:
        self._quit()
        return True

    def pick_folder(self, directory: str = "") -> str:
        """Open the native folder picker and return the chosen path (or "")."""
        window = self._windows.get("window")
        if window is not None:
            try:
                import webview

                dialog = getattr(webview, "FOLDER_DIALOG", None)
                if dialog is None:
                    file_dialog = getattr(webview, "FileDialog", None)
                    dialog = getattr(file_dialog, "FOLDER", None) if file_dialog is not None else None
                if dialog is not None:
                    result = window.create_file_dialog(dialog, directory=directory or "")
                    if result:
                        path = result[0] if isinstance(result, (list, tuple)) else result
                        if path:
                            return str(path)
            except Exception:
                pass
        from .folderpick import pick_folder as native_pick

        return native_pick(directory) or ""

    def drag_files(self, paths: list[str] | None = None) -> bool:
        from .windrag import drag_files

        return bool(drag_files(list(paths or [])))

    def reveal(self, path: str = "") -> bool:
        from .windrag import reveal_path

        return bool(reveal_path(path))

    def open_url(self, url: str = "") -> bool:
        text = (url or "").strip()
        if not text.startswith(("https://", "http://")):
            return False
        if sys.platform == "win32":
            from .windrag import shell_open

            return bool(shell_open(text))
        try:
            webbrowser.open(text)
            return True
        except Exception:
            return False


def _start_tray(
    on_open: Callable[[], None],
    on_settings: Callable[[], None],
    on_updates: Callable[[], None],
    on_about: Callable[[], None],
    on_quit: Callable[[], None],
) -> Any:
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
        MenuItem("Open", lambda: on_open(), default=True),
        MenuItem("Settings", lambda: on_settings()),
        MenuItem("Check for updates", lambda: on_updates()),
        MenuItem("About", lambda: on_about()),
        MenuItem("Quit", lambda: on_quit()),
    )
    icon = Icon("Hearth", image, "Hearth", menu)
    thread = threading.Thread(target=icon.run, name="hearth-tray", daemon=True)
    thread.start()
    return icon


def run_desktop(url: str, *, background: bool = False, on_show: Callable[[Callable[[], None]], None] | None = None) -> None:
    """Show the local UI in a native window and a tray icon. Falls back to a browser."""
    window_holder: dict[str, Any] = _shell
    window_holder.clear()
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

    bridge = HearthBridge(window_holder, show, lambda: quit_app(window_holder.get("tray")))

    wait_until_up(f"{url}/api/state")
    if on_show:
        on_show(show)

    tray = None
    try:
        tray = _start_tray(
            show,
            bridge.open_settings,
            bridge.check_updates,
            bridge.open_about,
            lambda: quit_app(tray),
        )
        window_holder["tray"] = tray
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
        width=1280,
        height=800,
        min_size=(720, 520),
        background_color="#161310",
        hidden=background,
        confirm_close=False,
        js_api=bridge,
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
