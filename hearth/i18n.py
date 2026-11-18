"""Lightweight JSON catalogs. English text is the key and the fallback.

A missing key returns the English source. ``t`` never raises and never
returns a blank string for a non-empty key.
"""

from __future__ import annotations

import json
import locale
import os
import re
import threading
from typing import Any

from .paths import package_root

SUPPORTED = ("en", "es", "fr", "de", "pt-BR", "it", "ru", "pl", "ja", "ko", "zh-CN")

NATIVE_NAMES = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt-BR": "Português (Brasil)",
    "it": "Italiano",
    "ru": "Русский",
    "pl": "Polski",
    "ja": "日本語",
    "ko": "한국어",
    "zh-CN": "简体中文",
}

BCP47 = {code: code for code in SUPPORTED}

SIGNOFF = '"Hope this helps" - Ahmi'

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_lock = threading.Lock()
_active = "en"
_cache: dict[str, dict[str, str]] = {}


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text or ""))


def normalize(code: str | None) -> str | None:
    """Map a locale tag or language name to a supported code, or None."""
    if code is None:
        return None
    text = str(code).strip().replace("_", "-")
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"c", "posix"}:
        return "en"
    for item in SUPPORTED:
        if lowered == item.lower():
            return item
    primary = lowered.split("-", 1)[0]
    if primary in {"en", "english"} or lowered.startswith("english"):
        return "en"
    if primary in {"es", "spanish"} or lowered.startswith(("spanish", "español", "espanol")):
        return "es"
    if primary in {"fr", "french"} or lowered.startswith(("french", "français", "francais")):
        return "fr"
    if primary in {"de", "german"} or lowered.startswith(("german", "deutsch")):
        return "de"
    if primary in {"pt", "portuguese"} or lowered.startswith(("portuguese", "portugu")):
        return "pt-BR"
    if primary in {"it", "italian"} or lowered.startswith(("italian", "italiano")):
        return "it"
    if primary in {"ru", "russian"} or lowered.startswith("russian") or "русск" in lowered:
        return "ru"
    if primary in {"pl", "polish"} or lowered.startswith(("polish", "polski")):
        return "pl"
    if primary in {"ja", "japanese"} or lowered.startswith("japanese") or "日本" in text:
        return "ja"
    if primary in {"ko", "korean"} or lowered.startswith("korean") or "한국" in text:
        return "ko"
    if primary in {"zh", "chinese"} or lowered.startswith("chinese") or "中文" in text:
        if any(tok in lowered for tok in ("hant", "-tw", "-hk", "-mo", "traditional")):
            return None
        return "zh-CN"
    return None


def _windows_locale_name() -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
            return buf.value or None
    except Exception:
        return None
    return None


def _windows_ui_language() -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        lang = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return None
    primary = lang & 0x3FF
    return {
        0x09: "en",
        0x0A: "es",
        0x0C: "fr",
        0x07: "de",
        0x16: "pt",
        0x10: "it",
        0x19: "ru",
        0x15: "pl",
        0x11: "ja",
        0x12: "ko",
        0x04: "zh",
    }.get(primary)


def _explicitly_unsupported(raw: str) -> bool:
    text = raw.strip().replace("_", "-").lower()
    if not text or text in {"c", "posix"}:
        return False
    return any(tok in text for tok in ("hant", "-tw", "-hk", "-mo", "traditional"))


def detect_system_language() -> str:
    """Best supported language for this PC. Never raises."""
    try:
        name = _windows_locale_name()
        if name:
            code = normalize(name)
            if code:
                return code
            if _explicitly_unsupported(name):
                return "en"
        ui = _windows_ui_language()
        if ui:
            code = normalize(ui)
            if code:
                return code
        try:
            current_locale = locale.getlocale()[0]
        except Exception:
            current_locale = None
        for candidate in (
            current_locale,
            os.environ.get("LC_ALL"),
            os.environ.get("LC_MESSAGES"),
            os.environ.get("LANG"),
        ):
            if not candidate:
                continue
            for piece in str(candidate).split(":"):
                code = normalize(piece)
                if code:
                    return code
        language = os.environ.get("LANGUAGE") or ""
        for piece in language.split(":"):
            code = normalize(piece)
            if code:
                return code
    except Exception:
        return "en"
    return "en"


def resolve_language(saved: str | None) -> str:
    """Saved choice, then HEARTH_LANG, then the system language."""
    if saved and str(saved).strip():
        code = normalize(saved)
        if code:
            return code
    env = os.environ.get("HEARTH_LANG", "").strip()
    if env:
        code = normalize(env)
        if code:
            return code
    return detect_system_language()


def activate(code: str | None) -> str:
    """Select the language used by ``t``. Does not write settings."""
    global _active
    chosen = normalize(code) or "en"
    with _lock:
        _active = chosen
    return chosen


def current() -> str:
    with _lock:
        return _active


def locale_tag(code: str | None = None) -> str:
    chosen = normalize(code) if code else current()
    chosen = chosen or "en"
    return BCP47.get(chosen, chosen)


def choices() -> list[dict[str, str]]:
    return [
        {"code": code, "name": NATIVE_NAMES[code], "locale": BCP47[code]}
        for code in SUPPORTED
    ]


def _read_catalog(code: str) -> dict[str, str]:
    path = package_root() / "locales" / f"{code}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        out[key] = value
    return out


def _merged(code: str) -> dict[str, str]:
    """English overlaid with one locale. Caller must not hold ``_lock``."""
    with _lock:
        cached = _cache.get(code)
        if cached is not None:
            return cached
        english = _read_catalog("en")
        overlay = {} if code == "en" else _read_catalog(code)
        merged = dict(english)
        merged.update(overlay)
        _cache[code] = merged
        return merged


def catalog(code: str | None = None) -> dict[str, str]:
    chosen = normalize(code) if code else current()
    return dict(_merged(chosen or "en"))


def t(message: str, **kwargs: Any) -> str:
    """Translate ``message``. Unknown keys return the message itself."""
    key = "" if message is None else str(message)
    if not key:
        return ""
    # current() takes the lock. Call it before _merged(), which takes it too.
    table = _merged(current())
    text = table.get(key) or key
    if not text:
        text = key
    if not kwargs:
        return text
    try:
        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in kwargs and kwargs[name] is not None:
                return str(kwargs[name])
            return match.group(0)

        return _PLACEHOLDER.sub(repl, text)
    except Exception:
        return text or key


def check_catalogs() -> list[str]:
    """Every locale must match English keys, syntax, and placeholders."""
    errors: list[str] = []
    root = package_root() / "locales"
    en_path = root / "en.json"
    if not en_path.is_file():
        return ["missing en.json"]
    try:
        english = json.loads(en_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        return [f"en.json: {exc}"]
    if not isinstance(english, dict):
        return ["en.json is not an object"]
    en_keys: set[str] = set()
    for key, value in english.items():
        if not isinstance(key, str) or not key:
            errors.append("en.json has an empty or non-string key")
            continue
        if not isinstance(value, str):
            errors.append(f"en.json value for {key!r} is not a string")
            continue
        if not value.strip():
            errors.append(f"en.json blank value for {key!r}")
            continue
        if value != key:
            errors.append(f"en.json value differs from key: {key!r}")
        en_keys.add(key)
    for code in SUPPORTED:
        path = root / f"{code}.json"
        if not path.is_file():
            errors.append(f"missing {code}.json")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            errors.append(f"{code}.json: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{code}.json is not an object")
            continue
        keys: set[str] = set()
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str) or not str(value).strip():
                errors.append(f"{code}.json bad entry {key!r}")
                continue
            keys.add(key)
            if placeholders(value) != placeholders(english.get(key, key)):
                errors.append(f"{code}.json placeholders differ for {key!r}")
            if code != "en" and key == SIGNOFF and value != SIGNOFF:
                errors.append(f"{code}.json changed the sign-off line")
        missing = sorted(en_keys - keys)
        extra = sorted(keys - en_keys)
        if missing:
            errors.append(f"{code}.json missing {len(missing)} keys, first: {missing[:3]}")
        if extra:
            errors.append(f"{code}.json has {len(extra)} extra keys, first: {extra[:3]}")
    return errors
