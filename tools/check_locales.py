#!/usr/bin/env python3
"""Fail when a locale catalog drifts from English.

Checks syntax, the key set, placeholders, and the untranslated sign-off line.
English text is the key, so a missing translation falls back to English.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hearth.i18n import check_catalogs  # noqa: E402


def main() -> int:
    errors = check_catalogs()
    if errors:
        for err in errors:
            print(err)
        return 1
    print("locale catalogs match English")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
