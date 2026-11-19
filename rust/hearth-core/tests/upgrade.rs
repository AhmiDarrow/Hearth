//! Upgrading from the shipped Python Hearth (0.4) to this one loses nothing, and going back
//! loses nothing this version wrote: a profile made by the Python app itself (settings, the
//! home group and its password, shares, known PCs, public and private chat, extra hosts,
//! speed limits, the download folder, the device key, the transfer history, the window/tray
//! choice, the inbox and the log) is opened by the Rust engine exactly as the Python app sees
//! it, and the Python app reads back what the Rust engine changed with everything else intact.
//!
//! Every profile here is a temporary folder (`HEARTH_HOME` is never the user's).

// `serde_json::Value` indexing returns `Null` for anything missing; it never panics.
#![allow(clippy::indexing_slicing)]

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use hearth_core::Hearth;
use serde_json::{Value, json};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap_or_else(|e| unreachable!("{e}"))
}

fn python(args: &[&str]) -> String {
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/python_profile.py");
    let out = Command::new("python")
        .arg(script)
        .args(args)
        .env("PYTHONPATH", repo_root())
        .env("HEARTH_NO_UPDATE", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .output()
        .unwrap_or_else(|e| unreachable!("python: {e}"));
    assert!(
        out.status.success(),
        "python {args:?}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8_lossy(&out.stdout).into_owned()
}

fn dump(home: &Path) -> Value {
    serde_json::from_str(&python(&["dump", &home.to_string_lossy()]))
        .unwrap_or_else(|e| unreachable!("the dump parses: {e}"))
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(0)
}

fn get<'a>(v: &'a Value, path: &[&str]) -> &'a Value {
    path.iter()
        .fold(v, |v, k| v.get(*k).unwrap_or(&Value::Null))
}

fn read(p: &Path) -> Vec<u8> {
    std::fs::read(p).unwrap_or_else(|e| unreachable!("{}: {e}", p.display()))
}

/// The user data a dump holds, without what changes by itself (when a PC was last seen).
fn user_data(d: &Value) -> Value {
    let mut v = d.clone();
    if let Some(peers) = v
        .get_mut("data")
        .and_then(|d| d.get_mut("known_peers"))
        .and_then(Value::as_object_mut)
    {
        for p in peers.values_mut() {
            if let Some(p) = p.as_object_mut() {
                p.remove("last_seen");
            }
        }
    }
    v
}

#[test]
fn a_python_profile_upgrades_and_downgrades_without_losing_anything() {
    let dir = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
    let home = dir.path().join("profile");
    let files = dir.path().join("files");
    python(&["make", &home.to_string_lossy(), &files.to_string_lossy()]);
    // A key a newer Hearth might add.
    let state = home.join("state.json");
    let mut raw: Value =
        serde_json::from_slice(&read(&state)).unwrap_or_else(|e| unreachable!("{e}"));
    if let Some(o) = raw.as_object_mut() {
        o.insert("from_a_newer_hearth".into(), json!({"kept": true}));
    }
    std::fs::write(&state, raw.to_string()).unwrap_or_else(|e| unreachable!("{e}"));

    let before = dump(&home);
    let key = read(&home.join("device.key"));
    let inbox = read(&home.join("inbox/received.txt"));
    let log = read(&home.join("hearth.log"));

    // ---- Upgrade: the Rust engine opens the Python profile.
    let h = Hearth::start(&home, free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    let data = get(&before, &["data"]);
    assert_eq!(
        h.device_id(),
        get(&before, &["device_id"]).as_str().unwrap_or_default()
    );
    let s = h.settings();
    assert_eq!(
        s.device_name,
        get(data, &["device_name"]).as_str().unwrap_or_default()
    );
    assert_eq!(s.device_name, "Den PC – Wohnzimmer");
    assert_eq!(s.theme, "forest");
    assert_eq!(s.language, "de");
    assert!(!s.auto_check_updates);
    assert_eq!(
        s.download_folder,
        get(data, &["download_folder"]).as_str().unwrap_or_default()
    );
    assert_eq!(
        (s.download_limit, s.upload_limit),
        (5 * 1024 * 1024, 256 * 1024)
    );
    assert_eq!(
        s.last_update_check,
        get(data, &["last_update_check"]).as_f64().unwrap_or(-1.0)
    );
    assert!(s.bridge);
    let g = h
        .group()
        .unwrap_or_else(|| unreachable!("the group carried over"));
    assert_eq!(
        json!({"id": g.id, "name": g.name, "password": g.password, "salt": g.salt}),
        json!({
            "id": get(data, &["group", "id"]),
            "name": get(data, &["group", "name"]),
            "password": get(data, &["group", "password"]),
            "salt": get(data, &["group", "salt"]),
        })
    );
    let shares: Vec<Value> = h
        .shares()
        .iter()
        .map(|s| json!({"id": s.id, "path": s.path.to_string_lossy(), "label": s.label, "mode": s.mode()}))
        .collect();
    let py_shares: Vec<Value> = get(data, &["shares"])
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(|s| json!({"id": s["id"], "path": s["path"], "label": s["label"], "mode": s["mode"]}))
        .collect();
    assert_eq!(shares, py_shares);
    assert_eq!(shares.len(), 2);
    let texts: Vec<Value> = h.chat().iter().map(|m| m["text"].clone()).collect();
    assert_eq!(
        texts,
        [
            json!("Essen ist fertig 🍲"),
            json!("Komme gleich"),
            json!("Nur für dich")
        ]
    );
    let names: Vec<String> = h
        .members()
        .into_iter()
        .filter(|m| !m.is_self)
        .map(|m| m.name)
        .collect();
    assert!(
        names.contains(&"Küche".to_owned()) && names.contains(&"Büro".to_owned()),
        "{names:?}"
    );
    assert_eq!(h.manual_hosts(), ["192.0.2.11:7480", "198.51.100.4"]);
    let jobs: Vec<Value> = {
        let mut j: Vec<Value> = h
            .transfers()
            .iter()
            .map(|j| json!({"id": j.id, "name": j.name, "status": j.status.as_str(), "message": j.message}))
            .collect();
        j.sort_by_key(|v| v["id"].as_str().unwrap_or_default().to_owned());
        j
    };
    let py_jobs: Vec<Value> = get(&before, &["transfers"])
        .as_array()
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(|j| json!({"id": j["id"], "name": j["name"], "status": j["status"], "message": j["message"]}))
        .collect();
    assert_eq!(jobs, py_jobs, "the transfer history carried over");
    assert_eq!(
        hearth_core::relaunch::read_ui_mode(&home),
        Some(hearth_core::relaunch::UiMode::Window)
    );

    // Running a while (discovery, upkeep against the known PCs, which are not there) changes
    // nothing the Python app keeps.
    std::thread::sleep(Duration::from_secs(3));
    assert_eq!(
        user_data(&dump(&home)),
        user_data(&before),
        "running changed nothing"
    );
    assert_eq!(read(&home.join("device.key")), key);

    // ---- Changes made by this version are read by the Python app, beside everything else.
    h.set_device_name("Den PC 2");
    h.set_theme("cyan");
    let extra = files.join("Musik");
    std::fs::create_dir_all(&extra).unwrap_or_else(|e| unreachable!("{e}"));
    let added = h
        .add_share(&extra, "", true)
        .unwrap_or_else(|e| unreachable!("{e}"));
    h.add_host("203.0.113.7");
    h.set_limits(Some(0), None);
    let _ = h.send_chat("Hallo aus Rust", None);
    let after = dump(&home);
    let a = get(&after, &["data"]);
    assert_eq!(a["device_name"], json!("Den PC 2"));
    assert_eq!(a["theme"], json!("cyan"));
    assert_eq!(a["download_limit_bps"], json!(0.0));
    assert_eq!(a["upload_limit_bps"], json!(262144.0));
    assert_eq!(a["shares"].as_array().map(Vec::len), Some(3));
    assert_eq!(a["shares"][2]["id"], json!(added.id));
    assert_eq!(a["shares"][2]["mode"], json!("readwrite"));
    assert_eq!(a["shares"][2]["label"], json!("Musik"));
    assert_eq!(
        a["manual_hosts"],
        json!(["192.0.2.11:7480", "198.51.100.4", "203.0.113.7"])
    );
    assert_eq!(a["chat"].as_array().map(Vec::len), Some(4));
    assert_eq!(a["chat"][3]["text"], json!("Hallo aus Rust"));
    // Everything else exactly as the Python app left it.
    for k in [
        "group",
        "language",
        "bridge",
        "auto_check_updates",
        "download_folder",
        "last_update_check",
    ] {
        assert_eq!(a[k], data[k], "{k} kept");
    }
    assert_eq!(a["shares"][0], data["shares"][0]);
    assert_eq!(a["shares"][1], data["shares"][1]);
    for i in 0..3 {
        assert_eq!(a["chat"][i], data["chat"][i], "message {i} kept");
    }
    // The known PCs as they were (the added host may seed one more, as 0.4 does).
    let peers_after = user_data(&after)["data"]["known_peers"].clone();
    let peers_before = user_data(&before)["data"]["known_peers"].clone();
    for (id, p) in peers_before.as_object().into_iter().flatten() {
        assert_eq!(&peers_after[id], p, "{id} kept");
    }
    assert_eq!(after["device_id"], before["device_id"]);
    assert_eq!(after["device_key"], before["device_key"]);
    assert_eq!(after["transfers"], before["transfers"]);
    assert_eq!(after["relaunch_ui"], json!("window"));
    // What a newer version wrote survives this one's saves.
    let saved: Value =
        serde_json::from_slice(&read(&state)).unwrap_or_else(|e| unreachable!("{e}"));
    assert_eq!(saved["from_a_newer_hearth"], json!({"kept": true}));
    // Files this version never writes.
    assert_eq!(read(&home.join("device.key")), key);
    assert_eq!(read(&home.join("inbox/received.txt")), inbox);
    assert!(
        read(&home.join("hearth.log")).starts_with(&log),
        "the log is only appended to"
    );
}
