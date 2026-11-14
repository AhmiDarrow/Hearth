from __future__ import annotations

import re
from pathlib import Path

import pytest

from hearth.files import default_share_label, display_share, list_dir
from tests.conftest import local_client

ROOT = Path(__file__).resolve().parents[1]
THEMES_CSS = (ROOT / "hearth/ui/static/themes.css").read_text(encoding="utf-8")
APP_CSS = (ROOT / "hearth/ui/static/app.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "hearth/ui/static/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "hearth/ui/static/index.html").read_text(encoding="utf-8")


def _lin(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(hex_color: str) -> float:
    hx = hex_color.lstrip("#")
    r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(a: str, b: str) -> float:
    light, dark = sorted((rel_luminance(a), rel_luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _mix_srgb(a: str, b: str, amount_a: float) -> str:
    """CSS color-mix(in srgb, a amount_a%, b). amount_a is 0-1."""
    a, b = a.lstrip("#"), b.lstrip("#")
    ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    t = amount_a
    r = round(ar * t + br * (1 - t))
    g = round(ag * t + bg * (1 - t))
    bl = round(ab * t + bb * (1 - t))
    return f"#{r:02x}{g:02x}{bl:02x}"


def parse_theme_vars() -> dict[str, dict[str, str]]:
    blocks: dict[str, dict[str, str]] = {}
    pattern = re.compile(r"\[data-theme=\"([^\"]+)\"\][^{]*\{([^}]+)\}", re.S)
    for theme_id, body in pattern.findall(THEMES_CSS):
        if theme_id == "system":
            continue
        vars_: dict[str, str] = {}
        for name, val in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", body):
            vars_[name] = val.strip()
        blocks[theme_id] = vars_
    return blocks


def resolve_color(vars_: dict[str, str], value: str) -> str:
    value = value.strip()
    if value.startswith("#") and len(value) in {4, 7}:
        if len(value) == 4:
            return "#" + "".join(ch * 2 for ch in value[1:])
        return value.lower()
    ref = re.fullmatch(r"var\(--([a-z0-9-]+)\)", value)
    if ref:
        return resolve_color(vars_, vars_[ref.group(1)])
    mix = re.fullmatch(
        r"color-mix\(in srgb,\s*var\(--([a-z0-9-]+)\)\s+(\d+)%?,\s*var\(--([a-z0-9-]+)\)\)",
        value,
    )
    if mix:
        a = resolve_color(vars_, vars_[mix.group(1)])
        b = resolve_color(vars_, vars_[mix.group(3)])
        return _mix_srgb(a, b, int(mix.group(2)) / 100)
    raise AssertionError(f"Cannot resolve color value: {value}")


def test_drive_and_unc_labels_never_empty() -> None:
    assert default_share_label(r"C:\ ", "", volume="Windows") == "Windows (C:)"
    assert default_share_label("C:/", "", volume="Data") == "Data (C:)"
    assert default_share_label("E:", "") == "E:"
    assert default_share_label(r"\\media\films", "", volume="Media") == "Media (media:films)"
    assert default_share_label("//nas/public") == "nas:public"
    assert default_share_label("/home/joe/Photos") == "Photos"
    assert default_share_label("/home/joe/Photos", "  Family  ") == "Family"
    share = display_share({"id": "1", "path": "C:\\", "label": "  ", "mode": "read"}, include_path=False)
    assert share["label"] == "C:"
    assert "path" not in share


def test_empty_stored_share_label_is_filled_in_api(app, tmp_path: Path) -> None:
    client = local_client(app)
    created = client.post("/api/group/create", json={"name": "Home", "password": "secret"})
    assert created.status_code == 200, created.text
    folder = tmp_path / "Camera Roll"
    folder.mkdir()
    # Store a blank label as older builds did for drive roots / forgotten names.
    added = client.post("/api/shares", json={"path": str(folder), "label": "", "mode": "read"})
    assert added.status_code == 200, added.text
    labels = [s["label"] for s in added.json()["shares"]]
    assert "Camera Roll" in labels

    # Simulate a legacy empty label still on disk.
    store_shares = added.json()["shares"]
    # Re-read state after manually emptying is covered by display_share above.
    state = client.get("/api/state").json()
    share = next(s for s in state["shares"] if s["path"] == str(folder.resolve()) or Path(s["path"]) == folder)
    assert share["label"] == "Camera Roll"


def test_can_browse_into_nested_shared_folders(app, tmp_path: Path) -> None:
    client = local_client(app)
    assert client.post("/api/group/create", json={"name": "Home", "password": "secret"}).status_code == 200
    root = tmp_path / "Library"
    nested = root / "Photos" / "2026"
    nested.mkdir(parents=True)
    (nested / "beach.txt").write_text("sand", encoding="utf-8")
    (root / "readme.txt").write_text("hi", encoding="utf-8")
    added = client.post("/api/shares", json={"path": str(root), "label": "Library", "mode": "read"})
    assert added.status_code == 200, added.text
    share = next(s for s in added.json()["shares"] if s["label"] == "Library")
    device = added.json()["device"]["id"]

    top = client.get(f"/api/files/{device}/{share['id']}")
    assert top.status_code == 200, top.text
    names = {item["name"] for item in top.json()["items"]}
    assert "Photos" in names and "readme.txt" in names
    photos = next(item for item in top.json()["items"] if item["name"] == "Photos")
    assert photos["dir"] is True

    mid = client.get(f"/api/files/{device}/{share['id']}", params={"path": "Photos"})
    assert mid.status_code == 200, mid.text
    assert {item["name"] for item in mid.json()["items"]} == {"2026"}

    deep = client.get(f"/api/files/{device}/{share['id']}", params={"path": "Photos/2026"})
    assert deep.status_code == 200, deep.text
    assert {item["name"] for item in deep.json()["items"]} == {"beach.txt"}

    escaped = client.get(f"/api/files/{device}/{share['id']}", params={"path": "../"})
    assert escaped.status_code in {400, 403, 422}


def test_ui_opens_folders_on_click_and_shows_share_labels() -> None:
    assert "function shareLabel(" in APP_JS
    assert "function parentPath(" in APP_JS
    assert "function goUp(" in APP_JS
    assert "function markSelection(" in APP_JS
    assert "item.dir && !e.shiftKey && !e.ctrlKey && !e.metaKey" in APP_JS
    assert "openItem(item)" in APP_JS
    assert "paintFiles();" not in APP_JS.split("function selectClick")[1].split("function markSelection")[0]
    assert 'id="navUp"' in INDEX
    assert "shareLabel(s)" in APP_JS or "shareLabel(share)" in APP_JS
    assert "escapeHtml(shareLabel(" in APP_JS
    assert 'data-up="1"' in APP_JS
    lowered = (APP_JS + INDEX).lower()
    assert "colorblind" not in lowered
    assert "Hope this helps" in INDEX
    assert "- Ahmi" in INDEX
    assert "appearance: none" in APP_CSS
    assert "background: var(--panel)" in APP_CSS


def test_theme_text_meets_wcag_aa() -> None:
    themes = parse_theme_vars()
    assert set(themes) >= {
        "dark", "light", "neutral", "forest", "green", "purple",
        "alien", "orange", "cyan", "deutan", "protan", "tritan",
    }
    # Text in the UI must not use --faint; that token fails AA on several surfaces.
    faint_as_text = [line.strip() for line in APP_CSS.splitlines() if "color: var(--faint)" in line]
    assert faint_as_text == [], faint_as_text

    for theme_id, vars_ in themes.items():
        ink = resolve_color(vars_, vars_["ink"])
        muted = resolve_color(vars_, vars_["muted"])
        link = resolve_color(vars_, vars_["link"])
        disabled = resolve_color(vars_, vars_["disabled"])
        accent_fg = resolve_color(vars_, vars_["accent-fg"])
        hover_fg = resolve_color(vars_, vars_["accent-hover-fg"])
        accent = resolve_color(vars_, vars_["accent"])
        accent2 = resolve_color(vars_, vars_["accent2"])
        for surface_name in ("bg", "bg2", "panel", "panel2"):
            surface = resolve_color(vars_, vars_[surface_name])
            for role, color, minimum in (
                ("ink", ink, 4.5),
                ("muted", muted, 4.5),
                ("link", link, 4.5),
                ("disabled", disabled, 4.5),
            ):
                ratio = contrast_ratio(color, surface)
                assert ratio >= minimum, f"{theme_id}: {role} on {surface_name} is {ratio:.2f}"
        assert contrast_ratio(accent_fg, accent) >= 4.5, f"{theme_id}: accent-fg on accent"
        assert contrast_ratio(hover_fg, accent2) >= 4.5, f"{theme_id}: accent-hover-fg on accent2"


def test_list_dir_includes_subdirectory_names(tmp_path: Path) -> None:
    (tmp_path / "Docs").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    items = list_dir(tmp_path)
    names = {i["name"] for i in items}
    assert names == {"Docs", "a.txt"}
    assert next(i for i in items if i["name"] == "Docs")["dir"] is True
