from __future__ import annotations

import os
from pathlib import Path

import pytest

from hearth.app import create_app
from hearth.state import Store

os.environ.setdefault("HEARTH_NO_UPDATE", "1")


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "hearth-data")


@pytest.fixture()
def app(store: Store):
    return create_app(store, 17474)
