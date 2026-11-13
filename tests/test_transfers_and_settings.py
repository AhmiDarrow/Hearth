from __future__ import annotations

import os
from pathlib import Path

from hearth.prefs import content_disposition, unique_dest, user_facing_error
from hearth.startup import set_start_at_login, shortcut_path, start_at_login_enabled
from tests.conftest import local_client


def test_content_disposition_handles_unicode() -> None:
    header = content_disposition("café notes.txt")
    assert "filename=" in header
    assert "filename*=UTF-8''" in header
    header.encode("latin-1")  # must be safe for HTTP headers


def test_unique_dest_adds_number(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("a", encoding="utf-8")
    second = unique_dest(tmp_path, "hello.txt")
    assert second.name == "hello (1).txt"


def test_start_at_login_toggle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HEARTH_STARTUP_DIR", str(tmp_path))
    assert start_at_login_enabled() is False
    assert set_start_at_login(True, target=tmp_path / "Hearth.exe") is True
    assert shortcut_path() == tmp_path / "Hearth.lnk"
    assert shortcut_path().is_file()
    text = shortcut_path().read_text(encoding="utf-8")
    assert "Hearth.exe" in text
    assert set_start_at_login(False) is False
    assert not shortcut_path().exists()


def test_settings_snapshot_and_toggles(app, store, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HEARTH_STARTUP_DIR", str(tmp_path / "Startup"))
    client = local_client(app)
    state = client.get("/api/state").json()
    assert "settings" in state
    settings = state["settings"]
    assert settings["device_name"]
    assert "start_at_login" in settings
    assert "download_folder" in settings
    assert settings["auto_check_updates"] is True
    assert settings["cert_thumbprint"]
    assert settings["github"].endswith("Hearth")
    assert "menuBtn" not in settings  # just a sanity check that payload is settings

    folder = tmp_path / "Downloads"
    folder.mkdir()
    res = client.post(
        "/api/settings",
        json={
            "device_name": "Kitchen PC",
            "theme": "light",
            "start_at_login": True,
            "download_folder": str(folder),
            "auto_check_updates": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["device"]["name"] == "Kitchen PC"
    assert body["theme"] == "light"
    assert body["settings"]["start_at_login"] is True
    assert body["settings"]["download_folder"] == str(folder)
    assert body["settings"]["auto_check_updates"] is False
    assert (tmp_path / "Startup" / "Hearth.lnk").is_file()


def test_index_exposes_menu_and_settings_sections(app) -> None:
    client = local_client(app)
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert 'id="menuBtn"' in html
    assert 'id="settingsBtn"' in html
    assert 'id="appMenu"' in html
    assert 'data-menu="settings"' in html
    assert 'data-menu="updates"' in html
    assert 'data-menu="about"' in html
    assert 'data-menu="help"' in html
    assert 'data-menu="quit"' in html
    for section in ("general", "sharing", "transfers", "network", "updates", "about"):
        assert f'id="settings-{section}"' in html
    assert "Start Hearth when I sign in" in html
    assert "Certificate thumbprint" in html
    assert "Check now" in html
    assert "Install update" in html
    assert '"Hope this helps" - Ahmi' in html
    assert "Hearth is one simple home for sharing - local file sharing and PC-to-PC chat, with no cloud." in html
    assert "HomeGroup" not in html
    assert "Clear downloads" in (Path(__file__).resolve().parents[1] / "hearth/ui/static/app.js").read_text(encoding="utf-8")


def test_local_download_unicode_filename(app, store, tmp_path: Path) -> None:
    shared = tmp_path / "docs"
    shared.mkdir()
    name = "résumé.txt"
    (shared / name).write_text("ok", encoding="utf-8")
    client = local_client(app)
    created = client.post("/api/group/create", json={"name": "Home"})
    assert created.status_code == 200
    shared_res = client.post("/api/shares", json={"path": str(shared), "label": "Docs", "mode": "read"})
    assert shared_res.status_code == 200
    sid = shared_res.json()["shares"][0]["id"]
    did = shared_res.json()["device"]["id"]
    download = client.get(f"/api/download/{did}/{sid}", params={"path": name})
    assert download.status_code == 200, download.text
    assert download.content == b"ok"
    disposition = download.headers.get("content-disposition", "")
    assert "filename" in disposition.lower()
    disposition.encode("latin-1")


def test_download_unreadable_file_is_not_a_bare_500(app, store, tmp_path: Path) -> None:
    shared = tmp_path / "docs"
    shared.mkdir()
    target = shared / "locked.txt"
    target.write_text("secret", encoding="utf-8")
    client = local_client(app)
    client.post("/api/group/create", json={"name": "Home"})
    shared_res = client.post("/api/shares", json={"path": str(shared), "label": "Docs", "mode": "read"})
    sid = shared_res.json()["shares"][0]["id"]
    did = shared_res.json()["device"]["id"]

    # Make the file unreadable after it is shared.
    target.chmod(0)
    try:
        res = client.get(f"/api/download/{did}/{sid}", params={"path": "locked.txt"})
        # 403 is the intended case; never a Starlette HTML 500 with no detail.
        assert res.status_code in {403, 503, 502}
        detail = res.json().get("detail")
        assert detail
        assert "Internal Server Error" not in str(detail)
    finally:
        target.chmod(0o644)


def test_transfer_download_local_copy(app, store, tmp_path: Path) -> None:
    shared = tmp_path / "docs"
    dest = tmp_path / "inbox"
    shared.mkdir()
    dest.mkdir()
    (shared / "notes.txt").write_text("hello house", encoding="utf-8")
    client = local_client(app)
    client.post("/api/group/create", json={"name": "Home"})
    client.post("/api/settings", json={"download_folder": str(dest)})
    shared_res = client.post("/api/shares", json={"path": str(shared), "label": "Docs", "mode": "read"})
    sid = shared_res.json()["shares"][0]["id"]
    did = shared_res.json()["device"]["id"]
    res = client.post(
        "/api/transfers/download",
        json={"device_id": did, "share_id": sid, "path": "notes.txt"},
    )
    assert res.status_code == 200, res.text
    started = res.json()["started"]
    assert started
    # The copy runs on the event loop; poll briefly.
    import time

    deadline = time.time() + 3
    status = None
    while time.time() < deadline:
        listing = client.get("/api/transfers").json()["transfers"]
        job = next((j for j in listing if j["id"] == started[0]["id"]), None)
        if job and job["status"] in {"done", "error"}:
            status = job
            break
        time.sleep(0.05)
    assert status and status["status"] == "done", status
    saved = dest / "notes.txt"
    assert saved.read_text(encoding="utf-8") == "hello house"


def test_user_facing_error_is_short() -> None:
    msg = user_facing_error(PermissionError("denied"))
    assert "locked" in msg.lower() or "write" in msg.lower()


def test_patch_share_mode(app, tmp_path: Path) -> None:
    folder = tmp_path / "pics"
    folder.mkdir()
    client = local_client(app)
    client.post("/api/group/create", json={"name": "Home"})
    added = client.post("/api/shares", json={"path": str(folder), "label": "Pics", "mode": "read"})
    sid = added.json()["shares"][0]["id"]
    patched = client.patch(f"/api/shares/{sid}", json={"mode": "readwrite"})
    assert patched.status_code == 200, patched.text
    share = next(s for s in patched.json()["shares"] if s["id"] == sid)
    assert share["mode"] == "readwrite"
