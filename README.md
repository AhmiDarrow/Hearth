# Hearth

A HomeGroup for the whole house: one passphrase, folder sharing, and a small PC-to-PC chat. It is built for home networks that include a second router, not only a single subnet.

No cloud. Membership is the password. Traffic between PCs is authenticated with a key stretched from that password.

## Why this exists

Windows HomeGroup made sharing boringly easy: create a group, read a password to the other PCs, share Pictures and Documents. It died in Windows 10. HomeGroup also assumed one broadcast domain. A second router in *router* mode (not access-point mode) puts bedrooms on another subnet, and broadcasts stop at the box.

Hearth keeps the HomeGroup ritual and adds three ways across that gap:

1. **Invite codes** carry the host PC's current IPs and port, then the joining PC immediately handshakes over HTTP.
2. **Unicast probes** hit common home ranges (`192.168.0/1/2`, `10.0.0`, Google/Eero/Orbi-ish nets) and the usual `.1` / `.254` router addresses.
3. **Bridge mode** on a PC that can already see both sides re-announces beacons so isolated rooms learn about each other.

Best long-term fix is still: set the second router to access-point mode so the house is one LAN. Hearth works either way.

## Features

- Create or join a hearth with a generated or chosen password
- Live member list with online presence
- Share Documents, Pictures, or any folder from a picker (read-only or read and write)
- Browse and download from another PC; drag files in to upload when they allow it
- House chat (stored locally, last 500 messages)
- A slick desktop app: sidebar of PCs, Finder-style file grid or list, drag-and-drop with upload progress
- Dark and light mode that follows Windows, plus optional Remedy themes
- System tray icon (Open / Quit) and an app window via Edge WebView2 (falls back to the browser)
- Auto-detect an existing home group on the LAN and join with just the password
- One-click Windows installer (`HearthSetup.exe`) — no Python, no scripts, no cloning
- Auto-update from GitHub Releases, with SHA-256 and Authenticode checks

## Install on Windows 11

Every home PC needs its own copy. Download `HearthSetup.exe` from the [GitHub Releases](https://github.com/AhmiDarrow/hearth/releases) page and double-click it. No Python, no scripts, no git.

The installer is **self-signed**. The first time you run a newly downloaded `HearthSetup.exe`, Windows may show SmartScreen or an "Unknown publisher" prompt. That is expected. A self-signed certificate does not clear SmartScreen; only a certificate bought from a public CA would. Click **More info** → **Run anyway** if Windows asks.

1. Approve the one User Account Control prompt (needed so firewall rules can be added).
2. Click **Install**. Leave the defaults checked: desktop shortcut and start at sign-in.
3. At the end, leave **Open Hearth now** checked.
4. On the first PC the name is already filled in from the computer name. Click **Start a home group**.
5. On the next PC, install the same way. If the first PC is already on, Hearth offers **Join Home on [that PC]** — type the group password and click Join. Or open **I have an invite** and paste the invite from the first PC.
6. Click **Share a folder** and pick Documents, Pictures, or any folder. Other PCs show up in the sidebar; open a PC to browse, click **Get** to download, or drag files onto a read-and-write folder to upload.

Hearth lives in the system tray. Closing the window hides it; **Quit** from the tray icon fully exits. Open it later from the Start Menu, the desktop shortcut, or the tray. It starts when you sign in. Remove it from **Settings → Apps → Installed apps**.

### Firewall and autostart

| Item | What the installer sets |
| --- | --- |
| Windows Defender Firewall | Inbound allow, **Private** profile only: TCP **7474** (UI, files, chat), UDP **45454** (discovery) |
| Autostart | A Run-key entry starts `Hearth.exe --background` at sign-in (tray only) |
| Start Menu and desktop | Shortcuts named **Hearth** |
| Uninstall | Standard Add/Remove Programs entry |

Confirm the Wi-Fi or Ethernet is set to a **Private** network (Windows Settings → Network & internet → Properties). Public profile stays blocked on purpose.

The `install.cmd` / `install.ps1` scripts remain for developers who want to run from source.

### Signing and trusting the publisher

Each release also includes the public certificate `Hearth.cer` (and `SHA256SUMS.txt`). This certificate is used to Authenticode-sign `Hearth.exe`, the uninstaller, and `HearthSetup.exe` in CI. The private key lives only in GitHub Actions secrets and is never committed.

To tell Windows this publisher is trusted on a home PC — this does **not** install a root CA, and it will not silence SmartScreen for a brand-new download — run:

```bat
certutil -user -addstore TrustedPublisher Hearth.cer
```

Or tick **Trust the Hearth publisher certificate** in the installer, or run `trust-hearth-cert.cmd` from the install folder. After that, later Hearth updates signed with the same certificate should no longer show "Unknown publisher."

### Auto-update

Installed copies check [GitHub Releases](https://github.com/AhmiDarrow/hearth/releases) (including prereleases) on startup and every few hours, and from **Network → Check for updates**. A newer `HearthSetup.exe` is downloaded, checked against `SHA256SUMS.txt` and the publisher certificate thumbprint, then installed silently. Hearth reopens and shows "Updated to vX". Set `HEARTH_NO_UPDATE=1` to disable checks (used by tests).


## First-run flow

1. This PC's name is pre-filled from the hostname — change it if you want.
2. Start a home group, or join one that was already found on the LAN, or paste an invite.
3. Copy the invite if another room cannot auto-detect you.
4. Share Documents, Pictures, or a dedicated drop folder.
5. Click a PC in the sidebar to browse its folders. Drag files onto a read-and-write folder to add them. Chat is in the middle pane.

## Sub-routers

| Situation | What to do |
| --- | --- |
| Second box is already an access point (same 192.168.x) | Do nothing. Beacons and the invite handshake find everyone. |
| Second box is a router (e.g. main 192.168.1.x, rooms 192.168.0.x) | Paste the invite (it includes IPs). The inner PC can usually reach the outer PC. If the outer PC cannot browse the inner PC's folders, port-forward TCP 7474 and UDP 45454 on the inner router, or put that router in access-point mode. |
| Chat works one way but files from the main LAN cannot open a bedroom PC | Same NAT issue as above. Chat will catch up because members pull messages from PCs they can reach. Folder browse needs a direct connection or a port forward. |
| One PC can ping both sides | Turn on Bridge discovery across subnets on that PC. |
| Windows firewall prompt | Allow Hearth on private networks. Port 7474 TCP and 45454 UDP. |

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
    update.py       GitHub Releases auto-update
  pack/             Installer, icon, public publisher certificate
  install.cmd       Developer fallback (double-click)
  install.ps1
  requirements.txt
```

State lives in `%USERPROFILE%\.hearth` on Windows (`~/.hearth` on other systems): `state.json`, `device.key`, `inbox/`.

## Run from source (any OS)

Python 3.11+.

```bash
cd hearth
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 -m hearth
```

Open http://127.0.0.1:7474

Two copies on one machine (for a dry run):

```bash
HEARTH_HOME=/tmp/hearth-a HEARTH_PORT=7474 python3 -m hearth --no-browser
HEARTH_HOME=/tmp/hearth-b HEARTH_PORT=7475 python3 -m hearth --no-browser
```

Then join B using A's invite. If the invite has no loopback address, add `127.0.0.1:7474` as a host on B. The join handshake does not depend on the shared UDP beacon port.

## Tests

```bash
pip install pytest
python -m pytest
```

## Themes

All RemedyAI / Remedy editor families shipped in the sidebar:

Grove and Studio are the RemedyAI rooms (home and workbench). Remedy Dark and Bright follow the Base16 Eighties orange of the VS Code / Zed Remedy themes. Adapted, Blur, Blur Light, and Glass match the extra Remedy UI treatments.
