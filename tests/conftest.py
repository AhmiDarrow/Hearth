from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hearth.app import create_app
from hearth.state import Store

os.environ.setdefault("HEARTH_NO_UPDATE", "1")


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "hearth-data")


@pytest.fixture()
def app(store: Store):
    return create_app(store, 17474)


def local_client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345))
