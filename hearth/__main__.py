"""python -m hearth"""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app
from .state import Store


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Hearth — HomeGroup for the whole house")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HEARTH_PORT", "7474")))
    parser.add_argument("--home", type=str, default=os.environ.get("HEARTH_HOME"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    if args.home:
        os.environ["HEARTH_HOME"] = args.home

    url = f"http://127.0.0.1:{args.port}"
    if already_running(args.port):
        print(f"Hearth is already running at {url}")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return

    port = pick_port(args.port)
    store = Store(Path(args.home) if args.home else None)
    app = create_app(store, port)

    url = f"http://127.0.0.1:{port}"
    print(f"Hearth  ·  {store.device_name}")
    print(f"  Open   {url}")
    print(f"  Data   {store.root}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
