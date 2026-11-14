#!/usr/bin/env python3
"""Render Hearth UI screenshots for each theme and a folder-browsing flow."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("HEARTH_NO_UPDATE", "1")

from hearth.app import create_app
from hearth.files import default_share_label
from hearth.state import Store

THEMES = (
    "light", "dark", "neutral", "forest", "green", "purple",
    "alien", "orange", "cyan", "deutan", "protan", "tritan", "system",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--out", default="/workspace/hearth-screens", type=Path)
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    root = Path(os.environ.get("HEARTH_SCREEN_TMP") or "/tmp/hearth-screens-data")
    root = root / f"run-{int(time.time())}"
    store_dir = root / "store"
    share = root / "Library"
    nested = share / "Photos" / "2026"
    nested.mkdir(parents=True)
    (share / "readme.txt").write_text("hello from Hearth", encoding="utf-8")
    (nested / "beach.txt").write_text("sand", encoding="utf-8")

    store = Store(store_dir)
    store.device_name = "Studio PC"
    app = create_app(store, 17474)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 8
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        print("server failed to start", file=sys.stderr)
        return 1

    import httpx

    base = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=base, timeout=10.0) as client:
        created = client.post(
            "/api/group/create",
            json={"name": "Home", "password": "secret", "device_name": "Studio PC"},
        )
        created.raise_for_status()
        shared = client.post(
            "/api/shares",
            json={"path": str(share), "label": "Library", "mode": "readwrite"},
        )
        shared.raise_for_status()
        store.data.setdefault("shares", []).extend(
            [
                {"id": "drivec", "path": "C:\\", "label": "", "mode": "read"},
                {"id": "drived", "path": "D:\\", "label": "", "mode": "read"},
            ]
        )
        store.save()
        state = client.get("/api/state").json()
        labels = [s["label"] for s in state["shares"]]
        print("share labels:", labels)
        assert "Library" in labels
        assert any(default_share_label(s["path"], s["label"]) in {"C:", "Windows (C:)"} or s["label"] == "C:" for s in state["shares"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_selector("#shareTabs .share-tab, #myShares .share-card", timeout=8000)

        def snap(name: str) -> None:
            dest = out / f"{name}.png"
            page.screenshot(path=str(dest), full_page=False)
            print("wrote", dest)

        tab_text = page.locator("#shareTabs .share-tab").all_text_contents()
        print("visible share tabs:", tab_text)
        if not any(t.strip() for t in tab_text):
            raise SystemExit("share tabs rendered without labels")

        for theme in THEMES:
            page.evaluate(
                """(theme) => {
                    document.documentElement.dataset.theme = theme;
                    document.documentElement.dataset.scheme = theme === "light" ? "light" : (theme === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : "dark");
                }""",
                theme,
            )
            page.wait_for_timeout(80)
            snap(f"theme-{theme}-files")

        page.evaluate(
            """() => {
                document.documentElement.dataset.theme = "light";
                document.documentElement.dataset.scheme = "light";
            }"""
        )
        library = page.locator("#shareTabs .share-tab", has_text="Library")
        if library.count():
            library.first.click()
            page.wait_for_timeout(200)
        photos = page.locator(".file-tile, .file-row", has_text="Photos")
        photos.first.click()
        page.wait_for_selector("#crumb button", timeout=4000)
        page.wait_for_timeout(200)
        crumb = page.locator("#crumb").inner_text()
        print("crumb after click:", crumb)
        if "Photos" not in crumb:
            raise SystemExit(f"expected Photos in breadcrumb, got {crumb!r}")
        snap("browse-photos")
        year = page.locator(".file-tile, .file-row", has_text="2026")
        year.first.click()
        page.wait_for_timeout(250)
        snap("browse-photos-2026")
        if page.locator("#navUp").is_enabled():
            page.locator("#navUp").click()
            page.wait_for_timeout(200)
            snap("browse-up-to-photos")

        if page.locator("#settingsBtn").count():
            page.locator("#settingsBtn").click()
            about = page.locator("#settingsNav [data-settings='about']")
            if about.count():
                about.first.click()
                page.wait_for_timeout(150)
            snap("settings-about")

        browser.close()

    server.should_exit = True
    thread.join(timeout=3)
    print("done, screenshots in", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
