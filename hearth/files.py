"""Safe path resolution for shared folders."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any


def resolve_share_path(shares: list[dict[str, Any]], share_id: str, rel: str = "") -> tuple[dict[str, Any], Path]:
    share = next((s for s in shares if s["id"] == share_id), None)
    if not share:
        raise FileNotFoundError("Unknown share")
    root = Path(share["path"]).resolve()
    if not root.exists():
        raise FileNotFoundError("Share folder is missing on this PC")
    target = (root / rel).resolve() if rel else root
    if root != target and root not in target.parents:
        raise PermissionError("Path escapes the shared folder")
    return share, target


def list_dir(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.is_dir():
        return items
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        items.append(
            {
                "name": child.name,
                "dir": child.is_dir(),
                "size": 0 if child.is_dir() else stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return items


def guess_type(path: Path) -> str:
    typ, _ = mimetypes.guess_type(str(path))
    return typ or "application/octet-stream"
