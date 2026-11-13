# Hearth

[![Latest release](https://img.shields.io/github/v/release/AhmiDarrow/Hearth)](https://github.com/AhmiDarrow/Hearth/releases/latest)
[![License: MIT](https://img.shields.io/github/license/AhmiDarrow/Hearth)](LICENSE)

**HomeGroup for the whole house** — local folder sharing and PC-to-PC chat, with no cloud and almost no setup.

Windows HomeGroup made it boringly easy to share Pictures and Documents across the PCs at home. It disappeared in Windows 10. Hearth brings that ritual back as a modern Windows 11 app: one passphrase, a sidebar of the other computers, drag-and-drop files, and a small house chat. Everything stays on your LAN.

[Download HearthSetup.exe](https://github.com/AhmiDarrow/Hearth/releases/latest/download/HearthSetup.exe) · [All releases](https://github.com/AhmiDarrow/Hearth/releases)

![Two PCs sharing a folder in Hearth](docs/hearth-two-pcs.png)

<p align="center"><em>Living-room and Office on the same hearth, browsing a shared folder.</em></p>

![The same files view in light mode](docs/hearth-files-light.png)

<p align="center"><em>Light mode follows Windows, or you can pick a Remedy theme in Settings.</em></p>

## Features

- One-click Windows installer — no Python, no scripts, no git
- Create a home group or join one already on the LAN with just the password
- Invite codes as a fallback when a second router hides broadcasts
- Share Documents, Pictures, or any folder (read-only or read and write)
- Browse another PC, download with **Get**, or drag files onto a writable folder
- House chat, stored locally on each PC
- System tray app with an Edge WebView2 window (falls back to the browser)
- Dark and light mode that follows Windows
- Auto-update from GitHub Releases, verified with SHA-256 and Authenticode
- Built for homes that have a second router, not only a single subnet

## Download and install

Every home PC needs its own copy.

1. Download **[HearthSetup.exe](https://github.com/AhmiDarrow/Hearth/releases/latest/download/HearthSetup.exe)** from the [latest release](https://github.com/AhmiDarrow/Hearth/releases/latest). No GitHub account is required.
2. Double-click the installer. The build is **self-signed**, so Windows may show SmartScreen or “Unknown publisher.” That is expected until a certificate from a public CA is used. Choose **More info** → **Run anyway** if Windows asks.
3. Approve the one User Account Control prompt (so firewall rules can be added).
4. Click **Install**. Leave the defaults checked: desktop shortcut and start at sign-in.
5. Leave **Open Hearth now** checked at the end.

Hearth lives in the system tray. Closing the window hides it; **Quit** from the tray icon exits fully. Open it later from the Start Menu, the desktop shortcut, or the tray. It starts when you sign in.

## First-run setup

1. This PC’s name is pre-filled from the computer name — change it if you want.
2. On the first PC, click **Start a home group**. Hearth generates a group password (or you can choose one).
3. Install Hearth on the next PC. If the first one is already running on the same LAN, the new PC offers **Join Home on [that PC]**. Type the group password and click **Join**.
4. If the other PC is not detected (a second router often causes this), open **I have an invite** on the first PC, copy the invite, and paste it on the second PC.
5. Click **Share a folder** and pick Documents, Pictures, or any folder you want the house to see.

## Sharing and drag-and-drop

- PCs in the group show up in the left sidebar. Click one to see its shared folders.
- Open a folder, then click **Get** to download a file, or drop files onto the grid to upload when the share is read-and-write.
- Upload progress appears at the bottom of the files pane.
- Switch between a Finder-style grid and a list with the **Grid** / **List** buttons.
- Shares you publish on this PC are listed under **Shared from this PC**. You can stop sharing at any time.

Dragging a file *out* of Hearth into File Explorer is a normal download (**Get**), not an OS-level drag to the desktop.

## Sub-routers and firewall

Confirm the Wi-Fi or Ethernet is set to a **Private** network (Windows Settings → Network & internet → Properties). The installer only opens ports on the Private profile, on purpose.

| Item | What the installer sets |
| --- | --- |
| Windows Defender Firewall | Inbound allow, **Private** profile only: TCP **7474** (UI, files, chat), UDP **45454** (discovery) |
| Autostart | A Run-key entry starts `Hearth.exe --background` at sign-in (tray only) |
| Start Menu and desktop | Shortcuts named **Hearth** |
| Uninstall | Standard Apps & Features / Installed apps entry |

A second router in the house is the usual catch:

| Situation | What to do |
| --- | --- |
| Second box is already an access point (same 192.168.x) | Do nothing. Beacons and the invite handshake find everyone. |
| Second box is a router (for example main 192.168.1.x, rooms 192.168.0.x) | Paste the invite (it includes IPs). The inner PC can usually reach the outer PC. If the outer PC cannot browse the inner PC’s folders, port-forward TCP 7474 and UDP 45454 on the inner router, or put that router in access-point mode. |
| Chat works one way but files from the main LAN cannot open a bedroom PC | Same NAT issue as above. Chat will catch up because members pull messages from PCs they can reach. Folder browse needs a direct connection or a port forward. |
| One PC can ping both sides | Turn on **Bridge discovery across subnets** on that PC (Settings). |
| Windows firewall prompt | Allow Hearth on private networks. Port 7474 TCP and 45454 UDP. |

The most reliable long-term fix is still to set the second router to access-point mode so the house is one LAN. Hearth works either way.

## Trusting the publisher certificate

Each release includes the public certificate `Hearth.cer` and a `SHA256SUMS.txt` file. CI uses the matching private certificate (stored only as GitHub Actions secrets) to Authenticode-sign `Hearth.exe`, the uninstaller, and `HearthSetup.exe`.

Trusting the publisher on a home PC does **not** install a root CA, and it will **not** silence SmartScreen for a brand-new download. It does help Windows recognize later Hearth updates signed with the same certificate, so they should no longer show “Unknown publisher.”

```bat
certutil -user -addstore TrustedPublisher Hearth.cer
```

Or tick **Trust the Hearth publisher certificate** in the installer, or run `trust-hearth-cert.cmd` from the install folder after setup.

## Auto-update

Installed copies check this repository’s [GitHub Releases](https://github.com/AhmiDarrow/Hearth/releases) (including prereleases) on startup and every few hours, and from **Settings → Check for updates**.

When a newer version is found, Hearth downloads `HearthSetup.exe`, verifies it against `SHA256SUMS.txt` and the publisher certificate thumbprint, then runs the installer silently. The app reopens and shows “Updated to vX.” Automatic install only runs in the packaged Windows app. Set `HEARTH_NO_UPDATE=1` to disable checks (used by tests).

## Uninstall

Remove Hearth from **Settings → Apps → Installed apps** (or Apps & Features). That removes the program files, Start Menu and desktop shortcuts, autostart entry, and the Private firewall rules.

Data under `%USERPROFILE%\.hearth` (group state, device key, chat history) is left in place so a reinstall can rejoin more easily. Delete that folder if you want a clean slate.

## FAQ and troubleshooting

**Windows blocked the installer / SmartScreen appeared.**  
The official build is self-signed. Choose **More info** → **Run anyway**. See [Trusting the publisher certificate](#trusting-the-publisher-certificate).

**The other PC does not show up.**  
Make sure both PCs are on a **Private** network, Hearth is running on each (check the tray), and they joined the same group. If there is a second router, paste an invite or use Bridge mode. See [Sub-routers and firewall](#sub-routers-and-firewall).

**I can send chat but I cannot open the other PC’s folders.**  
The other PC is probably behind NAT. Port-forward TCP 7474 and UDP 45454, or set that router to access-point mode.

**A second copy of Hearth opened instead of focusing the first.**  
Hearth is meant to be a single instance. If this happens, fully **Quit** from the tray and start it again. Please open an issue if it keeps occurring.

**How do I turn off auto-update?**  
Set the environment variable `HEARTH_NO_UPDATE=1` for the Hearth process, or just ignore the Settings prompt. There is no cloud channel other than GitHub Releases.

**Does this work on macOS or Linux?**  
You can run from source (see below) and use the browser UI. The one-click installer, tray app, and Authenticode updates are Windows-only today.

**Is my data uploaded somewhere?**  
No. Discovery, file transfers, and chat stay on your network. The only outbound check is the optional look at GitHub Releases for updates.

## Building from source

Python 3.11 or newer. The Windows installer is built in GitHub Actions (`windows-latest`) with PyInstaller and Inno Setup; developers can still run the app directly:

```bash
git clone https://github.com/AhmiDarrow/Hearth.git
cd Hearth
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 -m hearth
```

Open http://127.0.0.1:7474

The `install.cmd` / `install.ps1` scripts are a developer fallback if you want a Start Menu shortcut and firewall rules without the Setup EXE.

Two copies on one machine (for a dry run):

```bash
HEARTH_HOME=/tmp/hearth-a HEARTH_PORT=7474 python3 -m hearth --no-browser
HEARTH_HOME=/tmp/hearth-b HEARTH_PORT=7475 python3 -m hearth --no-browser
```

Join B using A’s invite. If the invite has no loopback address, add `127.0.0.1:7474` as a host on B. The join handshake does not depend on the shared UDP beacon port.

```bash
pip install pytest
python -m pytest
```

The app version lives in one place: `hearth/__init__.py` (`__version__`). The installer and release tag are derived from that string.

State lives in `%USERPROFILE%\.hearth` on Windows (`~/.hearth` on other systems): `state.json`, `device.key`, `inbox/`.

## Security model

- Group password → PBKDF2-HMAC-SHA256 (210k iterations) → 32-byte group key
- Discovery packets and peer HTTP calls carry an HMAC of the group id
- Each install has an Ed25519 device identity under `~/.hearth/device.key`
- This is home-network security, not a substitute for full-disk encryption or a VPN on a hostile LAN
- Do not reuse the hearth password on the public internet

## License

[MIT](LICENSE). Copyright © 2026 Ahmi Darrow.
