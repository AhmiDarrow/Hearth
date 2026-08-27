# Hearth

A HomeGroup for the whole house: one passphrase, folder sharing, and a small PC-to-PC chat. It is built for home networks that include a second router, not only a single subnet.

No cloud. Membership is the password. Traffic between PCs is authenticated with a key stretched from that password.

## Why this exists

Windows HomeGroup made sharing boringly easy: create a group, read a password to the other PCs, share Pictures and Documents. It died in Windows 10. HomeGroup also assumed one broadcast domain. A second router in *router* mode (not access-point mode) puts bedrooms on another subnet, and broadcasts stop at the box.

Hearth keeps the HomeGroup ritual and adds three ways across that gap:

1. **Invite codes** carry the host PC's current IPs and port.
2. **Unicast probes** hit common home ranges (`192.168.0/1/2`, `10.0.0`, Google/Eero/Orbi-ish nets) and the usual `.1` / `.254` router addresses.
3. **Bridge mode** on a PC that can already see both sides re-announces beacons so isolated rooms learn about each other.

Best long-term fix is still: set the second router to access-point mode so the house is one LAN. Hearth works either way.

## Features

- Create or join a hearth with a generated or chosen password
- Live member list with online presence
- Share any folder as read-only or read and write
- Browse and download from another PC; upload when they allow it
- House chat (stored locally, last 500 messages)
- Ten Remedy-family themes: Grove, Grove Light, Studio, Studio Light, Remedy Dark, Remedy Bright, Adapted, Blur, Blur Light, Glass
- Manual host entry when discovery cannot see a room

## Run

Python 3.11+.

```bash
cd hearth
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m hearth
```

Open http://127.0.0.1:7474

On another PC in the house, install the same way, choose Join with invite, and paste the invite copied from the first PC.

Two copies on one machine (for a dry run):

```bash
HEARTH_HOME=/tmp/hearth-a HEARTH_PORT=7474 python3 -m hearth --no-browser
HEARTH_HOME=/tmp/hearth-b HEARTH_PORT=7475 python3 -m hearth --no-browser
```

Then join B using A's invite and add `127.0.0.1:7474` as a host on B if beacons collide on the shared UDP port.

## First-run flow

1. Name this PC.
2. Light the hearth -- or paste an invite.
3. Copy the invite. Read it to the other rooms, or send it over any channel you already trust.
4. Share Documents, Pictures, or a dedicated drop folder.
5. Chat from the middle pane. Click a person to open their first shared folder.

## Sub-routers

| Situation | What to do |
| --- | --- |
| Second box is already an access point (same 192.168.x) | Do nothing. Beacons find everyone. |
| Second box is a router (e.g. main 192.168.1.x, rooms 192.168.0.x) | Paste the invite (it includes IPs). If that fails, on the inner PC add the outer PC's IP under Network. |
| One PC can ping both sides | Turn on Bridge discovery across subnets on that PC. |
| Windows / macOS firewall prompt | Allow Hearth on private networks. Port 7474 TCP and 45454 UDP. |

## Security model

- Group password -> PBKDF2-HMAC-SHA256 (210k) -> 32-byte group key
- Discovery packets and peer HTTP calls carry an HMAC of the group id
- Each install has an Ed25519 device identity under ~/.hearth/device.key
- This is home-network security, not a substitute for full-disk encryption or a VPN on a hostile LAN
- Do not reuse the hearth password on the public internet

## Layout

```
hearth/
  hearth/           Python package
    app.py          Local UI API + peer API
    discovery.py    Broadcast, multicast, unicast, bridge
    crypto.py       Password, HMAC, identity
    ui/static/      Themes + app
  requirements.txt
```

State lives in ~/.hearth (state.json, device.key, inbox/).

## Themes

All RemedyAI / Remedy editor families shipped in the sidebar:

Grove and Studio are the RemedyAI rooms (home and workbench). Remedy Dark and Bright follow the Base16 Eighties orange of the VS Code / Zed Remedy themes. Adapted, Blur, Blur Light, and Glass match the extra Remedy UI treatments.
