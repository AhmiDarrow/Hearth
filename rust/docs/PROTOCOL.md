# Hearth 0.3.x wire protocol (what the Rust peer must speak)

Read from the Python sources (v0.3.14). Plain HTTP/1.1 on TCP 7474, UDP beacons on 45454, no
TLS, no mDNS (zeroconf is listed but never imported). IPv4 only.

## Keys and identities
- Group create: `group_id = token_hex(8)` (16 lowercase hex), `salt = 16 random bytes` stored as
  32 lowercase hex. Generated password: 3×4 chars from
  `ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789` joined by `-`.
- Group key: `PBKDF2-HMAC-SHA256(utf8("{group_id}:{password}"), salt_bytes, 210000, 32)`.
- `verifier = key.hex()[:32]`, stored, never checked.
- Device: `device.key` = raw 32-byte Ed25519 seed; `device_id = base64url_nopad(pubkey)[:22]`
  (unreadable key → renamed `device.key.corrupt`, new one made). Ed25519 is not used on the wire.

## Peer auth
- Header `x-hearth-auth: hex(HMAC-SHA256(group_key, utf8(group_id)))` — 64 lowercase hex, a
  constant per group. Constant-time, case-sensitive compare.
- Failure (also: no group): 401 `{"detail":"Bad group token"}`. Errors are `{"detail": "..."}`.

## Endpoints (all need the header)
- `POST /peer/handshake` JSON `{"device_id","name","group_id","port":int,"ips":[...]}` →
  `{"ok":true,"device_id","name","port","ips"}` (ips = own private IPv4s or ["127.0.0.1"]).
  Server records `{device_id,name,port,ips: body.ips or [src], address: src ip, via:"handshake"}`.
  Client: non-200 → none; ignore own id; peer = `{device_id, name: name or ip, port, ips: ips or
  [ip], address: dialed ip, via:"handshake"}`.
- `GET /peer/ping` → `{"ok":true,"name","id","port","ips"}` (key is `id`).
- `GET /peer/shares` → `{"shares":[{"id","label","mode":"read|readwrite"}]}` (no paths).
- `GET /peer/files/{share}?path=` → `{"share":{id,label,mode},"path":<as sent>,"items":[{"name",
  "dir":bool,"size":int,"mtime":float}]}`; dirs first then name.lower(); dot names and
  unstatable entries skipped; a non-directory gives an empty list.
- `GET /peer/download/{share}?path=` (required) → 200 file bytes, `Content-Disposition:
  attachment; filename="<ascii, " → '>"; filename*=UTF-8''<pct, safe="">`, Content-Type by
  extension else application/octet-stream. Content-Length when unthrottled; chunked when the
  upload limit applies (4 MiB chunks, bucket per chunk). 404 `Not a file`, 403 `File is locked
  or unreadable`. Client never sends Range; requires 200; local name from the requested path.
- `POST /peer/upload/{share}?path=<dir>` multipart, one part `file` (filename = safe name).
  Share must be readwrite (403 `Share is read-only`); dest dir created; existing file
  overwritten atomically via `.<name>.<8hex>.part`; cap 8 GiB (`HEARTH_MAX_UPLOAD`) → 413
  `File is larger than the {cap} byte limit`. → `{"ok":true,"name"}`.
- `POST /peer/chat` message JSON → `{"ok":true}` (duplicates too); 400 `Chat payload must be
  JSON` / `Invalid chat message`.
- `GET /peer/chat` → `{"chat":[last 200]}`.
- Share errors: 404 `Unknown share`, 404 `Share folder is missing on this PC`, 403 `Path escapes
  the shared folder`, 403 `Absolute paths are not allowed`, 403 `UNC paths are not allowed`, 403
  `Path contains an illegal character`, 403 `Alternate data streams are not allowed`, 403 `That
  filename is reserved on Windows`.

## Path rules
- sanitize_relpath: strip; ""/"."/"./" → ""; `\` → `/`; reject NUL, leading `//`, leading `/` or
  `X:`; per segment drop "." and empty, reject "..", any ":", reserved (CON PRN AUX NUL COM1-9
  LPT1-9 by `seg.split('.')[0].upper()`). Resolve under the share root following symlinks; must
  stay inside.
- safe_filename: `\`→`/`; NUL → `upload.bin`; last segment, stripped; cut at first `:`; strip
  trailing `.`; empty/"."/".." or reserved → `upload.bin`; truncate to 255 chars.

## Discovery (UDP 45454, multicast 239.54.54.54, TTL 8, protocol "HEARTH1")
- Datagram: compact JSON `{"body":{...},"mac":"<hex64>"}`. Body keys in order: `v`
  ("HEARTH1"), `device_id`, `name`, `group_id`, `group_name`, `salt`, `port` (int), `ips`,
  `bridge` (bool), `ts` (int seconds).
- `mac = hex(HMAC-SHA256(group_key, json.dumps(body, sort_keys=True, separators=(",",":"),
  ensure_ascii=True)))` — non-ASCII as lowercase `\uXXXX` with surrogate pairs. Empty without key.
- Receive: v must match; ignore own id; with a group, group_id must match and the MAC must
  verify; without a group, accept unauthenticated (nearby offers). Peer = `{device_id, name:
  name or "PC", port, ips: ips or [src], address: src, bridge, via:"beacon", group_id,
  group_name: or "Home", salt}`. Per-device debounce 1 s. Bridge on: unicast own beacon back to
  a newly accepted peer.
- Send immediately then every 3 s, only with a group, to: 255.255.255.255, 239.54.54.54, each
  local /24 .255, hosts .1 .2 .100 .101 .254 of each local /24 (not own), .1 and .254 of the
  common home nets, and each manual host's IP; all port 45454.
- Local IPv4s: private, non-loopback/link-local; drop virtual adapters (tun|tap|br-|wsl|docker|
  vmware|…) when real ones exist.

## Invites
- `hearth|<group_id>|<salt_hex>|<password>|<group_name>|<ip:port,...>` (plain text, no escaping).
- Parse (stripped): `hearth|` needs ≥5 parts, hosts optional; legacy `hearth:` with `|` (≥4) or
  `:` split max 4. Errors `Invite code is incomplete`, `Not a Hearth invite. Ask a member to copy
  theirs.` Hosts become manual hosts; each is handshaked; failure seeds a placeholder peer
  `seed-<ip>-<port>` (name = ip, via "invite"), removed when a real peer appears at that
  address:port.

## Peers and timing
- Candidates: `[address, *ips]` deduplicated; `host:port` entries keep their port, else the
  peer's port (default 7474). Try in order; first status <500 wins; connection errors and 5xx
  move on.
- Timeouts: handshake and chat 5 s, shares 8 s, files 12 s, transfers none.
- Maintenance every 5 s: handshake manual hosts and online peers, pull chat from online peers.
- Online = seen within 30 s; nearby offers expire after 30 s. `known_peers` persists and is
  still dialed after restart.

## Chat
- Message `{"id": uuid4 hex, "from_id", "from_name", "to": null, "text", "ts": float}`. Local
  text 1–4000 chars, stripped non-empty ("Empty message"); needs a group ("Join a hearth first").
- Push: POST to every online peer (5 s, errors ignored). Pull: every 5 s, add unknown ids.
- Peer normalisation: id non-empty ≤80; text stripped non-empty ≤4000; from_name ≤128 (strip or
  "PC"); from_id ≤80; to null or ≤80; ts number else now; extra keys dropped.
- Storage arrival order, keep last 500; dedupe by id.

## Throttle
- One token bucket per direction shared by all transfers; 0 = unlimited; burst =
  max(1024, rate·0.05)/rate s; reserve: start = max(ok_at, now − burst), ok_at = start + n/rate,
  wait = max(0, start − now); rate changes keep ok_at when both rates are non-zero.
- Download bucket per received chunk; upload bucket per served chunk and per forwarded upload
  read; never on received uploads.
- Chunk 4 MiB, concurrency 4, progress every 0.25 s.

## state.json (profile `$HEARTH_HOME` or `~/.hearth`)
- Exactly these keys: `device_name` (hostname), `theme` ("system"), `group` (null or `{id, name,
  password, salt, verifier, created, creator_id}`), `known_peers` {device_id: peer + last_seen},
  `shares` [{id (8 hex), path, label, mode, added}], `chat`, `bridge`, `manual_hosts` (strings as
  typed), `auto_check_updates` (true), `download_folder` (""), `download_limit_bps` (0),
  `upload_limit_bps` (0), `last_update_check` (0.0). Numbers may be floats. Indent 2,
  ensure_ascii, written via `state.tmp` and atomic replace. Corrupt → `state.json.corrupt`.
- `transfers.json`: finished jobs only.
