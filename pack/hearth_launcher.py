"""PyInstaller entry point.

The frozen exe must not use hearth/__main__.py as its script: PyInstaller
runs the entry script as the top-level __main__ module, so relative imports
like `from .app import create_app` raise
"attempted relative import with no known parent package".

This tiny wrapper loads the real app with an absolute import.
"""

from __future__ import annotations

from hearth.__main__ import ensure_stdio, main

if __name__ == "__main__":
    ensure_stdio()
    raise SystemExit(main() or 0)
