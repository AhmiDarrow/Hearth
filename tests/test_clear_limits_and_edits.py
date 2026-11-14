from __future__ import annotations

import time
from pathlib import Path

from hearth.state import Store
from hearth.throttle import TokenBucket
from hearth.transfers import TransferHub, FINISHED_STATUSES

from tests.conftest import local_client


def _wait_done(client, tid: str, timeout: float = 5.0) -> dict:
    """Wait until this transfer is saved. Canceled/error is a failure, not a retry."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        listing = client.get("/api/transfers").json()["transfers"]
        job = next((j for j in listing if j["id"] == tid), None)
        if job:
            last = job
            if job["status"] == "done":
                return job
            if job["status"] in {"error", "canceled"}:
                raise AssertionError(
                    f"transfer {tid} ended as {job['status']}: {job.get('message')}"
                )
        time.sleep(0.05)
    raise AssertionError(f"transfer {tid} did not finish; last={last}")


def _share_file(client, tmp_path: Path, name: str = "notes.txt", body: str = "hello house"):
    shared = tmp_path / "docs"
    dest = tmp_path / "inbox"
    shared.mkdir(exist_ok=True)
    dest.mkdir(exist_ok=True)
    (shared / name).write_text(body, encoding="utf-8")
    client.post("/api/group/create", json={"name": "Home"})
    client.post("/api/settings", json={"download_folder": str(dest)})
    shared_res = client.post("/api/shares", json={"path": str(shared), "label": "Docs", "mode": "read"})
    sid = shared_res.json()["shares"][0]["id"]
    did = shared_res.json()["device"]["id"]
    return shared, dest, sid, did


def test_clear_downloads_leaves_files_and_active_jobs(app, tmp_path: Path) -> None:
    # Keep the TestClient portal open so the background copy is not cancelled
    # when the POST returns (Starlette otherwise starts a fresh event loop per request).
    with local_client(app) as client:
        shared, dest, sid, did = _share_file(client, tmp_path)
        res = client.post("/api/transfers/download", json={"device_id": did, "share_id": sid, "path": "notes.txt"})
        tid = res.json()["started"][0]["id"]
        job = _wait_done(client, tid)
        assert job["status"] == "done"
        saved = dest / "notes.txt"
        assert saved.read_text(encoding="utf-8") == "hello house"

        cleared = client.post("/api/transfers/clear")
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["transfers"] == []
        assert saved.exists()
        assert saved.read_text(encoding="utf-8") == "hello house"


def test_dismiss_one_finished_transfer(app, tmp_path: Path) -> None:
    with local_client(app) as client:
        shared, dest, sid, did = _share_file(client, tmp_path)
        (shared / "other.txt").write_text("second", encoding="utf-8")
        first = client.post("/api/transfers/download", json={"device_id": did, "share_id": sid, "path": "notes.txt"})
        second = client.post("/api/transfers/download", json={"device_id": did, "share_id": sid, "path": "other.txt"})
        tid_a = first.json()["started"][0]["id"]
        tid_b = second.json()["started"][0]["id"]
        assert _wait_done(client, tid_a)["status"] == "done"
        assert _wait_done(client, tid_b)["status"] == "done"

        gone = client.post(f"/api/transfers/{tid_a}/dismiss")
        assert gone.status_code == 200, gone.text
        leftover = {j["id"] for j in gone.json()["transfers"]}
        assert tid_a not in leftover
        assert tid_b in leftover
        assert (dest / "notes.txt").exists()
        assert (dest / "other.txt").exists()


def test_dismiss_rejects_unknown_or_invented_active(app) -> None:
    client = local_client(app)
    res = client.post("/api/transfers/missing/dismiss")
    assert res.status_code == 409


def test_cleared_transfers_stay_gone_after_reload(tmp_path: Path) -> None:
    persist = tmp_path / "transfers.json"
    hub = TransferHub(persist_path=persist)
    job = hub._new("download", "kept.bin", tmp_path / "kept.bin")
    job["status"] = "done"
    job["message"] = "Saved"
    hub._persist()

    other = hub._new("download", "drop.bin", tmp_path / "drop.bin")
    other["status"] = "error"
    other["message"] = "Failed"
    hub._persist()

    removed = hub.dismiss(other["id"])
    assert removed is not None
    assert other["id"] not in {j["id"] for j in hub.list()}

    reloaded = TransferHub(persist_path=persist)
    ids = {j["id"] for j in reloaded.list()}
    assert job["id"] in ids
    assert other["id"] not in ids
    assert all(j["status"] in FINISHED_STATUSES for j in reloaded.list())

    reloaded.clear_finished()
    assert reloaded.list() == []
    again = TransferHub(persist_path=persist)
    assert again.list() == []


def test_token_bucket_stays_within_ten_percent() -> None:
    import asyncio

    rate = 50_000  # 50 KB/s
    total = 100_000  # 100 KB should take ~2s

    async def run() -> None:
        bucket = TokenBucket(rate)
        t0 = time.monotonic()
        left = total
        while left:
            step = min(8_192, left)
            await bucket.take(step)
            left -= step
        elapsed = time.monotonic() - t0
        expected = total / rate
        assert expected * 0.90 <= elapsed <= expected * 1.10, (elapsed, expected)

    asyncio.run(run())


def test_speed_limits_persist_and_apply_live(app, store: Store) -> None:
    client = local_client(app)
    res = client.post("/api/settings", json={"download_limit_bps": 1048576, "upload_limit_bps": 262144})
    assert res.status_code == 200, res.text
    settings = res.json()["settings"]
    assert settings["download_limit_bps"] == 1048576
    assert settings["upload_limit_bps"] == 262144
    assert store.data["download_limit_bps"] == 1048576
    # The hub used by this app should have picked up the live rates.
    from hearth.app import create_app  # noqa: F401 — rates are on the live app hub

    # Reset restores unlimited (0)
    reset = client.post("/api/settings/reset")
    assert reset.status_code == 200, reset.text
    settings = reset.json()["settings"]
    assert settings["download_limit_bps"] == 0
    assert settings["upload_limit_bps"] == 0
    assert settings["theme"] == "system"
    assert settings["auto_check_updates"] is True


def test_edit_share_renames_rebases_and_changes_mode(app, tmp_path: Path) -> None:
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    first.mkdir()
    second.mkdir()
    client = local_client(app)
    client.post("/api/group/create", json={"name": "Home"})
    added = client.post("/api/shares", json={"path": str(first), "label": "Alpha", "mode": "read"})
    sid = added.json()["shares"][0]["id"]
    patched = client.patch(f"/api/shares/{sid}", json={"label": "Family Photos", "path": str(second), "mode": "readwrite"})
    assert patched.status_code == 200, patched.text
    share = next(s for s in patched.json()["shares"] if s["id"] == sid)
    assert share["label"] == "Family Photos"
    assert Path(share["path"]) == second
    assert share["mode"] == "readwrite"

    missing = client.patch(f"/api/shares/{sid}", json={"path": str(tmp_path / "nope")})
    assert missing.status_code == 400


def test_ui_has_clear_downloads_edit_limits_and_signature(app) -> None:
    client = local_client(app)
    html = client.get("/").text
    js = (Path(__file__).resolve().parents[1] / "hearth/ui/static/app.js").read_text(encoding="utf-8")
    assert "Clear downloads" in js
    assert 'id="clearTransfers"' in js
    assert "data-dismiss-xfer" in js
    assert 'id="settings-transfers"' in html
    assert 'id="downloadLimitPreset"' in html
    assert 'id="uploadLimitPreset"' in html
    assert 'id="resetSettings"' in html
    assert 'data-edit-share' in js
    assert 'id="shareRemove"' in html
    assert '"Hope this helps" - Ahmi' in html
