//! Transfers: downloads (four at a time) and uploads with progress, speed and time left;
//! finished ones are remembered in `transfers.json` until cleared (files are never deleted).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Instant;

use serde_json::{Value, json};
use tokio_util::sync::CancellationToken;

use crate::engine::{Event, Hearth, now};

/// Where a transfer is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    /// Waiting for a free slot.
    Queued,
    /// Moving bytes.
    Running,
    /// Finished.
    Done,
    /// Failed (see the message).
    Error,
    /// Stopped by the user.
    Canceled,
}

impl Status {
    fn as_str(self) -> &'static str {
        match self {
            Status::Queued => "queued",
            Status::Running => "running",
            Status::Done => "done",
            Status::Error => "error",
            Status::Canceled => "canceled",
        }
    }

    fn parse(s: &str) -> Status {
        match s {
            "queued" => Status::Queued,
            "running" => Status::Running,
            "done" => Status::Done,
            "canceled" => Status::Canceled,
            _ => Status::Error,
        }
    }

    /// Done, failed or canceled.
    pub fn finished(self) -> bool {
        matches!(self, Status::Done | Status::Error | Status::Canceled)
    }
}

/// One transfer.
#[derive(Clone, Debug, PartialEq)]
pub struct Job {
    /// 12 hex characters.
    pub id: String,
    /// `download` or `upload`.
    pub kind: String,
    /// The file's name.
    pub name: String,
    /// Where it was saved (downloads).
    pub dest: Option<PathBuf>,
    /// Bytes so far.
    pub bytes: u64,
    /// Bytes in all (0: unknown).
    pub total: u64,
    /// Bytes a second, recently.
    pub speed: f64,
    /// Seconds left, when known.
    pub eta: Option<f64>,
    /// Where it is.
    pub status: Status,
    /// "Saved", "Canceled", or what went wrong.
    pub message: String,
    started: f64,
    updated: f64,
}

impl Job {
    fn to_json(&self) -> Value {
        json!({
            "id": self.id, "kind": self.kind, "name": self.name, "dest": self.dest.as_ref().map(|d| d.to_string_lossy()),
            "bytes": self.bytes, "total": self.total, "speed": self.speed, "eta": self.eta, "status": self.status.as_str(),
            "message": self.message, "started": self.started, "updated": self.updated,
        })
    }

    fn from_json(v: &Value) -> Option<Job> {
        let s = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_owned);
        Some(Job {
            id: s("id")?,
            kind: s("kind").unwrap_or_else(|| "download".into()),
            name: s("name").unwrap_or_default(),
            dest: s("dest").map(PathBuf::from),
            bytes: v.get("bytes").and_then(Value::as_f64).unwrap_or(0.0) as u64,
            total: v.get("total").and_then(Value::as_f64).unwrap_or(0.0) as u64,
            speed: 0.0,
            eta: None,
            status: Status::parse(&s("status").unwrap_or_default()),
            message: s("message").unwrap_or_default(),
            started: v.get("started").and_then(Value::as_f64).unwrap_or(0.0),
            updated: v.get("updated").and_then(Value::as_f64).unwrap_or(0.0),
        })
    }
}

/// Every transfer, and how to stop the running ones.
#[derive(Debug)]
pub struct Transfers {
    jobs: Mutex<Vec<Job>>,
    cancels: Mutex<HashMap<String, CancellationToken>>,
    file: PathBuf,
    gate: tokio::sync::Semaphore,
}

impl Transfers {
    /// The finished transfers remembered in the profile.
    pub(crate) fn load(root: &Path) -> Transfers {
        let file = root.join("transfers.json");
        let jobs: Vec<Job> = std::fs::read_to_string(&file)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| {
                v.as_array().map(|a| {
                    a.iter()
                        .filter_map(Job::from_json)
                        .filter(|j| j.status.finished())
                        .collect()
                })
            })
            .unwrap_or_default();
        Transfers {
            jobs: Mutex::new(jobs),
            cancels: Mutex::new(HashMap::new()),
            file,
            gate: tokio::sync::Semaphore::new(4),
        }
    }

    fn persist(&self, jobs: &[Job]) {
        let list: Vec<Value> = jobs
            .iter()
            .filter(|j| j.status.finished())
            .map(Job::to_json)
            .collect();
        let tmp = self.file.with_extension("tmp");
        if std::fs::write(&tmp, crate::pyjson::dumps_indent(&Value::Array(list))).is_ok() {
            let _ = std::fs::rename(&tmp, &self.file);
        }
    }

    /// Every transfer, oldest first.
    pub fn list(&self) -> Vec<Job> {
        self.jobs.lock().map(|j| j.clone()).unwrap_or_default()
    }

    fn add(&self, kind: &str, name: &str) -> (String, CancellationToken) {
        let id = crate::crypto::random_hex(6);
        let t = now();
        let job = Job {
            id: id.clone(),
            kind: kind.into(),
            name: name.into(),
            dest: None,
            bytes: 0,
            total: 0,
            speed: 0.0,
            eta: None,
            status: Status::Queued,
            message: String::new(),
            started: t,
            updated: t,
        };
        if let Ok(mut jobs) = self.jobs.lock() {
            jobs.push(job);
            // Long lists keep their most recent finished entries.
            if jobs.len() > 80 {
                let mut finished = jobs.iter().filter(|j| j.status.finished()).count();
                jobs.retain(|j| {
                    if j.status.finished() && finished > 40 {
                        finished -= 1;
                        false
                    } else {
                        true
                    }
                });
            }
        }
        let token = CancellationToken::new();
        if let Ok(mut c) = self.cancels.lock() {
            c.insert(id.clone(), token.clone());
        }
        (id, token)
    }

    fn update(&self, id: &str, f: impl FnOnce(&mut Job)) {
        let Ok(mut jobs) = self.jobs.lock() else {
            return;
        };
        if let Some(j) = jobs.iter_mut().find(|j| j.id == id) {
            let was = j.status;
            f(j);
            j.updated = now();
            if j.status.finished() && !was.finished() {
                self.persist(&jobs);
                if let Ok(mut c) = self.cancels.lock() {
                    c.remove(id);
                }
            }
        }
    }

    /// Stops a running transfer. False when there is none with that id.
    pub fn cancel(&self, id: &str) -> bool {
        match self.cancels.lock().ok().and_then(|c| c.get(id).cloned()) {
            Some(t) => {
                t.cancel();
                true
            }
            None => false,
        }
    }

    /// Removes a finished transfer from the list (the file stays). False for running ones.
    pub fn dismiss(&self, id: &str) -> bool {
        let Ok(mut jobs) = self.jobs.lock() else {
            return false;
        };
        let before = jobs.len();
        jobs.retain(|j| !(j.id == id && j.status.finished()));
        let removed = jobs.len() != before;
        if removed {
            self.persist(&jobs);
        }
        removed
    }

    /// Removes every finished transfer from the list (files stay).
    pub fn clear_finished(&self) {
        if let Ok(mut jobs) = self.jobs.lock() {
            jobs.retain(|j| !j.status.finished());
            self.persist(&jobs);
        }
    }
}

/// Tracks speed and time left from progress.
struct Meter {
    start: Instant,
    last_emit: Instant,
}

impl Meter {
    fn new() -> Self {
        Meter {
            start: Instant::now(),
            last_emit: Instant::now(),
        }
    }

    /// Bytes a second and seconds left; `true` when it is time to show progress (every 0.25 s).
    fn tick(&mut self, bytes: u64, total: u64) -> (f64, Option<f64>, bool) {
        let secs = self.start.elapsed().as_secs_f64().max(0.001);
        let speed = bytes as f64 / secs;
        let eta = (total > bytes && speed > 0.0).then(|| (total - bytes) as f64 / speed);
        let show = self.last_emit.elapsed().as_millis() >= 250;
        if show {
            self.last_emit = Instant::now();
        }
        (speed, eta, show)
    }
}

impl Hearth {
    /// Every transfer.
    pub fn transfers(&self) -> Vec<Job> {
        self.inner.transfers.list()
    }

    /// Stops a running transfer.
    pub fn cancel_transfer(&self, id: &str) -> bool {
        self.inner.transfers.cancel(id)
    }

    /// Removes a finished transfer from the list.
    pub fn dismiss_transfer(&self, id: &str) -> bool {
        let ok = self.inner.transfers.dismiss(id);
        self.emit(Event::Transfers);
        ok
    }

    /// Removes every finished transfer from the list.
    pub fn clear_transfers(&self) {
        self.inner.transfers.clear_finished();
        self.emit(Event::Transfers);
    }

    /// Downloads files (relative paths in a share) from a PC, or copies them from this PC's own
    /// share, into the download folder. Returns the new transfers' ids.
    pub fn download(&self, device_id: &str, share_id: &str, paths: &[String]) -> Vec<String> {
        let mut ids = Vec::new();
        let mut unique: Vec<&String> = Vec::new();
        for p in paths {
            if !unique.contains(&p) {
                unique.push(p);
            }
        }
        for path in unique {
            let name = crate::paths::safe_filename(path.rsplit('/').next().unwrap_or(path));
            let (id, cancel) = self.inner.transfers.add("download", &name);
            ids.push(id.clone());
            let (me, dev, share, path) = (
                self.clone(),
                device_id.to_owned(),
                share_id.to_owned(),
                path.clone(),
            );
            self.spawn(
                async move {
                    let _slot = me.inner.transfers.gate.acquire().await;
                    me.inner
                        .transfers
                        .update(&id, |j| j.status = Status::Running);
                    me.emit(Event::Transfers);
                    let dest_dir = me.download_dir();
                    let meter = Mutex::new(Meter::new());
                    let (me2, id2) = (me.clone(), id.clone());
                    let progress = move |bytes: u64, total: u64| {
                        let (speed, eta, show) = meter
                            .lock()
                            .map(|mut m| m.tick(bytes, total))
                            .unwrap_or((0.0, None, false));
                        me2.inner.transfers.update(&id2, |j| {
                            j.bytes = bytes;
                            j.total = total;
                            j.speed = speed;
                            j.eta = eta;
                        });
                        if show {
                            me2.emit(Event::Transfers);
                        }
                    };
                    let result = if dev == me.device_id() {
                        me.copy_local(&share, &path, &dest_dir, &cancel, progress)
                            .await
                    } else {
                        crate::client::download(
                            &me, &dev, &share, &path, &dest_dir, &cancel, progress,
                        )
                        .await
                    };
                    me.inner.transfers.update(&id, |j| match result {
                        Ok(dest) => {
                            j.status = Status::Done;
                            j.message = "Saved".into();
                            j.dest = Some(dest);
                            j.eta = None;
                        }
                        Err(e) if e == "Canceled" => {
                            j.status = Status::Canceled;
                            j.message = "Canceled".into();
                        }
                        Err(e) => {
                            j.status = Status::Error;
                            j.message = e;
                        }
                    });
                    me.emit(Event::Transfers);
                },
                |()| {},
            );
        }
        self.emit(Event::Transfers);
        ids
    }

    async fn copy_local(
        &self,
        share: &str,
        path: &str,
        dest_dir: &Path,
        cancel: &CancellationToken,
        progress: impl Fn(u64, u64),
    ) -> Result<PathBuf, String> {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let (_, src) = crate::server::locate(self, share, path)
            .map_err(|_| "That file is no longer available.".to_owned())?;
        if !src.is_file() {
            return Err("That file is no longer available.".into());
        }
        let name = crate::paths::safe_filename(
            &src.file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default(),
        );
        tokio::fs::create_dir_all(dest_dir)
            .await
            .map_err(|e| e.to_string())?;
        let tmp = dest_dir.join(format!(".{name}.{}.part", crate::crypto::random_hex(4)));
        let mut from = tokio::fs::File::open(&src).await.map_err(|_| "This PC could not read or write that file. It may be locked or in a folder Hearth cannot use.".to_owned())?;
        let total = from.metadata().await.map(|m| m.len()).unwrap_or(0);
        let mut to = tokio::fs::File::create(&tmp)
            .await
            .map_err(|e| e.to_string())?;
        let mut buf = vec![0u8; crate::server::CHUNK];
        let mut got = 0u64;
        let r: Result<(), String> = async {
            loop {
                if cancel.is_cancelled() {
                    return Err("Canceled".into());
                }
                let n = from.read(&mut buf).await.map_err(|e| e.to_string())?;
                if n == 0 {
                    break;
                }
                to.write_all(buf.get(..n).unwrap_or_default())
                    .await
                    .map_err(|e| e.to_string())?;
                self.inner.download.take(n as u64).await;
                got += n as u64;
                progress(got, total);
            }
            to.flush().await.map_err(|e| e.to_string())
        }
        .await;
        drop(to);
        if let Err(e) = r {
            let _ = tokio::fs::remove_file(&tmp).await;
            return Err(e);
        }
        let dest = crate::paths::unique_name(dest_dir, &name);
        tokio::fs::rename(&tmp, &dest)
            .await
            .map_err(|e| e.to_string())?;
        Ok(dest)
    }

    /// Sends local files into a folder of a share (another PC's, or this PC's own).
    pub fn upload(
        &self,
        device_id: &str,
        share_id: &str,
        dir: &str,
        files: &[PathBuf],
    ) -> Vec<String> {
        let mut ids = Vec::new();
        for file in files {
            let name = file
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            let (id, _cancel) = self.inner.transfers.add("upload", &name);
            ids.push(id.clone());
            let (me, dev, share, dir, file) = (
                self.clone(),
                device_id.to_owned(),
                share_id.to_owned(),
                dir.to_owned(),
                file.clone(),
            );
            self.spawn(
                async move {
                    me.inner
                        .transfers
                        .update(&id, |j| j.status = Status::Running);
                    me.emit(Event::Transfers);
                    let meter = Mutex::new(Meter::new());
                    let (me2, id2) = (me.clone(), id.clone());
                    let progress = move |bytes: u64, total: u64| {
                        let (speed, eta, show) = meter
                            .lock()
                            .map(|mut m| m.tick(bytes, total))
                            .unwrap_or((0.0, None, false));
                        me2.inner.transfers.update(&id2, |j| {
                            j.bytes = bytes;
                            j.total = total;
                            j.speed = speed;
                            j.eta = eta;
                        });
                        if show {
                            me2.emit(Event::Transfers);
                        }
                    };
                    let result = if dev == me.device_id() {
                        me.upload_local(&share, &dir, &file).await
                    } else {
                        crate::client::upload(&me, &dev, &share, &dir, &file, progress).await
                    };
                    me.inner.transfers.update(&id, |j| match result {
                        Ok(_) => {
                            j.status = Status::Done;
                            j.bytes = j.total.max(j.bytes);
                            j.message = "Sent".into();
                        }
                        Err(e) => {
                            j.status = Status::Error;
                            j.message = e;
                        }
                    });
                    me.emit(Event::Transfers);
                    me.emit(Event::Shares);
                },
                |()| {},
            );
        }
        self.emit(Event::Transfers);
        ids
    }

    async fn upload_local(&self, share: &str, dir: &str, file: &Path) -> Result<String, String> {
        let (s, target) = crate::server::locate(self, share, dir)
            .map_err(|_| "That folder is no longer available.".to_owned())?;
        if !s.writable {
            return Err("Share is read-only".into());
        }
        let name = crate::paths::safe_filename(
            &file
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default(),
        );
        let raw = file
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        if crate::server::windows_illegal_name(&raw) {
            return Err("Windows names cannot end with a dot or a space".into());
        }
        // Uploads never make folders (0.4), on this PC either.
        if !target.is_dir() {
            return Err("That folder is not on this PC".into());
        }
        tokio::fs::copy(file, target.join(&name))
            .await
            .map_err(|e| e.to_string())?;
        Ok(name)
    }
}
