//! What Hearth remembers on this PC, in the same `state.json` Hearth 0.3.x keeps, so an
//! existing install carries over and either version can read the other's file.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::crypto::{self, Device};
use crate::pyjson;

/// The group this PC belongs to.
#[derive(Clone, Debug, PartialEq)]
pub struct Group {
    /// 16 hex characters.
    pub id: String,
    /// The name the house sees.
    pub name: String,
    /// The password (kept, as 0.3.x keeps it, so invites can be made again).
    pub password: String,
    /// The PBKDF2 salt, as hex.
    pub salt: String,
}

/// A folder this PC shares.
#[derive(Clone, Debug, PartialEq)]
pub struct Share {
    /// 8 hex characters.
    pub id: String,
    /// The folder.
    pub path: PathBuf,
    /// What the house sees (may be empty: then it comes from the path).
    pub label: String,
    /// Others may add files.
    pub writable: bool,
}

impl Share {
    /// The label, never empty or a placeholder: a typed name wins, otherwise the folder's
    /// name, or an Explorer-style drive name ("Windows (C:)", "Local Disk (D:)").
    pub fn display_label(&self) -> String {
        crate::paths::default_share_label(&self.path.to_string_lossy(), &self.label)
    }

    /// `"read"` or `"readwrite"`.
    pub fn mode(&self) -> &'static str {
        if self.writable { "readwrite" } else { "read" }
    }
}

fn hostname() -> String {
    crate::host::name().unwrap_or_else(|| "PC".to_owned())
}

/// The languages the interface speaks, as the Python app lists them.
pub const LANGUAGES: [&str; 11] = [
    "en", "es", "fr", "de", "pt-BR", "it", "ru", "pl", "ja", "ko", "zh-CN",
];

/// Maps a locale tag or language name to a supported code, as the Python app's `normalize`
/// does ("pt_BR", "Deutsch", "zh-Hans" and "C" are accepted; Traditional Chinese is not).
pub fn normalize_language(code: &str) -> Option<&'static str> {
    let text = code.trim().replace('_', "-");
    if text.is_empty() {
        return None;
    }
    let lowered = text.to_lowercase();
    if lowered == "c" || lowered == "posix" {
        return Some("en");
    }
    if let Some(l) = LANGUAGES.iter().find(|l| l.to_lowercase() == lowered) {
        return Some(l);
    }
    let primary = lowered.split('-').next().unwrap_or_default();
    let starts = |names: &[&str]| names.iter().any(|n| lowered.starts_with(n));
    if primary == "en" || primary == "english" || lowered.starts_with("english") {
        Some("en")
    } else if primary == "es" || primary == "spanish" || starts(&["spanish", "español", "espanol"])
    {
        Some("es")
    } else if primary == "fr" || primary == "french" || starts(&["french", "français", "francais"])
    {
        Some("fr")
    } else if primary == "de" || primary == "german" || starts(&["german", "deutsch"]) {
        Some("de")
    } else if primary == "pt" || primary == "portuguese" || starts(&["portuguese", "portugu"]) {
        Some("pt-BR")
    } else if primary == "it" || primary == "italian" || starts(&["italian", "italiano"]) {
        Some("it")
    } else if primary == "ru"
        || primary == "russian"
        || lowered.starts_with("russian")
        || lowered.contains("русск")
    {
        Some("ru")
    } else if primary == "pl" || primary == "polish" || starts(&["polish", "polski"]) {
        Some("pl")
    } else if primary == "ja"
        || primary == "japanese"
        || lowered.starts_with("japanese")
        || text.contains("日本")
    {
        Some("ja")
    } else if primary == "ko"
        || primary == "korean"
        || lowered.starts_with("korean")
        || text.contains("한국")
    {
        Some("ko")
    } else if primary == "zh"
        || primary == "chinese"
        || lowered.starts_with("chinese")
        || text.contains("中文")
    {
        let traditional = ["hant", "-tw", "-hk", "-mo", "traditional"];
        if traditional.iter().any(|t| lowered.contains(t)) {
            None
        } else {
            Some("zh-CN")
        }
    } else {
        None
    }
}

/// The keys `state.json` holds, with their defaults, in 0.3.x's order.
fn defaults() -> Map<String, Value> {
    let Value::Object(m) = json!({
        "device_name": hostname(),
        "theme": "system",
        "group": null,
        "known_peers": {},
        "shares": [],
        "chat": [],
        "bridge": false,
        "manual_hosts": [],
        "auto_check_updates": true,
        "download_folder": "",
        "download_limit_bps": 0,
        "upload_limit_bps": 0,
        "last_update_check": 0.0,
        "language": "",
    }) else {
        return Map::new();
    };
    m
}

/// The profile folder: `HEARTH_HOME`, else `~/.hearth`.
pub fn default_root() -> PathBuf {
    if let Some(h) = std::env::var_os("HEARTH_HOME") {
        return PathBuf::from(h);
    }
    let home = std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".hearth")
}

/// `state.json` and the device key.
#[derive(Debug)]
pub struct Store {
    root: PathBuf,
    data: Map<String, Value>,
    /// This PC.
    pub device: Device,
}

impl Store {
    /// Opens the profile at `root` (made if missing). A damaged `state.json` is kept as
    /// `state.json.corrupt` and the defaults are used, as 0.3.x does.
    pub fn open(root: &Path) -> std::io::Result<Store> {
        std::fs::create_dir_all(root)?;
        let device = Device::load_or_create(root)?;
        let mut data = defaults();
        let path = root.join("state.json");
        if path.exists() {
            match std::fs::read_to_string(&path)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| merge(&data, v))
            {
                Some(m) => data = m,
                None => {
                    let bak = root.join("state.json.corrupt");
                    let _ = std::fs::remove_file(&bak);
                    let _ = std::fs::rename(&path, &bak);
                }
            }
        }
        let mut store = Store {
            root: root.to_path_buf(),
            data,
            device,
        };
        store.refresh_share_labels();
        Ok(store)
    }

    /// Stores shares' paths canonically and rewrites placeholder names ("Shared folder", a
    /// bare "C:") to real ones. A failed save keeps the file as it is.
    fn refresh_share_labels(&mut self) {
        let Some(Value::Array(shares)) = self.data.get_mut("shares") else {
            return;
        };
        let mut changed = false;
        for share in shares.iter_mut() {
            let Some(share) = share.as_object_mut() else {
                continue;
            };
            let old_path = share
                .get("path")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            let new_path = if old_path.is_empty() {
                old_path.clone()
            } else {
                crate::paths::canonical_stored_path(&old_path)
            };
            if new_path != old_path {
                share.insert("path".into(), Value::String(new_path.clone()));
                changed = true;
            }
            let label = share
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            if crate::paths::share_label_needs_repair(&new_path, &label) {
                let better = crate::paths::default_share_label(&new_path, "");
                share.insert("label".into(), Value::String(better));
                changed = true;
            }
        }
        if changed {
            let _ = self.save();
        }
    }

    /// The profile folder.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Writes `state.json` (through `state.tmp`, replaced in one step).
    pub fn save(&self) -> std::io::Result<()> {
        let tmp = self.root.join("state.tmp");
        std::fs::write(
            &tmp,
            pyjson::dumps_indent(&Value::Object(self.data.clone())),
        )?;
        std::fs::rename(&tmp, self.root.join("state.json"))
    }

    fn str_of(&self, key: &str) -> String {
        self.data
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned()
    }

    fn set(&mut self, key: &str, v: Value) {
        self.data.insert(key.to_owned(), v);
    }

    /// This PC's name.
    pub fn device_name(&self) -> String {
        let n = self.str_of("device_name");
        if n.is_empty() { hostname() } else { n }
    }

    /// Renames this PC (blank: the computer's name).
    pub fn set_device_name(&mut self, name: &str) {
        let n = name.trim();
        self.set(
            "device_name",
            Value::String(if n.is_empty() {
                hostname()
            } else {
                n.to_owned()
            }),
        );
    }

    /// The theme id.
    pub fn theme(&self) -> String {
        let t = self.str_of("theme");
        if t.is_empty() { "system".into() } else { t }
    }

    /// Sets the theme id.
    pub fn set_theme(&mut self, id: &str) {
        self.set("theme", Value::String(id.to_owned()));
    }

    /// The language chosen for the interface ("" follows the system), as saved.
    pub fn language(&self) -> String {
        self.str_of("language")
    }

    /// Saves the interface language: a supported code, or "" to follow the system.
    pub fn set_language(&mut self, code: &str) {
        self.set("language", Value::String(code.to_owned()));
    }

    /// The group, if this PC is in one.
    pub fn group(&self) -> Option<Group> {
        let g = self.data.get("group")?.as_object()?;
        let s = |k: &str| g.get(k).and_then(Value::as_str).map(str::to_owned);
        Some(Group {
            id: s("id")?,
            name: s("name")?,
            password: s("password")?,
            salt: s("salt")?,
        })
    }

    /// The group key (PBKDF2 takes a moment: callers keep it).
    pub fn group_key(&self) -> Option<[u8; 32]> {
        let g = self.group()?;
        let salt = hex::decode(&g.salt).ok()?;
        Some(crypto::group_key(&g.id, &g.password, &salt))
    }

    /// Starts a group: a new id and salt, the password given or a generated one.
    pub fn create_group(&mut self, name: &str, password: Option<&str>) -> Group {
        let password = password
            .map(str::trim)
            .filter(|p| !p.is_empty())
            .map_or_else(crypto::generate_password, str::to_owned);
        let g = Group {
            id: crypto::random_hex(8),
            name: Some(name.trim())
                .filter(|n| !n.is_empty())
                .unwrap_or("Home")
                .to_owned(),
            password,
            salt: crypto::random_hex(16),
        };
        self.store_group(&g, Some(self.device.id.clone()));
        g
    }

    /// Joins a group someone else made.
    pub fn join_group(&mut self, g: &Group) {
        self.store_group(g, None);
    }

    fn store_group(&mut self, g: &Group, creator: Option<String>) {
        let verifier = hex::decode(&g.salt)
            .ok()
            .map(|salt| crypto::verifier(&crypto::group_key(&g.id, &g.password, &salt)));
        let created = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        self.set(
            "group",
            json!({"id": g.id, "name": g.name, "password": g.password, "salt": g.salt, "verifier": verifier, "created": created, "creator_id": creator}),
        );
    }

    /// Leaves the group: forgets it, its PCs and its chat; shares and the device key stay.
    pub fn leave_group(&mut self) {
        self.set("group", Value::Null);
        self.set("known_peers", json!({}));
        self.set("chat", json!([]));
    }

    /// The folders this PC shares.
    pub fn shares(&self) -> Vec<Share> {
        self.data
            .get("shares")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|s| {
                        Some(Share {
                            id: s.get("id")?.as_str()?.to_owned(),
                            path: PathBuf::from(s.get("path")?.as_str()?),
                            label: s
                                .get("label")
                                .and_then(Value::as_str)
                                .unwrap_or_default()
                                .to_owned(),
                            writable: s.get("mode").and_then(Value::as_str) == Some("readwrite"),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Shares `path`.
    pub fn add_share(&mut self, path: &Path, label: &str, writable: bool) -> Share {
        let stored = crate::paths::canonical_stored_path(&path.to_string_lossy());
        let share = Share {
            id: crypto::random_hex(4),
            label: crate::paths::default_share_label(&stored, label),
            path: PathBuf::from(stored),
            writable,
        };
        let added = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        if let Some(Value::Array(a)) = self.data.get_mut("shares") {
            a.push(json!({"id": share.id, "path": share.path.to_string_lossy(), "label": share.label, "mode": share.mode(), "added": added}));
        }
        share
    }

    /// Changes a share; `None` keeps a field as it is.
    pub fn update_share(
        &mut self,
        id: &str,
        path: Option<&Path>,
        label: Option<&str>,
        writable: Option<bool>,
    ) -> bool {
        let Some(Value::Array(a)) = self.data.get_mut("shares") else {
            return false;
        };
        let Some(s) = a
            .iter_mut()
            .find(|s| s.get("id").and_then(Value::as_str) == Some(id))
        else {
            return false;
        };
        if let Some(p) = path {
            let text = p.to_string_lossy();
            if !text.trim().is_empty() {
                s["path"] = Value::String(crate::paths::canonical_stored_path(&text));
            }
        }
        let current = s
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        if let Some(l) = label {
            s["label"] = Value::String(crate::paths::default_share_label(&current, l.trim()));
        } else {
            let stored = s.get("label").and_then(Value::as_str).unwrap_or_default();
            if crate::paths::share_label_needs_repair(&current, stored) {
                s["label"] = Value::String(crate::paths::default_share_label(&current, ""));
            }
        }
        if let Some(w) = writable {
            s["mode"] = Value::String(if w { "readwrite" } else { "read" }.into());
        }
        true
    }

    /// Stops sharing a folder.
    pub fn remove_share(&mut self, id: &str) -> bool {
        let Some(Value::Array(a)) = self.data.get_mut("shares") else {
            return false;
        };
        let before = a.len();
        a.retain(|s| s.get("id").and_then(Value::as_str) != Some(id));
        a.len() != before
    }

    /// Hosts the user added by hand (as typed).
    pub fn manual_hosts(&self) -> Vec<String> {
        self.data
            .get("manual_hosts")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|h| h.as_str().map(str::to_owned))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Remembers a host (once).
    pub fn add_manual_host(&mut self, host: &str) {
        let h = host.trim();
        if h.is_empty() || self.manual_hosts().iter().any(|x| x == h) {
            return;
        }
        if let Some(Value::Array(a)) = self.data.get_mut("manual_hosts") {
            a.push(Value::String(h.to_owned()));
        }
    }

    /// Forgets a host.
    pub fn remove_manual_host(&mut self, host: &str) {
        if let Some(Value::Array(a)) = self.data.get_mut("manual_hosts") {
            a.retain(|x| x.as_str() != Some(host));
        }
    }

    /// PCs met before, by device id (each entry as 0.3.x keeps it).
    pub fn known_peers(&self) -> Map<String, Value> {
        self.data
            .get("known_peers")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default()
    }

    /// Remembers a PC, merged over what was known.
    pub fn remember_peer(&mut self, id: &str, peer: &Map<String, Value>, last_seen: f64) {
        let Some(Value::Object(known)) = self.data.get_mut("known_peers") else {
            return;
        };
        let mut merged = known
            .get(id)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        for (k, v) in peer {
            merged.insert(k.clone(), v.clone());
        }
        merged.insert("last_seen".into(), json!(last_seen));
        known.insert(id.to_owned(), Value::Object(merged));
    }

    /// Forgets a PC.
    pub fn forget_peer(&mut self, id: &str) {
        if let Some(Value::Object(known)) = self.data.get_mut("known_peers") {
            known.remove(id);
        }
    }

    /// The stored chat, oldest first.
    pub fn chat(&self) -> Vec<Value> {
        self.data
            .get("chat")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    /// Adds a chat message unless one with its id is stored; keeps the last 500. True when added.
    pub fn append_chat(&mut self, msg: Value) -> bool {
        let Some(id) = msg.get("id").and_then(Value::as_str).map(str::to_owned) else {
            return false;
        };
        let Some(Value::Array(a)) = self.data.get_mut("chat") else {
            return false;
        };
        if a.iter()
            .any(|m| m.get("id").and_then(Value::as_str) == Some(id.as_str()))
        {
            return false;
        }
        a.push(msg);
        let over = a.len().saturating_sub(500);
        a.drain(..over);
        true
    }

    /// A boolean setting.
    pub fn flag(&self, key: &str) -> bool {
        self.data.get(key).and_then(Value::as_bool).unwrap_or(false)
    }

    /// Sets a boolean setting (`bridge`, `auto_check_updates`).
    pub fn set_flag(&mut self, key: &str, on: bool) {
        self.set(key, Value::Bool(on));
    }

    /// A number setting (`download_limit_bps`, `upload_limit_bps`, `last_update_check`); 0.3.x
    /// may have written them as floats.
    pub fn number(&self, key: &str) -> f64 {
        self.data.get(key).and_then(Value::as_f64).unwrap_or(0.0)
    }

    /// Sets a number setting.
    pub fn set_number(&mut self, key: &str, v: f64) {
        self.set(key, json!(v));
    }

    /// The download folder setting ("" for the user's Downloads).
    pub fn download_folder(&self) -> String {
        self.str_of("download_folder")
    }

    /// Sets the download folder ("" for the default).
    pub fn set_download_folder(&mut self, path: &str) {
        self.set("download_folder", Value::String(path.to_owned()));
    }
}

/// A loaded `state.json` laid over the defaults with 0.3.x's rules: wrong-typed values keep the
/// default, unknown keys are dropped, a group missing its fields means a damaged file.
fn merge(defaults: &Map<String, Value>, loaded: Value) -> Option<Map<String, Value>> {
    let Value::Object(loaded) = loaded else {
        return None;
    };
    let mut out = defaults.clone();
    for (k, d) in defaults {
        let Some(v) = loaded.get(k) else { continue };
        let ok = match (k.as_str(), d) {
            ("group", _) => match v {
                Value::Null => true,
                Value::Object(g)
                    if ["id", "name", "password", "salt"]
                        .iter()
                        .all(|f| g.contains_key(*f)) =>
                {
                    true
                }
                _ => return None,
            },
            (_, Value::String(_)) => v.as_str().is_some_and(|s| !s.is_empty()),
            (_, Value::Bool(_)) => v.is_boolean(),
            (_, Value::Array(_)) => v.is_array(),
            (_, Value::Object(_)) => v.is_object(),
            (_, Value::Number(_)) => v.is_number(),
            _ => false,
        };
        if ok {
            out.insert(k.clone(), v.clone());
        }
    }
    // Keys this version does not know (written by a newer Hearth) are kept as they are and
    // written back, so moving between versions never drops what another one saved.
    for (k, v) in loaded {
        if !defaults.contains_key(&k) {
            out.insert(k, v);
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> tempfile::TempDir {
        tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"))
    }

    #[test]
    fn a_python_state_file_loads_and_round_trips() {
        let dir = tmp();
        let python = r#"{
  "device_name": "Office",
  "theme": "forest",
  "group": {"id": "0123456789abcdef", "name": "Home", "password": "Ab-Cd", "salt": "000102030405060708090a0b0c0d0e0f", "verifier": "x", "created": 1.5, "creator_id": null},
  "known_peers": {"abc": {"device_id": "abc", "name": "Den", "port": 7474, "ips": ["192.168.1.9"], "address": "192.168.1.9", "via": "beacon", "salt": "s", "last_seen": 3.0}},
  "shares": [{"id": "deadbeef", "path": "C:\\Users\\x\\Pictures", "label": "", "mode": "readwrite", "added": 2.0}],
  "chat": [{"id": "m1", "from_id": "abc", "from_name": "Den", "to": null, "text": "hi", "ts": 4.0}],
  "bridge": true,
  "manual_hosts": ["192.168.0.12"],
  "auto_check_updates": false,
  "download_folder": "",
  "download_limit_bps": 1048576.0,
  "upload_limit_bps": 0.0,
  "last_update_check": 0.0,
  "language": "es",
  "some_future_key": 1
}"#;
        std::fs::write(dir.path().join("state.json"), python)
            .unwrap_or_else(|e| unreachable!("{e}"));
        let s = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(s.device_name(), "Office");
        assert_eq!(s.theme(), "forest");
        assert_eq!(s.language(), "es");
        assert_eq!(s.group().map(|g| g.name), Some("Home".into()));
        assert_eq!(
            s.shares()
                .first()
                .map(|sh| (sh.display_label(), sh.writable)),
            Some(("Pictures".into(), true))
        );
        assert_eq!(s.number("download_limit_bps"), 1_048_576.0);
        assert!(s.flag("bridge") && !s.flag("auto_check_updates"));
        assert_eq!(
            s.known_peers()
                .get("abc")
                .and_then(|p| p.get("salt"))
                .and_then(Value::as_str),
            Some("s"),
            "peer details kept"
        );
        s.save().unwrap_or_else(|e| unreachable!("{e}"));
        let back: Value = serde_json::from_str(
            &std::fs::read_to_string(dir.path().join("state.json")).unwrap_or_default(),
        )
        .unwrap_or_default();
        assert_eq!(
            back.get("some_future_key"),
            Some(&json!(1)),
            "a newer version's keys are kept"
        );
        assert_eq!(back.get("theme").and_then(Value::as_str), Some("forest"));
    }

    #[test]
    fn language_names_map_like_the_python_apps_normalize() {
        let n = normalize_language;
        assert_eq!(n("pt_BR"), Some("pt-BR"));
        assert_eq!(n("pt-PT"), Some("pt-BR"));
        assert_eq!(n("DE"), Some("de"));
        assert_eq!(n("Deutsch"), Some("de"));
        assert_eq!(n("zh-Hans-CN"), Some("zh-CN"));
        assert_eq!(n("zh-TW"), None);
        assert_eq!(n("zh-Hant"), None);
        assert_eq!(n("C"), Some("en"));
        assert_eq!(n("日本語"), Some("ja"));
        assert_eq!(n(""), None);
        assert_eq!(n("nope"), None);
        assert!(LANGUAGES.iter().all(|l| n(l) == Some(*l)));
    }

    #[test]
    fn a_damaged_file_is_set_aside() {
        let dir = tmp();
        std::fs::write(dir.path().join("state.json"), r#"{"group": {"id": "x"}}"#)
            .unwrap_or_else(|e| unreachable!("{e}"));
        let s = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert!(s.group().is_none());
        assert!(dir.path().join("state.json.corrupt").exists());
    }

    #[test]
    fn groups_shares_hosts_and_chat() {
        let dir = tmp();
        let mut s = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        let g = s.create_group("  ", None);
        assert_eq!(g.name, "Home");
        assert_eq!((g.id.len(), g.salt.len()), (16, 32));
        assert!(s.group_key().is_some());
        let sh = s.add_share(dir.path(), "", false);
        assert_eq!(sh.id.len(), 8);
        assert!(s.update_share(&sh.id, None, Some("Stuff"), Some(true)));
        assert_eq!(
            s.shares().first().map(|x| (x.label.clone(), x.writable)),
            Some(("Stuff".into(), true))
        );
        s.add_manual_host(" 10.0.0.5 ");
        s.add_manual_host("10.0.0.5");
        assert_eq!(s.manual_hosts(), vec!["10.0.0.5"]);
        assert!(s.append_chat(json!({"id": "a", "text": "x"})));
        assert!(!s.append_chat(json!({"id": "a", "text": "y"})));
        for i in 0..600 {
            s.append_chat(json!({"id": format!("m{i}"), "text": "x"}));
        }
        assert_eq!(s.chat().len(), 500);
        s.leave_group();
        assert!(s.group().is_none() && s.chat().is_empty());
        assert_eq!(s.shares().len(), 1, "shares stay");
    }

    fn write_state(root: &Path, shares: Value) {
        let payload = json!({"group": null, "shares": shares});
        std::fs::write(root.join("state.json"), payload.to_string())
            .unwrap_or_else(|e| unreachable!("{e}"));
    }

    #[test]
    fn opening_repairs_placeholders_and_keeps_custom_labels() {
        let dir = tmp();
        let downloads = dir.path().join("Downloads");
        std::fs::create_dir(&downloads).unwrap_or_else(|e| unreachable!("{e}"));
        let stored = crate::paths::canonical_stored_path(&downloads.to_string_lossy());
        write_state(
            dir.path(),
            json!([
                {"id": "drv", "path": "C:", "label": "Shared folder", "mode": "read"},
                {"id": "dl", "path": stored, "label": "Downloads", "mode": "read"},
            ]),
        );
        let s = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        let shares = s.shares();
        let by_id = |id: &str| {
            shares
                .iter()
                .find(|x| x.id == id)
                .unwrap_or_else(|| unreachable!("share {id}"))
        };
        assert_eq!(by_id("drv").path, PathBuf::from(r"C:\"));
        assert_eq!(
            by_id("drv").label,
            crate::paths::default_share_label(r"C:\", "")
        );
        assert!(by_id("drv").label.contains("(C:)"));
        assert_eq!(by_id("dl").path, PathBuf::from(&stored));
        assert_eq!(by_id("dl").label, "Downloads");
        // The repair was saved.
        let again = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(again.shares().len(), 2);
    }

    #[test]
    fn opening_does_not_rewrite_a_canonical_custom_share() {
        let dir = tmp();
        let folder = dir.path().join("Downloads");
        std::fs::create_dir(&folder).unwrap_or_else(|e| unreachable!("{e}"));
        let stored = crate::paths::canonical_stored_path(&folder.to_string_lossy());
        write_state(
            dir.path(),
            json!([{"id": "dl", "path": stored, "label": "Downloads", "mode": "read", "added": 1}]),
        );
        let first = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        first.save().unwrap_or_else(|e| unreachable!("{e}"));
        let before = std::fs::read(dir.path().join("state.json")).unwrap_or_default();
        Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(
            std::fs::read(dir.path().join("state.json")).unwrap_or_default(),
            before
        );
    }

    #[test]
    fn a_failed_repair_save_keeps_the_file() {
        let dir = tmp();
        write_state(
            dir.path(),
            json!([{"id": "a", "path": "C:", "label": "Shared folder", "mode": "read"}]),
        );
        // A directory where the temp file goes makes the save fail.
        std::fs::create_dir(dir.path().join("state.tmp")).unwrap_or_else(|e| unreachable!("{e}"));
        let before = std::fs::read(dir.path().join("state.json")).unwrap_or_default();
        let s = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(s.shares().len(), 1);
        assert_eq!(
            std::fs::read(dir.path().join("state.json")).unwrap_or_default(),
            before
        );
        assert!(!dir.path().join("state.json.corrupt").exists());
    }

    #[test]
    fn add_and_update_store_canonical_paths_and_real_labels() {
        let dir = tmp();
        let mut s = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        let drive = s.add_share(Path::new("C:"), "Shared folder", false);
        assert_eq!(drive.path, PathBuf::from(r"C:\"));
        assert_eq!(drive.label, crate::paths::default_share_label(r"C:\", ""));
        let folder = dir.path().join("Photos");
        std::fs::create_dir(&folder).unwrap_or_else(|e| unreachable!("{e}"));
        let photos = s.add_share(&folder, "Shared folder", false);
        assert_eq!(photos.label, "Photos");
        let docs = dir.path().join("Docs");
        std::fs::create_dir(&docs).unwrap_or_else(|e| unreachable!("{e}"));
        let kept = s.add_share(&docs, "Family Photos", false);
        assert_eq!(kept.label, "Family Photos");
        // A mode-only change keeps the custom name; a blank name goes back to the folder's.
        assert!(s.update_share(&kept.id, None, None, Some(true)));
        let label_of = |s: &Store| {
            s.shares()
                .iter()
                .find(|x| x.id == kept.id)
                .map(|x| (x.label.clone(), x.writable))
        };
        assert_eq!(label_of(&s), Some(("Family Photos".into(), true)));
        assert!(s.update_share(&kept.id, None, Some("   "), None));
        assert_eq!(label_of(&s), Some(("Docs".into(), true)));
    }
}
