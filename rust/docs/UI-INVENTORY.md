# Hearth 0.3.x interface inventory (for the native rewrite)

Source: the Python app's `hearth/ui/static/{index.html,app.js,app.css,themes.css}`, `desktop.py`,
`__main__.py`, `startup.py`, `singleinstance.py`, `windrag.py`, `folderpick.py`, `update.py`,
`authenticode.py`, and the `/api/*` routes in `app.py`. Strings are quoted exactly.

## Architecture facts
- The Python UI is a thin web page over local `/api/*`; every `/ws` message carries a full
  snapshot. The native app calls the core directly instead.
- Profile dir `%USERPROFILE%\.hearth` (`HEARTH_HOME` overrides): `state.json`, `device.key`,
  `inbox/`, `transfers.json`, `instance.json`, `hearth.log`, `just_updated`, `relaunch_ui.json`.
- UI prefs kept by the browser: file view `grid|list` (default grid), theme for first paint.
- User-facing errors: PermissionError "This PC could not read or write that file. It may be
  locked or in a folder Hearth cannot use."; missing "That file is no longer available.";
  timeout "The other PC did not respond in time."; else "Could not finish that transfer: <text ≤240>".

## Chrome (always)
- Title strip: hamburger `☰` (Menu), brand (hearth.png 22px + "Hearth" or group name), gear `⚙`
  (Settings). Native window title "Hearth", 1280x800, min 720x520, bg #161310; close hides to tray.
- App menu: "Settings", "Check for updates", "About", "Help", ─, "Quit". About/Help open
  Settings→About.
- Update banner (label, bar, percent) under the chrome.
- Toasts bottom-right: 3200 ms, 5000 ms when text matches /updated|saved|downloaded/i; update
  toasts de-duplicated by key, 5000 ms for errors.

## Gate (no group)
- "Hearth", "Share folders and chat with the other PCs in the house. No cloud, one home group."
- "This PC" input (placeholder "Living-room PC"), prefilled with device name.
- Nearby offers (beacons seen <30 s): card "Join <group_name||Home>", "Found on <name> · <ip>",
  password input "Group password", button "Join". Empty password → "Enter the group password";
  offer without salt → "This group is too old to one-click join. Paste an invite instead.";
  success "Joined <name||the group>"; failure "Could not join. Check the password."
- "Start a home group" (primary) → create {name: group name||"Home", password|null,
  device_name}; toast "Home group is ready. Copy the invite for the other PCs."
- Details "I have an invite": textarea "Paste a Hearth invite", "Join with invite"; toast
  "Joined. Looking for the other PCs now."; errors "Not a Hearth invite. Ask a member to copy
  theirs." / "Invite code is incomplete".
- Details "Name or password for a new group": "Group name" (default "Home"), "Password (leave
  blank to generate)" placeholder "A short phrase the house can remember".
- Settings reachable before joining (Network: "Join or start a home group to get an invite.",
  "Join a group to add hosts.").

## Main window (in a group)
- Top bar: group name, "<N> on the network" (online members), search "Search PCs or files"
  (filters PC list and current listing by name), "Copy invite" (toast "Invite copied"),
  "Share a folder" (primary).
- Sidebar (280 px; hidden ≤900 px behind `▤` "PCs and shares"):
  - "PCs" + online count. Rows: initials avatar (first two words), name (+" · you"),
    subtitle first 2 IPs joined " · " or "local", + " · bridge"; green dot online. Order: self,
    online, name.
  - "Shared from this PC" (drop target): cards label + "Read & write"/"Read only", "Edit",
    `×` "Remove share"; click opens share. Empty: "Share a folder, or drop one here." + "Browse…".
- Tabs "Files" | "Chat".
- Files toolbar: Back `←`, Forward `→`, Up `↑` ("Up one folder"); share pills; "Grid"|"List";
  "Download"/"Download N" (only with files selected); "Upload" (only on readwrite share).
- Breadcrumb: share label then path segments.
- Items: folder/file icon, name, size ("Folder" or B/KB/MB/GB one decimal). "Up" entry inside
  subfolders (size column "Parent folder"). Folders first, case-insensitive, dot-names hidden.
- Empty: "Empty folder" / "Empty folder. Drag files here to add them."; writable hint card
  "Drag files here to add them"; no shares: "Share a folder with the house, or drop one here." +
  "Browse…"; listing error shown in place.
- Rubber-band selection; drop overlay "Drop files to add them to this folder" / "This folder is
  read-only".
- Chat: bubbles "<from_name>" (+" → private" when `to`), text, time (h:mm); own messages
  right. Last 200 shown, 500 stored. Empty "No one has spoken yet. Say hello to the other rooms."
  Auto-scroll on first render, later only within 40 px of bottom. Composer "Message the house…"
  + "Send"; 1–4000 chars; "Join a hearth first" without group.

## Share dialog (modes share / edit / download)
| | share | edit | download |
|---|---|---|---|
| Title | "Share a folder" | "Edit share" | "Download folder" |
| Hint | "Browse for a folder on this PC, type a path, or pick a library below." | "Rename this share, browse to a different folder, or change permissions. Changes apply as soon as you save." | "Browse for a folder, or pick a library below." |
| OK | "Share this folder" | "Save changes" | "Use this folder" |
- Name field "Name shown to the house" (not in download); "Folder" field "Type a folder path, or
  click Browse…"; "Browse…" (native picker "Choose a folder"); checkbox "Allow others to add
  files"; "Remove share" (edit only, no confirm, toast "Share removed").
- Library quick-picks: Documents, Pictures, Downloads, Desktop, Videos, Music (that exist);
  empty "No usual libraries found. Browse or type a path."
- In-app folder browser: "Up", path ("This PC" at root), subfolders ("No subfolders"); root =
  Home + known folders + drives (volume label); hides dot and system folders (windows, program
  files, program files (x86), programdata, system volume information, $recycle.bin, recovery,
  boot, config.msi) → 403 "That folder cannot be browsed".
- OK: nothing chosen "Choose a folder first"; invalid "That folder does not exist on this PC";
  download → "Downloads will go to <path>"; edit → "Share updated"; share → opens it, "Shared
  <label> with the house".
- Labels: folder name; drive root "<Volume> (C:)" or "C:"; UNC "host:share".

## Transfers panel
- Last 8 items, header "Downloads", "Clear downloads" when any finished (never deletes files).
- Active: "<pct>% · <speed>/s · <eta>" (eta "Ns left", "Nm Ns left", "Nh Nm left"); done "Done";
  else message ("Canceled", error).
- "Show" (done downloads) reveals in Explorer (fallback toast "Saved to <dest>"); `×` "Cancel"
  (active); `×` "Remove from list" (finished; active → "That transfer is still running").
- Jobs: id (12 hex), kind, name, dest, bytes, total, speed, eta, status queued|running|done|
  error|canceled, message, started, updated. Finished persist in transfers.json; >80 trims to 40.
  Concurrency 4, chunk 4 MiB, progress ≤ every 0.25 s; write `.<name>.<rand>.part` then rename;
  collisions "name (1).ext".
- Uploads: per file progress; errors server detail or "Network error"; >20 files toast
  "Uploading N files".

## Settings (Ctrl+,; sections General, Sharing, Transfers, Network, Updates, About)
- General: "PC name" + "Save" (blur/Enter saves, toast "PC name saved"; blank → hostname);
  "Start Hearth when I sign in to Windows" (Startup folder `Hearth.lnk`, args `--background`;
  toasts "Hearth will start when you sign in" / "Removed the Startup shortcut"); Theme System |
  Light | Dark + 10 palettes; "Reset settings to defaults" confirm "Reset theme, startup,
  download folder, update checks, bridge, and speed limits to defaults?" → "Settings restored to
  defaults". Defaults: theme system, auto updates on, download folder "" (Downloads else
  ~/.hearth/inbox), limits 0, bridge off, start_at_login off.
- Sharing: per share label, path, select "Read only"/"Read & write", "Edit", "Remove"; empty
  "Nothing shared yet. Share a folder so the other PCs can see it."; "Share a folder";
  "Default download folder" + "Browse…" (bad → "That download folder does not exist on this PC").
- Transfers: hint "Speed limits apply immediately to transfers already running and to new ones.
  Unlimited is the default and is fastest on a home network." Download/upload limits: Unlimited=0,
  256 KB/s, 512 KB/s, 1, 5, 10, 25, 50, 100 MB/s (KiB/MiB multiples), "Custom…" (number ≥1,
  KB/s|MB/s).
- Network: invite box + "Copy invite" ("Join or start a home group first"); password shown;
  "Extra host" input "192.168.0.12 or 192.168.1.8:7474" + "Add host" ("Host required"); checkbox
  "Bridge discovery across subnets"; "Known extra hosts" (× removes; "No extra hosts yet.");
  ports text "This PC talks to others on TCP <peer_port> (files and chat) and UDP <beacon_port>
  (discovery). The installer opens those ports on the Windows Private profile."; danger
  "Leave this hearth" confirm "Leave this hearth on this PC?" (keeps shares and device key).
- Updates: "Current version: v<ver>", "Last checked: <date|never>", message, "Check for updates
  automatically", "Check now", "Install update" (packaged builds), "Release notes on GitHub".
- About: lead "Hearth is one simple home for sharing - local file sharing and PC-to-PC chat, with
  no cloud."; Version, License MIT, GitHub "AhmiDarrow/Hearth", "Certificate thumbprint"
  C7B807D542B3BA7207AABED960F556B4DC85C02F; Help paragraph; signature `"Hope this helps" - Ahmi`.

## Actions
- Click folder opens; click file selects; Ctrl toggles; Shift range; double-click opens folder or
  downloads file; Enter opens first selected; Ctrl+A; Ctrl+C copies relative paths ("Path copied");
  Backspace/Alt+← back, Alt+→ forward, Alt+↑ up; Esc closes menus/dialogs.
- Context menu: "Open", "Download", "Copy path"; on own share cards also "Unshare".
- Download: "Select a file to download", "Downloading <name>" / "Downloading N files";
  "That PC is offline"; "Nothing to download".
- Clicking a PC opens its first share; none → "<name> has not shared a folder yet" / "Share a
  folder first" (self).
- Drop into file pane uploads (recursive, read-only → "This folder is read-only", nothing →
  "Nothing to upload. Empty folders are skipped."); drop folder on "Shared from this PC" shares it
  read-only; drop on a PC sends to its first writable share ("<name> has no folder that allows
  uploads", "Sent to <name>"); drag a local file out copies via OLE; remote → "Downloading
  first — drag it out once it finishes."

## Live updates and timers
- Discovery beacon every 3 s (UDP 45454: 255.255.255.255, multicast 239.54.54.54, each
  subnet .255, manual hosts; bridge rebroadcasts). Online and nearby windows 30 s.
- Maintenance every 5 s: handshake known hosts (5 s timeout), pull /peer/chat.
- Timeouts: handshake/chat 5 s, shares 8 s, files 12 s, transfers none.

## Desktop
- Flags: --port (7474), --home, --host (0.0.0.0), --no-browser, --desktop, --no-desktop,
  --background (tray only), --self-test, --updated [VER], positional open args.
- Single instance per profile; second launch forwards args and shows the window.
- Tray: Open (default), Settings, Check for updates, About, Quit.
- Startup: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Hearth.lnk` → exe
  `--background`; never a Run key.

## Themes
system, light, dark, neutral (Neutral Dark), forest (Dark Forest), green (Emerald), purple
(Amethyst), alien (Dark Purple), orange (Amber), cyan (Ocean), deutan, protan, tritan — the
RemedyAI palettes (same as `remedy-ui-themes`). System follows the OS light/dark.

## Updates
- Feed `https://api.github.com/repos/AhmiDarrow/Hearth/releases` (HEARTH_RELEASES_URL),
  assets `HearthSetup.exe` + `SHA256SUMS.txt` (required to install). Newest version strictly
  greater; prereleases included; drafts skipped.
- Checks 20 s after start then every 4 h when enabled; failure backoff min(4 h, 15 min·2^(n-1)).
- Install only on click: SHA-256 must match SHA256SUMS; Authenticode signer SHA-1 thumbprint
  must equal C7B807D542B3BA7207AABED960F556B4DC85C02F (self-signed, so NotTrusted is tolerated
  when the thumbprint matches; HashMismatch never).
- Installer args `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS
  /FORCECLOSEAPPLICATIONS /CURRENTUSER` (+ `/DIR=`), app exits; installer relaunches with
  `--updated`; toast "Updated to v<ver>".
- Failure opens the release page: "The in-app updater could not finish (<detail>). The release
  download page is opening so you can install the update yourself."

## Known quirks to fix in the rewrite
- Context menu "Open" on own share card navigates to a bogus path (open the share root).
- Launch args (`pending_open`) unused; private chat not composable; unshare/remove have no
  confirmation; join-by-password not verified up front; drop on a PC moves the file view.
