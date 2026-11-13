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
) -> None:
    """Install from_setup, let its real updater detect/verify/download to_setup, then relaunch."""
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
        proc = start_hearth(
            exe,
            home,
            from_app_port,
            {
                "HEARTH_RELEASES_URL": url,
                "HEARTH_UPDATE_SKIP_OPEN": "1",
            },
        )
        state = wait_state(from_app_port, case / "state-before.json")
        screenshot(case / "screenshot-before.png")
        log(f"running from-build version={state.get('version')}")
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
        stop_hearth(proc, from_app_port)
        proc = None
        to_exe = install_setup(setup_path, to_dir)
        proc2 = start_hearth(to_exe, temp / f"hearth-e2e-home-{install_prefix}-after", to_app_port)
        after = wait_state(to_app_port, case / "state-after.json")
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
