"""Rewrite hearth.__version__ in a file without touching anything else."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: ci_stamp_version.py <path-to-__init__.py> <version>")
    path = Path(sys.argv[1])
    version = sys.argv[2]
    text = path.read_text(encoding="utf-8")
    updated, n = re.subn(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', text, count=1)
    if n != 1:
        raise SystemExit(f"could not stamp __version__ in {path}")
    path.write_text(updated, encoding="utf-8")
    print(version, end="")


if __name__ == "__main__":
    main()
