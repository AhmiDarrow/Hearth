"""Silent-install cases that must leave Hearth answering on http://127.0.0.1:7474.

Cases:
  fresh         empty directory, no running Hearth, no uninstall key
  update-0.3.8  install v0.3.8, start it on 7474, overlay the new Setup
  half-install  payload files present, uninstall key absent

Each case checks that Setup exits 0, Hearth.exe is running, and
http://127.0.0.1:7474 answers. Installs go to an isolated directory with
HEARTH_HOME set, so the real per-user profile and Programs\\Hearth are
left alone. The existing update E2E still checks that a captured --port
is not replaced with 7474.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import winreg
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_winproc  # noqa: E402

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{8E4C1F2A-9B3D-4A71-8E6C-2F5D9A1B0C44}_is1"
PORT = 7474
STATE_URL = f"http://127.0.0.1:{PORT}/api/state"


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}", flush=True)


def work_root() -> Path:
    base = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or str(REPO / "dist"))
    return base / "hearth-scenarios"


def real_programs_exe() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / "Programs" / "Hearth" / "Hearth.exe"


def real_profile() -> Path:
    return Path.home() / ".hearth"


def program_files_exe() -> Path:
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Hearth" / "Hearth.exe"


def snapshot_tree(root: Path) -> list[tuple[str, int, int]]:
    if not root.exists():
        return []
    rows: list[tuple[str, int, int]] = []
    for path in root.rglob("*"):
        if path.is_file():
            stat = path.stat()
            rows.append((str(path.relative_to(root)).lower(), stat.st_size, stat.st_mtime_ns))
    return sorted(rows)


def snapshot_file(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def assert_isolated(dest: Path) -> None:
    resolved = dest.resolve()
    banned = [real_programs_exe().parent, program_files_exe().parent, real_profile()]
    for item in banned:
        try:
            other = item.resolve()
        except OSError:
            continue
        if resolved == other or other in resolved.parents:
            raise RuntimeError(f"refusing to use {dest}")


def uninstall_location() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "InstallLocation")
            return str(value or "")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def delete_uninstall_key_if_under(root: Path) -> None:
    location = uninstall_location()
    if not location:
        return
    try:
        resolved = Path(location).resolve()
        base = root.resolve()
    except OSError:
        return
    if resolved == base or base in resolved.parents:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        log(f"removed uninstall key that pointed at {location}")


def remove_shortcuts_under(root: Path) -> None:
    script = (
        "$root = $env:HEARTH_SCENARIO_ROOT\n"
        "$sh = New-Object -ComObject WScript.Shell\n"
        "$paths = @(\n"
        "  (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\Hearth.lnk'),\n"
        "  (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Hearth.lnk'),\n"
        "  (Join-Path $env:USERPROFILE 'Desktop\\Hearth.lnk'),\n"
        "  (Join-Path $env:USERPROFILE 'OneDrive\\Desktop\\Hearth.lnk')\n"
        ")\n"
        "foreach ($lnk in $paths) {\n"
        "  if (Test-Path -LiteralPath $lnk) {\n"
        "    $target = $sh.CreateShortcut($lnk).TargetPath\n"
        "    if ($target -and $target.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {\n"
        "      Remove-Item -LiteralPath $lnk -Force\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    env = os.environ.copy()
    env["HEARTH_SCENARIO_ROOT"] = str(root.resolve())
    system = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = str(Path(system) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    ci_winproc.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )


def fetch_state(timeout: float = 2) -> dict:
    with urllib.request.urlopen(STATE_URL, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        if resp.status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"unexpected state status {resp.status}")
        return body


def port_answers() -> bool:
    try:
        fetch_state()
        return True
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError):
        return False


def hearth_running() -> bool:
    completed = ci_winproc.run(
        ["tasklist", "/FI", "IMAGENAME eq Hearth.exe", "/NH"],
        check=False,
        capture_output=True,
        text=True,
    )
    return "Hearth.exe" in (completed.stdout or "")


def stop_hearth() -> None:
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{PORT}/api/quit", method="POST"),
            timeout=5,
        ).read()
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    ci_winproc.run(
        ["taskkill", "/IM", "Hearth.exe", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline and port_answers():
        time.sleep(0.3)


def wait_version(version: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    last = "not running"
    while time.time() < deadline:
        running = hearth_running()
        if running and port_answers():
            try:
                body = fetch_state()
                last = str(body.get("version"))
                if last == version:
                    if not hearth_running():
                        raise RuntimeError("state answered but Hearth.exe is not running")
                    return body
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError) as exc:
                last = str(exc)
        elif running:
            last = "process up, port silent"
        time.sleep(0.5)
    raise RuntimeError(f"http://127.0.0.1:7474 did not serve {version} ({last})")


def run_setup(setup: Path, dest: Path, log_path: Path, env: dict[str, str], timeout: int = 240) -> int:
    assert_isolated(dest)
    dest.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(setup),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        "/CURRENTUSER",
        f"/DIR={dest}",
        f"/LOG={log_path}",
    ]
    log(f"setup {setup.name} -> {dest}")
    proc = ci_winproc.popen(command, env=env)
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"Setup timed out: {setup}") from None
    if code != 0:
        tail = ""
        if log_path.is_file():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-40:])
        raise RuntimeError(f"Setup exit {code}\n{tail}")
    if not (dest / "Hearth.exe").is_file():
        raise RuntimeError(f"Hearth.exe missing after Setup in {dest}")
    return code


def scenario_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HEARTH_CI"] = "1"
    env["HEARTH_HOME"] = str(home)
    env["HEARTH_PORT"] = str(PORT)
    env.pop("HEARTH_DESKTOP", None)
    return env


def uninstall_dest(dest: Path, root: Path) -> None:
    stop_hearth()
    unins = dest / "unins000.exe"
    if unins.is_file():
        log(f"uninstall {unins}")
        try:
            ci_winproc.run(
                [str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            log("uninstaller timed out")
    delete_uninstall_key_if_under(root)
    remove_shortcuts_under(root)


def start_existing(exe: Path, home: Path) -> subprocess.Popen:
    home.mkdir(parents=True, exist_ok=True)
    env = scenario_env(home)
    log(f"start {exe} --port {PORT} --home {home}")
    return ci_winproc.popen(
        [
            str(exe),
            "--no-desktop",
            "--no-browser",
            "--background",
            "--port",
            str(PORT),
            "--home",
            str(home),
            "--host",
            "127.0.0.1",
        ],
        cwd=str(exe.parent),
        env=env,
    )


def download_previous(tag: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    ci_winproc.run(
        [
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            "AhmiDarrow/Hearth",
            "--pattern",
            "HearthSetup.exe",
            "--dir",
            str(dest),
            "--clobber",
        ],
        check=True,
    )
    found = dest / "HearthSetup.exe"
    if not found.is_file():
        raise RuntimeError(f"Could not download {tag} HearthSetup.exe")
    return found


def assert_programs_untouched(before_pf: tuple[int, int] | None, before_user: tuple[int, int] | None) -> None:
    if snapshot_file(program_files_exe()) != before_pf:
        raise RuntimeError("Program Files Hearth.exe changed")
    if snapshot_file(real_programs_exe()) != before_user:
        raise RuntimeError("per-user Programs\\Hearth\\Hearth.exe changed")


def scenario_fresh(setup: Path, version: str, root: Path) -> None:
    dest = root / "fresh-app"
    home = root / "fresh-home"
    log_path = root / "fresh-setup.log"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    try:
        run_setup(setup, dest, log_path, scenario_env(home))
        body = wait_version(version)
        log(f"fresh http://127.0.0.1:7474 version={body.get('version')}")
        if not home.exists():
            raise RuntimeError("fresh install did not use the isolated HEARTH_HOME")
    finally:
        uninstall_dest(dest, root)
        shutil.rmtree(dest, ignore_errors=True)


def scenario_update(setup: Path, version: str, previous_tag: str, root: Path) -> None:
    previous = download_previous(previous_tag, root / "previous-download")
    dest = root / "update-app"
    home = root / "update-home"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    before_pf = snapshot_file(program_files_exe())
    proc = None
    try:
        run_setup(previous, dest, root / "update-previous.log", scenario_env(home))
        assert_programs_untouched(before_pf, snapshot_file(real_programs_exe()))
        proc = start_existing(dest / "Hearth.exe", home)
        wait_version("0.3.8")
        run_setup(setup, dest, root / "update-overlay.log", scenario_env(home))
        body = wait_version(version)
        log(f"update http://127.0.0.1:7474 version={body.get('version')}")
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
        uninstall_dest(dest, root)
        shutil.rmtree(dest, ignore_errors=True)


def scenario_half(setup: Path, payload: Path, version: str, root: Path) -> None:
    dest = root / "half-app"
    home = root / "half-home"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    shutil.copytree(payload, dest)
    delete_uninstall_key_if_under(root)
    if uninstall_location() and Path(uninstall_location()).resolve() == dest.resolve():
        raise RuntimeError("half-install still has an uninstall key")
    try:
        if not (dest / "Hearth.exe").is_file():
            raise RuntimeError("half-install payload has no Hearth.exe")
        run_setup(setup, dest, root / "half-setup.log", scenario_env(home))
        body = wait_version(version)
        log(f"half-install http://127.0.0.1:7474 version={body.get('version')}")
    finally:
        uninstall_dest(dest, root)
        shutil.rmtree(dest, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--previous-tag", default="v0.3.8")
    parser.add_argument("--expect-version", required=True)
    args = parser.parse_args()
    setup = Path(args.setup)
    payload = Path(args.payload)
    if not setup.is_file():
        raise SystemExit(f"missing setup {setup}")
    if not (payload / "Hearth.exe").is_file():
        raise SystemExit(f"missing payload {payload}")
    if port_answers() or hearth_running():
        raise SystemExit("Hearth is already running; refusing to install over it")
    root = work_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    profile_before = snapshot_tree(real_profile())
    programs_before = snapshot_file(real_programs_exe())
    pf_before = snapshot_file(program_files_exe())
    failures: list[str] = []
    cases = (
        ("fresh", lambda: scenario_fresh(setup, args.expect_version, root)),
        ("update-from-0.3.8", lambda: scenario_update(setup, args.expect_version, args.previous_tag, root)),
        ("half-install", lambda: scenario_half(setup, payload, args.expect_version, root)),
    )
    try:
        for name, fn in cases:
            log(f"==== {name} ====")
            try:
                fn()
                log(f"SCENARIO {name} PASSED")
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                log(f"SCENARIO {name} FAILED: {exc}")
                stop_hearth()
    finally:
        delete_uninstall_key_if_under(root)
        remove_shortcuts_under(root)
        stop_hearth()
    if snapshot_tree(real_profile()) != profile_before:
        failures.append("real ~/.hearth changed")
    if snapshot_file(real_programs_exe()) != programs_before:
        failures.append("per-user Programs\\Hearth changed")
    if snapshot_file(program_files_exe()) != pf_before:
        failures.append("Program Files Hearth changed")
    if failures:
        for item in failures:
            log(f"FAIL {item}")
        return 1
    log("all install scenarios passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        log(f"scenarios aborted: {exc}")
        raise SystemExit(1) from exc
