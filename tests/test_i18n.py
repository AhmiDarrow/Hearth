"""Locale catalogs, language detection, and the settings picker."""

from __future__ import annotations

import json
from pathlib import Path

from hearth.i18n import (
    SUPPORTED,
    activate,
    check_catalogs,
    current,
    detect_system_language,
    normalize,
    resolve_language,
    t,
)
from hearth.state import Store
from tests.conftest import local_client

ROOT = Path(__file__).resolve().parents[1]
SIGNOFF = '"Hope this helps" - Ahmi'


def test_catalogs_match_english_keys_and_placeholders() -> None:
    errors = check_catalogs()
    assert errors == []
    activate("es")
    assert t("Clear downloads") == "Borrar descargas"
    assert t("Settings") == "Ajustes"
    assert t("this key is not in the catalog") == "this key is not in the catalog"
    assert t("Saved to {path}", path="D:\\docs") == "Guardado en D:\\docs"
    for code in SUPPORTED:
        activate(code)
        assert t(SIGNOFF) == SIGNOFF
        assert t("Quit")
    activate("en")
    assert current() == "en"
    assert t("Clear downloads") == "Clear downloads"


def test_normalize_and_resolve_order(monkeypatch) -> None:
    assert normalize("Deutsch") == "de"
    assert normalize("pt_BR") == "pt-BR"
    assert normalize("日本語") == "ja"
    assert normalize("zh-TW") is None
    assert normalize("zh-Hant") is None
    assert normalize("en") == "en"
    monkeypatch.setenv("HEARTH_LANG", "ja")
    assert resolve_language("es") == "es"
    assert resolve_language(None) == "ja"
    assert resolve_language("") == "ja"
    monkeypatch.setenv("HEARTH_LANG", "not-a-language")
    monkeypatch.setattr("hearth.i18n.detect_system_language", lambda: "pl")
    assert resolve_language(None) == "pl"


def test_traditional_chinese_does_not_become_simplified(monkeypatch) -> None:
    monkeypatch.setattr("hearth.i18n._windows_locale_name", lambda: "zh-TW")
    monkeypatch.setattr("hearth.i18n._windows_ui_language", lambda: "zh")
    assert detect_system_language() == "en"


def test_language_post_keeps_the_device_name(app, store, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HEARTH_STARTUP_DIR", str(tmp_path))
    store.device_name = "Living Room"
    store.save()
    client = local_client(app)
    res = client.post("/api/settings", json={"language": "es"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["device"]["name"] == "Living Room"
    assert body["settings"]["language"] == "es"
    assert body["settings"]["locale"] == "es"
    names = [item["name"] for item in body["settings"]["languages"]]
    assert names[0] == "English"
    assert "Deutsch" in names
    assert "Português (Brasil)" in names
    assert "日本語" in names
    assert "简体中文" in names
    assert store.data["language"] == "es"
    assert store.device_name == "Living Room"
    catalog = client.get("/api/i18n").json()
    assert catalog["language"] == "es"
    assert catalog["catalog"]["Home group"] == "Grupo del hogar"
    assert catalog["catalog"][SIGNOFF] == SIGNOFF


def test_unknown_language_is_rejected_in_the_active_language(app, store) -> None:
    client = local_client(app)
    assert client.post("/api/settings", json={"language": "de"}).status_code == 200
    bad = client.post("/api/settings", json={"language": "nope"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Unbekannte Sprache"
    assert store.data["language"] == "de"


def test_reset_settings_does_not_clear_language(app, store, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HEARTH_STARTUP_DIR", str(tmp_path))
    client = local_client(app)
    saved = client.post("/api/settings", json={"language": "ja", "theme": "dark"})
    assert saved.status_code == 200, saved.text
    reset = client.post("/api/settings/reset")
    assert reset.status_code == 200, reset.text
    body = reset.json()
    assert body["settings"]["language"] == "ja"
    assert body["theme"] == "system"
    assert store.data["language"] == "ja"
    assert client.get("/api/i18n").json()["catalog"]["Quit"] == "終了"


def test_detecting_a_language_does_not_rewrite_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HEARTH_LANG", "de")
    root = tmp_path / "data"
    root.mkdir()
    payload = {
        "device_name": "PC",
        "theme": "system",
        "group": None,
        "known_peers": {},
        "shares": [],
        "chat": [],
        "bridge": False,
        "manual_hosts": [],
        "auto_check_updates": True,
        "download_folder": "",
        "download_limit_bps": 0,
        "upload_limit_bps": 0,
        "last_update_check": 0.0,
    }
    path = root / "state.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    before = path.read_bytes()
    store = Store(root)
    assert path.read_bytes() == before
    assert store.data.get("language", "") == ""
    assert current() == "de"
    assert "language" not in json.loads(path.read_text(encoding="utf-8"))


def test_ui_offers_a_language_picker_and_cjk_fonts() -> None:
    html = (ROOT / "hearth" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "hearth" / "ui" / "static" / "themes.css").read_text(encoding="utf-8")
    app_css = (ROOT / "hearth" / "ui" / "static" / "app.css").read_text(encoding="utf-8")
    assert 'id="languageSelect"' in html
    assert SIGNOFF in html
    assert "Yu Gothic UI" in css
    assert "Malgun Gothic" in css
    assert "Microsoft YaHei" in css
    assert ":lang(ja)" in app_css
    assert ":lang(zh-CN)" in app_css
