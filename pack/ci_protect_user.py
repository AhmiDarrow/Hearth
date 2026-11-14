"""Read-only guard for the interactive user's Hearth install.

CI may snapshot and compare that install. It must not kill, uninstall, or
rewrite it. Process starts go through ci_winproc so no window appears.
"""

from __future__ import annotations

import os
from pathlib import Path


def user_programs_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return Path("Programs") / "Hearth"
    return Path(local) / "Programs" / "Hearth"


def program_files_hearth() -> Path:
    root = os.environ.get("ProgramFiles", r"C:\Program Files")
    return Path(root) / "Hearth"


def user_profile() -> Path:
    return Path.home() / ".hearth"


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def assert_isolated_dest(dest: Path) -> None:
    """Refuse a directory that is, contains, or sits inside the real install or data."""
    resolved = _resolved(Path(dest))
    if resolved is None:
        raise RuntimeError(f"refusing to use {dest}")
    for item in (user_programs_dir(), program_files_hearth(), user_profile()):
        other = _resolved(item)
        if other is None:
            continue
        if resolved == other or other in resolved.parents or resolved in other.parents:
            raise RuntimeError(f"refusing to use {dest}")


def snapshot_path_for(role: str) -> Path:
    base = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or ".")
    name = {
        "e2e": "hearth-user-install-e2e.json",
        "scenarios": "hearth-user-install-scenarios.json",
    }.get(role, "hearth-user-install.json")
    return base / name


def _script() -> Path:
    return Path(__file__).resolve().parent / "ci-protect-user-install.ps1"


def _run_mode(mode: str, path: Path) -> None:
    import ci_winproc

    script = _script()
    if not script.is_file():
        raise RuntimeError(f"missing {script}")
    completed = ci_winproc.run(
        [
            "pwsh",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
            "-Mode",
            mode,
            "-SnapshotPath",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    text = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if text:
        print(text, flush=True)
    if completed.returncode != 0:
        raise RuntimeError(f"user-install {mode} failed ({completed.returncode})")


def protect_user_install(mode: str, role: str = "e2e") -> Path:
    """Snapshot or verify. An existing HEARTH_USER_SNAPSHOT makes Snapshot a no-op."""
    action = "Snapshot" if mode.lower() == "snapshot" else "Verify"
    existing = os.environ.get("HEARTH_USER_SNAPSHOT", "").strip()
    if action == "Snapshot" and existing:
        return Path(existing)
    path = Path(existing) if existing else snapshot_path_for(role)
    if not existing:
        os.environ["HEARTH_USER_SNAPSHOT"] = str(path)
    _run_mode(action, path)
    return path
