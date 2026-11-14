"""Read-only guard for the interactive user's Hearth install.

CI may snapshot and compare that install. It must not kill, uninstall, or
rewrite it. Process starts go through ci_winproc so no window appears.
"""

from __future__ import annotations

import hashlib
import json
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


# The running app rewrites these while it is up. Peer beacons refresh
# known_peers, and the update loop refreshes last_update_check.
_LIVE_STATE_KEYS = ("known_peers", "last_update_check")
# state.json is replaced via state.tmp. A snapshot can see that file mid-save.
_SKIP_PROFILE_NAMES = {"state.tmp"}


def stable_state_text(raw: str) -> str:
    """State JSON without fields the live app rewrites on its own."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("state is not an object")
    for key in _LIVE_STATE_KEYS:
        data.pop(key, None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def snapshot_profile(root: Path) -> dict:
    """File sizes and hashes, plus the stable part of state.json.

    A same-size rewrite can keep the old timestamp on a coarse filesystem, so
    identity files are compared by hash.
    """
    files: list[tuple[str, int, str]] = []
    if root.exists():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix().lower()
            if Path(rel).name in _SKIP_PROFILE_NAMES:
                continue
            data = path.read_bytes()
            files.append((rel, len(data), hashlib.sha256(data).hexdigest()))
    files.sort()
    state_text = None
    state_path = root / "state.json"
    if state_path.is_file():
        state_text = stable_state_text(state_path.read_text(encoding="utf-8"))
    return {"files": files, "state": state_text}


def profile_changes(before: dict, root: Path) -> list[str]:
    """What CI must not have done to the real profile.

    known_peers and last_update_check may change while Hearth is open.
    hearth.log may grow. Everything else, including device.key, is compared
    by hash so a same-size rewrite is still a failure.
    """
    after = snapshot_profile(root)
    before_files = {rel: (size, digest) for rel, size, digest in before["files"]}
    after_files = {rel: (size, digest) for rel, size, digest in after["files"]}
    reasons: list[str] = []
    for rel in sorted(set(before_files) | set(after_files)):
        if rel == "state.json":
            continue
        if rel not in before_files:
            reasons.append(f"real ~/.hearth file appeared: {rel}")
            continue
        if rel not in after_files:
            reasons.append(f"real ~/.hearth file disappeared: {rel}")
            continue
        old_size, old_digest = before_files[rel]
        new_size, new_digest = after_files[rel]
        if rel == "hearth.log":
            if new_size < old_size:
                reasons.append("real ~/.hearth hearth.log shrank")
            continue
        if old_digest != new_digest:
            reasons.append(f"real ~/.hearth file changed: {rel}")
    before_state = "state.json" in before_files
    after_state = "state.json" in after_files
    if before_state and not after_state:
        reasons.append("real ~/.hearth file disappeared: state.json")
    elif after_state and not before_state:
        reasons.append("real ~/.hearth file appeared: state.json")
    elif before_state and after_state and before.get("state") != after.get("state"):
        reasons.append("real ~/.hearth state.json user data changed")
    return reasons


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
