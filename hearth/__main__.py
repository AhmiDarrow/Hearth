"""python -m hearth"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app
from .state import Store
from . import update as hearth_update
from . import __version__


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def pick_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", preferred))
            return preferred
        except OSError:
            sock.bind(("0.0.0.0", 0))
            return sock.getsockname()[1]


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return isinstance(data, dict) and "device" in data
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return False


def request_show(port: int) -> bool:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/ui/show",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def acquire_mutex() -> bool:
    """Best-effort single-instance lock on Windows; always true elsewhere."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "HearthSingleInstance")
        return kernel32.GetLastError() != 183
    except Exception:
        return True


def configure_logging(root: Path) -> None:
    if not is_frozen() and sys.stderr and sys.stderr.isatty():
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        log = (root / "hearth.log").open("a", encoding="utf-8")
        sys.stdout = log
        sys.stderr = log
    except OSError:
        pass


def main() -> None:
    default_desktop = is_frozen() or os.environ.get("HEARTH_DESKTOP") == "1"
    parser = argparse.ArgumentParser(description="Hearth — HomeGroup for the whole house")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HEARTH_PORT", "7474")))
    parser.add_argument("--home", type=str, default=os.environ.get("HEARTH_HOME"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--desktop", action="store_true", default=default_desktop)
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--background", action="store_true", help="Start in the tray without opening a window")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--updated",
        nargs="?",
        const=__version__,
        default=None,
        help="Written by the updater after a successful install; shows an in-app toast",
    )
    args = parser.parse_args()
    desktop = (args.desktop or args.background) and not args.no_desktop
    if args.no_browser and not args.background:
        desktop = False

    if args.home:
        os.environ["HEARTH_HOME"] = args.home

    url = f"http://127.0.0.1:{args.port}"
    if already_running(args.port):
        shown = request_show(args.port)
        if not args.no_browser and not args.background and not shown:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        else:
            print(f"Hearth is already running at {url}")
        return

    if not acquire_mutex() and already_running(args.port):
        request_show(args.port)
        return

    port = pick_port(args.port)
    store = Store(Path(args.home) if args.home else None)
    configure_logging(store.root)
    if args.updated:
        hearth_update.write_just_updated(store.root, str(args.updated))
    app = create_app(store, port)

    url = f"http://127.0.0.1:{port}"
    print(f"Hearth  ·  {store.device_name}")
    print(f"  Open   {url}")
    print(f"  Data   {store.root}")

    config = uvicorn.Config(app, host=args.host, port=port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)

    if desktop:
        from .desktop import run_desktop

        def register_show(show_cb) -> None:
            app.state.show_ui_callbacks.append(show_cb)

        thread = threading.Thread(target=server.run, name="hearth-http", daemon=True)
        thread.start()
        try:
            run_desktop(url, background=args.background, on_show=register_show)
        except Exception:
            if not args.background:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            server.run()
        return

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    server.run()


if __name__ == "__main__":
    main()
