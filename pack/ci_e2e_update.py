"""Windows CI end-to-end auto-update test. New -> newer and previous -> new must pass.

This is a Python driver so we do not depend on PowerShell parameter binding
(pwsh -File treated the second -PreviousTags value as $RepoRoot, which made
the releases server look in D:\\a\\Hearth\\Hearth\\v0.3.4\\pack\\...).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LATEST_PAGE = "https://github.com/AhmiDarrow/Hearth/releases/latest"


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {message}", flush=True)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def kill_hearth() -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/IM", "Hearth.exe", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def install_setup(setup: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Silent install {setup} -> {dest}")
    completed = subprocess.run(
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS",
            f"/DIR={dest}",
        ],
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Silent install failed with exit {completed.returncode}: {setup}")
    for candidate in (
        dest / "Hearth.exe",
        Path(r"C:\Program Files\Hearth\Hearth.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Hearth" / "Hearth.exe",
    ):
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"Hearth.exe not found after installing {setup} into {dest}")


def uninstall_hearth(dest: Path) -> None:
    kill_hearth()
    for candidate in (
        dest / "unins000.exe",
        Path(r"C:\Program Files\Hearth\unins000.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Hearth" / "unins000.exe",
    ):
        if candidate.is_file():
            log(f"Uninstall {candidate}")
            subprocess.run(
                [str(candidate), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                check=False,
            )
            break


def start_server(setup: Path, sums: Path, tag: str, port: int, log_dir: Path) -> tuple[subprocess.Popen, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    script = REPO / "pack" / "ci_e2e_releases.py"
    if not script.is_file():
        raise FileNotFoundError(f"missing releases stand-in: {script}")
    out = log_dir / "server.out"
    err = log_dir / "server.err"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(script),
            "--setup",
            str(setup.resolve()),
            "--sums",
            str(sums.resolve()),
            "--tag",
            tag,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO),
        env=env,
        stdout=out.open("w", encoding="utf-8"),
        stderr=err.open("w", encoding="utf-8"),
    )
    deadline = time.time() + 15
    url = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if out.is_file():
            for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("E2E_RELEASES_URL="):
                    url = line.split("=", 1)[1].strip()
                    log(f"fake releases at {url}")
                    return proc, url
        time.sleep(0.2)
    out_text = out.read_text(encoding="utf-8", errors="replace") if out.is_file() else ""
    err_text = err.read_text(encoding="utf-8", errors="replace") if err.is_file() else ""
    raise RuntimeError(f"Fake releases server did not start. out={out_text!r} err={err_text!r}")


def start_hearth(exe: Path, home: Path, port: int, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    home.mkdir(parents=True, exist_ok=True)
    kill_hearth()
    time.sleep(0.3)
    env = os.environ.copy()
    env.pop("HEARTH_NO_UPDATE", None)
    env.pop("HEARTH_RELEASES_URL", None)
    env.pop("HEARTH_UPDATE_SKIP_OPEN", None)
    env["HEARTH_HOME"] = str(home)
    env["HEARTH_PORT"] = str(port)
    if extra_env:
        env.update(extra_env)
    log(f"Starting {exe} --port {port} --home {home} --host 127.0.0.1")
    return subprocess.Popen(
        [str(exe), "--port", str(port), "--home", str(home), "--host", "127.0.0.1"],
        cwd=str(exe.parent),
        env=env,
        stdout=(home / "stdout.log").open("w", encoding="utf-8"),
        stderr=(home / "stderr.log").open("w", encoding="utf-8"),
    )


def stop_hearth(proc: subprocess.Popen | None, port: int | None = None) -> None:
    if port:
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", method="POST"),
                timeout=5,
            ).read()
        except Exception:
            pass
        time.sleep(0.4)
    kill_hearth()
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def http_json(url: str, payload: dict | None = None, timeout: float = 180.0) -> tuple[int, object, str]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    else:
        req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            return int(resp.status), body, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return int(exc.code), body, raw


def wait_state(port: int, dest: Path, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            status, body, raw = http_json(f"http://127.0.0.1:{port}/api/state", timeout=3)
            if status == 200 and isinstance(body, dict):
                write_text(dest, raw)
                return body
            last = f"status={status} body={raw}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"Timed out waiting for /api/state on port {port} ({last})")


def screenshot(path: Path) -> None:
    if sys.platform != "win32":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Best-effort; a black desktop is still useful as a timestamped artifact.
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
        f"$bmp.Save('{path.as_posix()}'); "
        "$g.Dispose(); $bmp.Dispose()"
    )
    try:
        subprocess.run(["pwsh", "-NoProfile", "-Command", script], check=False, timeout=20)
    except Exception as exc:
        log(f"Screenshot failed: {exc}")



CLOSE_PROMPT_NEEDLES = (
    "applications using files",
    "using files that need to be updated",
    "close the following applications",
    "currently running",
    "please close",
    "must be closed",
    "setup has detected that",
    "close applications",
    "is still running",
    "close hearth",
)


def is_close_app_prompt(text: str) -> bool:
    lowered = (text or "").lower()
    return any(needle in lowered for needle in CLOSE_PROMPT_NEEDLES)


def iter_window_texts() -> list[str]:
    """Visible top-level and child window titles/text, for close-prompt detection."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    texts: list[str] = []

    def _text(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()

    def _walk(hwnd: int) -> None:
        value = _text(hwnd)
        if value:
            texts.append(value)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _child(hwnd, _lparam):
        _walk(hwnd)
        return True

    child_cb = WNDENUMPROC(_child)

    def _top(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            _walk(hwnd)
            user32.EnumChildWindows(hwnd, child_cb, 0)
        return True

    user32.EnumWindows(WNDENUMPROC(_top), 0)
    return texts


def pe_product_version(path: Path) -> str | None:
    if sys.platform != "win32" or not path.is_file():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        dummy = wintypes.DWORD()
        size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(dummy))
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(buf, r"\StringFileInfo\040904B0\ProductVersion", ctypes.byref(ptr), ctypes.byref(length)):
            if not version.VerQueryValueW(buf, r"\StringFileInfo\040904b0\ProductVersion", ctypes.byref(ptr), ctypes.byref(length)):
                return None
        if not ptr.value or not length.value:
            return None
        return ctypes.wstring_at(ptr.value).strip() or None
    except Exception:
        return None


def running_process_paths(*image_names: str) -> list[tuple[str, str]]:
    if sys.platform != "win32":
        return []
    names = ", ".join(f"'{name}'" for name in image_names)
    script = (
        f"$names = @({names}); "
        "Get-CimInstance Win32_Process | Where-Object { $names -contains $_.Name } | "
        "Select-Object Name, ExecutablePath | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return []
    raw = (completed.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    found: list[tuple[str, str]] = []
    for row in data or []:
        name = str(row.get("Name") or "")
        path = str(row.get("ExecutablePath") or "")
        if name:
            found.append((name, path))
    return found


def assert_no_close_prompt(texts: list[str], evidence: Path) -> None:
    for text in texts:
        if is_close_app_prompt(text):
            screenshot(evidence / "screenshot-close-prompt.png")
            write_text(evidence / "close-prompt.txt", "\n".join(texts) + "\n")
            raise RuntimeError(f"Close-app prompt appeared: {text}")


def wait_for_updated_hearth(
    *,
    install_dir: Path,
    expected_version: str,
    ports: list[int],
    evidence: Path,
    timeout: float = 180.0,
) -> dict:
    """Watch the desktop during Setup and wait until Hearth is running on expected_version."""
    deadline = time.time() + timeout
    titles: list[str] = []
    last = ""
    exe = install_dir / "Hearth.exe"
    shot_at = 0.0
    while time.time() < deadline:
        texts = iter_window_texts()
        for text in texts:
            if text not in titles:
                titles.append(text)
        assert_no_close_prompt(texts, evidence)
        now = time.time()
        if now - shot_at >= 15:
            screenshot(evidence / f"screenshot-update-{int(now)}.png")
            shot_at = now
        for port in ports:
            try:
                status, body, raw = http_json(f"http://127.0.0.1:{port}/api/state", timeout=3)
                if status == 200 and isinstance(body, dict) and str(body.get("version")) == expected_version:
                    write_text(evidence / "state-after.json", raw if isinstance(raw, str) else json.dumps(body, indent=2))
                    write_text(evidence / "window-titles.txt", "\n".join(titles) + "\n")
                    write_text(
                        evidence / "no-close-prompt",
                        f"PASSED no close-app prompt; running v{expected_version} on port {port}\n",
                    )
                    log(f"updated app is running version={expected_version} port={port}")
                    return body
                last = f"port {port} status={status} version={(body or {}).get('version') if isinstance(body, dict) else body}"
            except Exception as exc:
                last = f"port {port}: {exc}"
        version = pe_product_version(exe)
        processes = running_process_paths("Hearth.exe", "HearthSetup.exe")
        write_json(evidence / "processes.json", {"version": version, "processes": processes, "last": last})
        time.sleep(0.5)
    write_text(evidence / "window-titles.txt", "\n".join(titles) + "\n")
    screenshot(evidence / "screenshot-timeout.png")
    raise RuntimeError(
        f"Timed out waiting for Hearth v{expected_version} to be running after the update ({last})"
    )


def run_update_pair(
    *,
    from_setup: Path,
    from_version: str,
    to_setup: Path,
    to_sums: Path,
    to_version: str,
    to_tag: str,
    case: Path,
    server_port: int,
    from_app_port: int,
    to_app_port: int,
    install_prefix: str,
    in_app_launch: bool,
) -> None:
    """Install from_setup, let its real updater pull to_setup, and assert a promptless relaunch."""
    case.mkdir(parents=True, exist_ok=True)
    temp = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or "/tmp")
    install_dir = temp / f"hearth-e2e-{install_prefix}-from"
    to_dir = temp / f"hearth-e2e-{install_prefix}-to"
    proc = None
    proc2 = None
    server = None
    try:
        exe = install_setup(from_setup.resolve(), install_dir)
        write_text(case / "installed-exe.txt", str(exe))
        server, url = start_server(
            to_setup.resolve(),
            to_sums.resolve(),
            to_tag,
            server_port,
            case / "server",
        )
        home = temp / f"hearth-e2e-home-{install_prefix}"
        extra_env = {
            "HEARTH_RELEASES_URL": url,
            "HEARTH_UPDATE_DIR": str(install_dir),
        }
        if not in_app_launch:
            extra_env["HEARTH_UPDATE_SKIP_OPEN"] = "1"
        proc = start_hearth(
            exe,
            home,
            from_app_port,
            extra_env,
        )
        state = wait_state(from_app_port, case / "state-before.json")
        screenshot(case / "screenshot-before.png")
        log(f"running from-build version={state.get('version')} in_app_launch={in_app_launch}")
        if str(state.get("version")) != from_version:
            raise RuntimeError(
                f"Installed build reported version {state.get('version')}, expected {from_version}"
            )
        status, detect, raw = http_json(
            f"http://127.0.0.1:{from_app_port}/api/updates/check",
            {"install": False},
        )
        write_text(case / "detect.json", raw if isinstance(raw, str) else json.dumps(detect))
        log(f"detect status={status} raw={raw}")
        if status != 200 or not isinstance(detect, dict) or not detect.get("available"):
            raise RuntimeError(f"Updater did not detect {to_version}: {raw}")
        status, install, raw = http_json(
            f"http://127.0.0.1:{from_app_port}/api/updates/check",
            {"install": True},
        )
        write_text(case / "install.json", raw if isinstance(raw, str) else json.dumps(install))
        log(f"install status={status} raw={raw}")
        screenshot(case / "screenshot-after-check.png")
        setup_path = Path(str((install or {}).get("setup") or ""))
        if status != 200 or not setup_path.is_file():
            raise RuntimeError(f"Updater failed to download/verify {to_version}: {raw}")
        ports = [from_app_port, to_app_port, 7474]
        if in_app_launch:
            log("waiting for in-app update to close Hearth, run Setup, and relaunch")
            after = wait_for_updated_hearth(
                install_dir=install_dir,
                expected_version=to_version,
                ports=ports,
                evidence=case,
            )
            proc = None
        else:
            log("previous updater cannot auto-close; launching new Setup while Hearth is running")
            install_setup(setup_path, install_dir)
            after = wait_for_updated_hearth(
                install_dir=install_dir,
                expected_version=to_version,
                ports=ports,
                evidence=case,
            )
            proc = None
        screenshot(case / "screenshot-after.png")
        log(f"relaunched version={after.get('version')}")
        if str(after.get("version")) != to_version:
            raise RuntimeError(
                f"Relaunched version {after.get('version')}, expected {to_version}"
            )
        write_text(case / "PASSED", f"PASSED {from_version} -> {to_version}\n")
        log(f"{from_version} -> {to_version} PASSED")
    finally:
        stop_hearth(proc, from_app_port)
        stop_hearth(proc2, to_app_port)
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        uninstall_hearth(to_dir)
        uninstall_hearth(install_dir)


def run_new_to_newer(args: argparse.Namespace, evidence: Path) -> None:
    run_update_pair(
        from_setup=Path(args.new_setup),
        from_version=args.new_version,
        to_setup=Path(args.newer_setup),
        to_sums=Path(args.newer_sums),
        to_version=args.newer_version,
        to_tag=f"v{args.newer_version}",
        case=evidence / "new-to-newer",
        server_port=18765,
        from_app_port=17511,
        to_app_port=17512,
        install_prefix="new-to-newer",
        in_app_launch=True,
    )


def run_previous_to_new(args: argparse.Namespace, evidence: Path, tag: str) -> dict:
    """Install a shipped previous release and let its updater pull the new build."""
    case = evidence / "previous-to-new" / tag
    case.mkdir(parents=True, exist_ok=True)
    row = {"tag": tag, "passed": False, "reason": ""}
    expected_from = tag[1:] if tag.startswith("v") else tag
    try:
        setup = maybe_download_previous(tag, case)
        run_update_pair(
            from_setup=setup,
            from_version=expected_from,
            to_setup=Path(args.new_setup),
            to_sums=Path(args.new_sums),
            to_version=args.new_version,
            to_tag=f"v{args.new_version}",
            case=case,
            server_port=18766,
            from_app_port=17513,
            to_app_port=17514,
            install_prefix=f"prev-{tag.replace('.', '_')}",
            in_app_launch=False,
        )
        row["passed"] = True
        row["reason"] = f"PASSED {expected_from} -> {args.new_version}"
    except Exception as exc:
        row["reason"] = str(exc)
        log(f"previous {tag} -> {args.new_version} FAILED: {exc}")
        traceback.print_exc()
        write_text(case / "FAILED", f"{exc}\n")
        kill_hearth()
    write_json(case / "result.json", row)
    return row


def maybe_download_previous(tag: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    setup = dest / "HearthSetup-previous.exe"
    subprocess.run(
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
        cwd=str(REPO),
    )
    found = dest / "HearthSetup.exe"
    if not found.is_file():
        raise RuntimeError(f"Could not download {tag} HearthSetup.exe")
    found.replace(setup)
    return setup




def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-setup", required=True)
    parser.add_argument("--new-sums", required=True)
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--newer-setup", required=True)
    parser.add_argument("--newer-sums", required=True)
    parser.add_argument("--newer-version", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--previous-tags", default="v0.3.7")
    args = parser.parse_args()
    os.chdir(REPO)
    evidence = Path(args.evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    transcript = evidence / "transcript.txt"
    # Line-buffer a simple transcript alongside stdout (already captured by CI).
    log(f"Repo {REPO}")
    log(f"New setup: {args.new_setup} version {args.new_version}")
    if not Path(args.new_setup).is_file() or not Path(args.new_sums).is_file():
        raise SystemExit("New release assets are missing")
    if not Path(args.newer_setup).is_file() or not Path(args.newer_sums).is_file():
        raise SystemExit("Throwaway newer assets are missing")

    new_ok = False
    previous: list[dict] = []
    error = ""
    try:
        log(f"==== new {args.new_version} -> newer {args.newer_version} (fixed updater) ====")
        run_new_to_newer(args, evidence)
        new_ok = True
    except Exception as exc:
        error = str(exc)
        log(f"new -> newer FAILED: {exc}")
        traceback.print_exc()
        write_text(evidence / "new-to-newer" / "FAILED", f"{exc}\n")

    tags = [t.strip() for t in args.previous_tags.split(",") if t.strip()]
    if tags:
        for tag in tags:
            log(f"==== previous {tag} -> new {args.new_version} (shipped updater) ====")
            previous.append(run_previous_to_new(args, evidence, tag))

    prev_ok = bool(previous) and all(row.get("passed") for row in previous)
    summary = {
        "newVersion": args.new_version,
        "newerVersion": args.newer_version,
        "newToNewerPassed": new_ok,
        "previousToNewPassed": prev_ok,
        "error": error,
        "previous": previous,
    }
    write_json(evidence / "summary.json", summary)
    write_text(transcript, json.dumps(summary, indent=2) + "\n")
    log("==== E2E summary ====")
    print(json.dumps(summary, indent=2), flush=True)
    if not new_ok:
        log("Required new -> newer auto-update test did not pass. Not safe to publish to Latest.")
        return 1
    if tags and not prev_ok:
        failed_prev = [row for row in previous if not row.get("passed")]
        log(
            "Required previous -> new auto-update test did not pass "
            f"({len(failed_prev)} case(s)). Not safe to publish to Latest."
        )
        return 1
    log(f"E2E finished. Evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
