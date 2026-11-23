//! The engine: one per profile. It owns the state, serves other PCs, finds them, keeps the
//! group's chat in step and runs transfers, on its own threads; the interface reads
//! snapshots and is told when something changed.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

use crate::state::{Group, Share, Store};
use crate::throttle::Bucket;
use crate::{crypto, invite, net};

/// What changed (the interface refreshes what shows it).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    /// PCs came, went or changed.
    Peers,
    /// Groups announced nearby (before joining).
    Nearby,
    /// The group was made, joined or left.
    Group,
    /// This PC's shares.
    Shares,
    /// Settings.
    Settings,
    /// Extra hosts.
    Hosts,
    /// A chat message.
    Chat,
    /// Transfer progress.
    Transfers,
}

type Notify = Arc<dyn Fn(Event) + Send + Sync>;

/// Seconds since 1970, as 0.3.x stamps things.
pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// How long a PC counts as online after it was last heard from.
pub const ONLINE_FOR: f64 = 30.0;

pub(crate) struct Inner {
    pub(crate) store: Mutex<Store>,
    /// The group key and the token it makes (PBKDF2 runs once per group).
    pub(crate) key: Mutex<Option<([u8; 32], String)>>,
    pub(crate) online: Mutex<HashMap<String, Map<String, Value>>>,
    pub(crate) nearby: Mutex<HashMap<String, Map<String, Value>>>,
    pub(crate) port: AtomicU16,
    pub(crate) download: Bucket,
    pub(crate) upload: Bucket,
    pub(crate) transfers: crate::transfers::Transfers,
    notify: Mutex<Option<Notify>>,
    pub(crate) http: reqwest::Client,
    /// Wakes discovery to restart with new settings.
    pub(crate) rediscover: tokio::sync::Notify,
    pub(crate) max_upload: u64,
}

/// The running engine. Cheap to clone; every clone is the same engine.
#[derive(Clone)]
pub struct Hearth {
    pub(crate) inner: Arc<Inner>,
    rt: Arc<tokio::runtime::Runtime>,
}

impl std::fmt::Debug for Hearth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Hearth(port {})", self.port())
    }
}

/// A PC as the interface shows it.
#[derive(Clone, Debug, PartialEq)]
pub struct Member {
    /// Its id.
    pub device_id: String,
    /// Its name.
    pub name: String,
    /// This PC.
    pub is_self: bool,
    /// Heard from in the last 30 seconds.
    pub online: bool,
    /// Its addresses.
    pub ips: Vec<String>,
    /// It relays beacons across subnets.
    pub bridge: bool,
}

/// A group announced nearby, which this PC could join with the password.
#[derive(Clone, Debug, PartialEq)]
pub struct Offer {
    /// The group's id.
    pub group_id: String,
    /// Its name.
    pub group_name: String,
    /// Its salt (offers without one cannot be joined in one step).
    pub salt: Option<String>,
    /// The PC announcing it.
    pub from: String,
    /// Where to reach that PC.
    pub host: String,
}

/// Settings the interface shows and changes.
#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    /// This PC's name.
    pub device_name: String,
    /// The theme id.
    pub theme: String,
    /// The typeface's id ("" for the default one).
    pub font: String,
    /// Relay beacons across subnets.
    pub bridge: bool,
    /// Check for updates on its own.
    pub auto_check_updates: bool,
    /// Where downloads go ("" for the default).
    pub download_folder: String,
    /// Speed limits, bytes a second (0: none).
    pub download_limit: u64,
    /// Speed limits, bytes a second (0: none).
    pub upload_limit: u64,
    /// When updates were last checked (seconds since 1970; 0 never).
    pub last_update_check: f64,
    /// The interface language as saved: a code such as "fr", or "" to follow the system.
    pub language: String,
}

impl Hearth {
    /// Starts the engine on the profile at `root`, listening on `port` (or a free port when it
    /// is taken), and starts discovery and upkeep.
    pub fn start(root: &Path, port: u16) -> std::io::Result<Hearth> {
        let store = Store::open(root)?;
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("hearth-net")
            .enable_all()
            .build()?;
        let http = reqwest::Client::builder()
            .no_proxy()
            .tcp_nodelay(true)
            .build()
            .map_err(|e| std::io::Error::other(e.to_string()))?;
        let limits = (
            store.number("download_limit_bps").max(0.0) as u64,
            store.number("upload_limit_bps").max(0.0) as u64,
        );
        let max_upload = std::env::var("HEARTH_MAX_UPLOAD")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(8 * 1024 * 1024 * 1024);
        let transfers = crate::transfers::Transfers::load(root);
        let inner = Arc::new(Inner {
            store: Mutex::new(store),
            key: Mutex::new(None),
            online: Mutex::new(HashMap::new()),
            nearby: Mutex::new(HashMap::new()),
            port: AtomicU16::new(port),
            download: Bucket::new(limits.0),
            upload: Bucket::new(limits.1),
            transfers,
            notify: Mutex::new(None),
            http,
            rediscover: tokio::sync::Notify::new(),
            max_upload,
        });
        let h = Hearth {
            inner,
            rt: Arc::new(rt),
        };
        h.refresh_key();
        // The port: the one asked for, else whatever is free (advertised as it is).
        let listener = h.rt.block_on(async {
            match tokio::net::TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], port))).await {
                Ok(l) => Ok(l),
                Err(_) => tokio::net::TcpListener::bind(SocketAddr::from(([0, 0, 0, 0], 0))).await,
            }
        })?;
        h.inner
            .port
            .store(listener.local_addr()?.port(), Ordering::SeqCst);
        let app = crate::server::router(h.clone());
        h.rt.spawn(async move {
            let _ = axum::serve(
                listener,
                app.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await;
        });
        crate::discovery::start(&h);
        let h2 = h.clone();
        h.rt.spawn(async move { h2.upkeep().await });
        Ok(h)
    }

    /// Calls `f` whenever something changes (from the engine's threads).
    pub fn on_event(&self, f: impl Fn(Event) + Send + Sync + 'static) {
        if let Ok(mut n) = self.inner.notify.lock() {
            *n = Some(Arc::new(f));
        }
    }

    pub(crate) fn emit(&self, e: Event) {
        let f = self.inner.notify.lock().ok().and_then(|n| n.clone());
        if let Some(f) = f {
            f(e);
        }
    }

    /// Runs `fut` on the engine's threads and hands its result to `done` (on those threads).
    pub fn spawn<T: Send + 'static>(
        &self,
        fut: impl std::future::Future<Output = T> + Send + 'static,
        done: impl FnOnce(T) + Send + 'static,
    ) {
        self.rt.spawn(async move {
            let v = fut.await;
            done(v);
        });
    }

    /// Runs `fut` to completion on the engine (tests, shutdown).
    pub fn block_on<T>(&self, fut: impl std::future::Future<Output = T>) -> T {
        self.rt.block_on(fut)
    }

    pub(crate) fn store(&self) -> MutexGuard<'_, Store> {
        self.inner
            .store
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub(crate) fn save(&self) {
        let _ = self.store().save();
    }

    /// The port this PC listens on.
    pub fn port(&self) -> u16 {
        self.inner.port.load(Ordering::SeqCst)
    }

    /// This PC's id.
    pub fn device_id(&self) -> String {
        self.store().device.id.clone()
    }

    /// The profile folder.
    pub fn root(&self) -> PathBuf {
        self.store().root().to_path_buf()
    }

    pub(crate) fn refresh_key(&self) {
        let g = self.store().group();
        let key = g.and_then(|g| {
            let salt = hex::decode(&g.salt).ok()?;
            let k = crypto::group_key(&g.id, &g.password, &salt);
            Some((k, crypto::auth_token(&k, &g.id)))
        });
        if let Ok(mut slot) = self.inner.key.lock() {
            *slot = key;
        }
    }

    /// The group key and token.
    pub(crate) fn key(&self) -> Option<([u8; 32], String)> {
        self.inner.key.lock().ok().and_then(|k| k.clone())
    }

    // ---------- what the interface shows ----------

    /// The group.
    pub fn group(&self) -> Option<Group> {
        self.store().group()
    }

    /// The invite for this group (every local address at this PC's port).
    pub fn invite(&self) -> Option<String> {
        let g = self.group()?;
        let hosts: Vec<String> = net::local_ipv4s()
            .into_iter()
            .map(|ip| format!("{ip}:{}", self.port()))
            .collect();
        Some(invite::make(&g.id, &g.salt, &g.password, &g.name, &hosts))
    }

    /// The PCs in the group: this one first, then online ones, then by name.
    pub fn members(&self) -> Vec<Member> {
        // One lock at a time: a second `store()` in the same statement would wait forever.
        let (device_id, name, bridge) = {
            let s = self.store();
            (s.device.id.clone(), s.device_name(), s.flag("bridge"))
        };
        let me = Member {
            device_id,
            name,
            is_self: true,
            online: true,
            ips: net::local_ipv4s(),
            bridge,
        };
        if self.group().is_none() {
            return vec![me];
        }
        let t = now();
        let online = self
            .inner
            .online
            .lock()
            .map(|o| o.clone())
            .unwrap_or_default();
        let mut all: HashMap<String, Map<String, Value>> = self
            .store()
            .known_peers()
            .into_iter()
            .filter_map(|(k, v)| v.as_object().map(|o| (k, o.clone())))
            .collect();
        for (k, v) in &online {
            all.insert(k.clone(), v.clone());
        }
        let mut others: Vec<Member> = all
            .into_iter()
            .filter(|(id, _)| *id != me.device_id)
            .map(|(id, p)| {
                let seen = online
                    .get(&id)
                    .and_then(|o| o.get("last_seen"))
                    .and_then(Value::as_f64)
                    .unwrap_or(0.0);
                Member {
                    name: p
                        .get("name")
                        .and_then(Value::as_str)
                        .unwrap_or("PC")
                        .to_owned(),
                    online: online.contains_key(&id) && t - seen < ONLINE_FOR,
                    ips: p
                        .get("ips")
                        .and_then(Value::as_array)
                        .map(|a| {
                            a.iter()
                                .filter_map(|i| i.as_str().map(str::to_owned))
                                .collect()
                        })
                        .unwrap_or_default(),
                    bridge: p.get("bridge").and_then(Value::as_bool).unwrap_or(false),
                    device_id: id,
                    is_self: false,
                }
            })
            .collect();
        others.sort_by(|a, b| {
            b.online
                .cmp(&a.online)
                .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
        });
        let mut out = vec![me];
        out.extend(others);
        out
    }

    /// Groups announced nearby in the last 30 seconds.
    pub fn offers(&self) -> Vec<Offer> {
        let t = now();
        let nearby = self
            .inner
            .nearby
            .lock()
            .map(|n| n.clone())
            .unwrap_or_default();
        let mut out: Vec<Offer> = nearby
            .into_iter()
            .filter(|(_, p)| {
                t - p.get("last_seen").and_then(Value::as_f64).unwrap_or(0.0) < ONLINE_FOR
            })
            .map(|(gid, p)| {
                let s = |k: &str| p.get(k).and_then(Value::as_str).map(str::to_owned);
                let addr = s("address").unwrap_or_default();
                let port = p
                    .get("port")
                    .and_then(Value::as_u64)
                    .unwrap_or(u64::from(net::PEER_PORT));
                Offer {
                    group_id: gid,
                    group_name: s("group_name").unwrap_or_else(|| "Home".into()),
                    salt: s("salt").filter(|x| !x.is_empty()),
                    from: s("name").unwrap_or_else(|| "PC".into()),
                    host: format!("{addr}:{port}"),
                }
            })
            .collect();
        out.sort_by(|a, b| a.group_name.cmp(&b.group_name));
        out
    }

    /// This PC's shares.
    pub fn shares(&self) -> Vec<Share> {
        self.store().shares()
    }

    /// The last 200 chat messages, oldest first.
    pub fn chat(&self) -> Vec<Value> {
        let me = self.device_id();
        let c = self.store().chat();
        // Public messages, and private ones to or from this PC; then the last 200 (0.4).
        let visible: Vec<Value> = c
            .into_iter()
            .filter(|m| {
                crate::server::is_public(m)
                    || m.get("to").and_then(Value::as_str) == Some(me.as_str())
                    || m.get("from_id").and_then(Value::as_str) == Some(me.as_str())
            })
            .collect();
        let skip = visible.len().saturating_sub(200);
        visible.into_iter().skip(skip).collect()
    }

    /// The settings.
    pub fn settings(&self) -> Settings {
        let s = self.store();
        Settings {
            device_name: s.device_name(),
            theme: s.theme(),
            font: s.font(),
            bridge: s.flag("bridge"),
            auto_check_updates: s.flag("auto_check_updates"),
            download_folder: s.download_folder(),
            download_limit: s.number("download_limit_bps").max(0.0) as u64,
            upload_limit: s.number("upload_limit_bps").max(0.0) as u64,
            last_update_check: s.number("last_update_check"),
            language: s.language(),
        }
    }

    /// Extra hosts added by hand.
    pub fn manual_hosts(&self) -> Vec<String> {
        self.store().manual_hosts()
    }

    // ---------- changes ----------

    /// Renames this PC.
    pub fn set_device_name(&self, name: &str) {
        self.store().set_device_name(name);
        self.save();
        self.inner.rediscover.notify_waiters();
        self.emit(Event::Settings);
    }

    /// Sets the theme id.
    pub fn set_theme(&self, id: &str) {
        self.store().set_theme(id);
        self.save();
        self.emit(Event::Settings);
    }

    /// Sets the typeface's id ("" for the default one).
    pub fn set_font(&self, id: &str) {
        self.store().set_font(id);
        self.save();
        self.emit(Event::Settings);
    }

    /// Sets the interface language: a supported code (or a name such as "Deutsch"), or ""
    /// to follow the system. Resetting the settings leaves it alone, as in 0.3.x.
    pub fn set_language(&self, code: &str) -> Result<(), String> {
        let chosen = if code.trim().is_empty() {
            ""
        } else {
            crate::state::normalize_language(code).ok_or_else(|| "Unknown language".to_owned())?
        };
        self.store().set_language(chosen);
        self.save();
        self.emit(Event::Settings);
        Ok(())
    }

    /// Relays beacons across subnets, or stops.
    pub fn set_bridge(&self, on: bool) {
        self.store().set_flag("bridge", on);
        self.save();
        self.inner.rediscover.notify_waiters();
        self.emit(Event::Settings);
    }

    /// Checks for updates on its own, or not.
    pub fn set_auto_check_updates(&self, on: bool) {
        self.store().set_flag("auto_check_updates", on);
        self.save();
        self.emit(Event::Settings);
    }

    /// Records an update check.
    pub fn set_last_update_check(&self, at: f64) {
        self.store().set_number("last_update_check", at);
        self.save();
    }

    /// Speed limits (bytes a second, 0: none); transfers under way follow them.
    pub fn set_limits(&self, download: Option<u64>, upload: Option<u64>) {
        if let Some(d) = download {
            self.store().set_number("download_limit_bps", d as f64);
            self.inner.download.set_rate(d);
        }
        if let Some(u) = upload {
            self.store().set_number("upload_limit_bps", u as f64);
            self.inner.upload.set_rate(u);
        }
        self.save();
        self.emit(Event::Settings);
    }

    /// Where downloads go ("" for the default); refused if it is not a folder.
    pub fn set_download_folder(&self, path: &str) -> Result<(), String> {
        if !path.is_empty() && !Path::new(path).is_dir() {
            return Err("That download folder does not exist on this PC".into());
        }
        self.store().set_download_folder(path);
        self.save();
        self.emit(Event::Settings);
        Ok(())
    }

    /// Where downloads go now: the chosen folder, else Downloads, else the profile's inbox.
    pub fn download_dir(&self) -> PathBuf {
        let chosen = self.store().download_folder();
        if !chosen.is_empty() && Path::new(&chosen).is_dir() {
            return PathBuf::from(chosen);
        }
        let home = std::env::var_os("USERPROFILE")
            .or_else(|| std::env::var_os("HOME"))
            .map(PathBuf::from)
            .unwrap_or_default();
        let downloads = home.join("Downloads");
        if downloads.is_dir() {
            return downloads;
        }
        let inbox = self.root().join("inbox");
        let _ = std::fs::create_dir_all(&inbox);
        inbox
    }

    /// Back to the defaults (theme, download folder, update checks, bridge, limits); the name,
    /// group, shares and hosts stay.
    pub fn reset_settings(&self) {
        {
            let mut s = self.store();
            s.set_theme("system");
            // Only a profile that chose a typeface has the key: the default writes none.
            if !s.font().is_empty() {
                s.set_font("");
            }
            s.set_download_folder("");
            s.set_flag("auto_check_updates", true);
            s.set_flag("bridge", false);
            s.set_number("download_limit_bps", 0.0);
            s.set_number("upload_limit_bps", 0.0);
        }
        self.inner.download.set_rate(0);
        self.inner.upload.set_rate(0);
        self.save();
        self.inner.rediscover.notify_waiters();
        self.emit(Event::Settings);
    }

    /// Starts a group.
    pub fn create_group(&self, name: &str, password: Option<&str>) -> Group {
        let g = self.store().create_group(name, password);
        self.after_group_change();
        g
    }

    /// Joins a group from a nearby offer with its password, then reaches the PC that offered it.
    pub fn join_offer(&self, offer: &Offer, password: &str) -> Result<(), String> {
        let salt = offer
            .salt
            .clone()
            .ok_or("This group is too old to one-click join. Paste an invite instead.")?;
        if hex::decode(&salt).is_err() {
            return Err("That group's details are damaged. Paste an invite instead.".into());
        }
        let g = Group {
            id: offer.group_id.clone(),
            name: offer.group_name.clone(),
            password: password.to_owned(),
            salt,
        };
        self.store().join_group(&g);
        self.after_group_change();
        self.add_host(&offer.host);
        Ok(())
    }

    /// Joins with an invite, then reaches every PC it names.
    pub fn join_invite(&self, text: &str) -> Result<Group, String> {
        let i = invite::parse(text).map_err(|e| e.message().to_owned())?;
        if hex::decode(&i.salt).is_err() {
            return Err("Invite code is incomplete".into());
        }
        let g = Group {
            id: i.group_id,
            name: if i.name.trim().is_empty() {
                "Home".into()
            } else {
                i.name
            },
            password: i.password,
            salt: i.salt,
        };
        self.store().join_group(&g);
        self.after_group_change();
        for h in i.hosts {
            self.add_host(&h);
        }
        Ok(g)
    }

    /// Leaves the group (shares and this PC's identity stay).
    pub fn leave_group(&self) {
        self.store().leave_group();
        self.after_group_change();
    }

    fn after_group_change(&self) {
        self.save();
        self.refresh_key();
        if let Ok(mut o) = self.inner.online.lock() {
            o.clear();
        }
        if let Ok(mut n) = self.inner.nearby.lock() {
            n.clear();
        }
        self.inner.rediscover.notify_waiters();
        self.emit(Event::Group);
        self.emit(Event::Peers);
    }

    /// Shares a folder.
    pub fn add_share(&self, path: &Path, label: &str, writable: bool) -> Result<Share, String> {
        if !crate::paths::folder_is_available(&path.to_string_lossy()) {
            return Err("Folder does not exist on this PC".into());
        }
        let s = self.store().add_share(path, label, writable);
        self.save();
        self.emit(Event::Shares);
        Ok(s)
    }

    /// Changes a share.
    pub fn update_share(
        &self,
        id: &str,
        path: Option<&Path>,
        label: Option<&str>,
        writable: Option<bool>,
    ) -> Result<(), String> {
        if path.is_some_and(|p| !crate::paths::folder_is_available(&p.to_string_lossy())) {
            return Err("That folder does not exist on this PC".into());
        }
        if !self.store().update_share(id, path, label, writable) {
            return Err("Unknown share".into());
        }
        self.save();
        self.emit(Event::Shares);
        Ok(())
    }

    /// Stops sharing a folder.
    pub fn remove_share(&self, id: &str) {
        if self.store().remove_share(id) {
            self.save();
            self.emit(Event::Shares);
        }
    }

    /// Adds a host to reach directly (across a sub-router) and tries it now.
    pub fn add_host(&self, host: &str) {
        let h = host.trim().to_owned();
        if h.is_empty() {
            return;
        }
        self.store().add_manual_host(&h);
        self.save();
        self.inner.rediscover.notify_waiters();
        self.emit(Event::Hosts);
        let me = self.clone();
        self.rt.spawn(async move {
            if let Some((ip, port)) = net::parse_host(&h)
                && !crate::client::handshake(&me, &ip, port).await
            {
                me.seed_peer(&ip, port);
            }
        });
    }

    /// Forgets a host.
    pub fn remove_host(&self, host: &str) {
        self.store().remove_manual_host(host);
        self.save();
        self.inner.rediscover.notify_waiters();
        self.emit(Event::Hosts);
    }

    /// A placeholder for a PC an invite named that has not answered yet (replaced when it does).
    fn seed_peer(&self, ip: &str, port: u16) {
        let id = format!("seed-{ip}-{port}");
        let known = self.inner.online.lock().map(|o| {
            o.values().any(|p| {
                p.get("address").and_then(Value::as_str) == Some(ip)
                    && p.get("port").and_then(Value::as_u64) == Some(u64::from(port))
                    && !p
                        .get("device_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .starts_with("seed-")
            })
        });
        if known.unwrap_or(false) {
            return;
        }
        let peer = json!({"device_id": id, "name": ip, "address": ip, "ips": [ip], "port": port, "via": "invite"});
        if let Value::Object(p) = peer {
            self.peer_seen(p);
        }
    }

    /// A PC was heard from: online now, remembered, and placeholders for its address dropped.
    pub(crate) fn peer_seen(&self, mut peer: Map<String, Value>) {
        let Some(id) = peer
            .get("device_id")
            .and_then(Value::as_str)
            .map(str::to_owned)
        else {
            return;
        };
        if id == self.device_id() {
            return;
        }
        let t = now();
        if self.group().is_none() {
            if let Some(gid) = peer
                .get("group_id")
                .and_then(Value::as_str)
                .map(str::to_owned)
            {
                peer.insert("last_seen".into(), json!(t));
                if let Ok(mut n) = self.inner.nearby.lock() {
                    n.insert(gid, peer);
                }
                self.emit(Event::Nearby);
            }
            return;
        }
        // A real PC at a placeholder's address replaces it.
        if !id.starts_with("seed-") {
            let addr = peer
                .get("address")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_owned();
            let port = peer.get("port").and_then(Value::as_u64).unwrap_or(0);
            let seed = format!("seed-{addr}-{port}");
            if let Ok(mut o) = self.inner.online.lock() {
                o.remove(&seed);
            }
            self.store().forget_peer(&seed);
        }
        let mut stored = peer.clone();
        stored.insert("last_seen".into(), json!(t));
        let changed = {
            let mut o = self
                .inner
                .online
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let before = o
                .get(&id)
                .map(|p| (p.get("name").cloned(), p.get("address").cloned()));
            o.insert(id.clone(), stored);
            before != Some((peer.get("name").cloned(), peer.get("address").cloned()))
        };
        self.store().remember_peer(&id, &peer, t);
        if changed {
            self.save();
            self.emit(Event::Peers);
        }
    }

    /// Where to reach a PC: its address, then its other addresses (as `(host, port)`).
    pub(crate) fn candidates(&self, device_id: &str) -> Vec<(String, u16)> {
        let p = self
            .inner
            .online
            .lock()
            .ok()
            .and_then(|o| o.get(device_id).cloned())
            .or_else(|| {
                self.store()
                    .known_peers()
                    .get(device_id)
                    .and_then(Value::as_object)
                    .cloned()
            });
        let Some(p) = p else { return Vec::new() };
        let port = p
            .get("port")
            .and_then(Value::as_u64)
            .and_then(|x| u16::try_from(x).ok())
            .unwrap_or(net::PEER_PORT);
        let mut raw: Vec<String> = p
            .get("address")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .into_iter()
            .collect();
        raw.extend(
            p.get("ips")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_str().map(str::to_owned)),
        );
        let mut out: Vec<(String, u16)> = Vec::new();
        for r in raw.into_iter().filter(|r| !r.is_empty()) {
            let c = if r.matches(':').count() == 1 && !r.starts_with('[') {
                let (h, pt) = r.split_once(':').unwrap_or((&r, ""));
                (h.to_owned(), pt.parse().unwrap_or(port))
            } else {
                (r.split(':').next().unwrap_or(&r).to_owned(), port)
            };
            if !out.contains(&c) {
                out.push(c);
            }
        }
        out
    }

    /// Every 5 seconds: say hello to known hosts and online PCs, and fetch chat.
    async fn upkeep(&self) {
        loop {
            if self.group().is_some() {
                let mut targets: Vec<(String, u16)> = self
                    .manual_hosts()
                    .iter()
                    .filter_map(|h| net::parse_host(h))
                    .collect();
                let online: Vec<Map<String, Value>> = self
                    .inner
                    .online
                    .lock()
                    .map(|o| o.values().cloned().collect())
                    .unwrap_or_default();
                for p in &online {
                    if let Some(a) = p.get("address").and_then(Value::as_str) {
                        let port = p
                            .get("port")
                            .and_then(Value::as_u64)
                            .and_then(|x| u16::try_from(x).ok())
                            .unwrap_or(net::PEER_PORT);
                        targets.push((a.to_owned(), port));
                    }
                }
                targets.sort();
                targets.dedup();
                // Concurrently: one silent PC must not hold up the others.
                let shakes = targets.into_iter().map(|(ip, port)| {
                    let me = self.clone();
                    async move { crate::client::handshake(&me, &ip, port).await }
                });
                futures_util::future::join_all(shakes).await;
                crate::client::pull_chat(self).await;
            }
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    }
}
