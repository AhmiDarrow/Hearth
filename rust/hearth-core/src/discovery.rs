//! Finding the other PCs: UDP beacons on 45454 (broadcast, multicast and direct probes),
//! signed with the group key, every 3 seconds — Hearth 0.3.x's `HEARTH1` beacons, byte for byte.

use std::collections::HashMap;
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4};
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use crate::engine::Hearth;
use crate::{crypto, net, pyjson};

/// The beacon protocol's name.
pub const PROTOCOL: &str = "HEARTH1";

/// This PC's beacon, or `None` outside a group (PCs without a group stay quiet).
pub fn beacon(h: &Hearth) -> Option<Vec<u8>> {
    let g = h.group()?;
    let s = h.settings();
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let body = json!({
        "v": PROTOCOL,
        "device_id": h.device_id(),
        "name": s.device_name,
        "group_id": g.id,
        "group_name": g.name,
        "salt": g.salt,
        "port": h.port(),
        "ips": net::local_ipv4s(),
        "bridge": s.bridge,
        "ts": ts,
    });
    let mac = h
        .key()
        .map(|(k, _)| crypto::hmac_hex(&k, pyjson::dumps_sorted(&body).as_bytes()))
        .unwrap_or_default();
    Some(pyjson::dumps_compact(&json!({"body": body, "mac": mac})).into_bytes())
}

/// A received beacon as a peer, if it is one this PC accepts.
pub fn read(h: &Hearth, data: &[u8], from: Ipv4Addr) -> Option<Map<String, Value>> {
    let env: Value = serde_json::from_slice(data).ok()?;
    let body = env.get("body")?;
    if body.get("v").and_then(Value::as_str) != Some(PROTOCOL) {
        return None;
    }
    let id = body.get("device_id")?.as_str()?;
    if id == h.device_id() {
        return None;
    }
    if let Some(g) = h.group() {
        if body.get("group_id").and_then(Value::as_str) != Some(g.id.as_str()) {
            return None;
        }
        let (key, _) = h.key()?;
        let mac = env.get("mac").and_then(Value::as_str).unwrap_or("");
        let want = crypto::hmac_hex(&key, pyjson::dumps_sorted(body).as_bytes());
        if mac.is_empty() || !crypto::same(&want, mac) {
            return None;
        }
    }
    let s = |k: &str| body.get(k).and_then(Value::as_str).map(str::to_owned);
    let ips = body
        .get("ips")
        .and_then(Value::as_array)
        .filter(|a| !a.is_empty())
        .cloned()
        .unwrap_or_else(|| vec![json!(from.to_string())]);
    let peer = json!({
        "device_id": id,
        "name": s("name").filter(|n| !n.is_empty()).unwrap_or_else(|| "PC".into()),
        "port": body.get("port").and_then(Value::as_u64).unwrap_or(u64::from(h.port())),
        "ips": ips,
        "address": from.to_string(),
        "bridge": body.get("bridge").and_then(Value::as_bool).unwrap_or(false),
        "via": "beacon",
        "group_id": s("group_id"),
        "group_name": s("group_name").filter(|n| !n.is_empty()).unwrap_or_else(|| "Home".into()),
        "salt": s("salt"),
    });
    peer.as_object().cloned()
}

fn socket() -> std::io::Result<std::net::UdpSocket> {
    use socket2::{Domain, Protocol, Socket, Type};
    let s = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::UDP))?;
    s.set_reuse_address(true)?;
    s.set_broadcast(true)?;
    let bound = s.bind(&SocketAddr::from(([0, 0, 0, 0], net::BEACON_PORT)).into());
    if bound.is_err() {
        // Another program holds the port: this PC can still announce itself.
        s.bind(&SocketAddr::from(([0, 0, 0, 0], 0)).into())?;
    }
    let _ = s.join_multicast_v4(&net::MULTICAST, &Ipv4Addr::UNSPECIFIED);
    let _ = s.set_multicast_ttl_v4(8);
    s.set_nonblocking(true)?;
    Ok(s.into())
}

/// Starts announcing and listening (on the engine's threads).
pub(crate) fn start(h: &Hearth) {
    let me = h.clone();
    h.spawn(
        async move {
            let Ok(std_sock) = socket() else { return };
            let Ok(sock) = tokio::net::UdpSocket::from_std(std_sock) else {
                return;
            };
            let sock = std::sync::Arc::new(sock);
            let (tx, rx) = (sock.clone(), sock);
            let sender = me.clone();
            let send = async move {
                loop {
                    if let Some(b) = beacon(&sender) {
                        let targets =
                            net::beacon_targets(&net::local_ipv4s(), &sender.manual_hosts());
                        for t in targets {
                            if let Ok(ip) = t.parse::<Ipv4Addr>() {
                                let _ = tx
                                    .send_to(&b, SocketAddrV4::new(ip, net::BEACON_PORT))
                                    .await;
                            }
                        }
                    }
                    // Every 3 s, or at once when settings change.
                    let _ = tokio::time::timeout(
                        Duration::from_secs(3),
                        sender.inner.rediscover.notified(),
                    )
                    .await;
                }
            };
            let listener = me.clone();
            let recv = async move {
                let mut seen: HashMap<String, Instant> = HashMap::new();
                let mut buf = vec![0u8; 65_536];
                loop {
                    let Ok((n, from)) = rx.recv_from(&mut buf).await else {
                        continue;
                    };
                    let SocketAddr::V4(from) = from else { continue };
                    let Some(peer) = buf.get(..n).and_then(|d| read(&listener, d, *from.ip()))
                    else {
                        continue;
                    };
                    let id = peer
                        .get("device_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_owned();
                    // Once a second per PC is plenty.
                    let fresh = seen
                        .get(&id)
                        .is_none_or(|t| t.elapsed() > Duration::from_secs(1));
                    seen.insert(id, Instant::now());
                    if !fresh {
                        continue;
                    }
                    let new = !listener
                        .inner
                        .online
                        .lock()
                        .map(|o| {
                            o.contains_key(
                                peer.get("device_id")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default(),
                            )
                        })
                        .unwrap_or(true);
                    listener.peer_seen(peer);
                    // A bridge answers new PCs directly, so subnets that cannot hear each other's
                    // broadcasts still meet.
                    if new
                        && listener.settings().bridge
                        && let Some(b) = beacon(&listener)
                    {
                        let _ = rx
                            .send_to(&b, SocketAddrV4::new(*from.ip(), net::BEACON_PORT))
                            .await;
                    }
                }
            };
            tokio::join!(send, recv);
        },
        |()| {},
    );
}
