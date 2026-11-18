//! What this PC asks other PCs, as Hearth 0.3.x asks them: the same requests, timeouts and
//! fallbacks over a PC's addresses.

use std::path::{Path, PathBuf};
use std::time::Duration;

use futures_util::StreamExt;
use serde_json::{Value, json};
use tokio::io::AsyncWriteExt;

use crate::engine::{Event, Hearth};
use crate::net;

/// A remote share.
#[derive(Clone, Debug, PartialEq)]
pub struct RemoteShare {
    /// Its id.
    pub id: String,
    /// What the house sees.
    pub label: String,
    /// Others may add files.
    pub writable: bool,
}

/// An entry in a shared folder.
#[derive(Clone, Debug, PartialEq)]
pub struct Entry {
    /// Its name.
    pub name: String,
    /// A folder.
    pub dir: bool,
    /// Bytes (0 for folders).
    pub size: u64,
    /// Modified, seconds since 1970.
    pub mtime: f64,
}

fn token(h: &Hearth) -> Option<String> {
    h.key().map(|(_, t)| t)
}

/// Whether `password` opens the group a nearby PC offered, asked of that PC before joining:
/// `Ok(true)` it accepted the token, `Ok(false)` it refused it, `Err` it did not answer.
pub async fn check_password(
    h: &Hearth,
    offer: &crate::engine::Offer,
    password: &str,
) -> Result<bool, String> {
    let salt = offer
        .salt
        .as_deref()
        .and_then(|s| hex::decode(s).ok())
        .ok_or("This group is too old to one-click join. Paste an invite instead.")?;
    let (group_id, pw) = (offer.group_id.clone(), password.to_owned());
    // The key derivation is deliberately slow: off the async workers.
    let tok = tokio::task::spawn_blocking(move || {
        let k = crate::crypto::group_key(&group_id, &pw, &salt);
        crate::crypto::auth_token(&k, &group_id)
    })
    .await
    .map_err(|e| e.to_string())?;
    let (ip, port) = net::parse_host(&offer.host).ok_or("That PC did not say where to reach it")?;
    let ips = net::local_ipv4s();
    let body = json!({"device_id": h.device_id(), "name": h.settings().device_name, "group_id": offer.group_id, "port": h.port(), "ips": if ips.is_empty() { vec!["127.0.0.1".to_owned()] } else { ips }});
    let r = h
        .inner
        .http
        .post(format!("http://{ip}:{port}/peer/handshake"))
        .header("x-hearth-auth", tok)
        .json(&body)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .map_err(|_| {
            format!(
                "{} did not answer. Try again, or paste an invite.",
                offer.from
            )
        })?;
    match r.status() {
        reqwest::StatusCode::OK => Ok(true),
        reqwest::StatusCode::UNAUTHORIZED | reqwest::StatusCode::FORBIDDEN => Ok(false),
        s => Err(format!(
            "{} answered {s}. Try again, or paste an invite.",
            offer.from
        )),
    }
}

/// Says hello to `ip:port`: on an answer from a PC in the group, it is online. True when it
/// answered.
pub async fn handshake(h: &Hearth, ip: &str, port: u16) -> bool {
    let (Some(tok), Some(g)) = (token(h), h.group()) else {
        return false;
    };
    let ips = {
        let l = net::local_ipv4s();
        if l.is_empty() {
            vec!["127.0.0.1".to_owned()]
        } else {
            l
        }
    };
    let body = json!({"device_id": h.device_id(), "name": h.settings().device_name, "group_id": g.id, "port": h.port(), "ips": ips});
    let url = format!("http://{ip}:{port}/peer/handshake");
    let r = h
        .inner
        .http
        .post(&url)
        .header("x-hearth-auth", tok)
        .json(&body)
        .timeout(Duration::from_secs(5))
        .send()
        .await;
    let Ok(r) = r else { return false };
    if r.status() != reqwest::StatusCode::OK {
        return false;
    }
    let Ok(info) = r.json::<Value>().await else {
        return false;
    };
    let Some(id) = info.get("device_id").and_then(Value::as_str) else {
        return false;
    };
    if id == h.device_id() {
        return false;
    }
    let name = info
        .get("name")
        .and_then(Value::as_str)
        .filter(|n| !n.is_empty())
        .unwrap_or(ip);
    let their_port = info
        .get("port")
        .and_then(Value::as_u64)
        .unwrap_or(u64::from(port));
    let their_ips = info
        .get("ips")
        .and_then(Value::as_array)
        .filter(|a| !a.is_empty())
        .cloned()
        .unwrap_or_else(|| vec![json!(ip)]);
    if let Value::Object(p) = json!({"device_id": id, "name": name, "port": their_port, "ips": their_ips, "address": ip, "via": "handshake"})
    {
        h.peer_seen(p);
    }
    true
}

/// Asks a PC at each of its addresses in turn: the first answer below 500 wins; connection
/// failures and server errors move on.
async fn ask(
    h: &Hearth,
    device_id: &str,
    build: impl Fn(&str) -> reqwest::RequestBuilder,
) -> Result<reqwest::Response, String> {
    let tok = token(h).ok_or("Join a hearth first")?;
    let mut last: Option<reqwest::Response> = None;
    for (ip, port) in h.candidates(device_id) {
        match build(&format!("http://{ip}:{port}"))
            .header("x-hearth-auth", &tok)
            .send()
            .await
        {
            Ok(r) if r.status().as_u16() < 500 => return Ok(r),
            Ok(r) => last = Some(r),
            Err(_) => {}
        }
    }
    match last {
        Some(r) => Ok(r),
        None => Err("That PC is offline".into()),
    }
}

async fn detail(r: reqwest::Response) -> String {
    let status = r.status();
    match r.json::<Value>().await {
        Ok(v) => v
            .get("detail")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| v.to_string()),
        Err(_) => format!("The other PC answered {status}"),
    }
}

/// A PC's shares.
pub async fn shares(h: &Hearth, device_id: &str) -> Result<Vec<RemoteShare>, String> {
    let r = ask(h, device_id, |base| {
        h.inner
            .http
            .get(format!("{base}/peer/shares"))
            .timeout(Duration::from_secs(8))
    })
    .await?;
    if !r.status().is_success() {
        return Err(detail(r).await);
    }
    let v: Value = r.json().await.map_err(|e| e.to_string())?;
    Ok(v.get("shares")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|s| {
            Some(RemoteShare {
                id: s.get("id")?.as_str()?.to_owned(),
                label: s
                    .get("label")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
                writable: s.get("mode").and_then(Value::as_str) == Some("readwrite"),
            })
        })
        .collect())
}

/// A folder in a PC's share.
pub async fn files(
    h: &Hearth,
    device_id: &str,
    share: &str,
    path: &str,
) -> Result<Vec<Entry>, String> {
    let r = ask(h, device_id, |base| {
        h.inner
            .http
            .get(format!("{base}/peer/files/{share}"))
            .query(&[("path", path)])
            .timeout(Duration::from_secs(12))
    })
    .await?;
    if !r.status().is_success() {
        return Err(detail(r).await);
    }
    let v: Value = r.json().await.map_err(|e| e.to_string())?;
    Ok(entries(&v))
}

/// A listing's entries.
pub fn entries(v: &Value) -> Vec<Entry> {
    v.get("items")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|i| {
            Some(Entry {
                name: i.get("name")?.as_str()?.to_owned(),
                dir: i.get("dir").and_then(Value::as_bool).unwrap_or(false),
                size: i.get("size").and_then(Value::as_u64).unwrap_or(0),
                mtime: i.get("mtime").and_then(Value::as_f64).unwrap_or(0.0),
            })
        })
        .collect()
}

/// Downloads `path` from a PC's share into `dest_dir`, calling `progress(bytes, total)`; the
/// file appears under its final name only once complete. Returns where it went.
pub async fn download(
    h: &Hearth,
    device_id: &str,
    share: &str,
    path: &str,
    dest_dir: &Path,
    cancel: &tokio_util::sync::CancellationToken,
    progress: impl Fn(u64, u64),
) -> Result<PathBuf, String> {
    let r = ask(h, device_id, |base| {
        h.inner
            .http
            .get(format!("{base}/peer/download/{share}"))
            .query(&[("path", path)])
    })
    .await?;
    if r.status() != reqwest::StatusCode::OK {
        return Err(detail(r).await);
    }
    let total = r.content_length().unwrap_or(0);
    let name = crate::paths::safe_filename(path.rsplit('/').next().unwrap_or(path));
    tokio::fs::create_dir_all(dest_dir)
        .await
        .map_err(|e| e.to_string())?;
    let tmp = dest_dir.join(format!(".{name}.{}.part", crate::crypto::random_hex(4)));
    let mut f = tokio::fs::File::create(&tmp)
        .await
        .map_err(|e| e.to_string())?;
    let mut got = 0u64;
    let mut body = r.bytes_stream();
    let result: Result<(), String> = async {
        while let Some(chunk) = body.next().await {
            if cancel.is_cancelled() {
                return Err("Canceled".into());
            }
            let chunk = chunk.map_err(|_| "The other PC stopped sending".to_owned())?;
            got += chunk.len() as u64;
            if got > h.inner.max_upload {
                return Err("That file is larger than Hearth accepts".into());
            }
            f.write_all(&chunk).await.map_err(|e| e.to_string())?;
            h.inner.download.take(chunk.len() as u64).await;
            progress(got, total);
        }
        f.flush().await.map_err(|e| e.to_string())
    }
    .await;
    drop(f);
    if let Err(e) = result {
        let _ = tokio::fs::remove_file(&tmp).await;
        return Err(e);
    }
    let dest = crate::paths::unique_name(dest_dir, &name);
    tokio::fs::rename(&tmp, &dest)
        .await
        .map_err(|e| e.to_string())?;
    Ok(dest)
}

/// Sends a local file into a folder of a PC's share, calling `progress(bytes, total)`.
pub async fn upload(
    h: &Hearth,
    device_id: &str,
    share: &str,
    dir: &str,
    file: &Path,
    progress: impl Fn(u64, u64) + Send + Sync + 'static,
) -> Result<String, String> {
    let name = crate::paths::safe_filename(
        &file
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default(),
    );
    let total = tokio::fs::metadata(file)
        .await
        .map_err(|e| e.to_string())?
        .len();
    let mime = mime_guess::from_path(file)
        .first_or_octet_stream()
        .to_string();
    let progress = std::sync::Arc::new(progress);
    let mut last: Result<String, String> = Err("That PC is offline".into());
    let tok = token(h).ok_or("Join a hearth first")?;
    for (ip, port) in h.candidates(device_id) {
        // Each attempt reads the file afresh (and through the upload limit).
        let f = tokio::fs::File::open(file)
            .await
            .map_err(|e| e.to_string())?;
        let (h2, p2) = (h.clone(), progress.clone());
        let sent = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
        let stream =
            tokio_util::io::ReaderStream::with_capacity(f, crate::server::CHUNK).then(move |c| {
                let (h3, p3, s3) = (h2.clone(), p2.clone(), sent.clone());
                async move {
                    if let Ok(b) = &c {
                        h3.inner.upload.take(b.len() as u64).await;
                        let n = s3.fetch_add(b.len() as u64, std::sync::atomic::Ordering::SeqCst)
                            + b.len() as u64;
                        p3(n, total);
                    }
                    c
                }
            });
        let part =
            reqwest::multipart::Part::stream_with_length(reqwest::Body::wrap_stream(stream), total)
                .file_name(name.clone())
                .mime_str(&mime)
                .map_err(|e| e.to_string())?;
        let form = reqwest::multipart::Form::new().part("file", part);
        let url = format!("http://{ip}:{port}/peer/upload/{share}");
        match h
            .inner
            .http
            .post(&url)
            .query(&[("path", dir)])
            .header("x-hearth-auth", &tok)
            .multipart(form)
            .send()
            .await
        {
            Ok(r) if r.status().is_success() => {
                let v: Value = r.json().await.unwrap_or_default();
                return Ok(v
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or(&name)
                    .to_owned());
            }
            Ok(r) if r.status().as_u16() < 500 => return Err(detail(r).await),
            Ok(r) => last = Err(detail(r).await),
            Err(_) => {}
        }
    }
    last
}

/// Sends a chat message to every online PC, or a private one to its PC only (failures are
/// fine for public messages: the others pull them later).
pub async fn push_chat(h: &Hearth, msg: &Value) {
    let to = msg
        .get("to")
        .and_then(Value::as_str)
        .filter(|t| !t.is_empty());
    let ids: Vec<String> = h
        .inner
        .online
        .lock()
        .map(|o| {
            o.keys()
                .filter(|k| to.is_none_or(|t| t == k.as_str()))
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    let sends = ids.into_iter().map(|id| {
        let (h, m) = (h.clone(), msg.clone());
        async move {
            let _ = ask(&h, &id, |base| {
                h.inner
                    .http
                    .post(format!("{base}/peer/chat"))
                    .json(&m)
                    .timeout(Duration::from_secs(5))
            })
            .await;
        }
    });
    futures_util::future::join_all(sends).await;
}

/// Fetches recent chat from every online PC and keeps what is new.
pub async fn pull_chat(h: &Hearth) {
    let ids: Vec<String> = h
        .inner
        .online
        .lock()
        .map(|o| o.keys().cloned().collect())
        .unwrap_or_default();
    let mut added = false;
    for id in ids {
        let Ok(r) = ask(h, &id, |base| {
            h.inner
                .http
                .get(format!("{base}/peer/chat"))
                .timeout(Duration::from_secs(5))
        })
        .await
        else {
            continue;
        };
        let Ok(v) = r.json::<Value>().await else {
            continue;
        };
        for m in v
            .get("chat")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            // Messages are checked the same way pushed ones are; private ones never come by
            // this route (a peer that sends one anyway is ignored).
            if let Some(m) = crate::server::normalize_chat(m).filter(crate::server::is_public) {
                added |= h.store().append_chat(m);
            }
        }
    }
    if added {
        h.save();
        h.emit(Event::Chat);
    }
}
