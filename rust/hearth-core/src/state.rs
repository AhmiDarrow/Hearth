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
    /// The label, never empty.
    pub fn display_label(&self) -> String {
        crate::paths::share_label(&self.path.to_string_lossy(), &self.label, None)
    }

    /// `"read"` or `"readwrite"`.
    pub fn mode(&self) -> &'static str {
        if self.writable { "readwrite" } else { "read" }
    }
}

fn hostname() -> String {
    std::env::var("COMPUTERNAME").or_else(|_| std::env::var("HOSTNAME")).unwrap_or_else(|_| "PC".to_owned())
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
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from).unwrap_or_default();
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
            match std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|v| merge(&data, v)) {
                Some(m) => data = m,
                None => {
                    let bak = root.join("state.json.corrupt");
                    let _ = std::fs::remove_file(&bak);
                    let _ = std::fs::rename(&path, &bak);
                }
            }
        }
        Ok(Store { root: root.to_path_buf(), data, device })
    }

    /// The profile folder.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Writes `state.json` (through `state.tmp`, replaced in one step).
    pub fn save(&self) -> std::io::Result<()> {
        let tmp = self.root.join("state.tmp");
        std::fs::write(&tmp, pyjson::dumps_indent(&Value::Object(self.data.clone())))?;
        std::fs::rename(&tmp, self.root.join("state.json"))
    }

    fn str_of(&self, key: &str) -> String {
        self.data.get(key).and_then(Value::as_str).unwrap_or_default().to_owned()
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
        self.set("device_name", Value::String(if n.is_empty() { hostname() } else { n.to_owned() }));
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

    /// The group, if this PC is in one.
    pub fn group(&self) -> Option<Group> {
        let g = self.data.get("group")?.as_object()?;
        let s = |k: &str| g.get(k).and_then(Value::as_str).map(str::to_owned);
        Some(Group { id: s("id")?, name: s("name")?, password: s("password")?, salt: s("salt")? })
    }

    /// The group key (PBKDF2 takes a moment: callers keep it).
    pub fn group_key(&self) -> Option<[u8; 32]> {
        let g = self.group()?;
        let salt = hex::decode(&g.salt).ok()?;
        Some(crypto::group_key(&g.id, &g.password, &salt))
    }

    /// Starts a group: a new id and salt, the password given or a generated one.
    pub fn create_group(&mut self, name: &str, password: Option<&str>) -> Group {
        let password = password.map(str::trim).filter(|p| !p.is_empty()).map_or_else(crypto::generate_password, str::to_owned);
        let g = Group {
            id: crypto::random_hex(8),
            name: Some(name.trim()).filter(|n| !n.is_empty()).unwrap_or("Home").to_owned(),
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
        let verifier = hex::decode(&g.salt).ok().map(|salt| crypto::verifier(&crypto::group_key(&g.id, &g.password, &salt)));
        let created = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
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
                            label: s.get("label").and_then(Value::as_str).unwrap_or_default().to_owned(),
                            writable: s.get("mode").and_then(Value::as_str) == Some("readwrite"),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Shares `path`.
    pub fn add_share(&mut self, path: &Path, label: &str, writable: bool) -> Share {
        let path = std::fs::canonicalize(path).map(strip_verbatim).unwrap_or_else(|_| path.to_path_buf());
        let share = Share { id: crypto::random_hex(4), path, label: label.trim().to_owned(), writable };
        let added = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
        if let Some(Value::Array(a)) = self.data.get_mut("shares") {
            a.push(json!({"id": share.id, "path": share.path.to_string_lossy(), "label": share.label, "mode": share.mode(), "added": added}));
        }
        share
    }

    /// Changes a share; `None` keeps a field as it is.
    pub fn update_share(&mut self, id: &str, path: Option<&Path>, label: Option<&str>, writable: Option<bool>) -> bool {
        let Some(Value::Array(a)) = self.data.get_mut("shares") else { return false };
        let Some(s) = a.iter_mut().find(|s| s.get("id").and_then(Value::as_str) == Some(id)) else { return false };
        if let Some(p) = path {
            let p = std::fs::canonicalize(p).map(strip_verbatim).unwrap_or_else(|_| p.to_path_buf());
            s["path"] = Value::String(p.to_string_lossy().into_owned());
        }
        if let Some(l) = label.map(str::trim).filter(|l| !l.is_empty()) {
            s["label"] = Value::String(l.to_owned());
        }
        if let Some(w) = writable {
            s["mode"] = Value::String(if w { "readwrite" } else { "read" }.into());
        }
        true
    }

    /// Stops sharing a folder.
    pub fn remove_share(&mut self, id: &str) -> bool {
        let Some(Value::Array(a)) = self.data.get_mut("shares") else { return false };
        let before = a.len();
        a.retain(|s| s.get("id").and_then(Value::as_str) != Some(id));
        a.len() != before
    }

    /// Hosts the user added by hand (as typed).
    pub fn manual_hosts(&self) -> Vec<String> {
        self.data.get("manual_hosts").and_then(Value::as_array).map(|a| a.iter().filter_map(|h| h.as_str().map(str::to_owned)).collect()).unwrap_or_default()
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
        self.data.get("known_peers").and_then(Value::as_object).cloned().unwrap_or_default()
    }

    /// Remembers a PC, merged over what was known.
    pub fn remember_peer(&mut self, id: &str, peer: &Map<String, Value>, last_seen: f64) {
        let Some(Value::Object(known)) = self.data.get_mut("known_peers") else { return };
        let mut merged = known.get(id).and_then(Value::as_object).cloned().unwrap_or_default();
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
        self.data.get("chat").and_then(Value::as_array).cloned().unwrap_or_default()
    }

    /// Adds a chat message unless one with its id is stored; keeps the last 500. True when added.
    pub fn append_chat(&mut self, msg: Value) -> bool {
        let Some(id) = msg.get("id").and_then(Value::as_str).map(str::to_owned) else { return false };
        let Some(Value::Array(a)) = self.data.get_mut("chat") else { return false };
        if a.iter().any(|m| m.get("id").and_then(Value::as_str) == Some(id.as_str())) {
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

/// Windows' `\\?\C:\…` canonical form, made ordinary (what 0.3.x stores).
fn strip_verbatim(p: PathBuf) -> PathBuf {
    let s = p.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC") => PathBuf::from(rest),
        Some(rest) => PathBuf::from(format!(r"\{}", rest.trim_start_matches("UNC"))),
        None => p,
    }
}

/// A loaded `state.json` laid over the defaults with 0.3.x's rules: wrong-typed values keep the
/// default, unknown keys are dropped, a group missing its fields means a damaged file.
fn merge(defaults: &Map<String, Value>, loaded: Value) -> Option<Map<String, Value>> {
    let Value::Object(loaded) = loaded else { return None };
    let mut out = defaults.clone();
    for (k, d) in defaults {
        let Some(v) = loaded.get(k) else { continue };
        let ok = match (k.as_str(), d) {
            ("group", _) => match v {
                Value::Null => true,
                Value::Object(g) if ["id", "name", "password", "salt"].iter().all(|f| g.contains_key(*f)) => true,
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
  "some_future_key": 1
}"#;
        std::fs::write(dir.path().join("state.json"), python).unwrap_or_else(|e| unreachable!("{e}"));
        let s = Store::open(dir.path()).unwrap_or_else(|e| unreachable!("{e}"));
        assert_eq!(s.device_name(), "Office");
        assert_eq!(s.theme(), "forest");
        assert_eq!(s.group().map(|g| g.name), Some("Home".into()));
        assert_eq!(s.shares().first().map(|sh| (sh.display_label(), sh.writable)), Some(("Pictures".into(), true)));
        assert_eq!(s.number("download_limit_bps"), 1_048_576.0);
        assert!(s.flag("bridge") && !s.flag("auto_check_updates"));
        assert_eq!(s.known_peers().get("abc").and_then(|p| p.get("salt")).and_then(Value::as_str), Some("s"), "peer details kept");
        s.save().unwrap_or_else(|e| unreachable!("{e}"));
        let back: Value = serde_json::from_str(&std::fs::read_to_string(dir.path().join("state.json")).unwrap_or_default()).unwrap_or_default();
        assert!(back.get("some_future_key").is_none(), "unknown keys dropped, as 0.3.x drops them");
        assert_eq!(back.get("theme").and_then(Value::as_str), Some("forest"));
    }

    #[test]
    fn a_damaged_file_is_set_aside() {
        let dir = tmp();
        std::fs::write(dir.path().join("state.json"), r#"{"group": {"id": "x"}}"#).unwrap_or_else(|e| unreachable!("{e}"));
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
        assert_eq!(s.shares().first().map(|x| (x.label.clone(), x.writable)), Some(("Stuff".into(), true)));
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
}
