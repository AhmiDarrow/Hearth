from __future__ import annotations

from pathlib import Path

import pytest

from hearth.files import list_local_dirs, resolve_share_path, resolve_user_path
from hearth.netutil import candidate_probe_ips, parse_host


def test_share_path_blocks_escape(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    shares = [{"id": "abc", "path": str(shared), "label": "Shared", "mode": "read"}]
    _, target = resolve_share_path(shares, "abc", "ok")
    assert target == (shared / "ok").resolve() or not (shared / "ok").exists()
    with pytest.raises(PermissionError):
        resolve_share_path(shares, "abc", "../secret.txt")
    with pytest.raises(FileNotFoundError):
        resolve_share_path(shares, "missing", "")


def test_resolve_user_path_home_relative(tmp_path: Path, monkeypatch) -> None:
    documents = tmp_path / "Documents"
    documents.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert resolve_user_path("Documents") == documents


def test_list_local_dirs_home(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Pictures").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    listing = list_local_dirs("")
    paths = {item["path"] for item in listing["items"]}
    assert str(tmp_path) in paths


def test_parse_host() -> None:
    assert parse_host("192.168.0.12") == ("192.168.0.12", 7474)
    assert parse_host("192.168.0.12:7475") == ("192.168.0.12", 7475)
    with pytest.raises(ValueError):
        parse_host("  ")


def test_candidate_probes_include_manual_hosts() -> None:
    targets = candidate_probe_ips(["192.168.1.10"], ["10.0.0.5:7474"])
    assert "10.0.0.5" in targets
    assert "192.168.1.10" not in targets
    assert "192.168.1.1" in targets
