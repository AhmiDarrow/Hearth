from __future__ import annotations

import hashlib
import json
from pathlib import Path

import asyncio

import httpx
import pytest

from hearth import update
from hearth.app import create_app
from hearth.state import Store
from hearth.update import Version, parse_sha256sums, pick_latest_release, verify_sha256


RELEASES = [
    {
        "tag_name": "v0.1.0",
        "draft": False,
        "prerelease": False,
        "html_url": "https://github.com/AhmiDarrow/Hearth/releases/tag/v0.1.0",
        "assets": [{"name": "HearthSetup.exe", "browser_download_url": "https://example.test/v0.1.0/HearthSetup.exe"}],
    },
    {
        "tag_name": "v0.3.0-beta",
        "draft": False,
        "prerelease": True,
        "html_url": "https://github.com/AhmiDarrow/Hearth/releases/tag/v0.3.0-beta",
        "assets": [
            {"name": "HearthSetup.exe", "browser_download_url": "https://example.test/v0.3.0-beta/HearthSetup.exe"},
            {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.test/v0.3.0-beta/SHA256SUMS.txt"},
        ],
    },
    {
        "tag_name": "v0.2.5-draft",
        "draft": True,
        "prerelease": True,
        "assets": [{"name": "HearthSetup.exe", "browser_download_url": "https://example.test/draft/HearthSetup.exe"}],
    },
    {
        "tag_name": "not-a-version",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": "HearthSetup.exe", "browser_download_url": "https://example.test/skip/HearthSetup.exe"}],
    },
]


def test_version_order_treats_final_as_newer_than_beta() -> None:
    assert Version.parse("0.2.0") < Version.parse("0.3.0-beta")
    assert Version.parse("v0.2.0-beta") < Version.parse("0.2.0")
    assert Version.parse("0.3.0-beta") < Version.parse("0.3.0-beta.1")
    assert Version.parse("0.3.0-beta") < Version.parse("0.3.0")
    assert Version.parse("0.3.0") < Version.parse("0.3.1")
    assert Version.parse("0.3.1") < Version.parse("0.3.3")
    assert Version.parse("0.3.2") < Version.parse("0.3.3")
    assert Version.parse("v0.3.0-beta") == Version.parse("0.3.0-beta")
    assert not (Version.parse("0.3.0-beta") < Version.parse("0.3.0-beta"))
    assert Version.parse("0.9.0") < Version.parse("0.10.0")


def test_pick_latest_skips_drafts_and_includes_prereleases() -> None:
    latest = pick_latest_release(RELEASES, "0.2.0-beta")
    assert latest is not None
    assert latest["version"] == "0.3.0-beta"
    assert latest["prerelease"] is True
    assert latest["setup_url"].endswith("HearthSetup.exe")
    assert latest["sums_url"].endswith("SHA256SUMS.txt")
    assert pick_latest_release(RELEASES, "0.3.0-beta") is None
    assert pick_latest_release(RELEASES, "0.3.0") is None


def test_stable_release_is_newer_than_matching_beta() -> None:
    """v0.3.0-beta installs must see a later v0.3.0 stable release as an update."""
    releases = RELEASES + [
        {
            "tag_name": "v0.3.0",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/AhmiDarrow/Hearth/releases/tag/v0.3.0",
            "assets": [
                {"name": "HearthSetup.exe", "browser_download_url": "https://example.test/v0.3.0/HearthSetup.exe"},
                {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.test/v0.3.0/SHA256SUMS.txt"},
            ],
        }
    ]
    latest = pick_latest_release(releases, "0.3.0-beta")
    assert latest is not None
    assert latest["version"] == "0.3.0"
    assert latest["prerelease"] is False
    assert pick_latest_release(releases, "0.3.0") is None


def test_parse_and_verify_sha256sums(tmp_path: Path) -> None:
    payload = b"hearth-setup-body"
    digest = hashlib.sha256(payload).hexdigest()
    text = f"# comment\n{digest}  HearthSetup.exe\n{digest} *Hearth.cer\n"
    parsed = parse_sha256sums(text)
    assert parsed["HearthSetup.exe"] == digest
    assert parsed["Hearth.cer"] == digest
    setup = tmp_path / "HearthSetup.exe"
    setup.write_bytes(payload)
    verify_sha256(setup, digest)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify_sha256(setup, "0" * 64)


def test_check_and_download_from_0_2_to_0_3(tmp_path: Path) -> None:
    payload = b"hearth-0.3-installer"
    digest = hashlib.sha256(payload).hexdigest()
    sums = f"{digest}  HearthSetup.exe\n"
    files = {
        update.RELEASES_URL: json.dumps(RELEASES).encode(),
        "https://example.test/v0.3.0-beta/SHA256SUMS.txt": sums.encode(),
        "https://example.test/v0.3.0-beta/HearthSetup.exe": payload,
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        return files[url]

    result = update.check_for_update(current="0.2.0-beta", getter=getter)
    assert result["available"] is True
    assert result["version"] == "0.3.0-beta"

    dest = tmp_path / "dl"
    setup = update.download_and_verify(result, dest_dir=dest, getter=getter, require_authenticode=False)
    assert setup.read_bytes() == payload
    assert setup.name == "HearthSetup.exe"

    files["https://example.test/v0.3.0-beta/HearthSetup.exe"] = b"tampered"
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        update.download_and_verify(result, dest_dir=tmp_path / "bad", getter=getter, require_authenticode=False)


def test_auto_install_is_skipped_when_not_frozen() -> None:
    files = {update.RELEASES_URL: json.dumps(RELEASES).encode()}

    def getter(url: str, timeout: float = 30.0) -> bytes:
        return files[url]

    result = update.check_and_maybe_install(current="0.2.0", auto=True, getter=getter)
    assert result["available"] is True
    assert result["installed"] is False
    assert result.get("opened_installer") is False
    assert result["frozen"] is False
    assert "HearthSetup.exe" in result["message"] or "available" in result["message"].lower()


def test_auto_never_opens_installer_even_when_frozen(tmp_path: Path, monkeypatch) -> None:
    payload = b"hearth-installer"
    digest = hashlib.sha256(payload).hexdigest()
    files = {
        update.RELEASES_URL: json.dumps(RELEASES).encode(),
        "https://example.test/v0.3.0-beta/SHA256SUMS.txt": f"{digest}  HearthSetup.exe\n".encode(),
        "https://example.test/v0.3.0-beta/HearthSetup.exe": payload,
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        return files[url]

    def _should_not_open(path: Path) -> None:
        raise AssertionError(f"auto check opened installer {path}")

    monkeypatch.setattr(update, "is_frozen", lambda: True)
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr(update, "open_installer", _should_not_open)
    result = update.check_and_maybe_install(
        current="0.2.0",
        auto=True,
        getter=getter,
        require_authenticode=False,
    )
    assert result["available"] is True
    assert result["installed"] is False
    assert result["opened_installer"] is False
    assert "Install update" in result["message"]


def test_install_opens_verified_setup(tmp_path: Path, monkeypatch) -> None:
    payload = b"hearth-installer"
    digest = hashlib.sha256(payload).hexdigest()
    files = {
        update.RELEASES_URL: json.dumps(RELEASES).encode(),
        "https://example.test/v0.3.0-beta/SHA256SUMS.txt": f"{digest}  HearthSetup.exe\n".encode(),
        "https://example.test/v0.3.0-beta/HearthSetup.exe": payload,
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        return files[url]

    opened: list[Path] = []
    monkeypatch.setattr(update, "is_frozen", lambda: True)
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr(update, "open_installer", lambda path: opened.append(Path(path)))
    dest = tmp_path / "dl"
    result = update.check_and_maybe_install(
        current="0.2.0",
        install=True,
        getter=getter,
        require_authenticode=False,
    )
    # apply_update uses mkdtemp when dest_dir is None; still must have opened the file.
    assert result["opened_installer"] is True
    assert result["installed"] is False
    assert opened and opened[0].name == "HearthSetup.exe"
    assert opened[0].read_bytes() == payload
    assert "installer is opening" in result["message"]


def test_older_releases_can_see_0_3_3() -> None:
    releases = [
        {
            "tag_name": "v0.3.3",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/AhmiDarrow/Hearth/releases/tag/v0.3.3",
            "assets": [
                {"name": "HearthSetup.exe", "browser_download_url": "https://example.test/v0.3.3/HearthSetup.exe"},
                {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.test/v0.3.3/SHA256SUMS.txt"},
            ],
        }
    ]
    for current in ("0.3.1", "0.3.2"):
        latest = pick_latest_release(releases, current)
        assert latest is not None
        assert latest["version"] == "0.3.3"


def test_running_app_source_does_not_shell_out() -> None:
    root = Path(__file__).resolve().parents[1] / "hearth"
    banned = ("powershell.exe", "cmd.exe", "netsh.exe", "schtasks", "subprocess")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append(f"{path.relative_to(root)}: {token}")
    assert offenders == []


def test_open_installer_is_windows_only(tmp_path: Path) -> None:
    setup = tmp_path / "HearthSetup.exe"
    setup.write_bytes(b"fake")
    if update.sys.platform == "win32":
        return
    import pytest
    with pytest.raises(RuntimeError, match="Windows"):
        update.open_installer(setup)


def test_just_updated_marker(tmp_path: Path) -> None:
    assert update.read_just_updated(tmp_path) is None
    update.write_just_updated(tmp_path, "0.3.0-beta")
    assert update.read_just_updated(tmp_path) == "0.3.0-beta"
    update.clear_just_updated(tmp_path)
    assert update.read_just_updated(tmp_path) is None


def test_normalize_thumbprint() -> None:
    assert update.normalize_thumbprint("c7:b8:07:d5:42:b3:ba:72:07:aa:be:d9:60:f5:56:b4:dc:85:c0:2f") == update.CERT_THUMBPRINT
    assert update.CERT_THUMBPRINT == "C7B807D542B3BA7207AABED960F556B4DC85C02F"


def test_updates_api_is_local_only(store: Store) -> None:
    app = create_app(store, 17474)

    async def _run() -> None:
        transport = httpx.ASGITransport(app=app, client=("10.0.0.8", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://10.0.0.8") as client:
            denied = await client.post("/api/updates/check", json={})
            assert denied.status_code == 403
        local = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
        async with httpx.AsyncClient(transport=local, base_url="http://127.0.0.1") as client:
            # HEARTH_NO_UPDATE is set in conftest; the handler must not call GitHub.
            res = await client.post("/api/updates/check", json={})
            assert res.status_code == 200, res.text
            body = res.json()
            assert "current" in body
            assert body["available"] is False

    asyncio.run(_run())


def test_self_test_cli(tmp_path: Path) -> None:
    import socket
    import subprocess
    import sys

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    home = tmp_path / "self-test"
    completed = subprocess.run(
        [sys.executable, "-m", "hearth", "--self-test", "--home", str(home), "--port", str(port)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    log = (home / "self-test.log").read_text(encoding="utf-8") if (home / "self-test.log").exists() else ""
    assert completed.returncode == 0, completed.stdout + completed.stderr + log
    assert "self-test passed" in (completed.stdout + log)
    assert (home / "self-test.log").is_file()


def test_releases_url_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HEARTH_RELEASES_URL", "http://127.0.0.1:9/releases")
    assert update.releases_url() == "http://127.0.0.1:9/releases"
    seen: list[str] = []

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del timeout
        seen.append(url)
        return b"[]"

    result = update.check_for_update("0.3.4", getter=getter)
    assert seen == ["http://127.0.0.1:9/releases"]
    assert result["available"] is False


def test_apply_update_can_skip_opening_the_wizard(monkeypatch, tmp_path: Path) -> None:
    payload = b"setup-bytes"
    dest = tmp_path / "setup"
    digest = __import__("hashlib").sha256(payload).hexdigest()
    release = {
        "version": "0.3.99-e2e",
        "setup_url": "http://example.invalid/HearthSetup.exe",
        "sums_url": "http://example.invalid/SHA256SUMS.txt",
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del timeout
        if url.endswith("SHA256SUMS.txt"):
            return f"{digest}  HearthSetup.exe\n".encode()
        return payload

    opened: list[Path] = []
    monkeypatch.setattr(update, "open_installer", lambda path: opened.append(path))
    monkeypatch.setenv("HEARTH_UPDATE_SKIP_OPEN", "1")
    monkeypatch.setattr(update, "verify_authenticode", lambda path, expected=None: None)
    result = update.apply_update(release, getter=getter, dest_dir=dest, require_authenticode=False)
    assert result["skipped_open"] is True
    assert result["opened_installer"] is False
    assert Path(result["setup"]).is_file()
    assert opened == []
