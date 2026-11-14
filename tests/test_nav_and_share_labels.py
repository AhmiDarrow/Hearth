"""Drive labels, stacked folder clicks, and Windows path shapes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hearth.files import (
    DRIVE_CDROM,
    DRIVE_FIXED,
    DRIVE_NO_ROOT,
    DRIVE_RAMDISK,
    DRIVE_REMOTE,
    DRIVE_UNKNOWN,
    canonical_stored_path,
    default_share_label,
    display_share,
    iter_mounted_drives,
    list_dir,
    resolve_user_path,
)
from hearth.state import Store
from tests.conftest import local_client

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "hearth/ui/static/app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "hearth/ui/static/app.css").read_text(encoding="utf-8")
CREATE_NO_WINDOW = 0x08000000

_NAV_CHECKS = r"""
const empty = { path: "", loading: false, token: 0, committedPath: "" };
let step = beginFolderNav(empty, "Program Files");
assert.equal(step.path, "Program Files");
assert.equal(step.loading, true);
assert.equal(step.ignored, false);
const second = beginFolderNav(step, "Program Files");
assert.equal(second.ignored, true);
assert.equal(second.path, "Program Files");
const third = beginFolderNav(second, "Program Files");
assert.equal(third.ignored, true);
assert.equal(third.path, "Program Files");
const stale = applyFolderResult(step, step.token + 9, true, "");
assert.equal(stale.path, "Program Files");
assert.equal(stale.loading, true);
const done = applyFolderResult(step, step.token, true, "");
assert.equal(done.committedPath, "Program Files");
assert.equal(done.loading, false);
const nested = beginFolderNav(done, "Program Files");
assert.equal(nested.ignored, false);
assert.equal(nested.path, "Program Files/Program Files");
const failed = applyFolderResult(nested, nested.token, false, "Folder does not exist");
assert.equal(failed.path, "Program Files");
assert.equal(failed.error, "Folder does not exist");
assert.equal(failed.loading, false);
const jumped = beginPathNav({ path: "Program Files", loading: true, token: 1, committedPath: "" }, "Notes");
assert.equal(jumped.path, "Notes");
assert.equal(jumped.token, 2);
assert.equal(jumped.ignored, false);
assert.equal(shareLabel({ label: "Shared folder", path: "C:\\" }), "Local Disk (C:)");
assert.equal(shareLabel({ label: "Windows (C:)" }), "Windows (C:)");
assert.equal(shareLabel({ label: "Shared folder", path: "D:\\Games" }), "Games");
assert.equal(shareLabel({ label: "Shared folder" }), "Shared folder");
assert.equal(shareLabel({ label: "", path: "\\\\nas\\games" }), "nas:games");
assert.equal(shareLabel({ label: "Folder" }), "Folder");
"""


def test_nav_source_guards_repeated_clicks() -> None:
    assert "function beginFolderNav(" in APP_JS
    assert "new AbortController(" in APP_JS
    assert "filesLoading" in APP_JS
    assert "if (step.ignored) return;" in APP_JS
    assert "if (filesLoading) return;" in APP_JS
    assert "if (token !== pickerToken) return;" in APP_JS
    assert 'joinParts(path, file.hearthDir || "")' in APP_JS
    handler = APP_JS.split('$("files").addEventListener("dblclick"')[1].split("addEventListener")[0]
    assert "if (!item || item.dir || filesLoading) return;" in handler
    assert handler.index("item.dir") < handler.index("openItem(item)")
    assert "#files.loading" in APP_CSS
    assert "pointer-events: none" in APP_CSS


def test_nav_helpers_ignore_clicks_while_loading(tmp_path: Path) -> None:
    node = shutil.which("node") or shutil.which("nodejs")
    start = APP_JS.index("// BEGIN HEARTH_NAV")
    end = APP_JS.index("// END HEARTH_NAV")
    assert start < end
    assert "function beginFolderNav(" in APP_JS[start:end]
    assert "function shareLabel(" in APP_JS[start:end]
    if not node:
        return
    script = tmp_path / "hearth-nav.mjs"
    script.write_text(
        'import assert from "node:assert/strict";\n' + APP_JS[start:end] + "\n" + _NAV_CHECKS,
        encoding="utf-8",
    )
    flags = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, **flags)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_canonical_drive_and_unc_paths() -> None:
    assert canonical_stored_path("C:") == "C:\\"
    assert canonical_stored_path("c:/") == "C:\\"
    assert canonical_stored_path("C:\\") == "C:\\"
    assert canonical_stored_path("C:foo") == "C:\\foo"
    assert canonical_stored_path("//nas/public/files") == "\\\\nas\\public\\files"
    assert canonical_stored_path("\\\\nas\\public") == "\\\\nas\\public"


def test_resolve_user_path_drive_root_is_not_cwd(tmp_path: Path) -> None:
    previous = os.getcwd()
    try:
        os.chdir(tmp_path)
        resolved = resolve_user_path("C:")
    finally:
        os.chdir(previous)
    assert str(resolved) == "C:\\"
    assert tmp_path.name not in str(resolved)


def test_drive_kind_labels_and_custom_names() -> None:
    assert default_share_label("Z:", "", volume="", drive_type=DRIVE_REMOTE, unc_target=r"\\nas\games") == "games (Z:)"
    assert default_share_label("Z:", "", volume="Games", drive_type=DRIVE_REMOTE, unc_target=r"\\nas\games") == "Games (Z:)"
    assert default_share_label("E:", "", volume="", drive_type=2) == "Removable Disk (E:)"
    assert default_share_label("D:", "", volume="", drive_type=DRIVE_CDROM) == "CD Drive (D:)"
    assert default_share_label("R:", "", volume="", drive_type=DRIVE_RAMDISK) == "RAM Disk (R:)"
    assert default_share_label("C:\\", "OS Disk", volume="Windows", drive_type=DRIVE_FIXED) == "OS Disk"
    assert default_share_label("C:\\", "Shared folder", volume="Windows", drive_type=DRIVE_FIXED) == "Windows (C:)"
    assert default_share_label("/home/joe/Photos", "Shared folder") == "Photos"
    hidden = display_share(
        {"id": "1", "path": "C:\\", "label": "Shared folder", "mode": "read"},
        include_path=False,
    )
    # display_share asks drive_facts when no volume override is passed. Force the fallback.
    assert "path" not in hidden


def test_display_share_hides_path_and_repairs_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hearth.files.drive_facts", lambda path: (DRIVE_UNKNOWN, "", ""))
    hidden = display_share({"id": "1", "path": "C:\\", "label": "Shared folder", "mode": "read"}, include_path=False)
    assert hidden == {"id": "1", "label": "Local Disk (C:)", "mode": "read"}
    blank = display_share({"id": "2", "path": "C:\\", "label": "", "mode": "read"}, include_path=False)
    assert blank["label"] == "Local Disk (C:)"
    assert "path" not in blank


def test_iter_mounted_drives_skips_missing_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    def facts(path: str) -> tuple[int, str, str]:
        if str(path).upper().startswith("C"):
            return (DRIVE_FIXED, "Windows", "")
        return (DRIVE_NO_ROOT, "", "")

    monkeypatch.setattr("hearth.files.drive_facts", facts)
    seen: list[str] = []
    real_exists = Path.exists

    def spy(self: Path, *args: object, **kwargs: object) -> bool:
        seen.append(str(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", spy)
    mounted = iter_mounted_drives(["C:\\", "Z:\\"])
    assert len(mounted) == 1
    root, kind, volume, unc = mounted[0]
    assert root.upper().startswith("C")
    assert default_share_label(root, "", volume=volume, drive_type=kind, unc_target=unc) == "Windows (C:)"
    assert not any(item.upper().startswith("Z") for item in seen)


@pytest.mark.skipif(os.name != "nt", reason="Win32 drive type")
def test_missing_drive_does_not_query_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    import hearth.files as files

    files._DRIVE_FACT_CACHE.clear()
    monkeypatch.setattr(files, "lookup_drive_type", lambda root: DRIVE_NO_ROOT)

    def boom(root: str) -> str:
        raise AssertionError(f"volume queried for {root}")

    monkeypatch.setattr(files, "lookup_volume", boom)
    assert default_share_label("Z:", "") == "Local Disk (Z:)"
    files._DRIVE_FACT_CACHE.clear()


def test_store_repairs_placeholder_and_keeps_custom_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hearth.files.drive_facts", lambda path: (DRIVE_UNKNOWN, "", ""))
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    stored = canonical_stored_path(str(downloads))
    root = tmp_path / "hearth-data"
    root.mkdir()
    payload = {
        "group": None,
        "shares": [
            {"id": "drv", "path": "C:", "label": "Shared folder", "mode": "read"},
            {"id": "dl", "path": stored, "label": "Downloads", "mode": "read"},
        ],
    }
    (root / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    store = Store(root)
    by_id = {share["id"]: share for share in store.data["shares"]}
    assert by_id["drv"]["path"] == "C:\\"
    assert by_id["drv"]["label"] == "Local Disk (C:)"
    assert by_id["dl"]["path"] == stored
    assert by_id["dl"]["label"] == "Downloads"


def test_store_does_not_rewrite_a_canonical_custom_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hearth.files.drive_facts", lambda path: (DRIVE_UNKNOWN, "", ""))
    folder = tmp_path / "Downloads"
    folder.mkdir()
    stored = canonical_stored_path(str(folder))
    root = tmp_path / "data"
    root.mkdir()
    payload = {
        "device_name": "PC",
        "theme": "system",
        "group": None,
        "known_peers": {},
        "shares": [{"id": "dl", "path": stored, "label": "Downloads", "mode": "read", "added": 1}],
        "chat": [],
        "bridge": False,
        "manual_hosts": [],
        "auto_check_updates": True,
        "download_folder": "",
        "download_limit_bps": 0,
        "upload_limit_bps": 0,
        "last_update_check": 0.0,
    }
    path = root / "state.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    before = path.read_bytes()
    Store(root)
    assert path.read_bytes() == before


def test_add_share_stores_drive_root_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hearth.files.drive_facts", lambda path: (DRIVE_FIXED, "", ""))
    store = Store(tmp_path / "data")
    previous = os.getcwd()
    try:
        os.chdir(tmp_path)
        share = store.add_share("C:", "Shared folder")
    finally:
        os.chdir(previous)
    assert share["path"] == "C:\\"
    assert share["label"] == "Local Disk (C:)"


def test_validate_drive_letter_uses_volume_name(app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hearth.files.drive_facts", lambda path: (DRIVE_FIXED, "Windows", ""))
    client = local_client(app)
    res = client.post("/api/path/validate", json={"path": "C:"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["path"] == "C:\\"
    assert body["name"] == "Windows (C:)"


def test_placeholder_label_becomes_folder_name(app, tmp_path: Path) -> None:
    client = local_client(app)
    folder = tmp_path / "Photos"
    folder.mkdir()
    added = client.post("/api/shares", json={"path": str(folder), "label": "Shared folder", "mode": "read"})
    assert added.status_code == 200, added.text
    share = added.json()["shares"][0]
    assert share["label"] == "Photos"
    assert Path(share["path"]) == folder


def test_mode_only_patch_keeps_custom_label(app, tmp_path: Path) -> None:
    client = local_client(app)
    folder = tmp_path / "Docs"
    folder.mkdir()
    added = client.post("/api/shares", json={"path": str(folder), "label": "Family Photos", "mode": "read"})
    assert added.status_code == 200, added.text
    share = added.json()["shares"][0]
    patched = client.patch(f"/api/shares/{share['id']}", json={"mode": "readwrite"})
    assert patched.status_code == 200, patched.text
    updated = next(item for item in patched.json()["shares"] if item["id"] == share["id"])
    assert updated["label"] == "Family Photos"
    assert updated["mode"] == "readwrite"
    cleared = client.patch(f"/api/shares/{share['id']}", json={"label": "   "})
    assert cleared.status_code == 200, cleared.text
    renamed = next(item for item in cleared.json()["shares"] if item["id"] == share["id"])
    assert renamed["label"] == "Docs"


def test_missing_stacked_path_is_404_and_empty_dir_is_empty(app, tmp_path: Path) -> None:
    client = local_client(app)
    root = tmp_path / "Library"
    nested = root / "Program Files" / "My Photos" / "写真"
    nested.mkdir(parents=True)
    (nested / "note.txt").write_text("hi", encoding="utf-8")
    real = root / "Program Files" / "Program Files"
    real.mkdir()
    (real / "real.txt").write_text("ok", encoding="utf-8")
    (root / "empty").mkdir()
    added = client.post("/api/shares", json={"path": str(root), "label": "Library", "mode": "read"})
    assert added.status_code == 200, added.text
    share = next(item for item in added.json()["shares"] if item["label"] == "Library")
    device = added.json()["device"]["id"]

    missing = client.get(
        f"/api/files/{device}/{share['id']}",
        params={"path": "Program Files/Program Files/Program Files"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Folder does not exist"

    empty = client.get(f"/api/files/{device}/{share['id']}", params={"path": "empty"})
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []

    deep = client.get(
        f"/api/files/{device}/{share['id']}",
        params={"path": "Program Files/My Photos/写真"},
    )
    assert deep.status_code == 200, deep.text
    assert {item["name"] for item in deep.json()["items"]} == {"note.txt"}

    twice = client.get(
        f"/api/files/{device}/{share['id']}",
        params={"path": "Program Files/Program Files"},
    )
    assert twice.status_code == 200, twice.text
    assert {item["name"] for item in twice.json()["items"]} == {"real.txt"}


def test_unreadable_listing_is_403(app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = local_client(app)
    folder = tmp_path / "locked"
    folder.mkdir()
    added = client.post("/api/shares", json={"path": str(folder), "label": "Locked", "mode": "read"})
    assert added.status_code == 200, added.text
    share = added.json()["shares"][0]
    device = added.json()["device"]["id"]

    def boom(path: Path) -> list:
        raise PermissionError("That folder cannot be browsed")

    monkeypatch.setattr("hearth.app.list_dir", boom)
    res = client.get(f"/api/files/{device}/{share['id']}")
    assert res.status_code == 403
    assert res.json()["detail"] == "That folder cannot be browsed"


def test_list_dir_turns_iterdir_errors_into_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()

    def boom(self: Path):
        raise OSError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)
    with pytest.raises(PermissionError, match="cannot be browsed"):
        list_dir(folder)


def test_list_dir_times_out_when_iterdir_hangs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    folder = tmp_path / "stuck"
    folder.mkdir()
    monkeypatch.setenv("HEARTH_LIST_TIMEOUT", "0.2")

    def hang(self: Path):
        time.sleep(5)
        return []

    monkeypatch.setattr(Path, "iterdir", hang)
    with pytest.raises(TimeoutError, match="That folder is not responding"):
        list_dir(folder)


def test_items_or_http_maps_timeout_to_504(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from hearth.app import items_or_http

    def stuck(_path: Path) -> list:
        raise TimeoutError("That folder is not responding")

    monkeypatch.setattr("hearth.app.list_dir", stuck)
    with pytest.raises(HTTPException) as caught:
        items_or_http(tmp_path)
    assert caught.value.status_code == 504
    assert caught.value.detail == "That folder is not responding"


def test_local_folders_timeout_is_504(app, monkeypatch: pytest.MonkeyPatch) -> None:
    def stuck(_path: str = "") -> dict:
        raise TimeoutError("That folder is not responding")

    monkeypatch.setattr("hearth.app.list_local_dirs", stuck)
    res = local_client(app).get("/api/local-folders")
    assert res.status_code == 504
    assert res.json()["detail"] == "That folder is not responding"
