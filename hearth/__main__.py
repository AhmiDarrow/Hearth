"""python -m hearth"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import time
import threading
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn
from .throttle import tune_tcp_socket

from .app import create_app
from .state import Store
from . import update as hearth_update
from . import __version__


def ensure_stdio() -> None:
    """Windowed (console=False) frozen apps start with stdout and stderr set to None.

    uvicorn's log formatter calls sys.stdout.isatty() while configuring, which
    would crash Hearth.exe at launch if we do not give it real file objects.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")



def _tuned_config(*args, **kwargs) -> uvicorn.Config:
    config = uvicorn.Config(*args, **kwargs)
    original = config.bind_socket

    def bind():
        sock = original()
        tune_tcp_socket(sock)
        return sock

    config.bind_socket = bind  # type: ignore[method-assign]
    return config


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


def _self_test_log(home: Path, lines: list[str]) -> None:
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / "self-test.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _self_test_files(url: str, home: Path, log) -> None:
    """Exercise share, upload, download, path safety, and CSRF origin checks."""
    import httpx

    shared = home / "share"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "hello.txt").write_text("hello hearth", encoding="utf-8")
    with httpx.Client(base_url=url, timeout=8.0) as client:
        spoof = client.get("/api/state", headers={"origin": "http://evil.example"})
        if spoof.status_code != 403:
            raise RuntimeError(f"CSRF origin was not rejected: {spoof.status_code}")
        log("origin check 403")

        created = client.post("/api/group/create", json={"name": "SelfTest"})
        if created.status_code != 200:
            raise RuntimeError(f"create group failed: {created.status_code} {created.text}")
        share = client.post(
            "/api/shares",
            json={"path": str(shared), "label": "SelfTest", "mode": "readwrite"},
        )
        if share.status_code != 200:
            raise RuntimeError(f"add share failed: {share.status_code} {share.text}")
        body = share.json()
        sid = body["shares"][0]["id"]
        did = body["device"]["id"]

        listing = client.get(f"/api/files/{did}/{sid}")
        if listing.status_code != 200:
            raise RuntimeError(f"list files failed: {listing.status_code} {listing.text}")
        names = {item["name"] for item in listing.json().get("items") or []}
        if "hello.txt" not in names:
            raise RuntimeError("share listing missed hello.txt")

        downloaded = client.get(f"/api/download/{did}/{sid}", params={"path": "hello.txt"})
        if downloaded.status_code != 200 or downloaded.content != b"hello hearth":
            raise RuntimeError("download mismatch")
        page = client.get("/")
        if page.status_code != 200 or 'id="menuBtn"' not in page.text or 'id="settings-updates"' not in page.text:
            raise RuntimeError("settings menu is missing from the UI")

        trav = client.get(f"/api/download/{did}/{sid}", params={"path": "../secret.txt"})
        if trav.status_code not in {403, 404}:
            raise RuntimeError(f"path traversal was not blocked: {trav.status_code}")
        win = client.get(f"/api/download/{did}/{sid}", params={"path": r"C:\\Windows\\win.ini"})
        if win.status_code not in {403, 404}:
            raise RuntimeError(f"absolute path was not blocked: {win.status_code}")

        up = client.post(
            f"/api/upload/{did}/{sid}",
            files={"file": ("café notes.txt", b"unicode-ok", "text/plain")},
        )
        if up.status_code != 200:
            raise RuntimeError(f"unicode upload failed: {up.status_code} {up.text}")
        evil = client.post(
            f"/api/upload/{did}/{sid}",
            files={"file": ("../escape.txt", b"nope", "text/plain")},
        )
        if evil.status_code != 200:
            raise RuntimeError(f"sanitized upload failed: {evil.status_code} {evil.text}")
        if (shared.parent / "escape.txt").exists():
            raise RuntimeError("upload escaped the shared folder")
        if not (shared / "escape.txt").is_file():
            raise RuntimeError("sanitized upload did not land in the share")
        log("file transfer ok")


def run_self_test(args: argparse.Namespace) -> int:
    """Start the HTTP server, hit a few local routes, then exit."""
    lines: list[str] = []

    def log(message: str) -> None:
        lines.append(message)
        print(message, flush=True)

    home = Path(args.home) if args.home else Path(tempfile.mkdtemp(prefix="hearth-self-test-"))
    os.environ["HEARTH_NO_UPDATE"] = "1"
    os.environ["HEARTH_HOME"] = str(home)
    log(f"self-test home={home}")
    try:
        from . import app as app_mod
        from . import crypto, desktop, discovery, files, netutil, state, update  # noqa: F401
        from .paths import static_ui_dir

        ui = static_ui_dir()
        index = ui / "index.html"
        if not index.is_file():
            raise FileNotFoundError(f"UI files missing at {ui}")
        log(f"ui ok: {index}")

        port = pick_port(args.port)
        store = state.Store(home)
        application = app_mod.create_app(store, port)
        config = _tuned_config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            loop="asyncio",
            use_colors=False,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="hearth-self-test", daemon=True)
        thread.start()

        url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        last_error = "server did not start"
        body = b""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{url}/api/state", timeout=1.5) as resp:
                    if resp.status != 200:
                        last_error = f"/api/state status {resp.status}"
                    else:
                        body = resp.read()
                        last_error = ""
                        break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.2)
        if last_error:
            raise RuntimeError(f"health check failed: {last_error}")
        data = json.loads(body.decode("utf-8"))
        if "version" not in data or "device" not in data:
            raise RuntimeError(f"unexpected /api/state payload: {sorted(data)}")
        log(f"GET /api/state 200 version={data.get('version')} port={port}")

        with urllib.request.urlopen(url + "/", timeout=3) as resp:
            page = resp.read().decode("utf-8", errors="replace")
        if "Hearth" not in page:
            raise RuntimeError("GET / did not return the Hearth UI")
        log("GET / 200")

        _self_test_files(url, home, log)

        server.should_exit = True
        thread.join(timeout=5)
        log("self-test passed")
        return 0
    except Exception as exc:
        log(f"self-test FAILED: {exc}")
        log(traceback.format_exc())
        return 1
    finally:
        _self_test_log(home, lines)


def main() -> None:
    default_desktop = is_frozen() or os.environ.get("HEARTH_DESKTOP") == "1"
    parser = argparse.ArgumentParser(description="Hearth — one simple home for sharing")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HEARTH_PORT", "7474")))
    parser.add_argument("--home", type=str, default=os.environ.get("HEARTH_HOME"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--desktop", action="store_true", default=default_desktop)
    parser.add_argument("--no-desktop", action="store_true")
    parser.add_argument("--background", action="store_true", help="Start in the tray without opening a window")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Import the app, serve /api/state for one request, and exit (used by CI)",
    )
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

    if args.self_test:
        raise SystemExit(run_self_test(args))

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

    config = _tuned_config(app, host=args.host, port=port, log_level="warning", loop="asyncio", use_colors=False)
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
