"""Wait out Inno Setup's second-phase uninstaller for one isolated directory.

unins000.exe copies itself under %TEMP% and exits. The copy's command line
still names the original unins000.exe (/SECONDPHASE=<that path>). The copy
restores the shared uninstall key in DeinitializeUninstall, then exits.
Returning as soon as the first process exits samples that key mid-restore.

This module never kills a process and never matches by image name alone.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

ProcessRow = Mapping[str, object]
Lister = Callable[[], Iterable[ProcessRow]]


def _field(row: ProcessRow, *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name])
    return ""


def _norm_command(command: str) -> str:
    return os.path.normcase(command.replace("/", "\\"))


def command_names_dest(command: str, dest: Path) -> bool:
    """True when command mentions dest itself, not a longer directory name."""
    if not command:
        return False
    needle = os.path.normcase(os.path.normpath(str(dest))).rstrip("\\")
    # A drive root would match every path on that drive.
    if len(needle) < 4 or needle.endswith(":"):
        return False
    hay = _norm_command(command)
    start = 0
    while True:
        index = hay.find(needle, start)
        if index < 0:
            return False
        after = index + len(needle)
        extends = after < len(hay) and hay[after] not in '\\"\' \t'
        before_ok = index == 0 or hay[index - 1] in ' "\'\t'
        if before_ok and not extends:
            return True
        start = index + 1


def path_is_under(path: str, dest: Path) -> bool:
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
        base = Path(dest).resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents


def process_targets_install(
    dest: Path,
    *,
    executable_path: str = "",
    command_line: str = "",
) -> bool:
    """True for the isolated copy, or for Inno's temp process that names it."""
    if path_is_under(executable_path, dest):
        return True
    return command_names_dest(command_line, dest)


def _ensure_pack_path() -> None:
    pack = str(Path(__file__).resolve().parent)
    if pack not in sys.path:
        sys.path.insert(0, pack)


def list_win32_uninstall_processes() -> list[dict[str, str]]:
    """unins000.exe and Hearth.exe rows. Other processes are not read.

    Command lines stay in the returned rows so the caller can match a path.
    Callers must not log them.
    """
    if sys.platform != "win32":
        return []
    _ensure_pack_path()
    import ci_winproc

    script = (
        "$ProgressPreference = 'SilentlyContinue'; "
        "$WarningPreference = 'SilentlyContinue'; "
        "$rows = @(Get-CimInstance Win32_Process -Filter "
        "\"Name = 'unins000.exe' OR Name = 'Hearth.exe'\" | "
        "Select-Object Name, ProcessId, ExecutablePath, CommandLine); "
        "if ($rows.Count -eq 0) { return }; "
        "$rows | ConvertTo-Json -Compress"
    )
    completed = ci_winproc.run(
        ["pwsh", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError("could not list uninstall processes")
    raw = (completed.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("could not read uninstall process list") from exc
    if isinstance(data, dict):
        data = [data]
    found: list[dict[str, str]] = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        found.append(
            {
                "name": str(row.get("Name") or ""),
                "pid": str(row.get("ProcessId") or ""),
                "executable_path": str(row.get("ExecutablePath") or ""),
                "command_line": str(row.get("CommandLine") or ""),
            }
        )
    return found


def wait_for_isolated_uninstall(
    dest: Path,
    *,
    timeout: float = 120.0,
    grace: float = 3.0,
    settle: float = 0.5,
    interval: float = 0.25,
    list_processes: Lister | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] | None = None,
) -> None:
    """Block until no listed process targets dest. Do not kill anything.

    grace covers the gap after the first unins000.exe exits and before the
    temp copy shows up. A process that never exits raises TimeoutError so the
    caller fails the uninstall instead of checking the user install early.
    """
    _ensure_pack_path()
    from ci_protect_user import assert_isolated_dest

    assert_isolated_dest(Path(dest))
    dest = Path(dest)
    if list_processes is None:
        list_processes = list_win32_uninstall_processes
    deadline = clock() + timeout
    grace_deadline = clock() + grace
    saw = False
    announced = False

    def matched() -> list[ProcessRow]:
        rows = []
        for row in list_processes() or []:
            if not isinstance(row, Mapping):
                continue
            if process_targets_install(
                dest,
                executable_path=_field(row, "executable_path", "ExecutablePath", "path"),
                command_line=_field(row, "command_line", "CommandLine"),
            ):
                rows.append(row)
        return rows

    def pids(rows: list[ProcessRow]) -> str:
        return ", ".join(_field(row, "pid", "ProcessId") or "?" for row in rows)

    while True:
        live = matched()
        now = clock()
        if live:
            saw = True
            if not announced and log is not None:
                log(f"waiting for isolated uninstaller of {dest} pid={pids(live)}")
                announced = True
            if now >= deadline:
                raise TimeoutError(
                    f"isolated uninstaller for {dest} still running after {timeout:g}s (pids {pids(live)})"
                )
            sleep(interval)
            continue
        if not saw and now < grace_deadline:
            sleep(interval)
            continue
        if settle > 0:
            sleep(settle)
            live = matched()
            if live:
                saw = True
                if not announced and log is not None:
                    log(f"waiting for isolated uninstaller of {dest} pid={pids(live)}")
                    announced = True
                continue
        if announced and log is not None:
            log(f"isolated uninstaller of {dest} exited")
        return
