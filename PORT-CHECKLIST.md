# Hearth on RemedyUI: port checklist

Source of truth: the shipped Python app (Hearth 0.4, `hearth/`, its page in `hearth/ui/static/`)
and `rust/docs/UI-INVENTORY.md`. The Rust app is `rust/hearth` (interface) over `rust/hearth-core`
(engine, wire-compatible with the Python app). Evidence: `port-evidence/before` (the Python app,
`tools/port_screens_before.py`) and `port-evidence/after` (the Rust app, the `shots` flow test),
the same 16 screens by name. Tests are named in brackets.

Targets: Windows (the app's only shipped platform). The engine and interface also build for
Linux; the Python app's Linux runs were its CI tests, which the Rust tests replace.

## Data (must never be lost)

- [x] `state.json` in the profile (`%USERPROFILE%\.hearth`, `HEARTH_HOME` overrides; `--home`
  too), same keys, same JSON layout, written through `state.tmp` [state::tests::*]
- [x] Keys a newer version adds are kept and written back (the Python app dropped them)
  [state::tests::a_python_state_file_loads_and_round_trips]
- [x] `device.key` (the identity) read as is, never rewritten; same device id
- [x] `transfers.json` (finished transfers) read and written in 0.4's form
- [x] `relaunch_ui.json` (window or tray after an update) read and written as 0.4 does
  [relaunch::tests::the_ui_mode_round_trips_in_0_4s_format]
- [x] `relaunch-args.json` / `relaunch-cmdline.txt` next to an installed `Hearth.exe`, ANSI
  command line for Setup, other profiles kept [relaunch::tests::*]
- [x] `inbox/` (downloads with no Downloads folder) used as 0.4 does; `hearth.log` appended to
- [x] Upgrade from a profile the Python app itself made, and back: nothing lost
  [upgrade::a_python_profile_upgrades_and_downgrades_without_losing_anything]
- [x] The interface's own choices (window place, grid or list) live in `<profile>/ui/`

## Look (the Python app's page, screen for screen)

The page's stylesheet (`hearth/ui/static/app.css`) is `rust/hearth/src/look.rs`: theme sizes
(16 px text, 36 px controls, the page's corner radii) and style rules over RemedyUI's sheet,
named by the page's classes. The palettes are RemedyUI's, the same RemedyAI colors. The face is
Remedy Sans UI where the page used the system's Segoe UI.

- [x] Chrome and top bar: bordered icon buttons, the mark beside the group's name over
  "N ON THE NETWORK", the pill search (up to 420 wide), Copy invite and Share a folder at the end
- [x] Sidebar: small capital headings, PC cards (initials in a framed box, name over address,
  online light; the PC being browsed is marked), share cards with Edit and ×
- [x] Files: pill tabs, bordered Back / Forward / Up, share pills, Grid | List, Upload and
  Download as worded buttons, the crumb in the link color; cards (icon box, name, size) or rows
  with the size at the end and no table header; the dashed "Drag files here to add them" box
- [x] Chat: bubbles as who, words, when (own on the right in the accent), the composer bar
- [x] First run: the card in the middle of the window with its shadow, the mark beside the
  name and lead, the tall Start button, and the two "details" rows that open (RemedyUI's
  `disclosure`)
- [x] Settings: 920 by 640 with the head, the sections down the side, and each setting as
  name, hint, control with a rule between; theme cards two to a row with their swatch
- [x] Share dialog: head and foot stay while the middle scrolls; library cards with their
  folders; the folder browser as a framed list
- [x] Toasts at the bottom right, stacked, over an open dialog
- [x] Better than the page where it fell short: accent text on panels and the name and time
  on this PC's own bubble meet 4.5:1 in every palette; paths and long names cut with "…"
  fill their line [look::tests::*, the accessibility audit on every flow screen]

## Window and chrome

- [x] Title "Hearth", 1280x800, minimum 720x520, closing hides to the tray, place remembered
- [x] Menu button: Settings, Check for updates, About, Help, Quit
- [x] Brand: the house mark and "Hearth" or the group's name; gear opens Settings
- [x] Update banner (checking, available with Install, downloading with progress, installing,
  failure) in the window's language
- [x] Toasts 3.2 s, 5 s for updated/saved/downloaded; queued while no window is open;
  assistive technology can dismiss them (engine fix)
- [x] Ctrl+, (Cmd+, on Apple) opens Settings; Esc closes menus and dialogs

## First run (no group)

- [x] "This PC" name, nearby groups with password (checked by the offering PC first),
  Start a home group, I have an invite, name and password for a new group, all 0.4 messages
  [first_run_names_the_pc_and_starts_a_group, a_bad_invite_says_so_and_settings_open_before_joining]
- [x] Settings reachable before joining (Network explains what to do first)
- [x] An invite passed on the command line joins (0.4 ignored launch arguments)

## The house

- [x] Top bar: group, "N on the network", search (PCs and files), Copy invite, Share a folder
- [x] PCs: initials, name (· you), first two addresses or local, · bridge, online light;
  self, online, by name; a click opens its first share, with 0.4's messages
- [x] Shared from this PC: cards with Read & write / Read only, Edit, × (asks first; 0.4 did
  not), drop a folder to share it read-only, empty state with Browse…
- [x] Narrow (≤ 900 px) hides the sidebar behind "PCs and shares"
- [x] Files | Chat tabs

## Files

- [x] Back, Forward, Up; share pills; breadcrumbs (Hearth's own names translated)
- [x] Grid or List (remembered between runs now; the desktop Python app forgot it)
- [x] Folders first, by name ignoring case, dot names hidden, Up entry in subfolders
- [x] Click, Ctrl, Shift, rubber band, Ctrl+A; Enter and double-click open/download;
  Backspace / Alt+←, Alt+→, Alt+↑; Ctrl+C copies paths
- [x] Context menu: Open, Download, Copy path; Unshare on own shares (opens the share root,
  fixing 0.4's bogus path)
- [x] Download / Download N, Upload (writable shares), upload by picker or by dropping
  (folders keep their shape), drop on a PC sends to its first writable share without moving
  the view (0.4 moved it), drag files out (remote ones download first)
- [x] Drop hints "Drop files to add them to this folder" / "This folder is read-only", and the
  "Drag files here to add them" note on writable folders
- [x] Empty, loading, listing errors (404/403/504 texts), no-match search
  [joined_by_invite_it_browses_downloads_uploads_and_chats_with_the_other_pc,
  this_pc_shares_a_dropped_folder_and_stops_sharing_after_asking]

## Transfers

- [x] Last 8, "Downloads", Clear downloads, percent · speed/s · time left, Done, Canceled,
  errors; Show in the file manager, Cancel, Remove from list; history kept in transfers.json

## Chat

- [x] Bubbles with name (→ private), text, local time; own on the right; last 200 shown,
  500 kept; follow at the bottom; empty state; 1–4000 characters
- [x] Private messages to one online PC (0.4 could receive but not write them)

## Share dialog (share / edit / download folder)

- [x] Titles, hints, OK texts per mode; name, folder, Browse… (system picker), Allow others to
  add files, Remove share (asks first)
- [x] Library quick picks (Documents, Pictures, Downloads, Desktop, Videos, Music that exist)
- [x] Folder browser: This PC (Home, libraries, drives by Explorer name), subfolders, Up,
  No subfolders, system folders refused, a click picks and a second click or double click opens
  [the_share_dialog_browses_this_pcs_folders]
- [x] Typed folders resolved as 0.4 does (`~`, drive letters, UNC, names under Home, library
  names) [localdirs::tests::*]
- [x] Visible title and close button

## Settings (six sections, each with the Python app's hints)

- [x] General: PC name (Save, Enter, or leaving the field; only when changed), Language
  (System + 11), Start at sign-in (Startup shortcut, never a Run key), Theme (System / Light /
  Dark and a card per palette with swatch and note), Reset (asks; leaves group, shares and
  language) [settings_explain_themselves_and_the_theme_cards_choose_a_palette]
- [x] Sharing: shares with permission select, Edit, Remove; Share a folder; download folder
- [x] Transfers: download/upload limits, presets and Custom (KB/s, MB/s), apply at once
- [x] Network: invite and Copy, password, extra hosts (add, remove), bridge, ports, Leave
- [x] Updates: version, last checked, automatic checks, Check now, Install update, release notes
- [x] About: lead, version, license, GitHub, certificate thumbprint, Help, signature

## Tray and system

- [x] Tray: Open (default), Settings, Check for updates, About, Quit, in the app's language
- [x] Single instance per profile; a second launch shows the window and passes an invite
- [x] Flags: --port, --home, --host, --background, --updated [VER], --desktop/--no-desktop/
  --no-browser accepted; HEARTH_PORT, HEARTH_HOME, HEARTH_LANG, HEARTH_LIST_TIMEOUT,
  HEARTH_UPDATE_DIR, HEARTH_CI
- [x] After an update the window comes back when it was open, the tray when it was not
- [x] Updates: GitHub releases with prereleases, SHA256SUMS and the Authenticode signer,
  20 s then every 4 h with back-off, install only on click, silent per-user Setup with /DIR as
  0.4 decides it, release page on failure
- [x] `hearth.log`: start banner, start and update failures

## Languages and themes

- [x] en, es, fr, de, pt-BR, it, ru, pl, ja, ko, zh-CN from the Python catalogs; switch live
  [the_language_follows_the_setting_live]
- [x] No right-to-left language is shipped, so there is no RTL screen to compare
- [x] system, light, dark and the ten RemedyAI palettes, following the OS when System
  [themes_follow_the_setting_live]
- [x] Accessibility audit clean on every flow screen; menus and tooltips inside the window in
  English and German, full size and narrow
  [menus_and_tooltips_stay_inside_the_window_in_any_language_and_size]

## Not carried over, on purpose

- The local web page and its `/api/*` (the native window replaces it; the peer protocol
  `/peer/*` is unchanged) and so `--self-test`, which exercised that page's API.
- Opening the page in a browser (`--no-desktop` without a window): the app is native now.
