//! What this PC answers other PCs: the `/peer/*` routes of Hearth 0.3.x, with the same
//! fields, status codes and messages.

use std::net::SocketAddr;
use std::path::PathBuf;

use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::{ConnectInfo, Multipart, Path as UrlPath, Query, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use tokio::io::AsyncWriteExt;

use crate::engine::{Event, Hearth};
use crate::paths;
use crate::state::Share;

/// Bytes read or written at a time.
pub const CHUNK: usize = 4 * 1024 * 1024;

pub(crate) fn router(h: Hearth) -> Router {
    Router::new()
        .route("/peer/handshake", post(handshake))
        .route("/peer/ping", get(ping))
        .route("/peer/shares", get(shares))
        .route("/peer/files/{share}", get(files))
        .route("/peer/download/{share}", get(download))
        .route("/peer/upload/{share}", post(upload))
        .route("/peer/chat", post(chat_in).get(chat_out))
        .layer(axum::extract::DefaultBodyLimit::disable())
        .with_state(h)
}

/// `{"detail": message}` with `status`, as FastAPI answers.
pub(crate) fn fail(status: StatusCode, message: &str) -> Response {
    (status, axum::Json(json!({"detail": message}))).into_response()
}

/// A refusal: a status and the message 0.3.x answers with.
pub(crate) struct Refused(StatusCode, &'static str);

impl IntoResponse for Refused {
    fn into_response(self) -> Response {
        fail(self.0, self.1)
    }
}

fn authorized(h: &Hearth, headers: &HeaderMap) -> Result<(), Refused> {
    let given = headers
        .get("x-hearth-auth")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    match h.key() {
        Some((_, token)) if crate::crypto::same(&token, given) => Ok(()),
        _ => Err(Refused(StatusCode::UNAUTHORIZED, "Bad group token")),
    }
}

fn local_ips() -> Vec<String> {
    let ips = crate::net::local_ipv4s();
    if ips.is_empty() {
        vec!["127.0.0.1".into()]
    } else {
        ips
    }
}

#[derive(Deserialize)]
struct HandshakeIn {
    device_id: String,
    name: String,
    #[allow(dead_code)]
    group_id: String,
    port: u16,
    #[serde(default)]
    ips: Vec<String>,
}

async fn handshake(
    State(h): State<Hearth>,
    ConnectInfo(from): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    // 0.3.x checks the body before the token; a malformed body is 422 either way.
    let Ok(b) = serde_json::from_slice::<HandshakeIn>(&body) else {
        return fail(StatusCode::UNPROCESSABLE_ENTITY, "Invalid handshake");
    };
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    let address = from.ip().to_string();
    let ips = if b.ips.is_empty() {
        vec![address.clone()]
    } else {
        b.ips
    };
    let peer = json!({"device_id": b.device_id, "name": b.name, "port": b.port, "ips": ips, "address": address, "via": "handshake"});
    if let Value::Object(p) = peer {
        h.peer_seen(p);
    }
    let name = h.settings().device_name;
    axum::Json(json!({"ok": true, "device_id": h.device_id(), "name": name, "port": h.port(), "ips": local_ips()})).into_response()
}

async fn ping(State(h): State<Hearth>, headers: HeaderMap) -> Response {
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    let name = h.settings().device_name;
    axum::Json(json!({"ok": true, "name": name, "id": h.device_id(), "port": h.port(), "ips": local_ips()})).into_response()
}

fn share_json(s: &Share) -> Value {
    json!({"id": s.id, "label": s.display_label(), "mode": s.mode()})
}

async fn shares(State(h): State<Hearth>, headers: HeaderMap) -> Response {
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    let list: Vec<Value> = h.shares().iter().map(share_json).collect();
    axum::Json(json!({"shares": list})).into_response()
}

#[derive(Deserialize)]
struct PathQuery {
    #[serde(default)]
    path: Option<String>,
}

/// A share by id and a path inside it, or the response that refuses them.
pub(crate) fn locate(h: &Hearth, share_id: &str, rel: &str) -> Result<(Share, PathBuf), Refused> {
    let Some(share) = h.shares().into_iter().find(|s| s.id == share_id) else {
        return Err(Refused(StatusCode::NOT_FOUND, "Unknown share"));
    };
    if !share.path.is_dir() {
        return Err(Refused(
            StatusCode::NOT_FOUND,
            "Share folder is missing on this PC",
        ));
    }
    match paths::resolve_in_share(&share.path, rel) {
        Ok(p) => Ok((share, p)),
        Err(e) => Err(Refused(StatusCode::FORBIDDEN, e.message())),
    }
}

/// A folder's entries as 0.3.x lists them: no dot names, folders first, then by name.
pub fn list_dir(dir: &std::path::Path) -> Vec<Value> {
    let mut items: Vec<(bool, String, u64, f64)> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') {
                return None;
            }
            let m = e.metadata().ok()?;
            let mtime = m
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map_or(0.0, |d| d.as_secs_f64());
            Some((
                m.is_dir(),
                name,
                if m.is_dir() { 0 } else { m.len() },
                mtime,
            ))
        })
        .collect();
    items.sort_by(|a, b| {
        b.0.cmp(&a.0)
            .then_with(|| a.1.to_lowercase().cmp(&b.1.to_lowercase()))
    });
    items.into_iter().map(|(dir, name, size, mtime)| json!({"name": name, "dir": dir, "size": size, "mtime": mtime})).collect()
}

async fn files(
    State(h): State<Hearth>,
    headers: HeaderMap,
    UrlPath(share_id): UrlPath<String>,
    Query(q): Query<PathQuery>,
) -> Response {
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    let rel = q.path.unwrap_or_default();
    let (share, dir) = match locate(&h, &share_id, &rel) {
        Ok(x) => x,
        Err(r) => return r.into_response(),
    };
    let items = if dir.is_dir() {
        list_dir(&dir)
    } else {
        Vec::new()
    };
    axum::Json(json!({"share": share_json(&share), "path": rel, "items": items})).into_response()
}

/// `attachment; filename="…"; filename*=UTF-8''…` for `name`.
pub fn content_disposition(name: &str) -> String {
    // Python's `quote(name, safe="")`: everything but letters, digits and `_.-~` escaped.
    const QUOTE: &percent_encoding::AsciiSet = &percent_encoding::NON_ALPHANUMERIC
        .remove(b'_')
        .remove(b'.')
        .remove(b'-')
        .remove(b'~');
    let safe = paths::safe_filename(name);
    let ascii = if safe.is_ascii() {
        safe.replace('"', "'")
    } else {
        let kept: String = safe.chars().filter(char::is_ascii).collect();
        paths::safe_filename(if kept.is_empty() {
            "download.bin"
        } else {
            &kept
        })
        .replace('"', "'")
    };
    let encoded = percent_encoding::utf8_percent_encode(&safe, QUOTE).to_string();
    format!("attachment; filename=\"{ascii}\"; filename*=UTF-8''{encoded}")
}

async fn download(
    State(h): State<Hearth>,
    headers: HeaderMap,
    UrlPath(share_id): UrlPath<String>,
    Query(q): Query<PathQuery>,
) -> Response {
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    let Some(rel) = q.path else {
        return fail(StatusCode::UNPROCESSABLE_ENTITY, "path is required");
    };
    let (_, file) = match locate(&h, &share_id, &rel) {
        Ok(x) => x,
        Err(r) => return r.into_response(),
    };
    if !file.is_file() {
        return fail(StatusCode::NOT_FOUND, "Not a file");
    }
    let Ok(f) = tokio::fs::File::open(&file).await else {
        return fail(StatusCode::FORBIDDEN, "File is locked or unreadable");
    };
    let len = f.metadata().await.map(|m| m.len()).unwrap_or(0);
    let name = file
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mime = mime_guess::from_path(&file)
        .first_or_octet_stream()
        .to_string();
    let limited = h.inner.upload.rate() > 0;
    let h2 = h.clone();
    let stream = tokio_util::io::ReaderStream::with_capacity(f, CHUNK).then(move |chunk| {
        let h3 = h2.clone();
        async move {
            if let Ok(c) = &chunk {
                h3.inner.upload.take(c.len() as u64).await;
            }
            chunk
        }
    });
    let mut resp = Response::new(Body::from_stream(stream));
    let hd = resp.headers_mut();
    if let Ok(v) = HeaderValue::from_str(&mime) {
        hd.insert(header::CONTENT_TYPE, v);
    }
    if let Ok(v) = HeaderValue::from_str(&content_disposition(&name)) {
        hd.insert(header::CONTENT_DISPOSITION, v);
    }
    // Unthrottled answers say how long they are (as 0.3.x's do); throttled ones are chunked.
    if !limited && let Ok(v) = HeaderValue::from_str(&len.to_string()) {
        hd.insert(header::CONTENT_LENGTH, v);
    }
    resp
}

async fn upload(
    State(h): State<Hearth>,
    headers: HeaderMap,
    UrlPath(share_id): UrlPath<String>,
    Query(q): Query<PathQuery>,
    mut parts: Multipart,
) -> Response {
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    let rel = q.path.unwrap_or_default();
    let (share, dir) = match locate(&h, &share_id, &rel) {
        Ok(x) => x,
        Err(r) => return r.into_response(),
    };
    if !share.writable {
        return fail(StatusCode::FORBIDDEN, "Share is read-only");
    }
    loop {
        let field = match parts.next_field().await {
            Ok(Some(f)) => f,
            Ok(None) => return fail(StatusCode::UNPROCESSABLE_ENTITY, "file is required"),
            Err(_) => return fail(StatusCode::BAD_REQUEST, "Could not read the upload"),
        };
        if field.name() != Some("file") {
            continue;
        }
        let name = paths::safe_filename(field.file_name().unwrap_or("upload.bin"));
        return match save_upload(&dir, &name, field, h.inner.max_upload).await {
            Ok(()) => {
                h.emit(Event::Shares);
                axum::Json(json!({"ok": true, "name": name})).into_response()
            }
            Err(Some(cap)) => fail(
                StatusCode::PAYLOAD_TOO_LARGE,
                &format!("File is larger than the {cap} byte limit"),
            ),
            Err(None) => fail(
                StatusCode::INTERNAL_SERVER_ERROR,
                "This PC could not read or write that file. It may be locked or in a folder Hearth cannot use.",
            ),
        };
    }
}

/// Streams an upload into `dir/name` through a temporary file, replacing what was there.
/// `Err(Some(cap))` when it is larger than `cap`.
async fn save_upload(
    dir: &std::path::Path,
    name: &str,
    mut field: axum::extract::multipart::Field<'_>,
    cap: u64,
) -> Result<(), Option<u64>> {
    tokio::fs::create_dir_all(dir).await.map_err(|_| None)?;
    let tmp = dir.join(format!(".{name}.{}.part", crate::crypto::random_hex(4)));
    let mut f = tokio::fs::File::create(&tmp).await.map_err(|_| None)?;
    let mut n = 0u64;
    let result = async {
        while let Some(chunk) = field.chunk().await.map_err(|_| None)? {
            n += chunk.len() as u64;
            if n > cap {
                return Err(Some(cap));
            }
            f.write_all(&chunk).await.map_err(|_| None)?;
        }
        f.flush().await.map_err(|_| None)?;
        Ok(())
    }
    .await;
    drop(f);
    match result {
        Ok(()) => tokio::fs::rename(&tmp, dir.join(name))
            .await
            .map_err(|_| None),
        Err(e) => {
            let _ = tokio::fs::remove_file(&tmp).await;
            Err(e)
        }
    }
}

/// A chat message from another PC, made safe as 0.3.x makes it (or refused).
pub(crate) fn normalize_chat(raw: &Value) -> Option<Value> {
    let o = raw.as_object()?;
    let s = |k: &str| o.get(k);
    let id = s("id")?
        .as_str()
        .filter(|x| !x.is_empty() && x.chars().count() <= 80)?;
    let text = s("text")?.as_str()?.trim();
    if text.is_empty() || text.chars().count() > 4000 {
        return None;
    }
    let from_name = match s("from_name") {
        None | Some(Value::Null) => "PC",
        Some(Value::String(n)) if n.is_empty() => "PC",
        Some(Value::String(n)) if n.chars().count() <= 128 => n.as_str(),
        _ => return None,
    };
    let from_name = if from_name.trim().is_empty() {
        "PC"
    } else {
        from_name.trim()
    };
    let from_id = match s("from_id") {
        None | Some(Value::Null) => "",
        Some(Value::String(n)) if n.chars().count() <= 80 => n.as_str(),
        _ => return None,
    };
    let to = match s("to") {
        None | Some(Value::Null) => Value::Null,
        Some(Value::String(t)) if t.chars().count() <= 80 => Value::String(t.clone()),
        _ => return None,
    };
    let ts = s("ts")
        .and_then(Value::as_f64)
        .filter(|t| *t != 0.0)
        .unwrap_or_else(crate::engine::now);
    let mut m = Map::new();
    m.insert("id".into(), json!(id));
    m.insert("from_id".into(), json!(from_id));
    m.insert("from_name".into(), json!(from_name));
    m.insert("to".into(), to);
    m.insert("text".into(), json!(text));
    m.insert("ts".into(), json!(ts));
    Some(Value::Object(m))
}

async fn chat_in(State(h): State<Hearth>, headers: HeaderMap, body: Bytes) -> Response {
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    let Ok(raw) = serde_json::from_slice::<Value>(&body) else {
        return fail(StatusCode::BAD_REQUEST, "Chat payload must be JSON");
    };
    let Some(msg) = normalize_chat(&raw) else {
        return fail(StatusCode::BAD_REQUEST, "Invalid chat message");
    };
    let added = h.store().append_chat(msg);
    if added {
        h.save();
        h.emit(Event::Chat);
    }
    axum::Json(json!({"ok": true})).into_response()
}

async fn chat_out(State(h): State<Hearth>, headers: HeaderMap) -> Response {
    if let Err(r) = authorized(&h, &headers) {
        return r.into_response();
    }
    axum::Json(json!({"chat": h.chat()})).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chat_from_other_pcs_is_checked_like_0_3() {
        let ok = normalize_chat(
            &json!({"id": "a", "text": "  hi ", "from_name": "", "ts": 5, "extra": 1}),
        )
        .unwrap_or_default();
        assert_eq!(ok.get("text").and_then(Value::as_str), Some("hi"));
        assert_eq!(ok.get("from_name").and_then(Value::as_str), Some("PC"));
        assert_eq!(ok.get("ts").and_then(Value::as_f64), Some(5.0));
        assert!(ok.get("extra").is_none());
        assert!(normalize_chat(&json!({"id": "", "text": "x"})).is_none());
        assert!(normalize_chat(&json!({"id": "a", "text": "   "})).is_none());
        assert!(normalize_chat(&json!({"id": "a", "text": "x".repeat(4001)})).is_none());
        assert!(normalize_chat(&json!({"id": "a", "text": "x", "to": "y".repeat(81)})).is_none());
        assert!(normalize_chat(&json!({"id": "a", "text": "x", "from_name": 5})).is_none());
    }

    #[test]
    fn downloads_name_their_file_for_any_client() {
        // Checked against CPython's prefs.content_disposition.
        assert_eq!(
            content_disposition("report \"final\".pdf"),
            "attachment; filename=\"report 'final'.pdf\"; filename*=UTF-8''report%20%22final%22.pdf"
        );
        assert_eq!(
            content_disposition("säge_1.txt"),
            "attachment; filename=\"sge_1.txt\"; filename*=UTF-8''s%C3%A4ge_1.txt"
        );
    }
}
