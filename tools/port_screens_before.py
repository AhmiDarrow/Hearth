#!/usr/bin/env python3
"""Screenshots of the shipped Python Hearth (0.4) for the RemedyUI port's before/after
evidence: every screen, light and dark, wide and narrow, English and German.

    python tools/port_screens_before.py -o port-evidence/before

Runs the real app (its HTTP server and web page) on a temporary profile with made-up names,
in headless Chrome. Never touches a real profile.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["HEARTH_NO_UPDATE"] = "1"

import uvicorn  # noqa: E402

from hearth.state import Store  # noqa: E402


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", type=Path, required=True)
    out: Path = ap.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    real_env = dict(os.environ)
    root = Path(tempfile.mkdtemp(prefix="hearth-before-"))
    os.environ["HEARTH_HOME"] = str(root / "profile")
    # A made-up user: the libraries and paths shown are the temporary ones.
    user = root / "user"
    for lib in ("Documents", "Pictures", "Downloads", "Desktop", "Videos", "Music"):
        (user / lib).mkdir(parents=True)
    os.environ["USERPROFILE"] = os.environ["HOME"] = str(user)
    for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        os.environ.pop(k, None)
    library = root / "Library"
    (library / "Photos" / "2026").mkdir(parents=True)
    (library / "Music").mkdir()
    (library / "readme.txt").write_text("hello from Hearth", encoding="utf-8")
    (library / "Holiday plan.pdf").write_bytes(b"x" * 48_000)
    (library / "Photos" / "beach.jpg").write_bytes(b"x" * 2_400_000)

    import hearth.app as hearth_app
    from hearth.app import create_app

    # A documentation address, not this PC's.
    hearth_app.local_ipv4s = lambda: ["192.168.1.20"]

    store = Store(root / "profile")
    store.device_name = "Studio PC"
    port = free_port()
    app = create_app(store, port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    import httpx
    from playwright.sync_api import sync_playwright

    api = httpx.Client(base_url=base, timeout=10.0)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"], env=real_env)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def snap(name: str) -> None:
            page.wait_for_timeout(250)
            page.screenshot(path=str(out / f"{name}.png"))
            print("wrote", name)

        def theme(t: str) -> None:
            api.post("/api/theme", json={"theme": t})
            page.evaluate("t => { localStorage.setItem('hearth-theme', t); }", t)
            page.reload(wait_until="networkidle")

        page.goto(base + "/", wait_until="networkidle")
        theme("dark")
        snap("01-gate-dark")
        api.post("/api/group/create", json={"name": "Home", "password": "kettle on", "device_name": "Studio PC"}).raise_for_status()
        api.post("/api/shares", json={"path": str(library), "label": "Library", "mode": "readwrite"}).raise_for_status()
        api.post("/api/chat", json={"text": "Dinner at seven?"})
        api.post("/api/chat", json={"text": "I'll bring the photos from the trip."})
        page.reload(wait_until="networkidle")
        page.locator("#myShares .share-card .meta b").first.click()
        page.wait_for_selector(".file-tile, .file-row", timeout=8000)
        snap("02-files-grid-dark")
        page.locator("#viewList").click()
        snap("03-files-list-dark")
        page.locator("#viewGrid").click()
        page.locator("[data-view='chat']").click()
        snap("04-chat-dark")
        page.locator("[data-view='files']").click()
        page.locator("#settingsBtn").click()
        for i, sec in enumerate(["general", "sharing", "transfers", "network", "updates", "about"], 5):
            page.locator(f"#settingsNav [data-settings='{sec}']").click()
            snap(f"{i:02d}-settings-{sec}-dark")
        page.keyboard.press("Escape")
        page.locator("#settingsClose").click() if page.locator("#settingsClose").is_visible() else None
        page.locator("#shareFolder").click()
        page.wait_for_timeout(600)
        snap("11-share-dialog-dark")
        page.keyboard.press("Escape")

        theme("light")
        page.locator("#myShares .share-card .meta b").first.click()
        page.wait_for_selector(".file-tile, .file-row", timeout=8000)
        snap("12-files-grid-light")
        page.locator("#settingsBtn").click()
        snap("13-settings-general-light")

        page.set_viewport_size({"width": 760, "height": 640})
        page.reload(wait_until="networkidle")
        None
        snap("14-narrow-light")

        page.set_viewport_size({"width": 1280, "height": 800})
        api.post("/api/settings", json={"language": "de"})
        theme("dark")
        page.locator("#myShares .share-card .meta b").first.click()
        page.wait_for_selector(".file-tile, .file-row", timeout=8000)
        snap("15-files-german-dark")
        page.locator("#settingsBtn").click()
        page.locator("#settingsNav [data-settings='network']").click()
        snap("16-settings-network-german-dark")
        browser.close()
    server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
