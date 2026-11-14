from __future__ import annotations

from pathlib import Path

from hearth import __version__
from hearth.folderpick import pick_folder

from tests.conftest import local_client


REMEDY_SOURCE = "AhmiDarrow/RemedyAI desktop/src/themes.ts"
REMEDY_BLOB = "0895f377026d449687cf055bdc6ca9378f2d7c16"

# Exact hex tokens from RemedyAI themes.ts — do not invent or loosen these.
REMEDY_HEXES = {
    "dark": ["#0c0c14", "#14141f", "#1c1c2a", "#2a2a3d", "#8b5cf6", "#7c3aed", "#f0f0f5", "#a8a8c0", "#7a7a96", "#34d399", "#f87171", "#fbbf24"],
    "light": ["#f4f4f8", "#ffffff", "#e8e8ef", "#a8a8b8", "#7c3aed", "#6d28d9", "#14141f", "#3d3d52", "#5c5c72", "#15803d", "#b91c1c", "#b45309"],
    "neutral": ["#0f1115", "#161a20", "#1e2430", "#2e3644", "#94a3b8", "#cbd5e1", "#e2e8f0", "#64748b"],
    "forest": ["#0a0e0b", "#121812", "#1a221c", "#2a352c", "#4d7a5a", "#3d6349", "#e6ebe7", "#9aa89e", "#6b7870", "#5a8f6a", "#b87a7a", "#a89058"],
    "green": ["#0a1410", "#0f1f18", "#163026", "#1f4a38", "#34d399", "#10b981", "#d8ebe2", "#8fb8a4", "#5f8574", "#6ee7b7", "#fca5a5", "#fcd34d"],
    "purple": ["#100a16", "#1a1224", "#261a36", "#3d2a55", "#c084fc", "#a855f7", "#e4dff0", "#b0a0c8", "#7d7096"],
    "alien": ["#07040f", "#0f081a", "#1a0f2e", "#3b1d66", "#b026ff", "#9d00ff", "#f2e8ff", "#c4a0e8", "#8a6aad", "#5eead4", "#ff6b9d", "#f0abfc"],
    "orange": ["#14100a", "#1f1810", "#2e2418", "#4a3a24", "#fb923c", "#f97316", "#efe6da", "#c4a88a", "#8f7a60"],
    "cyan": ["#0a1216", "#0f1c22", "#163038", "#1e4a55", "#22d3ee", "#06b6d4", "#d8eef2", "#8fb8c2", "#5f8790"],
    "deutan": ["#0b0f14", "#121821", "#1a2330", "#2c3a4a", "#1d7fc4", "#14689f", "#e9f0f6", "#a9b9c7", "#7b8b99", "#2fb5a0", "#d9601c", "#f2d24b"],
    "protan": ["#0d1013", "#151a1f", "#1e252c", "#303a44", "#0f8f75", "#0b7360", "#eef1f4", "#adb8c2", "#7f8a94", "#4aa8e0", "#f0803a", "#f5e05f"],
    "tritan": ["#12100f", "#1b1817", "#262120", "#3a3231", "#d63a72", "#b62a5d", "#f3efee", "#c0b6b3", "#8e8481", "#3fae4a", "#cf3b30", "#e8842a"],
}

THEME_IDS = (
    "system", "light", "dark", "neutral", "forest", "green", "purple",
    "alien", "orange", "cyan", "deutan", "protan", "tritan",
)
THEME_NAMES = (
    "System", "Light", "Dark", "Neutral Dark", "Dark Forest", "Emerald",
    "Amethyst", "Dark Purple", "Amber", "Ocean",
    "Deutan", "Protan", "Tritan",
)


def test_version_is_0313() -> None:
    assert __version__ == "0.3.13"


def test_share_modal_has_browse_and_path_field() -> None:
    html = (Path(__file__).resolve().parents[1] / "hearth/ui/static/index.html").read_text(encoding="utf-8")
    assert 'id="browseFolder"' in html
    assert "Browse…" in html
    assert 'id="sharePathInput"' in html
    assert 'id="shareModalOk"' in html
    assert "Share this folder" in html
    css = (Path(__file__).resolve().parents[1] / "hearth/ui/static/app.css").read_text(encoding="utf-8")
    assert "translateY(-1px)" not in css
    assert ".modal-body" in css
    assert ".modal-actions" in css
    assert "text-overflow: ellipsis" in css


def test_app_js_uses_remedy_themes_and_native_browse() -> None:
    js = (Path(__file__).resolve().parents[1] / "hearth/ui/static/app.js").read_text(encoding="utf-8")
    for name in THEME_NAMES:
        assert name in js
    for theme_id in THEME_IDS:
        assert f'id: "{theme_id}"' in js
    assert "grove-dark" not in js
    assert "studio-dark" not in js
    assert "remedy-blur" not in js
    assert "pick_folder" in js
    assert "browseNativeFolder" in js
    assert "data-browse-share" in js
    assert "window.openShareModal" in js
    assert 'KNOWN_THEMES.has(theme) ? theme : "system"' in js
    lowered = js.lower()
    assert "colorblind" not in lowered
    assert "color blind" not in lowered
    html = (Path(__file__).resolve().parents[1] / "hearth/ui/static/index.html").read_text(encoding="utf-8").lower()
    assert "colorblind" not in html
    assert "color blind" not in html


def test_themes_css_matches_remedyai_palettes() -> None:
    css = (Path(__file__).resolve().parents[1] / "hearth/ui/static/themes.css").read_text(encoding="utf-8")
    assert REMEDY_SOURCE in css
    assert REMEDY_BLOB in css
    for theme_id, hexes in REMEDY_HEXES.items():
        assert f'[data-theme="{theme_id}"]' in css or theme_id in ("dark", "light")
        for hexv in hexes:
            assert hexv in css, f"{theme_id} missing {hexv}"
    # System reuses the light/dark palettes rather than inventing a third one.
    assert '[data-theme="system"][data-scheme="dark"]' in css
    assert '[data-theme="system"][data-scheme="light"]' in css


def test_folderpick_is_a_noop_off_windows() -> None:
    import sys

    if sys.platform == "win32":
        return
    assert pick_folder("/tmp") is None
    source = (Path(__file__).resolve().parents[1] / "hearth/folderpick.py").read_text(encoding="utf-8")
    assert "IFileOpenDialog" in source
    assert "FOS_PICKFOLDERS" in source
    assert "subprocess" not in source


def test_validate_path_accepts_real_folder_and_rejects_junk(app, tmp_path: Path) -> None:
    client = local_client(app)
    folder = tmp_path / "Photos"
    folder.mkdir()
    ok = client.post("/api/path/validate", json={"path": str(folder)})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ok"] is True
    assert body["name"] == "Photos"
    assert Path(body["path"]) == folder

    missing = client.post("/api/path/validate", json={"path": str(tmp_path / "no-such-folder")})
    assert missing.status_code == 400

    file_path = tmp_path / "notes.txt"
    file_path.write_text("hi", encoding="utf-8")
    as_file = client.post("/api/path/validate", json={"path": str(file_path)})
    assert as_file.status_code == 400

    empty = client.post("/api/path/validate", json={"path": "  "})
    assert empty.status_code == 400


def test_typed_path_still_shares(app, tmp_path: Path) -> None:
    client = local_client(app)
    created = client.post("/api/group/create", json={"name": "Home", "password": "secret"})
    assert created.status_code == 200, created.text
    folder = tmp_path / "Docs"
    folder.mkdir()
    shared = client.post("/api/shares", json={"path": str(folder), "label": "Docs", "mode": "read"})
    assert shared.status_code == 200, shared.text
    shares = shared.json()["shares"]
    assert any(s["label"] == "Docs" and Path(s["path"]) == folder for s in shares)
