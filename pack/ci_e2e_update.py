"""Windows CI end-to-end auto-update test. New -> newer and previous -> new must pass.

This is a Python driver so we do not depend on PowerShell parameter binding
(pwsh -File treated the second -PreviousTags value as $RepoRoot, which made
the releases server look in D:\\a\\Hearth\\Hearth\\v0.3.4\\pack\\...).
"""

from __future__ import annotations

import argparse
import hashlib
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

# Hidden process helper lives next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_winproc  # noqa: E402
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
        ci_winproc.run(
            ["taskkill", "/IM", "Hearth.exe", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def install_setup(setup: Path, dest: Path, log_path: Path | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Silent install {setup} -> {dest}")
    command = [
        str(setup),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        "/CURRENTUSER",
        f"/DIR={dest}",
    ]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"/LOG={log_path}")
    completed = ci_winproc.run(
        command,
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
    """Uninstall only the isolated dest copy. Never touch Program Files or the
    machine's default per-user Hearth under LocalAppData\\Programs\\Hearth.
    """
    kill_hearth()
    candidate = Path(dest) / "unins000.exe"
    if candidate.is_file():
        log(f"Uninstall {candidate}")
        ci_winproc.run(
            [str(candidate), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            check=False,
        )
        return
    log(f"No uninstaller in {dest}; leaving other Hearth installs alone")


def start_server(setup: Path, sums: Path, tag: str, port: int, log_dir: Path) -> tuple[subprocess.Popen, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    script = REPO / "pack" / "ci_e2e_releases.py"
    if not script.is_file():
        raise FileNotFoundError(f"missing releases stand-in: {script}")
    out = log_dir / "server.out"
    err = log_dir / "server.err"
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = ci_winproc.popen(
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
    log(f"Starting {exe} --no-desktop --no-browser --port {port} --home {home} --host 127.0.0.1")
    env["HEARTH_CI"] = "1"
    return ci_winproc.popen(
        [str(exe), "--no-desktop", "--no-browser", "--port", str(port), "--home", str(home), "--host", "127.0.0.1"],
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


def is_update_shutdown_error(exc: BaseException) -> bool:
    """True when the app closed the install HTTP call because it is exiting to update."""
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError)):
        return True
    reason = getattr(exc, "reason", None)
    if reason is not None and reason is not exc and is_update_shutdown_error(reason):
        return True
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "10054",
            "10053",
            "forcibly closed",
            "connection reset",
            "connection aborted",
            "broken pipe",
        )
    )


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
        ci_winproc.run(["pwsh", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script], check=False, timeout=20)
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


FOREIGN_WINDOW_NEEDLES = (
    "user account control",
    "windows security",
    "open file - security warning",
    "do you want to allow this app",
    "publisher: unknown",
    "innosetup",
    "hearth setup",
    "hearthsetup",
    "setup is preparing",
    "setup is installing",
    "installing hearth",
    "this will install",
    "select destination location",
    "license agreement",
    "ready to install",
    "completing the hearth setup",
    "wizard",
)

# Pre-existing CI / desktop chrome that is not an installer dialog.
CI_WINDOW_ALLOW = (
    "github actions",
    "actions runner",
    "pwsh",
    "powershell",
    "windows powershell",
    "command prompt",
    "cmd.exe",
    "administrator: ",
    "c:\actions-runner",
    "agent job",
    "task manager",
    "program manager",
    "cursor",
    "visual studio",
)


def is_hearth_window(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return lowered == "hearth" or lowered.startswith("hearth —") or lowered.startswith("hearth -")


def is_foreign_update_window(text: str) -> bool:
    """True for installer, UAC, or close-app dialogs that must never appear."""
    if is_close_app_prompt(text):
        return True
    lowered = (text or "").lower()
    if is_hearth_window(text):
        return False
    return any(needle in lowered for needle in FOREIGN_WINDOW_NEEDLES)


def is_ci_noise_window(text: str) -> bool:
    lowered = (text or "").lower()
    return any(needle in lowered for needle in CI_WINDOW_ALLOW)


def assert_no_foreign_windows(texts: list[str], preexisting: set[str], evidence: Path) -> None:
    """Fail only on installer, UAC, or close-app dialogs.

    Other new windows on this shared desktop (chat titles, spinners, browsers)
    are recorded as informational and must not fail the update hop.
    """
    foreign: list[str] = []
    newcomers: list[str] = []
    for text in texts:
        if is_foreign_update_window(text):
            foreign.append(text)
            continue
        if text in preexisting or is_hearth_window(text) or is_ci_noise_window(text):
            continue
        if text.strip():
            newcomers.append(text)
    if newcomers:
        write_text(evidence / "other-windows.txt", "\n".join(newcomers) + "\n")
        log(f"Ignoring non-installer windows during update: {newcomers}")
    if foreign:
        screenshot(evidence / "screenshot-foreign-window.png")
        write_text(evidence / "foreign-windows.txt", "\n".join(texts) + "\n")
        raise RuntimeError(f"Foreign installer or consent window appeared: {foreign}")


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


def running_process_records(*image_names: str) -> list[dict[str, str]]:
    if sys.platform != "win32":
        return []
    names = ", ".join(f"'{name}'" for name in image_names)
    script = (
        f"$names = @({names}); "
        "Get-CimInstance Win32_Process | Where-Object { $names -contains $_.Name } | "
        "Select-Object Name, ExecutablePath, ProcessId | ConvertTo-Json -Compress"
    )
    try:
        completed = ci_winproc.run(
            ["pwsh", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
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
    found: list[dict[str, str]] = []
    for row in data or []:
        name = str(row.get("Name") or "")
        path = str(row.get("ExecutablePath") or "")
        pid = str(row.get("ProcessId") or "")
        if name:
            found.append({"name": name, "path": path, "pid": pid})
    return found


def running_process_paths(*image_names: str) -> list[tuple[str, str]]:
    return [(row["name"], row["path"]) for row in running_process_records(*image_names)]


def _same_exe(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def hearth_processes_for(exe: Path) -> list[dict[str, str]]:
    target = str(exe)
    return [row for row in running_process_records("Hearth.exe") if _same_exe(row.get("path") or "", target)]


def assert_second_launch_does_not_spawn(
    exe: Path,
    evidence: Path,
    home: Path,
    port: int,
    timeout: float = 20.0,
) -> None:
    """Start-menu style second launch must exit and leave exactly one Hearth.exe.

    Pass --home (and --port) so the second process talks to the isolated E2E
    profile. Locks are per --home; launching bare Hearth.exe would open the
    default profile and hang, which is not a product bug.
    """
    before = hearth_processes_for(exe)
    write_json(evidence / "single-instance-before.json", before)
    if len(before) != 1:
        raise RuntimeError(f"Expected one running Hearth.exe at {exe} before second launch, found {before}")
    original_pid = before[0]["pid"]
    log(f"asserting single-instance: running pid={original_pid} exe={exe} home={home}")
    proc = ci_winproc.popen(
        [
            str(exe),
            "--no-desktop",
            "--no-browser",
            "--home",
            str(home),
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        after = hearth_processes_for(exe)
        write_json(evidence / "single-instance-after.json", after)
        raise RuntimeError(f"Second Hearth launch did not exit within {timeout}s; processes={after}")
    # Let a raced child die or attach.
    deadline = time.time() + 3
    after = hearth_processes_for(exe)
    while time.time() < deadline and (len(after) != 1 or after[0]["pid"] != original_pid):
        time.sleep(0.2)
        after = hearth_processes_for(exe)
    write_json(evidence / "single-instance-after.json", after)
    write_text(evidence / "second-launch-exit.txt", f"exit={proc.returncode}\n")
    if len(after) != 1 or after[0]["pid"] != original_pid:
        raise RuntimeError(
            f"Second launch created another Hearth process (before pid={original_pid}, after={after}, exit={proc.returncode})"
        )
    write_text(
        evidence / "single-instance",
        f"PASSED second launch did not create a second process (pid {original_pid} exit {proc.returncode})\n",
    )
    log(f"single-instance PASSED pid={original_pid} second_exit={proc.returncode}")


def _json_ok(port: int, path: str, body: dict | None = None) -> object:
    status, data, raw = http_json(f"http://127.0.0.1:{port}{path}", body)
    if status != 200:
        raise RuntimeError(f"{path} failed: {status} {raw}")
    return data


def seed_user_data(port: int, home: Path, work: Path) -> dict:
    """Put distinctive settings, a share, a group, and chat into this profile."""
    share = work / "e2e-share"
    share.mkdir(parents=True, exist_ok=True)
    (share / "notes.txt").write_text("keep-share-bytes", encoding="utf-8")
    downloads = work / "e2e-downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "keep-download.txt").write_text("keep-download-bytes", encoding="utf-8")
    _json_ok(
        port,
        "/api/settings",
        {
            "device_name": "E2E-Alpha",
            "theme": "dark",
            "download_folder": str(downloads),
        },
    )
    _json_ok(port, "/api/group/create", {"name": "E2E Home", "password": "e2e-pass", "device_name": "E2E-Alpha"})
    _json_ok(port, "/api/shares", {"path": str(share), "label": "E2E Notes", "mode": "readwrite"})
    _json_ok(port, "/api/chat", {"text": "keep-this-chat"})
    return capture_user_data(port, home, downloads)


def _share_key(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "label": item.get("label"),
        "mode": item.get("mode"),
        "path": item.get("path"),
    }


def capture_user_data(port: int, home: Path, downloads: Path | None = None) -> dict:
    status, body, _raw = http_json(f"http://127.0.0.1:{port}/api/state", timeout=10)
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"Could not snapshot /api/state: {status}")
    raw_path = Path(home) / "state.json"
    on_disk = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else {}
    key_path = Path(home) / "device.key"
    key_hash = ""
    if key_path.is_file():
        key_hash = hashlib.sha256(key_path.read_bytes()).hexdigest()
    group = body.get("group") or on_disk.get("group") or {}
    shares = [_share_key(s) for s in (body.get("shares") or on_disk.get("shares") or [])]
    shares.sort(key=lambda item: str(item.get("id") or ""))
    chat = []
    for msg in body.get("chat") or on_disk.get("chat") or []:
        chat.append({"from_name": msg.get("from_name"), "text": msg.get("text")})
    peers = []
    for member in body.get("members") or []:
        if member.get("self"):
            continue
        peers.append({"device_id": member.get("device_id"), "name": member.get("name")})
    peers.sort(key=lambda item: str(item.get("device_id") or ""))
    download_folder = (body.get("settings") or {}).get("download_folder") or on_disk.get("download_folder") or ""
    kept = ""
    if download_folder:
        kept_path = Path(download_folder) / "keep-download.txt"
        if kept_path.is_file():
            kept = kept_path.read_text(encoding="utf-8")
    return {
        "device_id": (body.get("device") or {}).get("id") or on_disk.get("device_id"),
        "device_name": (body.get("device") or {}).get("name") or on_disk.get("device_name"),
        "theme": body.get("theme") or (body.get("settings") or {}).get("theme") or on_disk.get("theme"),
        "group": {"id": group.get("id"), "name": group.get("name"), "salt": group.get("salt")} if group else None,
        "shares": shares,
        "chat": chat,
        "peers": peers,
        "download_folder": download_folder,
        "download_file": kept,
        "device_key_sha256": key_hash,
        "auto_check_updates": (body.get("settings") or {}).get("auto_check_updates", on_disk.get("auto_check_updates")),
        "bridge": (body.get("settings") or {}).get("bridge", on_disk.get("bridge")),
    }


def assert_user_data_unchanged(before: dict, after: dict, evidence: Path) -> None:
    write_json(evidence / "user-data-after.json", after)
    missing = [key for key, value in before.items() if after.get(key) != value]
    if missing:
        write_json(evidence / "user-data-mismatch.json", {"before": before, "after": after, "missing": missing})
        raise RuntimeError(f"User data changed across the update: {missing}")
    write_text(evidence / "user-data-snapshot", "PASSED user data identical after update\n")
    log("user-data snapshot PASSED")


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
    preexisting_windows: set[str] | None = None,
) -> dict:
    """Watch the desktop during Setup and wait until Hearth is running on expected_version."""
    deadline = time.time() + timeout
    titles: list[str] = []
    last = ""
    exe = install_dir / "Hearth.exe"
    shot_at = 0.0
    preexisting = set(preexisting_windows or [])
    while time.time() < deadline:
        texts = iter_window_texts()
        for text in texts:
            if text not in titles:
                titles.append(text)
        assert_no_close_prompt(texts, evidence)
        assert_no_foreign_windows(texts, preexisting, evidence)
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
                    write_text(
                        evidence / "no-foreign-windows",
                        f"PASSED no foreign window; running v{expected_version} on port {port}\n",
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
        exe = install_setup(from_setup.resolve(), install_dir, case / "setup-from.log")
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
        before_data = seed_user_data(from_app_port, home, case / "user-data")
        write_json(case / "user-data-before.json", before_data)
        preexisting_windows = set(iter_window_texts())
        write_text(case / "windows-before.txt", "\n".join(sorted(preexisting_windows)) + "\n")
        status, detect, raw = http_json(
            f"http://127.0.0.1:{from_app_port}/api/updates/check",
            {"install": False},
        )
        write_text(case / "detect.json", raw if isinstance(raw, str) else json.dumps(detect))
        log(f"detect status={status} raw={raw}")
        if status != 200 or not isinstance(detect, dict) or not detect.get("available"):
            raise RuntimeError(f"Updater did not detect {to_version}: {raw}")
        status = 0
        install: object = None
        raw = ""
        closed_for_update = False
        try:
            status, install, raw = http_json(
                f"http://127.0.0.1:{from_app_port}/api/updates/check",
                {"install": True},
            )
        except Exception as exc:
            if in_app_launch and is_update_shutdown_error(exc):
                closed_for_update = True
                raw = f"connection closed during install: {exc}"
                install = {"closed_for_update": True, "opened_installer": True}
                log(f"install POST closed as Hearth exited for update: {exc}")
            else:
                raise
        write_text(case / "install.json", raw if isinstance(raw, str) else json.dumps(install))
        log(f"install status={status} raw={raw}")
        screenshot(case / "screenshot-after-check.png")
        setup_path = Path(str((install or {}).get("setup") or "")) if isinstance(install, dict) else Path()
        ports = [from_app_port, to_app_port, 7474]
        if in_app_launch:
            if not closed_for_update and (status != 200 or not (isinstance(install, dict) and (setup_path.is_file() or install.get("closed_for_update") or install.get("opened_installer") or install.get("commit_pending")))):
                raise RuntimeError(f"Updater failed to download/verify {to_version}: {raw}")
            log("waiting for in-app update to close Hearth, run Setup, and relaunch")
            after = wait_for_updated_hearth(
                install_dir=install_dir,
                expected_version=to_version,
                ports=ports,
                evidence=case,
                preexisting_windows=preexisting_windows,
            )
            proc = None
        else:
            if status != 200 or not setup_path.is_file():
                raise RuntimeError(f"Updater failed to download/verify {to_version}: {raw}")
            log("previous updater cannot auto-close; launching new Setup while Hearth is running")
            install_setup(setup_path, install_dir, case / "setup-overlay.log")
            after = wait_for_updated_hearth(
                install_dir=install_dir,
                expected_version=to_version,
                ports=ports,
                evidence=case,
                preexisting_windows=preexisting_windows,
            )
            proc = None
        screenshot(case / "screenshot-after.png")
        log(f"relaunched version={after.get('version')}")
        if str(after.get("version")) != to_version:
            raise RuntimeError(
                f"Relaunched version {after.get('version')}, expected {to_version}"
            )
        used_port = 0
        device = after.get("device") if isinstance(after.get("device"), dict) else {}
        try:
            used_port = int(device.get("peer_port") or 0)
        except (TypeError, ValueError):
            used_port = 0
        if used_port != from_app_port:
            raise RuntimeError(
                f"Relaunch bound port {used_port or 'unknown'} instead of the original --port {from_app_port}; "
                "a silent update must preserve --home/--port and not fall back to 7474"
            )
        after_data = capture_user_data(used_port, home)
        assert_user_data_unchanged(before_data, after_data, case)
        assert_second_launch_does_not_spawn(install_dir / "Hearth.exe", case, home, from_app_port)
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
