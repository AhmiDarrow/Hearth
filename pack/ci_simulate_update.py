"""CI: run the in-tree updater and a v0.3.4 diagnostic against signed assets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hearth import __version__  # noqa: E402
from hearth.update import (  # noqa: E402
    CERT_THUMBPRINT,
    RELEASES_URL,
    authenticode_info,
    check_for_update,
    download_and_verify,
    parse_sha256sums,
    verify_authenticode,
    verify_installer,
)


def _fetch_v034_sources(dest: Path) -> None:
    """Materialize the v0.3.4 updater modules for a diagnostic import."""
    pkg = dest / "hearth"
    pkg.mkdir(parents=True, exist_ok=True)
    # Shallow CI checkouts may not have the tag yet.
    fetch = subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", "tag", "v0.3.4"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        print("git fetch v0.3.4:", fetch.stderr.strip() or fetch.stdout.strip())
    for rel in ("hearth/__init__.py", "hearth/authenticode.py", "hearth/update.py"):
        shown = subprocess.run(
            ["git", "show", f"v0.3.4:{rel}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        (dest / rel).write_bytes(shown.stdout)


def _simulate(setup: Path, sums_path: Path, from_version: str, require_authenticode: bool) -> None:
    sums_text = sums_path.read_text(encoding="utf-8")
    sums = parse_sha256sums(sums_text)
    expected = sums.get("HearthSetup.exe")
    if not expected:
        raise SystemExit("SHA256SUMS.txt does not list HearthSetup.exe")
    verify_installer(setup, expected, require_authenticode=require_authenticode)
    print(f"verify_installer: OK (require_authenticode={require_authenticode})")

    setup_bytes = setup.read_bytes()
    release = {
        "tag_name": f"v{__version__}",
        "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/AhmiDarrow/Hearth/releases/tag/v{__version__}",
        "assets": [
            {
                "name": "HearthSetup.exe",
                "browser_download_url": "https://example.invalid/HearthSetup.exe",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://example.invalid/SHA256SUMS.txt",
            },
        ],
    }

    def getter(url: str, timeout: float = 30.0) -> bytes:
        del timeout
        if url.rstrip("/").endswith("/releases") or url == RELEASES_URL:
            return json.dumps([release]).encode("utf-8")
        if url.endswith("SHA256SUMS.txt"):
            return sums_text.encode("utf-8")
        if url.endswith("HearthSetup.exe"):
            return setup_bytes
        raise RuntimeError(f"unexpected url {url}")

    result = check_for_update(from_version, getter=getter)
    if not result.get("available"):
        raise SystemExit(f"a v{from_version} client would not see this release: {result}")
    if result.get("version") != __version__:
        raise SystemExit(f"unexpected offered version: {result}")
    dest = setup.parent / "_update_test"
    dest.mkdir(parents=True, exist_ok=True)
    downloaded = download_and_verify(
        result,
        dest_dir=dest,
        getter=getter,
        require_authenticode=require_authenticode,
    )
    print(
        f"simulated v{from_version} -> v{__version__} download_and_verify: OK -> {downloaded}"
    )


def _v034_diagnostic(setup: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="hearth-v034-") as raw:
        dest = Path(raw)
        try:
            _fetch_v034_sources(dest)
        except (subprocess.CalledProcessError, OSError) as exc:
            print("skipping v0.3.4 diagnostic; could not load v0.3.4 sources:", exc)
            return
        # Import the historical modules under a unique name.
        sys.path.insert(0, str(dest))
        # Drop any already-imported in-tree hearth.* so 0.3.4 code loads.
        dropped = [name for name in list(sys.modules) if name == "hearth" or name.startswith("hearth.")]
        saved = {name: sys.modules.pop(name) for name in dropped}
        try:
            from hearth.update import (  # type: ignore
                authenticode_info as info_034,
                verify_authenticode as verify_034,
            )
            info = info_034(setup)
            print("v0.3.4 authenticode_info=", info)
            try:
                verify_034(setup, CERT_THUMBPRINT)
                print(
                    "v0.3.4 verify_authenticode: OK "
                    "(this runner extracted the thumbprint)"
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    "v0.3.4 verify_authenticode: FAILED. This is the known "
                    "v0.3.4 ctypes bug when 64-bit pointers are truncated:",
                    exc,
                )
                traceback.print_exc()
        finally:
            for name in list(sys.modules):
                if name == "hearth" or name.startswith("hearth."):
                    sys.modules.pop(name, None)
            sys.modules.update(saved)
            if str(dest) in sys.path:
                sys.path.remove(str(dest))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", required=True)
    parser.add_argument("--sums", required=True)
    parser.add_argument("--from-version", default="0.3.4")
    parser.add_argument(
        "--require-authenticode",
        action=argparse.BooleanOptionalAction,
        default=sys.platform == "win32",
    )
    args = parser.parse_args()
    setup = Path(args.setup).resolve()
    sums_path = Path(args.sums).resolve()
    if not setup.is_file():
        raise SystemExit(f"missing installer: {setup}")
    if not sums_path.is_file():
        raise SystemExit(f"missing SHA256SUMS.txt: {sums_path}")

    if sys.platform == "win32":
        info = authenticode_info(setup)
        print("in-tree authenticode_info=", info)
        verify_authenticode(setup, CERT_THUMBPRINT)
        print("in-tree verify_authenticode: OK")

    _simulate(setup, sums_path, args.from_version, args.require_authenticode)

    if sys.platform == "win32":
        _v034_diagnostic(setup)


if __name__ == "__main__":
    main()
