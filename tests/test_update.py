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

@pytest.fixture(autouse=True)
def _reset_update_gate() -> None:
    update.reset_update_gate()
    yield
    update.reset_update_gate()


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
    exits: list[float] = []
    monkeypatch.setattr(update, "request_process_exit", lambda delay=1.0: exits.append(delay))
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
    assert result["closed_for_update"] is True
    assert opened and opened[0].name == "HearthSetup.exe"
    assert opened[0].read_bytes() == payload
    assert exits == [1.0]
    assert "closing itself" in result["message"]


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


def test_failed_check_opens_release_page_when_user_clicks(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(update, "open_release_page", lambda url=None: opened.append(url or update.LATEST_RELEASE_URL) or True)

    def getter(url: str, timeout: float = 30.0) -> bytes:
        raise RuntimeError("GitHub download failed (503)")

    result = update.check_and_maybe_install(current="0.3.4", auto=False, getter=getter)
    assert result["failed"] is True
    assert result["opened_release_page"] is True
    assert result["open_release_page"] is True
    assert opened == [update.LATEST_RELEASE_URL]
    assert "503" in result["message"]
    assert "release download page is opening" in result["message"]


def test_auto_check_failure_does_not_open_a_browser(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(update, "open_release_page", lambda url=None: opened.append(url or "") or True)

    def getter(url: str, timeout: float = 30.0) -> bytes:
        raise RuntimeError("offline")

    result = update.check_and_maybe_install(current="0.3.4", auto=True, getter=getter)
    assert result["failed"] is True
    assert result["opened_release_page"] is False
    assert opened == []
    assert "Download HearthSetup.exe from" in result["message"]


def test_failed_install_opens_release_page(monkeypatch) -> None:
    payload = b"hearth-installer"
    digest = "0" * 64
    files = {
        update.RELEASES_URL: json.dumps(
            [
                {
                    "tag_name": "v0.9.0",
                    "draft": False,
                    "prerelease": False,
                    "html_url": "https://github.com/AhmiDarrow/Hearth/releases/tag/v0.9.0",
                    "assets": [
                        {"name": "HearthSetup.exe", "browser_download_url": "https://example.test/HearthSetup.exe"},
                        {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.test/SHA256SUMS.txt"},
                    ],
                }
            ]
        ).encode(),
        "https://example.test/SHA256SUMS.txt": f"{digest}  HearthSetup.exe\n".encode(),
        "https://example.test/HearthSetup.exe": payload,
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        return files[url]

    opened: list[str] = []
    monkeypatch.setattr(update, "is_frozen", lambda: True)
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr(
        update,
        "open_installer",
        lambda path: (_ for _ in ()).throw(AssertionError("should not open a bad setup")),
    )
    monkeypatch.setattr(update, "open_release_page", lambda url=None: opened.append(url or "") or True)
    result = update.check_and_maybe_install(
        current="0.3.4",
        install=True,
        getter=getter,
        require_authenticode=False,
    )
    assert result["failed"] is True
    assert result["opened_installer"] is False
    assert result["opened_release_page"] is True
    assert opened == ["https://github.com/AhmiDarrow/Hearth/releases/tag/v0.9.0"]
    assert "SHA256" in result["message"]


def test_installer_launch_args_force_close_silently(monkeypatch, tmp_path: Path) -> None:
    args = update.installer_launch_args()
    for flag in (
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        "/CURRENTUSER",
    ):
        assert flag in args
    assert "/SILENT" not in args.split()
    monkeypatch.setenv("HEARTH_UPDATE_DIR", str(tmp_path / "Hearth"))
    with_dir = update.installer_launch_args()
    assert f'/DIR="{tmp_path / "Hearth"}"' in with_dir
    assert update.installer_launch_args(install_dir="") == update.INSTALLER_LAUNCH_ARGS


def test_apply_update_flushes_then_launches_then_exits(tmp_path: Path, monkeypatch) -> None:
    payload = b"setup-bytes"
    dest = tmp_path / "setup"
    digest = hashlib.sha256(payload).hexdigest()
    release = {
        "version": "0.3.9",
        "setup_url": "http://example.invalid/HearthSetup.exe",
        "sums_url": "http://example.invalid/SHA256SUMS.txt",
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del timeout
        if url.endswith("SHA256SUMS.txt"):
            return f"{digest}  HearthSetup.exe\n".encode()
        return payload

    order: list[str] = []
    update.clear_pre_install_hooks()
    update.register_pre_install(lambda: order.append("flush"))
    monkeypatch.delenv("HEARTH_UPDATE_SKIP_OPEN", raising=False)
    monkeypatch.setattr(update, "open_installer", lambda path: order.append(f"open:{Path(path).name}"))
    monkeypatch.setattr(update, "request_process_exit", lambda delay=1.0: order.append(f"exit:{delay}"))
    monkeypatch.setattr(update, "verify_authenticode", lambda path, expected=None: None)
    result = update.apply_update(release, getter=getter, dest_dir=dest, require_authenticode=False)
    assert result["opened_installer"] is True
    assert result["closed_for_update"] is True
    assert result.get("commit_pending") is not True
    assert order == ["flush", "open:HearthSetup.exe", "exit:1.0"]


def test_apply_update_can_defer_commit_until_after_the_http_response(tmp_path: Path, monkeypatch) -> None:
    payload = b"setup-bytes"
    dest = tmp_path / "setup"
    digest = hashlib.sha256(payload).hexdigest()
    release = {
        "version": "0.3.9",
        "setup_url": "http://example.invalid/HearthSetup.exe",
        "sums_url": "http://example.invalid/SHA256SUMS.txt",
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del timeout
        if url.endswith("SHA256SUMS.txt"):
            return f"{digest}  HearthSetup.exe\n".encode()
        return payload

    order: list[str] = []
    update.clear_pre_install_hooks()
    update.register_pre_install(lambda: order.append("flush"))
    monkeypatch.delenv("HEARTH_UPDATE_SKIP_OPEN", raising=False)
    monkeypatch.setattr(update, "open_installer", lambda path: order.append(f"open:{Path(path).name}"))
    monkeypatch.setattr(update, "request_process_exit", lambda delay=1.0: order.append(f"exit:{delay}"))
    monkeypatch.setattr(update, "verify_authenticode", lambda path, expected=None: None)
    result = update.apply_update(
        release, getter=getter, dest_dir=dest, require_authenticode=False, commit=False
    )
    assert result["opened_installer"] is True
    assert result["closed_for_update"] is True
    assert result["commit_pending"] is True
    assert order == []
    setup = Path(result["setup"])
    update.commit_hidden_install(setup)
    assert order == ["flush", "open:HearthSetup.exe", "exit:1.0"]


def test_open_installer_passes_silent_force_close_flags(tmp_path: Path, monkeypatch) -> None:
    setup = tmp_path / "HearthSetup.exe"
    setup.write_bytes(b"fake")
    seen: list[tuple] = []

    def _fake_open(target, directory=None, params=None, show=None):
        seen.append((target, directory, params, show))
        return 42

    monkeypatch.setattr(update, "_shell_open", _fake_open)
    monkeypatch.setattr(update.sys, "platform", "win32")
    update.open_installer(setup)
    assert seen == [(str(setup), str(setup.parent), update.installer_launch_args(), update.SW_HIDE)]


def test_iss_force_closes_without_a_prompt_and_relaunches() -> None:
    text = (Path(__file__).resolve().parents[1] / "pack" / "hearth.iss").read_text(encoding="utf-8")
    assert "CloseApplications=force" in text
    assert "RestartApplications=yes" in text
    assert not any(line.strip().startswith("AppMutex=") for line in text.splitlines())
    assert "skipifsilent" not in text
    assert "taskkill.exe" in text
    assert "/IM Hearth.exe /F" in text
    assert "HearthSingleInstance" not in text
    assert "CheckForMutexes" not in text
    assert 'Parameters: "{code:GetRelaunchParams}"' in text
    assert "nowait postinstall" in text
    assert "runhidden" in text
    assert "Check: ShouldRelaunch" in text
    assert "relaunch-cmdline.txt" in text
    assert "LoadStringFromFile" in text
    assert "GetRelaunchParams" in text
    assert "RunRelaunchScript('Capture', '')" in text
    assert "RunRelaunchScript('Start'" not in text
    assert "-WindowStyle Hidden" not in text
    assert "-NonInteractive" in text
    assert "relaunch.ps1" in text
    silent = [
        line
        for line in text.splitlines()
        if "ShouldRelaunchSilent" in line and line.strip().startswith("Filename:")
    ]
    assert len(silent) == 1
    assert "runhidden" in silent[0] and "{code:GetRelaunchParams}" in silent[0]
    interactive = [
        line
        for line in text.splitlines()
        if "ShouldRelaunchInteractive" in line and line.strip().startswith("Filename:")
    ]
    assert len(interactive) == 1
    assert "runhidden" not in interactive[0]
    assert "FileExists(ExpandConstant('{app}\\relaunch-cmdline.txt'))" in text
    # Capture must run before taskkill so a 0.3.8 (or older) process's
    # command line is saved before the exe is gone.
    capture_at = text.index("RunRelaunchScript('Capture', '')")
    kill_at = text.index("/IM Hearth.exe /F")
    assert capture_at < kill_at
    assert "ShouldRelaunchSilent" in text
    # Inno Pascal comments are { ... } and cannot contain another '{',
    # including constants such as {app}. That would be a compile error.
    code = text[text.index("[Code]"):]
    i = 0
    while i < len(code):
        ch = code[i]
        if ch == "'":
            i += 1
            while i < len(code) and code[i] != "'":
                i += 1
            i += 1
            continue
        if ch == "{":
            end = code.find("}", i + 1)
            assert end != -1, "unclosed Pascal comment in hearth.iss"
            body = code[i + 1:end]
            assert "{" not in body, f"Pascal comment contains a nested brace: {{ {body} }}"
            i = end + 1
            continue
        i += 1

    assert "/SKIPRELAUNCH" in text
    assert "PlainRelaunchAllowed" in text
    assert "GetEnv('HEARTH_HOME')" in text
    assert "--background --no-browser --no-desktop" in text
    assert "Win32_ProcessStartup" in text
    assert "HiddenExec" in text
    assert "CreateFlags" not in text
    code = text[text.index("[Code]"):]
    assert "ewWaitUntilTerminated" not in code
    assert "except" in code
    assert "PrivilegesRequired=lowest" in text
    assert "PrivilegesRequiredOverridesAllowed=commandline" in text
    assert "PrivilegesRequiredOverridesAllowed=dialog" not in text
    assert "UsePreviousPrivileges=no" in text
    assert "UsePreviousAppDir=no" in text
    assert "DefaultDirName={localappdata}\\Programs\\Hearth" in text
    assert "{autopf}" not in text
    lowered = text.lower()
    assert ".hearth" not in lowered
    assert "userprofile" not in lowered
    assert "{userappdata}" not in lowered
    assert 'Name: "{app}"' in text


def test_e2e_detects_close_app_prompts(tmp_path: Path) -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "pack" / "ci_e2e_update.py"
    spec = importlib.util.spec_from_file_location("hearth_ci_e2e_update", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.is_close_app_prompt("Setup has detected that Hearth is currently running.")
    assert mod.is_close_app_prompt("The following applications are using files that need to be updated by Setup.")
    assert not mod.is_close_app_prompt("Hearth Setup")
    assert not mod.is_close_app_prompt("Installing")
    assert mod.is_foreign_update_window("Hearth Setup")
    assert mod.is_foreign_update_window("User Account Control")
    assert mod.is_foreign_update_window("Setup has detected that Hearth is currently running.")
    assert not mod.is_foreign_update_window("Hearth")
    assert not mod.is_hearth_window("Hearth Setup")
    grok = "⠧ - Waiting for response… - Continue Springfield Project Zomboid map… - grok"
    assert not mod.is_foreign_update_window(grok)
    evidence = tmp_path / "e2e-windows"
    evidence.mkdir()
    # Shared-desktop chat titles must be recorded, not treated as a failed hop.
    mod.assert_no_foreign_windows([grok, "Hearth"], preexisting=set(), evidence=evidence)
    assert (evidence / "other-windows.txt").is_file()
    assert not (evidence / "foreign-windows.txt").exists()
    try:
        mod.assert_no_foreign_windows(["Hearth Setup"], preexisting=set(), evidence=evidence)
    except RuntimeError as exc:
        assert "Foreign installer or consent window appeared" in str(exc)
    else:
        raise AssertionError("installer window should fail the hop")
    # E2E must only uninstall the isolated dest, never the machine's real Hearth.
    src = path.read_text(encoding="utf-8")
    assert "def capture_user_data" in src
    assert "assert_user_data_unchanged" in src
    assert "preexisting_windows" in src
    assert "is_update_shutdown_error" in src
    assert "No uninstaller in" in src
    assert "C:\\Program Files\\Hearth\\unins000.exe" not in src
    assert "LOCALAPPDATA" not in src or ' / "Programs" / "Hearth" / "unins000.exe"' not in src


def test_workflow_finds_iscc_outside_program_files() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    finder = (root / "pack" / "find-iscc.ps1").read_text(encoding="utf-8")
    assert "pack\\find-iscc.ps1" in workflow or "pack/find-iscc.ps1" in workflow or "pack\\find-iscc.ps1" in workflow
    assert "scoop\\apps\\inno-setup\\current\\ISCC.exe" in finder
    assert "HEARTH_ISCC" in workflow


def test_defender_gate_ignores_unrelated_history() -> None:
    text = (Path(__file__).resolve().parents[1] / "pack" / "ci-defender.ps1").read_text(encoding="utf-8")
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    for banned in ("Set-MpPreference", "Remove-MpThreat", "Add-MpPreference", "ExclusionPath", "DisableRealtimeMonitoring"):
        assert banned not in text
    assert "HEARTH_JOB_START" in text
    assert "HEARTH_JOB_START" in workflow
    assert "Historical or out-of-scope detections (informational, ignored)" in text
    assert "Get-ScopeRoots" in text
    assert "InitialDetectionTime" in text
    assert "new in-scope Hearth file after the job start" in text



def test_apply_update_reports_download_then_install_progress(tmp_path: Path, monkeypatch) -> None:
    payload = b"setup-bytes-for-progress"
    dest = tmp_path / "setup"
    digest = __import__("hashlib").sha256(payload).hexdigest()
    release = {
        "version": "0.3.9",
        "setup_url": "http://example.invalid/HearthSetup.exe",
        "sums_url": "http://example.invalid/SHA256SUMS.txt",
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del timeout
        if url.endswith("SHA256SUMS.txt"):
            return f"{digest}  HearthSetup.exe\n".encode()
        return payload

    phases: list[str] = []
    update.clear_pre_install_hooks()
    monkeypatch.delenv("HEARTH_UPDATE_SKIP_OPEN", raising=False)
    monkeypatch.setattr(update, "open_installer", lambda path: None)
    monkeypatch.setattr(update, "request_process_exit", lambda delay=1.0: None)
    monkeypatch.setattr(update, "verify_authenticode", lambda path, expected=None: None)
    result = update.apply_update(
        release,
        getter=getter,
        dest_dir=dest,
        require_authenticode=False,
        progress=lambda info: phases.append(str(info.get("phase"))),
    )
    assert result["closed_for_update"] is True
    assert "download" in phases
    assert phases[-1] == "install"


def test_relaunch_ui_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "home"
    update.write_relaunch_ui(root, "tray")
    assert update.read_relaunch_ui(root) == "tray"
    update.write_relaunch_ui(root, "window")
    assert update.read_relaunch_ui(root) == "window"
    update.clear_relaunch_ui(root)
    assert update.read_relaunch_ui(root) is None


def test_default_update_dir_skips_program_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HEARTH_UPDATE_DIR", raising=False)
    monkeypatch.setattr(update, "is_frozen", lambda: True)
    monkeypatch.setattr(update.sys, "executable", str(tmp_path / "Program Files" / "Hearth" / "Hearth.exe"))
    (tmp_path / "Program Files" / "Hearth").mkdir(parents=True)
    assert update.default_update_dir() == ""
    user = tmp_path / "Users" / "joe" / "AppData" / "Local" / "Programs" / "Hearth"
    user.mkdir(parents=True)
    monkeypatch.setattr(update.sys, "executable", str(user / "Hearth.exe"))
    assert update.default_update_dir() == str(user)


def test_silent_install_uses_isolated_dir_and_timeout() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    start = workflow.index("Smoke test silent install")
    end = workflow.index("Defender runtime watch")
    step = workflow[start:end]
    assert "/DIR=" in step and "hearth-ci-silent-install" in step
    assert "/SKIPRELAUNCH" in step
    assert "WaitForExit" in step
    assert "C:\\Program Files\\Hearth" not in step
    assert "timeout-minutes: 10" in step
    assert "pack\\ci-setup-guard.ps1" in step
    assert "Wait-ThenStop-RunnerScopedSetup -WaitSeconds 60" in step
    assert "Stop-ProcessTree -ProcessId $proc.Id" in step
    assert "process tree killed" in step
    assert "$proc.Kill()" not in step


def test_setup_guard_only_targets_runner_scoped_setup() -> None:
    text = (Path(__file__).resolve().parents[1] / "pack" / "ci-setup-guard.ps1").read_text(encoding="utf-8")
    assert "Wait-ThenStop-RunnerScopedSetup" in text
    assert "[int]$WaitSeconds = 60" in text
    assert "taskkill.exe /PID $ProcessId /T /F" in text
    assert "$env:RUNNER_TEMP" in text
    assert "$env:RUNNER_WORKSPACE" in text
    assert "$env:GITHUB_WORKSPACE" in text
    assert "refusing to select any Setup process" in text
    assert "Test-IsRunnerScopedSetup" in text
    assert "Never kill a Setup whose path and command line are outside" in text
    assert "HearthSetup" in text
    assert "is-" in text and ".tmp" in text
    # Drive roots must not be treated as in-scope, or every Setup on the PC would match.
    assert r"^[A-Za-z]:$" in text


def test_updates_api_defers_hidden_install_until_after_the_response() -> None:
    text = (Path(__file__).resolve().parents[1] / "hearth" / "app.py").read_text(encoding="utf-8")
    assert "commit=False" in text
    assert "BackgroundTask(_commit_hidden_install)" in text
    assert "commit_hidden_install" in text


def test_repeated_install_checks_do_not_start_a_second_download(tmp_path: Path, monkeypatch) -> None:
    import threading

    payload = b"hearth-installer-once"
    digest = hashlib.sha256(payload).hexdigest()
    files = {
        update.RELEASES_URL: json.dumps(RELEASES).encode(),
        "https://example.test/v0.3.0-beta/SHA256SUMS.txt": f"{digest}  HearthSetup.exe\n".encode(),
        "https://example.test/v0.3.0-beta/HearthSetup.exe": payload,
    }
    started = threading.Event()
    release = threading.Event()
    downloads: list[str] = []

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del timeout
        if url.endswith("HearthSetup.exe"):
            downloads.append(url)
            started.set()
            assert release.wait(timeout=5), "first download was not released"
        return files[url]

    monkeypatch.setattr(update, "is_frozen", lambda: True)
    monkeypatch.setattr(update.sys, "platform", "win32")
    monkeypatch.setattr(update, "open_installer", lambda path: None)
    monkeypatch.setattr(update, "request_process_exit", lambda delay=1.0: None)
    monkeypatch.setattr(update, "verify_authenticode", lambda path, expected=None: None)
    monkeypatch.setattr(update, "run_pre_install", lambda: None)

    first: dict = {}

    def _run() -> None:
        first.update(
            update.check_and_maybe_install(
                current="0.2.0",
                install=True,
                getter=getter,
                require_authenticode=False,
            )
        )

    thread = threading.Thread(target=_run, name="hearth-update-first")
    thread.start()
    assert started.wait(timeout=5)
    second = update.check_and_maybe_install(
        current="0.2.0",
        install=True,
        getter=getter,
        require_authenticode=False,
    )
    assert second.get("in_flight") is True
    assert second.get("phase") == "download"
    assert len(downloads) == 1
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(downloads) == 1
    assert first.get("opened_installer") is True or first.get("commit_pending") is True


def test_auto_check_backs_off_after_a_failure(monkeypatch) -> None:
    monkeypatch.setattr(update, "is_frozen", lambda: True)

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del url, timeout
        raise RuntimeError("GitHub is down")

    first = update.check_and_maybe_install(current="0.2.0", auto=True, getter=getter)
    assert first.get("failed") is True
    deferred = update.check_and_maybe_install(current="0.2.0", auto=True, getter=getter)
    assert deferred.get("deferred") is True
    assert update.auto_check_deferred() is True


def test_ui_has_a_single_in_place_update_notice() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "hearth" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "hearth" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="updateBanner"' in html
    assert 'id="updateProgress"' not in html
    assert "updateProgress" not in js
    assert "function toastUpdate(" in js
    assert "function applyUpdateEvent(" in js
    assert 'if (upd.phase === "download" || upd.phase === "install") return;' in js
    assert "id = \"updateToast\"" in js or "id = 'updateToast'" in js or 'el.id = "updateToast"' in js
    assert "lastUpdateToastKey" in js
    assert "updateCheckInFlight" in js
    assert (root / "tests" / "fixtures" / "update_notice_single.png").is_file()


def test_ci_starts_processes_hidden_and_without_focus() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    hidden = (root / "pack" / "ci-hidden.ps1").read_text(encoding="utf-8")
    winproc = (root / "pack" / "ci_winproc.py").read_text(encoding="utf-8")
    e2e = (root / "pack" / "ci_e2e_update.py").read_text(encoding="utf-8")
    defender = (root / "pack" / "ci-defender.ps1").read_text(encoding="utf-8")
    main = (root / "hearth" / "__main__.py").read_text(encoding="utf-8")
    desktop = (root / "hearth" / "desktop.py").read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW" in winproc
    assert "STARTF_USESHOWWINDOW" in winproc
    assert 'WindowStyle = "Hidden"' in hidden
    assert "/VERYSILENT" in hidden and "/SUPPRESSMSGBOXES" in hidden
    assert "Start-HiddenProcess" in workflow
    assert "WindowStyle Hidden" in workflow
    assert "HEARTH_WINDOWS_RUNNER_ENABLED" in workflow
    assert 'HEARTH_CI: "1"' in workflow
    assert "--no-desktop" in e2e and "--no-browser" in e2e
    assert 'env["HEARTH_CI"] = "1"' in e2e
    assert "ci_winproc.popen" in e2e
    assert "--no-desktop" in defender
    assert "-WindowStyle Hidden" in defender
    assert 'os.environ.get("HEARTH_CI") == "1"' in main
    assert "args.no_desktop = True" in main
    assert "def running_in_ci" in desktop
    assert "if running_in_ci():" in desktop


def test_relaunch_args_roundtrip_and_apply(tmp_path: Path) -> None:
    from types import SimpleNamespace

    install = tmp_path / "Programs" / "Hearth"
    home = tmp_path / "homes" / "e2e"
    argv = ["--no-desktop", "--no-browser", "--port", "17511", "--home", str(home), "--host", "127.0.0.1"]
    path = update.write_relaunch_args(install, argv, home=home)
    assert path.name == "relaunch-args.json"
    rows = update.read_relaunch_args(install)
    assert len(rows) == 1
    assert rows[0]["home"] == str(home)
    assert rows[0]["argv"] == argv

    # --updated is stripped so the new installer can add the new version.
    update.write_relaunch_args(install, ["--updated", "0.3.8", "--home", str(home), "--port", "17511"], home=home)
    rows = update.read_relaunch_args(install)
    assert "--updated" not in rows[0]["argv"]
    assert "0.3.8" not in rows[0]["argv"]
    assert rows[0]["argv"] == ["--home", str(home), "--port", "17511"]

    args = SimpleNamespace(home=None, port=7474, host="0.0.0.0", no_desktop=False, no_browser=False, background=False)
    # Simulates Setup passing only --updated, as 0.3.8 Setup did.
    assert update.apply_saved_relaunch_args(args, argv=["--updated", "0.3.11"], install_dir=install) is True
    assert args.home == str(home)
    assert args.port == 17511
    assert args.host == "0.0.0.0"  # this write had no --host, leave the default
    # The last write did not include --host/--no-desktop. Reset and use the full argv.
    update.write_relaunch_args(install, argv, home=home)
    args = SimpleNamespace(home=None, port=7474, host="0.0.0.0", no_desktop=False, no_browser=False, background=False)
    assert update.apply_saved_relaunch_args(args, argv=["--updated", "0.3.11"], install_dir=install) is True
    assert args.home == str(home)
    assert args.port == 17511
    assert args.host == "127.0.0.1"
    assert args.no_desktop is True
    assert args.no_browser is True

    # Explicit argv wins over the file.
    args = SimpleNamespace(home=str(tmp_path / "other"), port=18000, host="0.0.0.0", no_desktop=False, no_browser=False, background=False)
    assert update.apply_saved_relaunch_args(
        args,
        argv=["--updated", "0.3.11", "--home", str(tmp_path / "other"), "--port", "18000"],
        install_dir=install,
    ) is False or args.home == str(tmp_path / "other")
    assert args.home == str(tmp_path / "other")
    assert args.port == 18000

    cmdline = install / "relaunch-cmdline.txt"
    assert cmdline.is_file()
    assert "--home" in cmdline.read_text(encoding="mbcs" if __import__("os").name == "nt" else "utf-8")
    update.clear_relaunch_args(install)
    assert update.read_relaunch_args(install) == []
    assert not cmdline.exists()
    args = SimpleNamespace(home=None, port=7474, host="0.0.0.0", no_desktop=False, no_browser=False, background=False)
    assert update.apply_saved_relaunch_args(args, argv=["--updated", "0.3.11"], install_dir=install) is False
    assert args.home is None
    assert args.port == 7474


def test_relaunch_args_unions_other_homes(tmp_path: Path) -> None:
    install = tmp_path / "Programs" / "Hearth"
    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    update.write_relaunch_args(install, ["--home", str(home_a), "--port", "10001"], home=home_a)
    update.write_relaunch_args(install, ["--home", str(home_b), "--port", "10002"], home=home_b)
    rows = update.read_relaunch_args(install)
    homes = {row["home"] for row in rows}
    assert homes == {str(home_a), str(home_b)}
    # Rewriting home_b replaces that row and keeps home_a.
    update.write_relaunch_args(install, ["--home", str(home_b), "--port", "10003"], home=home_b)
    by_home = {row["home"]: row["argv"] for row in update.read_relaunch_args(install)}
    assert by_home[str(home_a)] == ["--home", str(home_a), "--port", "10001"]
    assert by_home[str(home_b)] == ["--home", str(home_b), "--port", "10003"]


def test_argv_from_command_line_strips_exe_and_updated() -> None:
    exe = "C:/Users/Joe/AppData/Local/Programs/Hearth/Hearth.exe"
    command = f'"{exe}" --no-desktop --no-browser --port 17511 --home C:/tmp/e2e --updated 0.3.8'
    argv = update.argv_from_command_line(command, exe)
    assert "--updated" not in argv
    assert "0.3.8" not in argv
    assert argv[0] == "--no-desktop"
    assert "--port" in argv and "17511" in argv
    assert "--home" in argv
    assert exe not in argv
    assert update.home_from_argv(argv).endswith("e2e")
    assert update.drop_updated_args(["--updated", "0.3.11", "--home", "x"]) == ["--home", "x"]
    assert update.drop_updated_args(["--updated=0.3.11", "--port", "1"]) == ["--port", "1"]


def test_write_current_relaunch_args_uses_install_dir(tmp_path: Path) -> None:
    from types import SimpleNamespace

    install = tmp_path / "Programs" / "Hearth"
    home = tmp_path / "homes" / "profile"
    args = SimpleNamespace(host="127.0.0.1", no_desktop=True, no_browser=True, background=False)
    path = update.write_current_relaunch_args(args, home=home, port=17511, install_dir=install)
    assert path is not None and path.is_file()
    rows = update.read_relaunch_args(install)
    assert rows[0]["home"] == str(home)
    assert rows[0]["argv"] == [
        "--home",
        str(home),
        "--port",
        "17511",
        "--host",
        "127.0.0.1",
        "--no-desktop",
        "--no-browser",
    ]


def test_startup_and_e2e_preserve_relaunch_args() -> None:
    root = Path(__file__).resolve().parents[1]
    main = (root / "hearth" / "__main__.py").read_text(encoding="utf-8")
    e2e = (root / "pack" / "ci_e2e_update.py").read_text(encoding="utf-8")
    ps1 = (root / "pack" / "relaunch.ps1").read_text(encoding="utf-8")
    iss = (root / "pack" / "hearth.iss").read_text(encoding="utf-8")
    assert "apply_saved_relaunch_args" in main
    assert "write_current_relaunch_args" in main
    assert 'os.environ["HEARTH_PORT"]' in main
    # E2E must fail if Setup comes up on the default port instead of --port.
    assert "must preserve --home/--port and not fall back to 7474" in e2e
    assert "from_app_port" in e2e
    assert "setup-overlay.log" in e2e
    assert "/LOG=" in e2e
    assert "ValidateSet('Capture', 'Start')" in ps1
    assert "Do not invent a default-profile instance after an update." in ps1
    assert "relaunch-cmdline.txt" in ps1
    assert "ConvertTo-RelaunchJson" in ps1
    assert "Encoding]::Default" in ps1
    assert "Start-Process" not in ps1
    assert "-WindowStyle Hidden" not in ps1
    # Windows PowerShell treats $HOME as read-only. Assigning it aborts Capture.
    assert "$home =" not in ps1
    assert "$home=" not in ps1
    assert "if ($instances.Count -eq 0) { exit 0 }" in ps1
    assert "Win32_Process" in ps1
    # Start with no args file must exit without launching Hearth.exe.
    start = ps1.index("if ($Mode -eq 'Start')")
    missing = ps1.index("if (-not (Test-Path -LiteralPath $ArgsFile))", start)
    invent = ps1.index("Do not invent a default-profile instance after an update.", start)
    launch = ps1.index("ProcessStartInfo", start)
    assert start < missing < invent < launch
    assert "RunRelaunchScript('Start'" not in iss
    assert "ExtractTemporaryFile('relaunch.ps1')" in iss
    assert "dontcopy" in iss
    wf = (root / ".github" / "workflows" / "windows-installer.yml").read_text(encoding="utf-8")
    scenarios = (root / "pack" / "ci_install_scenarios.py").read_text(encoding="utf-8")
    assert "ci_install_scenarios.py" in wf
    assert "fresh" in scenarios
    assert "v0.3.8" in scenarios
    assert "half-install" in scenarios
    assert "http://127.0.0.1:7474" in scenarios
    half = scenarios[scenarios.index("def scenario_half"):]
    assert "relaunch-cmdline.txt" in half and "relaunch-args.json" in half
    assert half.index("relaunch-cmdline.txt") < half.index("run_setup")
    assert "must preserve --home/--port and not fall back to 7474" in e2e


def test_relaunch_cmdline_quotes_paths_and_strips_updated(tmp_path: Path) -> None:
    import os

    install = tmp_path / "Programs" / "Hearth"
    home = tmp_path / "My Home"
    text = update.format_relaunch_cmdline(["--updated", "0.3.11", "--home", str(home), "--port", "17511"])
    assert text == f'--home "{home}" --port 17511'
    assert "--updated" not in text
    assert update.quote_windows_arg("--port") == "--port"
    assert update.quote_windows_arg("") == '""'
    assert update.quote_windows_arg("C:\\temp\\dir\\") == "C:\\temp\\dir\\"
    assert update.quote_windows_arg("C:\\temp\\my dir\\") == '"C:\\temp\\my dir\\\\"'
    update.write_relaunch_args(
        install,
        ["--updated", "0.3.8", "--home", str(home), "--port", "17511"],
        home=home,
    )
    encoding = "mbcs" if os.name == "nt" else "utf-8"
    cmdline = (install / "relaunch-cmdline.txt").read_text(encoding=encoding)
    assert cmdline == text
    # An empty snapshot still leaves the file, so Setup can relaunch with --updated only.
    update.write_relaunch_args(install, [], home=None)
    assert (install / "relaunch-cmdline.txt").read_text(encoding=encoding) == ""
    update.clear_relaunch_args(install)
    assert not (install / "relaunch-args.json").exists()
    assert not (install / "relaunch-cmdline.txt").exists()


def _hidden_powershell(argv: list[str]):
    import subprocess
    import sys

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, creationflags=flags)


def test_relaunch_capture_keeps_one_instance_and_writes_ansi_cmdline(tmp_path: Path) -> None:
    import os
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows PowerShell Capture runs on Windows")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        pytest.skip("Windows PowerShell 5.1 is not installed")
    script = Path(__file__).resolve().parents[1] / "pack" / "relaunch.ps1"
    install = tmp_path / "Programs" / "Hearth"
    install.mkdir(parents=True)
    home = tmp_path / "e2e profile"
    argv = ["--home", str(home), "--port", "17511", "--no-desktop", "--no-browser"]
    args_file = install / "relaunch-args.json"
    args_file.write_text(
        json.dumps({"version": 1, "instances": [{"home": str(home), "argv": argv}]}),
        encoding="utf-8",
    )
    # No Hearth.exe here, so Capture must keep the saved instance instead of wiping it.
    completed = _hidden_powershell(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Mode",
            "Capture",
            "-AppExe",
            str(install / "Hearth.exe"),
            "-ArgsFile",
            str(args_file),
        ]
    )
    assert completed.returncode == 0, completed.stderr
    saved = json.loads(args_file.read_text(encoding="utf-8"))
    assert isinstance(saved["instances"], list)
    assert len(saved["instances"]) == 1
    assert saved["instances"][0]["argv"] == argv
    cmdline = (install / "relaunch-cmdline.txt").read_text(encoding="mbcs")
    assert f'"{home}"' in cmdline
    assert "--port 17511" in cmdline
    assert "--updated" not in cmdline

    # A failed snapshot must not delete a command line that is already on disk.
    sentinel = "--home C:\\keep --port 9"
    (install / "relaunch-cmdline.txt").write_text(sentinel, encoding="mbcs")
    args_file.write_text("{}", encoding="utf-8")
    completed = _hidden_powershell(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Mode",
            "Capture",
            "-AppExe",
            str(install / "Hearth.exe"),
            "-ArgsFile",
            str(args_file),
        ]
    )
    assert completed.returncode == 0, completed.stderr
    assert (install / "relaunch-cmdline.txt").read_text(encoding="mbcs") == sentinel

    missing = tmp_path / "absent" / "relaunch-args.json"
    completed = _hidden_powershell(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Mode",
            "Start",
            "-AppExe",
            str(script),
            "-ArgsFile",
            str(missing),
            "-Updated",
            "0.3.11",
        ]
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
    assert not (missing.parent / "relaunch-cmdline.txt").exists()
