//! The Rust engine against a real Hearth 0.3.x peer (the Python app in this repository):
//! they must meet, browse, transfer and chat in both directions, and read each other's beacons.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use hearth_core::Hearth;
use serde_json::{Value, json};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap_or_else(|e| unreachable!("{e}"))
}

fn python(args: &[&str]) -> Command {
    let mut c = Command::new("python");
    c.args(args)
        .env("PYTHONPATH", repo_root())
        .env("HEARTH_NO_UPDATE", "1");
    c
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(0)
}

fn script() -> String {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/python_peer.py")
        .to_string_lossy()
        .into_owned()
}

struct Py {
    child: Child,
    port: u16,
    id: String,
    home: PathBuf,
}

impl Drop for Py {
    fn drop(&mut self) {
        drop(self.child.stdin.take());
        let _ = self.child.wait();
    }
}

fn serve(cfg: &Value, dir: &Path) -> Py {
    let path = dir.join("py.json");
    std::fs::write(&path, cfg.to_string()).unwrap_or_else(|e| unreachable!("{e}"));
    let mut child = python(&[&script(), "serve", &path.to_string_lossy()])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap_or_else(|e| unreachable!("python: {e}"));
    let mut line = String::new();
    let out = child
        .stdout
        .take()
        .unwrap_or_else(|| unreachable!("stdout"));
    BufReader::new(out)
        .read_line(&mut line)
        .unwrap_or_else(|e| unreachable!("{e}"));
    let parts: Vec<&str> = line.split_whitespace().collect();
    assert_eq!(
        parts.first(),
        Some(&"READY"),
        "the Python peer started: {line}"
    );
    Py {
        port: parts.get(1).and_then(|p| p.parse().ok()).unwrap_or(0),
        id: parts.get(2).map(|s| (*s).to_owned()).unwrap_or_default(),
        home: PathBuf::from(cfg["home"].as_str().unwrap_or_default()),
        child,
    }
}

fn wait(what: &str, secs: u64, mut ok: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        if ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    unreachable!("timed out waiting for {what}");
}

fn py_chat(py: &Py) -> Vec<String> {
    std::fs::read_to_string(py.home.join("state.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.get("chat").and_then(Value::as_array).cloned())
        .unwrap_or_default()
        .iter()
        .filter_map(|m| m.get("text").and_then(Value::as_str).map(str::to_owned))
        .collect()
}

#[test]
fn rust_and_python_peers_meet_browse_transfer_and_chat() {
    let tmp = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
    let d = tmp.path();
    for sub in [
        "rust-home",
        "rust-share",
        "py-home",
        "py-share/sub",
        "py-downloads",
        "rust-downloads",
    ] {
        std::fs::create_dir_all(d.join(sub)).unwrap_or_else(|e| unreachable!("{e}"));
    }
    std::fs::write(d.join("rust-share/from rust ä.txt"), b"made in rust")
        .unwrap_or_else(|e| unreachable!("{e}"));
    std::fs::write(d.join("py-share/hello.txt"), b"made in python")
        .unwrap_or_else(|e| unreachable!("{e}"));

    // The Rust PC starts the group and shares a folder.
    let rust =
        Hearth::start(&d.join("rust-home"), free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    rust.set_device_name("Rusty");
    let g = rust.create_group("Home", Some("correct-horse"));
    let rshare = rust
        .add_share(&d.join("rust-share"), "", false)
        .unwrap_or_else(|e| unreachable!("{e}"));
    rust.set_download_folder(&d.join("rust-downloads").to_string_lossy())
        .unwrap_or_else(|e| unreachable!("{e}"));

    // The Python PC joins it (as an invite would) with a writable share.
    let py = serve(
        &json!({
            "home": d.join("py-home"), "port": free_port(), "name": "Pythonic",
            "group": {"id": g.id, "name": g.name, "password": g.password, "salt": g.salt},
            "shares": [{"path": d.join("py-share"), "label": "Py Stuff", "mode": "readwrite"}],
            "download_folder": d.join("py-downloads"),
        }),
        d,
    );

    // They meet: the Rust PC says hello; both now know each other.
    rust.add_host(&format!("127.0.0.1:{}", py.port));
    wait("the Python PC online in Rust", 15, || {
        rust.members()
            .iter()
            .any(|m| m.device_id == py.id && m.online && m.name == "Pythonic")
    });

    // Rust browses Python.
    let shares = rust
        .block_on(rust.shares_of(&py.id))
        .unwrap_or_else(|e| unreachable!("{e}"));
    let s = shares
        .iter()
        .find(|s| s.label == "Py Stuff")
        .unwrap_or_else(|| unreachable!("{shares:?}"));
    assert!(s.writable);
    let entries = rust
        .block_on(rust.files_of(&py.id, &s.id, ""))
        .unwrap_or_else(|e| unreachable!("{e}"));
    let names: Vec<(&str, bool)> = entries.iter().map(|e| (e.name.as_str(), e.dir)).collect();
    assert_eq!(
        names,
        vec![("sub", true), ("hello.txt", false)],
        "folders first, as 0.3.x lists them"
    );
    // A path outside the share is refused by Python with its message.
    let err = rust
        .block_on(rust.files_of(&py.id, &s.id, "../"))
        .err()
        .unwrap_or_default();
    assert_eq!(err, "Path escapes the shared folder");

    // Rust downloads from Python, and uploads into Python's writable share.
    let ids = rust.download(&py.id, &s.id, &["hello.txt".into()]);
    wait("the download", 15, || {
        rust.transfers()
            .iter()
            .any(|j| ids.contains(&j.id) && j.status.finished())
    });
    let job = rust
        .transfers()
        .into_iter()
        .find(|j| ids.contains(&j.id))
        .unwrap_or_else(|| unreachable!());
    assert_eq!(job.message, "Saved", "{job:?}");
    assert_eq!(
        std::fs::read(d.join("rust-downloads/hello.txt"))
            .ok()
            .as_deref(),
        Some(&b"made in python"[..])
    );
    let up = d.join("upload me.bin");
    std::fs::write(&up, vec![7u8; 300_000]).unwrap_or_else(|e| unreachable!("{e}"));
    let ids = rust.upload(&py.id, &s.id, "sub", &[up]);
    wait("the upload", 15, || {
        rust.transfers()
            .iter()
            .any(|j| ids.contains(&j.id) && j.status.finished())
    });
    assert_eq!(
        std::fs::read(d.join("py-share/sub/upload me.bin"))
            .map(|b| b.len())
            .ok(),
        Some(300_000)
    );

    // Chat both ways.
    rust.send_chat("hello from rust", None)
        .unwrap_or_else(|e| unreachable!("{e}"));
    wait("Python to receive the chat", 15, || {
        py_chat(&py).iter().any(|t| t == "hello from rust")
    });
    let http = reqwest::blocking_client();
    let r = http(
        &format!("http://127.0.0.1:{}/api/chat", py.port),
        &json!({"text": "hello from python"}),
    );
    assert!(r, "the Python app accepted a local message");
    wait("Rust to receive the chat", 20, || {
        rust.chat()
            .iter()
            .any(|m| m.get("text").and_then(Value::as_str) == Some("hello from python"))
    });

    // Python downloads from Rust (a name with spaces and non-ASCII letters).
    let ok = http(
        &format!("http://127.0.0.1:{}/api/transfers/download", py.port),
        &json!({"device_id": rust.device_id(), "share_id": rshare.id, "paths": ["from rust ä.txt"]}),
    );
    assert!(ok, "Python started the download");
    wait("Python's download from Rust", 15, || {
        std::fs::read(d.join("py-downloads/from rust ä.txt")).is_ok_and(|b| b == b"made in rust")
    });
}

#[test]
fn beacons_read_both_ways_and_a_wrong_group_is_refused() {
    let tmp = tempfile::tempdir().unwrap_or_else(|e| unreachable!("{e}"));
    let rust =
        Hearth::start(&tmp.path().join("r"), free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    rust.set_device_name("Ræven 🔥");
    let g = rust.create_group("Wohnzimmer", Some("pw"));
    let cfg = json!({"home": tmp.path().join("p"), "port": 7474, "name": "Py",
        "group": {"id": g.id, "name": g.name, "password": g.password, "salt": g.salt}});
    let cfg_path = tmp.path().join("cfg.json");
    std::fs::write(&cfg_path, cfg.to_string()).unwrap_or_else(|e| unreachable!("{e}"));

    // Python's beacon, read by Rust.
    let out = python(&[&script(), "beacon", &cfg_path.to_string_lossy()])
        .output()
        .unwrap_or_else(|e| unreachable!("{e}"));
    let datagram = hex::decode(String::from_utf8_lossy(&out.stdout).trim()).unwrap_or_default();
    let peer = hearth_core::discovery::read(
        &rust,
        &datagram,
        "192.168.77.7"
            .parse()
            .unwrap_or(std::net::Ipv4Addr::LOCALHOST),
    );
    let peer = peer.unwrap_or_else(|| unreachable!("Rust accepted Python's beacon"));
    assert_eq!(peer.get("name").and_then(Value::as_str), Some("Py"));
    assert_eq!(
        peer.get("address").and_then(Value::as_str),
        Some("192.168.77.7")
    );

    // Rust's beacon (with non-ASCII names, which the MAC covers), read by Python.
    let mine = hearth_core::discovery::beacon(&rust).unwrap_or_else(|| unreachable!("a beacon"));
    let mut child = python(&[&script(), "accept", &cfg_path.to_string_lossy()])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap_or_else(|e| unreachable!("{e}"));
    child
        .stdin
        .take()
        .map(|mut i| i.write_all(hex::encode(&mine).as_bytes()))
        .transpose()
        .unwrap_or_else(|e| unreachable!("{e}"));
    let out = child
        .wait_with_output()
        .unwrap_or_else(|e| unreachable!("{e}"));
    let seen: Value = serde_json::from_slice(&out.stdout).unwrap_or(Value::Null);
    assert_eq!(
        seen.get("name").and_then(Value::as_str),
        Some("Ræven 🔥"),
        "Python verified Rust's MAC: {seen}"
    );
    assert_eq!(
        seen.get("group_name").and_then(Value::as_str),
        Some("Wohnzimmer")
    );

    // A beacon from another group, or tampered with, is ignored.
    let mut tampered: Value = serde_json::from_slice(&mine).unwrap_or(Value::Null);
    if let Some(name) = tampered.get_mut("body").and_then(|b| b.get_mut("name")) {
        *name = json!("Mallory");
    }
    let other = serde_json::to_vec(&tampered).unwrap_or_default();
    let rust2 =
        Hearth::start(&tmp.path().join("r2"), free_port()).unwrap_or_else(|e| unreachable!("{e}"));
    rust2
        .join_invite(&rust.invite().unwrap_or_default())
        .unwrap_or_else(|e| unreachable!("{e}"));
    assert!(
        hearth_core::discovery::read(&rust2, &mine, std::net::Ipv4Addr::LOCALHOST).is_some(),
        "same group: accepted"
    );
    assert!(
        hearth_core::discovery::read(&rust2, &other, std::net::Ipv4Addr::LOCALHOST).is_none(),
        "tampered: refused"
    );
}

/// Posts JSON to the Python app's local API (it only answers this PC).
mod reqwest {
    pub fn blocking_client() -> impl Fn(&str, &serde_json::Value) -> bool {
        |url: &str, body: &serde_json::Value| {
            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build();
            let Ok(rt) = rt else { return false };
            rt.block_on(async {
                let c = ::reqwest::Client::new();
                c.post(url)
                    .header("host", "127.0.0.1")
                    .json(body)
                    .send()
                    .await
                    .is_ok_and(|r| r.status().is_success())
            })
        }
    }
}
