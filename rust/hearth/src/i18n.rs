//! Hearth's languages. The catalogs are the Python app's own (`hearth/locales/<code>.json`,
//! English text as the key, `{name}` placeholders), embedded here so both apps say the same
//! thing in every language. Text calls read the window's language, so a label made with
//! [`t`] inside a reactive closure follows a language change by itself.

use remedy_ui::Ui;
use remedy_ui_services_i18n::Catalog;

/// The languages, as (code, the language's own name), in the order the Python app lists them.
pub const LANGUAGES: [(&str, &str); 11] = [
    ("en", "English"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("pt-BR", "Português (Brasil)"),
    ("it", "Italiano"),
    ("ru", "Русский"),
    ("pl", "Polski"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("zh-CN", "简体中文"),
];

thread_local! {
    static CATALOG: Catalog = build();
}

fn build() -> Catalog {
    Catalog::new("en")
        .embedded_json("es", include_str!("../../../hearth/locales/es.json"))
        .embedded_json("fr", include_str!("../../../hearth/locales/fr.json"))
        .embedded_json("de", include_str!("../../../hearth/locales/de.json"))
        .embedded_json("pt-BR", include_str!("../../../hearth/locales/pt-BR.json"))
        .embedded_json("it", include_str!("../../../hearth/locales/it.json"))
        .embedded_json("ru", include_str!("../../../hearth/locales/ru.json"))
        .embedded_json("pl", include_str!("../../../hearth/locales/pl.json"))
        .embedded_json("ja", include_str!("../../../hearth/locales/ja.json"))
        .embedded_json("ko", include_str!("../../../hearth/locales/ko.json"))
        .embedded_json("zh-CN", include_str!("../../../hearth/locales/zh-CN.json"))
}

/// `s` (an English key of the catalogs) in the window's language.
pub fn t(ui: &Ui, s: &str) -> String {
    CATALOG.with(|c| c.text(ui, s))
}

/// [`t`] with `{name}` placeholders filled.
pub fn tf(ui: &Ui, s: &str, args: &[(&str, String)]) -> String {
    CATALOG.with(|c| c.format(ui, s, args))
}

/// English text from elsewhere (the engine's errors, a formatted message) in the window's
/// language: an exact key, or a key with placeholders.
pub fn tr(ui: &Ui, text: &str) -> String {
    CATALOG.with(|c| c.translate(ui, text))
}

/// Names Hearth gives places itself, shown in the window's language; anything else a person
/// typed is shown as typed (the Python app's `KNOWN_LABELS`).
const PLACE_NAMES: [&str; 14] = [
    "Home",
    "Shared folder",
    "Folder does not exist",
    "Read only",
    "Read & write",
    "Network",
    "Documents",
    "Pictures",
    "Downloads",
    "Desktop",
    "Videos",
    "Music",
    "Folder",
    "File",
];

/// Drive names as Hearth makes them ("Local Disk (C:)"), with their catalog keys.
const DRIVE_NAMES: [(&str, &str); 5] = [
    ("Local Disk", "Local Disk ({letter})"),
    ("Network", "Network ({letter})"),
    ("Removable Disk", "Removable Disk ({letter})"),
    ("CD Drive", "CD Drive ({letter})"),
    ("RAM Disk", "RAM Disk ({letter})"),
];

/// A share or place name as shown: Hearth's own names ("Documents", "Local Disk (C:)") in
/// the window's language, a name someone chose exactly as they wrote it.
pub fn place(ui: &Ui, raw: &str) -> String {
    if PLACE_NAMES.contains(&raw) {
        return t(ui, raw);
    }
    for (prefix, key) in DRIVE_NAMES {
        let letter = raw
            .strip_prefix(prefix)
            .and_then(|r| r.strip_prefix(" ("))
            .and_then(|r| r.strip_suffix(":)"))
            .filter(|l| l.len() == 1 && l.chars().all(|c| c.is_ascii_alphabetic()));
        if let Some(l) = letter {
            return tf(ui, key, &[("letter", l.to_owned())]);
        }
    }
    raw.to_owned()
}

/// `s` in the language `locale` (a BCP 47 tag), for what has no window (the tray).
pub fn t_in(locale: &str, s: &str) -> String {
    CATALOG.with(|c| c.text_in(locale, s))
}

/// The language the user chose for Hearth: the saved one, else `HEARTH_LANG` (as the Python
/// app reads it), else `None`: follow the system.
pub fn chosen(saved: &str) -> Option<String> {
    hearth_core::state::normalize_language(saved)
        .map(str::to_owned)
        .or_else(|| {
            std::env::var("HEARTH_LANG")
                .ok()
                .and_then(|e| hearth_core::state::normalize_language(&e).map(str::to_owned))
        })
}

/// The English catalog (the keys), for tests.
#[cfg(test)]
const EN: &str = include_str!("../../../hearth/locales/en.json");

/// Whether `s` is a key of the catalogs (for the tests of what is shown).
#[cfg(test)]
pub fn is_key(s: &str) -> bool {
    serde_json::from_str::<std::collections::HashMap<String, String>>(EN)
        .is_ok_and(|t| t.contains_key(s))
}

#[cfg(test)]
mod tests {
    use std::collections::{HashMap, HashSet};

    use super::*;

    const OTHERS: [(&str, &str); 10] = [
        ("es", include_str!("../../../hearth/locales/es.json")),
        ("fr", include_str!("../../../hearth/locales/fr.json")),
        ("de", include_str!("../../../hearth/locales/de.json")),
        ("pt-BR", include_str!("../../../hearth/locales/pt-BR.json")),
        ("it", include_str!("../../../hearth/locales/it.json")),
        ("ru", include_str!("../../../hearth/locales/ru.json")),
        ("pl", include_str!("../../../hearth/locales/pl.json")),
        ("ja", include_str!("../../../hearth/locales/ja.json")),
        ("ko", include_str!("../../../hearth/locales/ko.json")),
        ("zh-CN", include_str!("../../../hearth/locales/zh-CN.json")),
    ];
    /// The interface's own source files (this file and the flow tests only use keys to test).
    const SOURCES: [(&str, &str); 11] = [
        ("chat.rs", include_str!("chat.rs")),
        ("files.rs", include_str!("files.rs")),
        ("gate.rs", include_str!("gate.rs")),
        ("main.rs", include_str!("main.rs")),
        ("settings.rs", include_str!("settings.rs")),
        ("share_dialog.rs", include_str!("share_dialog.rs")),
        ("shell.rs", include_str!("shell.rs")),
        ("state.rs", include_str!("state.rs")),
        ("transfers.rs", include_str!("transfers.rs")),
        ("tray.rs", include_str!("tray.rs")),
        ("update.rs", include_str!("update.rs")),
    ];

    fn table(json: &str) -> HashMap<String, String> {
        serde_json::from_str(json).unwrap_or_else(|e| unreachable!("a catalog parses: {e}"))
    }

    fn placeholders(s: &str) -> HashSet<String> {
        let mut out = HashSet::new();
        let mut rest = s;
        while let Some(open) = rest.find('{') {
            let after = rest.get(open + 1..).unwrap_or_default();
            match after.find('}') {
                Some(close) => {
                    out.insert(after.get(..close).unwrap_or_default().to_owned());
                    rest = after.get(close + 1..).unwrap_or_default();
                }
                None => break,
            }
        }
        out
    }

    #[derive(Debug, PartialEq)]
    enum Tok {
        Ident(String),
        Str(String),
        Punct(char),
    }

    /// Splits Rust source into identifiers, string literals and punctuation, leaving out
    /// comments, character literals and lifetimes.
    fn tokens(src: &str) -> Vec<Tok> {
        let c: Vec<char> = src.chars().collect();
        let at = |i: usize| c.get(i).copied().unwrap_or('\0');
        let mut out = Vec::new();
        let mut i = 0;
        while i < c.len() {
            let ch = at(i);
            if ch == '/' && at(i + 1) == '/' {
                while i < c.len() && at(i) != '\n' {
                    i += 1;
                }
            } else if ch == '/' && at(i + 1) == '*' {
                let mut depth = 1;
                i += 2;
                while i < c.len() && depth > 0 {
                    if at(i) == '/' && at(i + 1) == '*' {
                        depth += 1;
                        i += 2;
                    } else if at(i) == '*' && at(i + 1) == '/' {
                        depth -= 1;
                        i += 2;
                    } else {
                        i += 1;
                    }
                }
            } else if ch == 'r'
                && (at(i + 1) == '"' || (at(i + 1) == '#' && matches!(at(i + 2), '"' | '#')))
            {
                // A raw string: r"..." or r#"..."#.
                let mut j = i + 1;
                let mut hashes = 0;
                while at(j) == '#' {
                    hashes += 1;
                    j += 1;
                }
                j += 1;
                let mut s = String::new();
                while j < c.len() {
                    if at(j) == '"' && (0..hashes).all(|k| at(j + 1 + k) == '#') {
                        j += 1 + hashes;
                        break;
                    }
                    s.push(at(j));
                    j += 1;
                }
                out.push(Tok::Str(s));
                i = j;
            } else if ch == '"' {
                let mut s = String::new();
                i += 1;
                while i < c.len() && at(i) != '"' {
                    if at(i) == '\\' {
                        i += 1;
                        match at(i) {
                            'n' => s.push('\n'),
                            't' => s.push('\t'),
                            'r' => s.push('\r'),
                            '0' => s.push('\0'),
                            'u' => {
                                let mut hex = String::new();
                                i += 2;
                                while i < c.len() && at(i) != '}' {
                                    hex.push(at(i));
                                    i += 1;
                                }
                                if let Some(u) =
                                    u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32)
                                {
                                    s.push(u);
                                }
                            }
                            '\n' | '\r' => {
                                // A line continuation drops the line break and the indent.
                                while at(i + 1).is_whitespace() {
                                    i += 1;
                                }
                            }
                            other => s.push(other),
                        }
                        i += 1;
                    } else {
                        s.push(at(i));
                        i += 1;
                    }
                }
                i += 1;
                out.push(Tok::Str(s));
            } else if ch == '\'' {
                // A character literal ('a', '\n') or a lifetime ('a).
                if at(i + 1) == '\\' {
                    i += 2;
                    while i < c.len() && at(i) != '\'' {
                        i += 1;
                    }
                    i += 1;
                } else if at(i + 2) == '\'' {
                    i += 3;
                } else {
                    i += 1;
                }
            } else if ch.is_alphabetic() || ch == '_' {
                let start = i;
                while at(i).is_alphanumeric() || at(i) == '_' {
                    i += 1;
                }
                out.push(Tok::Ident(
                    c.get(start..i).unwrap_or_default().iter().collect(),
                ));
            } else {
                if !ch.is_whitespace() {
                    out.push(Tok::Punct(ch));
                }
                i += 1;
            }
        }
        out
    }

    /// The string literals passed straight to the translating calls (`t`, `tf`, `tr`,
    /// `toast`, `toast_f`) in `src`.
    fn translated_literals(src: &str) -> Vec<String> {
        let toks = tokens(src);
        let mut found = Vec::new();
        for (i, tok) in toks.iter().enumerate() {
            let Tok::Ident(name) = tok else { continue };
            if !matches!(name.as_str(), "t" | "tf" | "tr" | "toast" | "toast_f") {
                continue;
            }
            if i > 0 && matches!(toks.get(i - 1), Some(Tok::Ident(p)) if p == "fn") {
                continue;
            }
            if toks.get(i + 1) != Some(&Tok::Punct('(')) {
                continue;
            }
            // The arguments: split at the commas that are directly inside the call.
            let mut depth = 0;
            let mut arg: Vec<&Tok> = Vec::new();
            let mut args: Vec<Vec<&Tok>> = Vec::new();
            for t in toks.iter().skip(i + 1) {
                match t {
                    Tok::Punct('(' | '[' | '{') => {
                        depth += 1;
                        if depth > 1 {
                            arg.push(t);
                        }
                    }
                    Tok::Punct(')' | ']' | '}') => {
                        depth -= 1;
                        if depth == 0 {
                            break;
                        }
                        arg.push(t);
                    }
                    Tok::Punct(',') if depth == 1 => args.push(std::mem::take(&mut arg)),
                    _ => arg.push(t),
                }
            }
            args.push(arg);
            for a in args {
                if let [Tok::Str(s)] = a.as_slice() {
                    found.push(s.clone());
                }
            }
        }
        found
    }

    #[test]
    fn every_translated_literal_is_a_key_of_the_catalogs() {
        let en = table(EN);
        let mut seen = 0;
        let mut missing = Vec::new();
        for (file, src) in SOURCES {
            for lit in translated_literals(src) {
                seen += 1;
                if !en.contains_key(&lit) {
                    missing.push(format!("{file}: {lit:?}"));
                }
            }
        }
        assert!(seen > 100, "the scan found only {seen} calls: it is broken");
        assert!(missing.is_empty(), "not in en.json: {missing:#?}");
    }

    #[test]
    fn the_scan_reads_calls_and_skips_the_rest() {
        let src = r#"
            fn t(ui: &Ui, s: &str) {}
            let a = t(ui, "One");
            let b = tf(ui, "Two {n}", &[("n", x)]);
            let c = h.toast("Three");
            let d = t(ui, name); // t(ui, "Four")
            let e = "t(ui, \"Five\")";
            let f = text(ui, "Six");
            h.toast_f("Seven {a}", &[("a", s)]);
            let g = tr(ui, &format!("Eight {}", 1));
        "#;
        assert_eq!(
            translated_literals(src),
            ["One", "Two {n}", "Three", "Seven {a}"]
        );
    }

    #[test]
    fn every_catalog_has_every_key_with_its_placeholders() {
        let en = table(EN);
        for (code, json) in OTHERS {
            let t = table(json);
            let bad: Vec<&String> = en
                .keys()
                .filter(|k| {
                    t.get(*k).is_none_or(|v| v.trim().is_empty())
                        || t.get(*k)
                            .is_some_and(|v| placeholders(v) != placeholders(k))
                })
                .collect();
            assert!(bad.is_empty(), "{code}: empty or mismatched: {bad:?}");
            assert_eq!(t.len(), en.len(), "{code} has keys English lacks");
        }
    }

    #[test]
    fn the_languages_are_the_engines_and_the_catalogs_load() {
        let codes: Vec<&str> = LANGUAGES.iter().map(|(c, _)| *c).collect();
        assert_eq!(codes, hearth_core::state::LANGUAGES);
        CATALOG.with(|c| {
            assert!(c.problems().is_empty(), "{:?}", c.problems());
            let mut have = c.locales();
            have.sort();
            let mut want: Vec<String> = codes.iter().map(|c| (*c).to_owned()).collect();
            want.sort();
            assert_eq!(have, want);
        });
    }

    #[test]
    fn text_comes_back_in_the_chosen_language() {
        assert_eq!(t_in("fr", "Share a folder"), "Partager un dossier");
        assert_eq!(t_in("pt-BR", "Share a folder"), "Compartilhar uma pasta");
        assert_eq!(t_in("zh-CN", "Share a folder"), "共享文件夹");
        assert_eq!(t_in("en-US", "Share a folder"), "Share a folder");
        assert_eq!(t_in("fr", "Not a key"), "Not a key");
    }

    #[test]
    fn english_formatted_here_is_found_by_its_placeholders() {
        CATALOG.with(|c| {
            assert_eq!(
                c.translate_in("fr", "3m 5s left"),
                c.format_in(
                    "fr",
                    "{m}m {s}s left",
                    &[("m", "3".into()), ("s", "5".into())]
                )
            );
            assert_eq!(c.translate_in("fr", "2.0 KB"), "2.0 Ko");
        });
    }

    #[test]
    fn a_saved_choice_wins_over_the_environment() {
        assert_eq!(chosen("fr").as_deref(), Some("fr"));
        assert_eq!(chosen("Deutsch").as_deref(), Some("de"));
    }
}
