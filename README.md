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
- Ten Remedy-family themes: Grove, Grove Light, Studio, Studio Light, Remedy Dark, Remedy Bright, Adapted, Blur, Blur Light, Glass
- Manual host entry when discovery cannot see a room
- Windows 11 installer with Start Menu shortcut, logon autostart, and Defender Firewall rules

## Install on Windows 11

Every home PC needs its own install. Python 3.11 or later is required (the installer will offer to install Python 3.12 with winget if it is missing).

1. Copy this repo onto the PC (clone it, or copy the folder from a USB drive).
2. Double-click `install.cmd`.
   - If Windows blocks the script, choose **More info → Run anyway**, or right-click `install.cmd` and run it from Explorer.
   - Approve the User Account Control prompt so Hearth can add private-network firewall rules.
3. The installer will:
   - Create a virtual environment under `%LOCALAPPDATA%\Hearth`
   - Add **Hearth** to the Start Menu
   - Register a Task Scheduler logon task so Hearth starts when you sign in
   - Allow inbound **TCP 7474** and **UDP 45454** on the **Private** firewall profile
   - Start Hearth and open http://127.0.0.1:7474
4. On the first PC, name it and click **Light the hearth**. Click **Copy invite**.
5. On each other PC, install the same way, choose **Join with invite**, and paste that invite.
6. Click **Share a folder** and pick Documents, Pictures, or any folder. Other PCs see it in the People list; open a PC to browse, click **Get** to download, or drag files onto a read-and-write folder to upload.

To start it later: use the Start Menu shortcut, or run `run.cmd`. If Hearth is already running, the shortcut just reopens the browser. To remove it, run `uninstall.cmd`.

### Firewall and autostart

| Item | What the installer sets |
| --- | --- |
| Windows Defender Firewall | Inbound allow, **Private** profile only: TCP **7474** (UI, files, chat), UDP **45454** (discovery) |
| Autostart | Task Scheduler task named **Hearth**, runs at logon for your user, no console window |
| Start Menu | **Hearth** shortcut opens the UI |

If you skipped the UAC prompt, other PCs may not see this one until you re-run `install.cmd` as administrator, or add those two inbound Private rules in Windows Security → Firewall & network protection → Advanced settings.

Confirm the Wi-Fi or Ethernet is set to a **Private** network (Windows Settings → Network & internet → Properties). Public profile stays blocked on purpose.

PowerShell, if you prefer not to use `install.cmd`:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

## First-run flow

1. Name this PC.
2. Light the hearth — or paste an invite.
3. Copy the invite. Read it to the other rooms, or send it over any channel you already trust.
4. Share Documents, Pictures, or a dedicated drop folder.
5. Chat from the middle pane. Click a person to open their shared folders. Drag files onto a read-and-write folder to add them.

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
  install.cmd       Windows 11 installer (double-click)
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
