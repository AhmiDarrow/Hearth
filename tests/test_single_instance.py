from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from hearth import singleinstance
from tests.conftest import local_client


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_hearth(home: Path, port: int, extra: list[str] | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["HEARTH_NO_UPDATE"] = "1"
    env["HEARTH_HOME"] = str(home)
    cmd = [
        sys.executable,
        "-m",
        "hearth",
        "--no-desktop",
        "--no-browser",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--home",
        str(home),
    ]
    if extra:
        cmd.extend(extra)
    home.mkdir(parents=True, exist_ok=True)
    stdout = (home / "test-stdout.log").open("ab")
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=stdout,
        stderr=subprocess.STDOUT,
    )


def _wait_state(port: int, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.0) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — wait loop
            last = exc
        time.sleep(0.1)
    raise AssertionError(f"Hearth on port {port} did not become ready: {last}")


def _stop(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def test_profile_lock_is_per_data_dir(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    assert singleinstance.mutex_name(one) != singleinstance.mutex_name(two)
    assert singleinstance.mutex_name(one) == singleinstance.mutex_name(one)
    assert singleinstance.mutex_name(one).startswith("Local\\HearthInstance-")
    first = singleinstance.InstanceLock(one)
    second = singleinstance.InstanceLock(one)
    other = singleinstance.InstanceLock(two)
    assert first.acquire()
    assert not second.acquire()
    assert other.acquire()
    first.release()
    assert second.acquire()
    second.release()
    other.release()


def test_stale_endpoint_does_not_block_a_new_lock(tmp_path: Path) -> None:
    home = tmp_path / "stale"
    home.mkdir()
    singleinstance.write_endpoint(home, port=9, token="old", pid=1)
    lock = singleinstance.InstanceLock(home)
    assert lock.acquire()
    singleinstance.write_endpoint(home, port=11, token="fresh")
    data = singleinstance.read_endpoint(home)
    assert data is not None
    assert data["token"] == "fresh"
    assert data["port"] == 11
    lock.release()


def test_handoff_requires_token_and_forwards_args(app) -> None:
    shown: list[bool] = []
    app.state.instance_token = "secret"
    app.state.show_ui_callbacks.append(lambda: shown.append(True))
    with local_client(app) as client:
        denied = client.post("/api/instance/handoff", json={"token": "nope", "argv": ["x"]})
        assert denied.status_code == 403
        ok = client.post(
            "/api/instance/handoff",
            json={"token": "secret", "argv": ["hearth://join", "some-invite"]},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["ok"] is True
        assert body["shown"] is True
        assert body["argv"] == ["hearth://join", "some-invite"]
        assert shown == [True]
        state = client.get("/api/state").json()
        assert state["pending_open"] == ["hearth://join", "some-invite"]


def test_handoff_yields_when_the_shell_is_missing(app) -> None:
    """A headless copy gives up the lock so the next launch can show the UI."""
    calls: list[str] = []
    app.state.instance_token = "secret"
    app.state.yield_for_ui = lambda: calls.append("yield")
    with local_client(app) as client:
        ok = client.post(
            "/api/instance/handoff",
            json={"token": "secret", "argv": [], "show": True},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["shown"] is False
        assert body["yield"] is True
        assert calls == ["yield"]
        app.state.show_ui_callbacks.append(lambda: calls.append("show"))
        again = client.post(
            "/api/instance/handoff",
            json={"token": "secret", "argv": ["notes"], "show": True},
        )
        assert again.status_code == 200, again.text
        shown = again.json()
        assert shown["shown"] is True
        assert shown["yield"] is False
        assert calls == ["yield", "show"]


def test_second_process_same_profile_exits_and_forwards(tmp_path: Path) -> None:
    home = tmp_path / "same"
    port = _free_port()
    first = _start_hearth(home, port)
    second = None
    try:
        state = _wait_state(port)
        assert "device" in state
        second = _start_hearth(home, port, extra=["forwarded-arg"])
        assert second.wait(timeout=8) == 0
        assert first.poll() is None
        after = _wait_state(port)
        assert "forwarded-arg" in after.get("pending_open", [])
    finally:
        _stop(second)
        _stop(first)


def test_separate_profiles_can_run_together_then_rebind_after_exit(tmp_path: Path) -> None:
    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    port_a = _free_port()
    port_b = _free_port()
    proc_a = _start_hearth(home_a, port_a)
    proc_b = _start_hearth(home_b, port_b)
    proc_again = None
    try:
        state_a = _wait_state(port_a)
        state_b = _wait_state(port_b)
        assert state_a["device"]["id"] != state_b["device"]["id"]
        _stop(proc_a)
        proc_a = None
        # Stale lock from the first profile must not block a relaunch.
        proc_again = _start_hearth(home_a, port_a)
        again = _wait_state(port_a)
        assert again["device"]["id"] == state_a["device"]["id"]
        assert proc_b.poll() is None
    finally:
        _stop(proc_again)
        _stop(proc_a)
        _stop(proc_b)


def test_no_test_only_multi_instance_bypass() -> None:
    for rel in ("hearth/__main__.py", "hearth/singleinstance.py", "hearth/app.py", "hearth/desktop.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "HEARTH_ALLOW_MULTI" not in text
    smoke = (ROOT / "pack" / "ci-peer-smoke.ps1").read_text(encoding="utf-8")
    assert "$env:HEARTH_ALLOW_MULTI =" not in smoke
    assert "Stop-PortListeners" in smoke
    assert "Write-HearthLog" in smoke
    assert "--home" in smoke
    e2e = (ROOT / "pack" / "ci_e2e_update.py").read_text(encoding="utf-8")
    assert "def assert_second_launch_does_not_spawn" in e2e
    assert 'assert_second_launch_does_not_spawn(install_dir / "Hearth.exe", case, home, from_app_port)' in e2e
    assert '"--home"' in e2e
    assert "str(home)" in e2e
    desktop_src = (ROOT / "hearth" / "desktop.py").read_text(encoding="utf-8")
    assert "def bring_window_to_front" in desktop_src
    assert "SetForegroundWindow" in desktop_src
    iss = (ROOT / "pack" / "hearth.iss").read_text(encoding="utf-8")
    assert "HearthSingleInstance" not in iss
    assert "CheckForMutexes" not in iss
    assert "/IM Hearth.exe /F" in iss
