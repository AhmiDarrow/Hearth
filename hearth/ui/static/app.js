const THEMES = [
  { id: "system", name: "System", note: "Follows Windows", colors: ["#121214", "#e39b5a", "#f4f1ec"] },
  { id: "light", name: "Light", note: "Always light", colors: ["#f4f1ec", "#c56a2d", "#2f7d55"] },
  { id: "dark", name: "Dark", note: "Always dark", colors: ["#121214", "#e39b5a", "#6ecb8b"] },
  { id: "grove-dark", name: "Grove", note: "Remedy home", colors: ["#14110e", "#e39b5a", "#8fbc8a"] },
  { id: "grove-light", name: "Grove Light", note: "Daylit grove", colors: ["#f4efe6", "#c56a2d", "#3f7d4e"] },
  { id: "studio-dark", name: "Studio", note: "Workbench", colors: ["#0e1013", "#f0a05a", "#7dcea0"] },
  { id: "studio-light", name: "Studio Light", note: "Paper bench", colors: ["#eef2f6", "#c56b24", "#2f7d55"] },
  { id: "remedy-dark", name: "Remedy Dark", note: "Eighties orange", colors: ["#2d2d2d", "#f99157", "#99cc99"] },
  { id: "remedy-bright", name: "Remedy Bright", note: "Base16 day", colors: ["#f7f4ef", "#d96b2b", "#4e8f4e"] },
  { id: "remedy-adapted", name: "Adapted", note: "Muted ember", colors: ["#1f1c1a", "#e8834c", "#8fbf8a"] },
  { id: "remedy-blur", name: "Blur", note: "Frosted night", colors: ["#12100f", "#f0a06a", "#9ad1a0"] },
  { id: "remedy-blur-light", name: "Blur Light", note: "Frosted paper", colors: ["#f6f1ea", "#c45e22", "#3d7a4b"] },
  { id: "glass", name: "Glass", note: "Transparent", colors: ["#0b0d12", "#f4a261", "#80c9a4"] },
];

let state = null;
let fileCursor = null;
let fileItems = [];
let pickerPath = "";
let pickerSelected = "";
let pickerMode = "share";
let fileView = localStorage.getItem("hearth-file-view") || "grid";
let uploads = [];
let selected = new Set();
let lastClicked = null;
let historyStack = [];
let historyIndex = -1;
let ignoringHistory = false;
let ctxTarget = null;
const $ = (id) => document.getElementById(id);

function systemScheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
function applyTheme(id) {
  const theme = id || "system";
  if (theme === "light" || theme === "dark") {
    document.documentElement.dataset.theme = "system";
    document.documentElement.dataset.scheme = theme;
  } else {
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.scheme = theme === "system" ? systemScheme() : (theme.endsWith("light") || theme === "remedy-bright" ? "light" : "dark");
  }
  localStorage.setItem("hearth-theme", theme);
}
function toast(text) {
  const host = $("toasts");
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  host.appendChild(el);
  setTimeout(() => el.remove(), text && /updated|saved|downloaded/i.test(text) ? 5000 : 3200);
}
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
    body: opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
  });
  if (!res.ok) {
    let detail = await res.text();
    try { detail = JSON.parse(detail).detail || detail; } catch {}
    throw new Error(typeof detail === "string" ? detail : (detail && JSON.stringify(detail)) || res.statusText);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}
function initials(name) {
  return (name || "PC").split(/\s+/).slice(0, 2).map((p) => p[0]).join("").toUpperCase();
}
function timeFmt(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function dateFmt(ts) {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function formatSize(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 ** 2) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + " MB";
  return (n / 1024 ** 3).toFixed(1) + " GB";
}
function formatSpeed(n) {
  if (!n) return "";
  return formatSize(n) + "/s";
}
function canUpload() {
  return Boolean(fileCursor?.share) && fileCursor.share.mode === "readwrite";
}
function joinRel(base, extra) {
  return [base || "", extra || ""].filter(Boolean).join("/").replace(/\\/g, "/");
}
function itemRel(name) {
  return joinRel(fileCursor?.path || "", name);
}
function setFileView(mode) {
  fileView = mode;
  localStorage.setItem("hearth-file-view", mode);
  $("files").classList.toggle("grid", mode === "grid");
  $("files").classList.toggle("list", mode === "list");
  $("viewGrid").classList.toggle("active", mode === "grid");
  $("viewList").classList.toggle("active", mode === "list");
}

function closeMenus() {
  $("appMenu").hidden = true;
  $("menuBtn").setAttribute("aria-expanded", "false");
  hideContextMenu();
}

function openSettings(section) {
  closeMenus();
  $("settings").hidden = false;
  if (section) showSettingsSection(section);
  renderSettings();
}
function closeSettings() {
  $("settings").hidden = true;
}
function showSettingsSection(id) {
  document.querySelectorAll("#settingsNav [data-settings]").forEach((b) => b.classList.toggle("active", b.dataset.settings === id));
  document.querySelectorAll(".settings-section").forEach((s) => s.classList.toggle("active", s.id === "settings-" + id));
}
function toggleMenu() {
  const menu = $("appMenu");
  const open = menu.hidden;
  menu.hidden = !open;
  $("menuBtn").setAttribute("aria-expanded", open ? "true" : "false");
}
async function quitApp() {
  closeMenus();
  try { await api("/api/quit", { method: "POST", body: {} }); } catch {}
  try { await window.pywebview?.api?.quit?.(); } catch {}
}

function renderThemes() {
  if (!$("themes")) return;
  const current = state?.theme || "system";
  $("themes").innerHTML = THEMES.filter((t) => !["system", "light", "dark"].includes(t.id)).map((t) => `
    <button class="theme-card ${current === t.id ? "active" : ""}" data-theme="${t.id}" type="button">
      <span class="swatch" style="background:linear-gradient(90deg,${t.colors.join(",")})"></span>
      <span class="meta"><b>${t.name}</b><span>${t.note}</span></span>
    </button>`).join("");
  document.querySelectorAll("[data-theme-basic]").forEach((b) => {
    b.classList.toggle("active", b.dataset.themeBasic === current || (current !== "light" && current !== "dark" && current !== "system" ? false : b.dataset.themeBasic === current));
  });
}
function renderNearby() {
  const box = $("nearbyBox");
  const offers = state?.nearby || [];
  if (!offers.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = offers.map((o) => `
    <form class="nearby-card" data-nearby="${escapeHtml(o.group_id)}">
      <div class="meta">
        <strong>Join ${escapeHtml(o.group_name || "Home")}</strong>
        <span>Found on ${escapeHtml(o.name)} · ${escapeHtml((o.ips || [o.address]).filter(Boolean)[0] || "")}</span>
      </div>
      <div class="row">
        <input name="password" type="text" placeholder="Group password" required />
        <button class="btn primary" type="submit">Join</button>
      </div>
    </form>`).join("");
}
function renderTransfers() {
  const el = $("transfers");
  if (!el) return;
  const server = state?.transfers || [];
  const items = [...server];
  uploads.forEach((u) => {
    if (!items.some((t) => t.id === u.id)) items.push(u);
  });
  if (!items.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = items.slice(-8).map((t) => {
    const pct = t.total ? Math.round((t.bytes || 0) / t.total * 100) : (t.pct || (t.status === "done" ? 100 : 0));
    const status = t.status === "up" || t.status === "running" || t.status === "queued"
      ? `${pct || 0}%${t.speed ? " · " + formatSpeed(t.speed) : ""}`
      : (t.status === "done" ? "Done" : t.message || t.status);
    const cancel = (t.status === "running" || t.status === "queued") ? `<button class="btn ghost icon" data-cancel-xfer="${t.id}" type="button" title="Cancel">×</button>` : "";
    const reveal = (t.status === "done" && t.dest) ? `<button class="btn ghost" data-reveal="${escapeHtml(t.dest)}" type="button">Show</button>` : "";
    return `<div class="transfer ${t.status}">
      <b>${escapeHtml(t.name)}</b>
      <span class="speed">${escapeHtml(status)}</span>
      <div class="xfer-actions">${reveal}${cancel}</div>
      <div class="bar"><span style="width:${pct || (t.status === "done" ? 100 : 0)}%"></span></div>
    </div>`;
  }).join("");
}
function renderSettings() {
  if (!state) return;
  const s = state.settings || {};
  if ($("settingsDeviceName") && document.activeElement !== $("settingsDeviceName")) {
    $("settingsDeviceName").value = s.device_name || state.device?.name || "";
  }
  if ($("startAtLogin")) $("startAtLogin").checked = !!s.start_at_login;
  if ($("downloadFolder")) $("downloadFolder").value = s.download_folder || "";
  if ($("autoCheckUpdates")) $("autoCheckUpdates").checked = s.auto_check_updates !== false;
  if ($("currentVersion")) $("currentVersion").textContent = "Current version: v" + (state.version || "");
  if ($("aboutVersion")) $("aboutVersion").textContent = "v" + (state.version || "");
  if ($("aboutLicense")) $("aboutLicense").textContent = s.license || "MIT";
  if ($("aboutThumb")) $("aboutThumb").textContent = s.cert_thumbprint || "—";
  if ($("aboutGithub") && s.github) $("aboutGithub").href = s.github;
  if ($("lastChecked")) $("lastChecked").textContent = "Last checked: " + dateFmt(s.last_update_check);
  if ($("portsHint")) {
    const peer = s.peer_port || state.device?.peer_port || 7474;
    const beacon = s.beacon_port || state.device?.beacon_port || 45454;
    $("portsHint").textContent = `This PC talks to others on TCP ${peer} (files and chat) and UDP ${beacon} (discovery). The installer opens those ports on the Windows Private profile.`;
  }
  const upd = state.update || {};
  if ($("updateHint")) {
    $("updateHint").textContent = upd.message || "Hearth checks GitHub Releases on startup and every few hours. Updates are never installed silently; click Install update to review the official installer.";
  }
  if ($("installUpdate")) $("installUpdate").hidden = !(upd.available && !upd.opened_installer && state.frozen);
  if ($("releaseNotes")) {
    $("releaseNotes").href = upd.html_url || "https://github.com/AhmiDarrow/Hearth/releases";
    $("releaseNotes").hidden = false;
  }
  const shareHost = $("settingsShares");
  if (shareHost) {
    const shares = state.shares || [];
    shareHost.innerHTML = shares.length ? shares.map((share) => `
      <div class="settings-share">
        <div class="meta"><b>${escapeHtml(share.label)}</b><span class="path">${escapeHtml(share.path)}</span></div>
        <select data-share-mode="${share.id}">
          <option value="read" ${share.mode !== "readwrite" ? "selected" : ""}>Read only</option>
          <option value="readwrite" ${share.mode === "readwrite" ? "selected" : ""}>Read & write</option>
        </select>
        <button class="btn ghost" data-unshare="${share.id}" type="button">Remove</button>
      </div>`).join("") : `<div class="empty">Nothing shared yet. Share Documents or Pictures so the other PCs can see them.</div>`;
  }
  if (state.group) {
    if ($("inviteBox")) $("inviteBox").textContent = state.group.invite || "";
    if ($("passwordBox")) $("passwordBox").textContent = state.group.password || "";
    if ($("bridge")) $("bridge").checked = state.bridge;
    if ($("hosts")) {
      $("hosts").innerHTML = (state.manual_hosts || []).map((h) => `
        <div class="share-card"><span class="meta"><b>${escapeHtml(h)}</b></span><button class="btn ghost icon" data-unhost="${escapeHtml(h)}" type="button">×</button></div>`).join("") || `<div class="empty">No extra hosts yet.</div>`;
    }
  } else {
    if ($("inviteBox")) $("inviteBox").textContent = "Join or start a home group to get an invite.";
    if ($("passwordBox")) $("passwordBox").textContent = "";
    if ($("hosts")) $("hosts").innerHTML = `<div class="empty">Join a group to add hosts.</div>`;
  }
  renderThemes();
}
function render() {
  if (!state) return;
  applyTheme(state.theme || "system");
  const inGroup = Boolean(state.group);
  $("gate").hidden = inGroup;
  $("app").hidden = !inGroup;
  $("chromeTitle").textContent = inGroup ? (state.group.name || "Hearth") : "Hearth";
  renderSettings();
  if (!inGroup) {
    if ($("deviceName") && !$("deviceName").value) $("deviceName").value = state.device.name;
    renderNearby();
    return;
  }
  $("hearthTitle").textContent = state.group.name;
  $("hearthSub").textContent = `${state.members.filter((m) => m.online).length} on the network`;
  $("onlineCount").textContent = state.members.filter((m) => m.online).length;
  const q = ($("filter")?.value || "").toLowerCase();
  const selectedId = fileCursor?.deviceId;
  $("members").innerHTML = state.members.filter((m) => m.name.toLowerCase().includes(q)).map((m) => `
    <button class="member ${m.device_id === selectedId ? "active" : ""}" data-open="${m.device_id}" type="button" draggable="false">
      <div class="avatar">${initials(m.name)}</div>
      <div class="meta"><b>${escapeHtml(m.name)}${m.self ? " · you" : ""}</b><span>${(m.ips || []).filter(Boolean).slice(0, 2).join(" · ") || "local"}${m.bridge ? " · bridge" : ""}</span></div>
      <span class="dot ${m.online ? "on" : ""}"></span>
    </button>`).join("");
  $("myShares").innerHTML = (state.shares || []).length ? state.shares.map((s) => `
    <div class="share-card" data-myshare="${s.id}">
      <div class="meta"><b>${escapeHtml(s.label)}</b><span>${s.mode === "readwrite" ? "Read & write" : "Read only"}</span></div>
      <button class="btn ghost icon" data-unshare="${s.id}" type="button">×</button>
    </div>`).join("") : `<div class="empty">Share a folder, or drop one here.</div>`;
  const log = $("chatLog");
  if (log) {
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
    log.innerHTML = (state.chat || []).map((m) => `
      <div class="bubble ${m.from_id === state.device.id ? "me" : ""}">
        <div class="who">${escapeHtml(m.from_name)}${m.to ? " → private" : ""}</div>
        <div>${escapeHtml(m.text)}</div>
        <div class="when">${timeFmt(m.ts)}</div>
      </div>`).join("") || `<div class="empty">No one has spoken yet. Say hello to the other rooms.</div>`;
    if (stick || !log.dataset.ready) { log.scrollTop = log.scrollHeight; log.dataset.ready = "1"; }
  }
  if ($("uploadBtn")) $("uploadBtn").hidden = !canUpload();
  updateDownloadButton();
  renderTransfers();
  if (!fileCursor && (state.shares || []).length) {
    openShare(state.device.id, state.shares[0], "", state.shares);
  }
}

function fileItem(item) {
  const isDir = !!item.dir;
  const size = isDir ? "Folder" : formatSize(item.size);
  const sel = selected.has(item.name) ? " selected" : "";
  const tag = fileView === "grid" ? "button" : "div";
  const cls = fileView === "grid" ? "file-tile" : "file-row";
  return `<${tag} class="${cls}${sel}" type="button" draggable="${isDir ? "false" : "true"}" data-dir="${isDir}" data-name="${escapeHtml(item.name)}" data-size="${item.size || 0}">
    <div class="avatar">${isDir ? "▦" : "▨"}</div>
    <div class="meta"><b>${escapeHtml(item.name)}</b></div>
    <span class="size">${size}</span>
  </${tag}>`;
}
function paintFiles() {
  const host = $("files");
  if (!host) return;
  const q = ($("filter")?.value || "").toLowerCase();
  const items = q ? fileItems.filter((i) => i.name.toLowerCase().includes(q)) : fileItems;
  const drop = canUpload() ? `<div class="drop-hint">Drag files here to add them</div>` : "";
  if (!items.length) {
    host.innerHTML = `<div class="empty">${canUpload() ? "Empty folder. Drag files here to add them." : "Empty folder"}</div>`;
    return;
  }
  // Virtualize very large lists: render a window around the scroll position.
  if (items.length > 250 && fileView === "list") {
    const rowH = 46;
    const scroll = host.scrollTop;
    const view = host.clientHeight || 400;
    const start = Math.max(0, Math.floor(scroll / rowH) - 10);
    const end = Math.min(items.length, start + Math.ceil(view / rowH) + 20);
    host.innerHTML = `<div style="height:${start * rowH}px"></div>` +
      items.slice(start, end).map(fileItem).join("") +
      `<div style="height:${(items.length - end) * rowH}px"></div>`;
    return;
  }
  host.innerHTML = items.map(fileItem).join("") + (canUpload() ? drop : "");
}
function updateNavButtons() {
  if ($("navBack")) $("navBack").disabled = historyIndex <= 0;
  if ($("navForward")) $("navForward").disabled = historyIndex < 0 || historyIndex >= historyStack.length - 1;
}
function pushHistory(entry) {
  if (ignoringHistory) return;
  const next = { deviceId: entry.deviceId, shareId: entry.share.id, path: entry.path || "" };
  const cur = historyStack[historyIndex];
  if (cur && cur.deviceId === next.deviceId && cur.shareId === next.shareId && cur.path === next.path) return;
  historyStack = historyStack.slice(0, historyIndex + 1);
  historyStack.push(next);
  historyIndex = historyStack.length - 1;
  updateNavButtons();
}
function historyEntry() {
  return historyStack[historyIndex];
}

async function openShare(deviceId, share, path = "", shares = null, opts = {}) {
  fileCursor = { deviceId, share, path, shares: shares || fileCursor?.shares || [share] };
  selected.clear();
  lastClicked = null;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === "files"));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-files"));
  $("shareTabs").innerHTML = (fileCursor.shares || []).map((s) => `
    <button class="share-tab ${s.id === share.id ? "active" : ""}" data-share="${s.id}" type="button">${escapeHtml(s.label)}</button>
  `).join("");
  const parts = path.split("/").filter(Boolean);
  $("crumb").innerHTML = `<button data-path="" type="button">${escapeHtml(share.label)}</button>` + parts.map((_, i, arr) => {
    const p = arr.slice(0, i + 1).join("/");
    return `<span>/</span><button data-path="${escapeHtml(p)}" type="button">${escapeHtml(arr[i])}</button>`;
  }).join("");
  $("uploadBtn").hidden = share.mode !== "readwrite";
  setFileView(fileView);
  if (!opts.fromHistory) pushHistory(fileCursor);
  updateNavButtons();
  try {
    const data = await api(`/api/files/${deviceId}/${share.id}?path=${encodeURIComponent(path)}`);
    if (data.share) {
      fileCursor.share = { ...share, ...data.share };
      $("uploadBtn").hidden = fileCursor.share.mode !== "readwrite";
    }
    fileItems = data.items || [];
    paintFiles();
    updateDownloadButton();
  } catch (err) {
    fileItems = [];
    $("files").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
  render();
}
async function goHistory(delta) {
  const next = historyIndex + delta;
  if (next < 0 || next >= historyStack.length) return;
  historyIndex = next;
  const entry = historyEntry();
  const shares = fileCursor?.shares;
  const share = (shares || state.shares || []).find((s) => s.id === entry.shareId) || { id: entry.shareId, label: "Folder", mode: "read" };
  ignoringHistory = true;
  try { await openShare(entry.deviceId, share, entry.path, shares, { fromHistory: true }); }
  finally { ignoringHistory = false; updateNavButtons(); }
}
async function openDeviceShares(deviceId) {
  const peer = state.members.find((m) => m.device_id === deviceId);
  if (!peer) return;
  try {
    const data = await api(`/api/remote-shares/${peer.device_id}`);
    if (!data.shares?.length) {
      toast(peer.self ? "Share a folder first" : peer.name + " has not shared a folder yet");
      return;
    }
    await openShare(peer.device_id, data.shares[0], "", data.shares);
  } catch (err) { toast(err.message); }
}

function selectedItems() {
  return fileItems.filter((i) => selected.has(i.name));
}
function updateDownloadButton() {
  const btn = $("downloadBtn");
  if (!btn) return;
  const files = selectedItems().filter((i) => !i.dir);
  btn.hidden = files.length === 0;
  btn.textContent = files.length > 1 ? "Download " + files.length : "Download";
}
function selectClick(item, ev) {
  if (ev.shiftKey && lastClicked) {
    const names = fileItems.map((i) => i.name);
    const a = names.indexOf(lastClicked);
    const b = names.indexOf(item.name);
    if (a >= 0 && b >= 0) {
      const [lo, hi] = a < b ? [a, b] : [b, a];
      if (!ev.ctrlKey && !ev.metaKey) selected.clear();
      names.slice(lo, hi + 1).forEach((n) => selected.add(n));
    }
  } else if (ev.ctrlKey || ev.metaKey) {
    if (selected.has(item.name)) selected.delete(item.name);
    else selected.add(item.name);
    lastClicked = item.name;
  } else {
    selected.clear();
    selected.add(item.name);
    lastClicked = item.name;
  }
  paintFiles();
  updateDownloadButton();
}
async function openItem(item) {
  if (!fileCursor) return;
  if (item.dir) {
    await openShare(fileCursor.deviceId, fileCursor.share, itemRel(item.name), fileCursor.shares);
  } else {
    await startDownload([item]);
  }
}
function copyPaths(items) {
  const text = items.map((i) => itemRel(i.name) || i.name).join("\n");
  navigator.clipboard.writeText(text).then(() => toast("Path copied"), () => toast("Could not copy"));
}
async function startDownload(items) {
  const files = (items || selectedItems()).filter((i) => !i.dir);
  if (!files.length || !fileCursor) { toast("Select a file to download"); return; }
  try {
    const res = await api("/api/transfers/download", {
      method: "POST",
      body: {
        device_id: fileCursor.deviceId,
        share_id: fileCursor.share.id,
        paths: files.map((f) => itemRel(f.name)),
      },
    });
    if (res.transfers) state.transfers = res.transfers;
    renderTransfers();
    const n = files.length;
    toast(n === 1 ? "Downloading " + files[0].name : "Downloading " + n + " files");
  } catch (err) { toast(err.message); }
}

function hideContextMenu() {
  $("ctxMenu").hidden = true;
  ctxTarget = null;
}
function showContextMenu(ev, items, extra) {
  ev.preventDefault();
  ev.stopPropagation();
  const menu = $("ctxMenu");
  const hasFile = items.some((i) => !i.dir);
  const hasDir = items.some((i) => i.dir);
  const canUnshare = extra?.unshareId;
  menu.innerHTML = `
    <button type="button" data-ctx="open" ${items.length !== 1 && !hasDir ? "disabled" : ""}>Open</button>
    <button type="button" data-ctx="download" ${hasFile ? "" : "disabled"}>Download</button>
    <button type="button" data-ctx="copy">Copy path</button>
    ${canUnshare ? `<div class="ctx-sep"></div><button type="button" data-ctx="unshare">Unshare</button>` : ""}
  `;
  menu.hidden = false;
  const x = Math.min(ev.clientX, window.innerWidth - 220);
  const y = Math.min(ev.clientY, window.innerHeight - 180);
  menu.style.left = x + "px";
  menu.style.top = y + "px";
  ctxTarget = { items, extra };
}

function readDroppedEntry(entry, prefix) {
  return new Promise((resolve) => {
    if (!entry) return resolve([]);
    if (entry.isFile) {
      entry.file((file) => {
        try { file.hearthDir = prefix || ""; } catch {}
        try { file.hearthEntry = entry; } catch {}
        resolve([file]);
      }, () => resolve([]));
      return;
    }
    if (entry.isDirectory) {
      const reader = entry.createReader();
      const collected = [];
      const next = () => {
        reader.readEntries(async (entries) => {
          if (!entries.length) {
            const flat = [];
            collected.forEach((group) => flat.push(...group));
            resolve(flat);
            return;
          }
          const groups = await Promise.all(entries.map((ent) => readDroppedEntry(ent, joinRel(prefix, entry.name))));
          collected.push(...groups);
          next();
        }, () => resolve(collected.flat()));
      };
      next();
      return;
    }
    resolve([]);
  });
}
async function collectDroppedFiles(dt) {
  const items = dt.items && dt.items.length ? [...dt.items] : [];
  if (items.some((item) => item.webkitGetAsEntry)) {
    const groups = await Promise.all(items.map((item) => readDroppedEntry(item.webkitGetAsEntry?.(), "")));
    const files = groups.flat().filter(Boolean);
    if (files.length) return files;
  }
  return [...(dt.files || [])];
}
function droppedFolderPath(dt) {
  const files = [...(dt.files || [])];
  for (const file of files) {
    const p = file.path || file.webkitRelativePath;
    if (file.path && (file.type === "" || file.name === file.path.split(/[/\\]/).pop())) {
      // WebView2 often exposes an absolute path.
      if (/[/\\]/.test(file.path)) {
        // If a folder was dropped, Chromium may give the files inside; parent is the folder.
        if (file.webkitRelativePath && file.webkitRelativePath.includes("/")) {
          const rel = file.webkitRelativePath.split("/")[0];
          const abs = file.path.replace(/[/\\][^/\\]+$/, "");
          // walk up to the dropped folder name
          const idx = abs.toLowerCase().lastIndexOf(rel.toLowerCase());
          if (idx >= 0) return abs.slice(0, idx + rel.length);
        }
        if (!file.webkitRelativePath) {
          // A single file with a path — use its parent if the drop was a folder intent; otherwise the file's parent is not a share request.
        }
      }
    }
  }
  // Direct folder path if the first entry is a directory with .path
  const item = dt.items && dt.items[0];
  const entry = item?.webkitGetAsEntry?.();
  if (entry?.isDirectory && (item.getAsFile?.()?.path)) {
    const f = item.getAsFile();
    return f.path;
  }
  return null;
}
function uploadFiles(fileList) {
  if (!canUpload() || !fileList?.length) return Promise.resolve();
  const { deviceId, share, path } = fileCursor;
  if (fileList.length > 20) toast("Uploading " + fileList.length + " files");
  const jobs = [...fileList].map((file) => new Promise((resolve) => {
    const row = { id: "up-" + Math.random().toString(16).slice(2), name: file.name, pct: 0, status: "up", kind: "upload" };
    uploads.push(row);
    renderTransfers();
    const body = new FormData();
    body.append("file", file);
    const dest = joinRel(path, file.hearthDir || "");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload/${deviceId}/${share.id}?path=${encodeURIComponent(dest)}`);
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) { row.pct = Math.round((ev.loaded / ev.total) * 100); row.bytes = ev.loaded; row.total = ev.total; renderTransfers(); }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { row.pct = 100; row.status = "done"; }
      else {
        let detail = xhr.responseText;
        try { detail = JSON.parse(detail).detail || detail; } catch {}
        row.status = "error";
        row.message = detail || xhr.statusText;
      }
      renderTransfers();
      resolve();
    };
    xhr.onerror = () => { row.status = "error"; row.message = "Network error"; renderTransfers(); resolve(); };
    xhr.send(body);
  }));
  return Promise.all(jobs).then(() => openShare(deviceId, share, path, fileCursor.shares));
}
function setPickerFolder(path) {
  pickerSelected = path;
  $("browsePath").textContent = path || "This PC";
  document.querySelectorAll(".quick-share, .folder-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.path === path);
  });
}
async function loadPicker(path = "") {
  const data = await api(`/api/local-folders?path=${encodeURIComponent(path)}`);
  pickerPath = data.path || "";
  setPickerFolder(pickerPath);
  $("browseUp").disabled = !data.parent;
  $("browseUp").dataset.parent = data.parent || "";
  $("folderList").innerHTML = (data.items || []).map((item) => `
    <button class="folder-item" type="button" data-path="${escapeHtml(item.path)}">
      <span class="avatar">▦</span>
      <span class="meta"><b>${escapeHtml(item.name)}</b><span>${escapeHtml(item.path)}</span></span>
    </button>
  `).join("") || `<div class="empty">No subfolders</div>`;
}
async function openShareModal(mode = "share") {
  pickerMode = mode;
  $("shareModal").hidden = false;
  $("shareWrite").checked = false;
  $("shareWriteRow").hidden = mode !== "share";
  $("shareModalTitle").textContent = mode === "download" ? "Download folder" : "Share a folder";
  $("shareModalHint").textContent = mode === "download"
    ? "Pick where files from other PCs should be saved."
    : "Pick a library to share with the house, or browse to any folder on this PC.";
  $("shareModalOk").textContent = mode === "download" ? "Use this folder" : "Share this folder";
  const folders = state?.known_folders?.length ? state.known_folders : (await api("/api/known-folders")).folders;
  $("quickShares").innerHTML = (folders || []).map((f) => `
    <button class="quick-share" type="button" data-path="${escapeHtml(f.path)}">
      <b>${escapeHtml(f.name)}</b>
      <span>${escapeHtml(f.path)}</span>
    </button>
  `).join("") || `<div class="hint">No usual libraries found. Browse below.</div>`;
  const start = mode === "download" ? (state?.settings?.download_folder || folders?.[0]?.path || "") : (folders?.[0]?.path || "");
  try { await loadPicker(start); } catch { await loadPicker(""); }
  if (start) setPickerFolder(start);
}
function closeShareModal() { $("shareModal").hidden = true; }
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.state) state = msg.state;
    if (msg.event === "transfers" && msg.payload) state.transfers = msg.payload;
    render();
  };
  ws.onclose = () => setTimeout(connectWs, 1500);
}

async function sharePath(path, mode) {
  state = await api("/api/shares", { method: "POST", body: { path, mode: mode || "read" } });
  render();
  const share = state.shares[state.shares.length - 1];
  if (share) await openShare(state.device.id, share, "", state.shares);
  toast("Shared " + (share?.label || "folder") + " with the house");
}

async function checkUpdates(install) {
  closeMenus();
  openSettings("updates");
  const button = $("checkUpdates");
  if (button) button.disabled = true;
  try {
    const res = await api("/api/updates/check", { method: "POST", body: { install: !!install } });
    if (state) {
      state.update = res;
      if (state.settings) state.settings.last_update_check = Date.now() / 1000;
    }
    renderSettings();
    if (res.opened_installer) toast("The installer is opening. Finish the wizard to update.");
    else if (res.available) toast("Version " + res.version + " is available");
    else toast(res.message || "You're up to date");
  } catch (err) {
    toast(err.message);
  } finally {
    if (button) button.disabled = false;
  }
}

window.hearthOpenSettings = () => openSettings("general");
window.hearthOpenAbout = () => openSettings("about");
window.hearthCheckUpdates = () => checkUpdates(false);
window.hearthOpenHelp = () => openSettings("about");

$("menuBtn").onclick = (e) => { e.stopPropagation(); toggleMenu(); };
$("settingsBtn").onclick = () => openSettings("general");
$("settingsClose").onclick = closeSettings;
$("settings").addEventListener("click", (e) => { if (e.target === $("settings")) closeSettings(); });
$("settingsNav").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-settings]");
  if (btn) showSettingsSection(btn.dataset.settings);
});
$("appMenu").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-menu]");
  if (!btn) return;
  const act = btn.dataset.menu;
  if (act === "settings") openSettings("general");
  else if (act === "updates") checkUpdates(false);
  else if (act === "about" || act === "help") openSettings("about");
  else if (act === "quit") quitApp();
});
$("saveDeviceName").onclick = async () => {
  try {
    state = await api("/api/settings", { method: "POST", body: { device_name: $("settingsDeviceName").value } });
    render(); toast("PC name saved");
  } catch (err) { toast(err.message); }
};
$("startAtLogin").onchange = async () => {
  try {
    state = await api("/api/settings", { method: "POST", body: { start_at_login: $("startAtLogin").checked } });
    render(); toast($("startAtLogin").checked ? "Hearth will start when you sign in" : "Removed the Startup shortcut");
  } catch (err) { toast(err.message); $("startAtLogin").checked = ! $("startAtLogin").checked; }
};
$("autoCheckUpdates").onchange = async () => {
  try {
    state = await api("/api/settings", { method: "POST", body: { auto_check_updates: $("autoCheckUpdates").checked } });
    render();
  } catch (err) { toast(err.message); }
};
$("pickDownloadFolder").onclick = () => openShareModal("download").catch((err) => toast(err.message));
$("settingsShareFolder").onclick = () => openShareModal("share").catch((err) => toast(err.message));
$("settingsCopyInvite").onclick = async () => {
  if (!state?.group?.invite) { toast("Join or start a home group first"); return; }
  await navigator.clipboard.writeText(state.group.invite);
  toast("Invite copied");
};
if ($("sidebarToggle")) {
  $("sidebarToggle").onclick = () => $("sidebar").classList.toggle("open");
}
$("navBack").onclick = () => goHistory(-1);
$("navForward").onclick = () => goHistory(1);
$("downloadBtn").onclick = () => startDownload();

$("createBtn").onclick = async () => {
  try {
    state = await api("/api/group/create", { method: "POST", body: { name: $("groupName").value || "Home", password: $("groupPass").value || null, device_name: $("deviceName").value } });
    render(); toast("Home group is ready. Copy the invite for the other PCs.");
  } catch (err) { toast(err.message); }
};
$("joinBtn").onclick = async () => {
  try {
    state = await api("/api/group/invite", { method: "POST", body: { invite: $("invite").value, device_name: $("deviceName").value } });
    render(); toast("Joined. Looking for the other PCs now.");
  } catch (err) { toast(err.message); }
};
$("nearbyBox").addEventListener("submit", async (e) => {
  const form = e.target.closest("[data-nearby]");
  if (!form) return;
  e.preventDefault();
  const offer = (state.nearby || []).find((o) => o.group_id === form.dataset.nearby);
  if (!offer) return;
  const password = form.querySelector("[name=password]").value.trim();
  if (!password) { toast("Enter the group password"); return; }
  if (!offer.salt) { toast("This group is too old to one-click join. Paste an invite instead."); return; }
  const host = offer.address && offer.port ? `${offer.address}:${offer.port}` : (offer.ips || [])[0];
  try {
    state = await api("/api/group/join", { method: "POST", body: {
      group_id: offer.group_id, name: offer.group_name || "Home", password, salt: offer.salt, device_name: $("deviceName").value, host,
    } });
    render(); toast("Joined " + (offer.group_name || "the group"));
  } catch (err) { toast(err.message || "Could not join. Check the password."); }
});
$("chatForm").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("chatText").value.trim();
  if (!text) return;
  $("chatText").value = "";
  try { await api("/api/chat", { method: "POST", body: { text } }); } catch (err) { toast(err.message); }
};
$("copyInvite").onclick = async () => {
  if (!state?.group?.invite) return;
  await navigator.clipboard.writeText(state.group.invite);
  toast("Invite copied");
};
$("shareFolder").onclick = () => openShareModal("share").catch((err) => toast(err.message));
$("shareModalClose").onclick = closeShareModal;
$("shareModalCancel").onclick = closeShareModal;
$("shareModal").addEventListener("click", (e) => { if (e.target === $("shareModal")) closeShareModal(); });
$("browseUp").onclick = () => {
  const parent = $("browseUp").dataset.parent;
  if (parent) loadPicker(parent).catch((err) => toast(err.message));
};
$("quickShares").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-path]");
  if (!btn) return;
  setPickerFolder(btn.dataset.path);
  loadPicker(btn.dataset.path).catch((err) => toast(err.message));
});
$("folderList").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-path]");
  if (!btn) return;
  if (pickerSelected === btn.dataset.path) {
    loadPicker(btn.dataset.path).catch((err) => toast(err.message));
    return;
  }
  setPickerFolder(btn.dataset.path);
});
$("folderList").addEventListener("dblclick", (e) => {
  const btn = e.target.closest("[data-path]");
  if (btn) loadPicker(btn.dataset.path).catch((err) => toast(err.message));
});
$("shareModalOk").onclick = async () => {
  if (!pickerSelected) { toast("Pick a folder first"); return; }
  try {
    if (pickerMode === "download") {
      state = await api("/api/settings", { method: "POST", body: { download_folder: pickerSelected } });
      render();
      closeShareModal();
      toast("Downloads will go to " + pickerSelected);
      return;
    }
    await sharePath(pickerSelected, $("shareWrite").checked ? "readwrite" : "read");
    closeShareModal();
  } catch (err) { toast(err.message); }
};
$("uploadBtn").onclick = () => $("uploadInput").click();
$("uploadInput").onchange = async (e) => {
  await uploadFiles(e.target.files);
  e.target.value = "";
};
$("viewGrid").onclick = () => { setFileView("grid"); paintFiles(); };
$("viewList").onclick = () => { setFileView("list"); paintFiles(); };

(() => {
  const wrap = $("filesWrap");
  const overlay = $("dropOverlay");
  const card = $("dropCard");
  let depth = 0;
  wrap.addEventListener("dragenter", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    depth += 1;
    card.textContent = canUpload() ? "Drop files to add them to this folder" : "This folder is read-only";
    overlay.hidden = false;
  });
  wrap.addEventListener("dragover", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = canUpload() ? "copy" : "none";
  });
  wrap.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) overlay.hidden = true;
  });
  wrap.addEventListener("drop", async (e) => {
    e.preventDefault();
    depth = 0;
    overlay.hidden = true;
    if (!canUpload()) { toast("This folder is read-only"); return; }
    try {
      const files = await collectDroppedFiles(e.dataTransfer);
      if (!files.length) { toast("Nothing to upload. Empty folders are skipped."); return; }
      await uploadFiles(files);
    } catch (err) { toast(err.message || "Could not read dropped files"); }
  });

  // Rubber-band selection in the file pane
  let rubber = null;
  wrap.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (e.target.closest(".file-tile, .file-row, button, a")) return;
    const rect = wrap.getBoundingClientRect();
    rubber = { x: e.clientX, y: e.clientY, sx: e.clientX - rect.left + wrap.scrollLeft, sy: e.clientY - rect.top + wrap.scrollTop };
    const box = $("marquee");
    box.hidden = false;
    box.style.left = rubber.sx + "px";
    box.style.top = rubber.sy + "px";
    box.style.width = "0px";
    box.style.height = "0px";
    if (!e.ctrlKey && !e.shiftKey) { selected.clear(); paintFiles(); updateDownloadButton(); }
  });
  window.addEventListener("mousemove", (e) => {
    if (!rubber) return;
    const rect = wrap.getBoundingClientRect();
    const x = e.clientX - rect.left + wrap.scrollLeft;
    const y = e.clientY - rect.top + wrap.scrollTop;
    const left = Math.min(x, rubber.sx);
    const top = Math.min(y, rubber.sy);
    const w = Math.abs(x - rubber.sx);
    const h = Math.abs(y - rubber.sy);
    const box = $("marquee");
    box.style.left = left + "px";
    box.style.top = top + "px";
    box.style.width = w + "px";
    box.style.height = h + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!rubber) return;
    const box = $("marquee");
    const r = box.getBoundingClientRect();
    if (r.width > 4 && r.height > 4) {
      document.querySelectorAll(".file-tile, .file-row").forEach((el) => {
        const cr = el.getBoundingClientRect();
        const hit = cr.right >= r.left && cr.left <= r.right && cr.bottom >= r.top && cr.top <= r.bottom;
        if (hit) selected.add(el.dataset.name);
      });
      paintFiles();
      updateDownloadButton();
    }
    box.hidden = true;
    rubber = null;
  });
})();

(() => {
  const shares = $("myShares");
  const highlight = (on) => shares.classList.toggle("drop-over", on);
  shares.addEventListener("dragenter", (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); highlight(true); } });
  shares.addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
  shares.addEventListener("dragleave", (e) => { if (!shares.contains(e.relatedTarget)) highlight(false); });
  shares.addEventListener("drop", async (e) => {
    e.preventDefault();
    highlight(false);
    const folder = droppedFolderPath(e.dataTransfer);
    if (folder) {
      try { await sharePath(folder, "read"); } catch (err) { toast(err.message); }
      return;
    }
    toast("To share a folder, use Share a folder — the browser does not always give Hearth the full path.");
  });
})();

(() => {
  const list = $("members");
  list.addEventListener("dragover", (e) => {
    if (!e.dataTransfer.types.includes("Files") && !e.dataTransfer.types.includes("application/x-hearth-files")) return;
    const member = e.target.closest("[data-open]");
    if (!member) return;
    e.preventDefault();
    member.classList.add("drop-over");
  });
  list.addEventListener("dragleave", (e) => {
    const member = e.target.closest("[data-open]");
    if (member) member.classList.remove("drop-over");
  });
  list.addEventListener("drop", async (e) => {
    const member = e.target.closest("[data-open]");
    if (!member) return;
    member.classList.remove("drop-over");
    e.preventDefault();
    const peer = state.members.find((m) => m.device_id === member.dataset.open);
    try {
      const data = await api(`/api/remote-shares/${member.dataset.open}`);
      const writable = (data.shares || []).find((s) => s.mode === "readwrite");
      if (!writable) { toast((peer?.name || "That PC") + " has no folder that allows uploads"); return; }
      const prev = fileCursor;
      fileCursor = { deviceId: member.dataset.open, share: writable, path: "", shares: data.shares };
      const files = await collectDroppedFiles(e.dataTransfer);
      if (!files.length) { fileCursor = prev; toast("Nothing to send"); return; }
      await uploadFiles(files);
      toast("Sent to " + (peer?.name || "that PC"));
    } catch (err) { toast(err.message); }
  });
})();

$("files").addEventListener("click", (e) => {
  const row = e.target.closest(".file-row, .file-tile");
  if (!row || !fileCursor) return;
  const item = fileItems.find((i) => i.name === row.dataset.name);
  if (!item) return;
  selectClick(item, e);
});
$("files").addEventListener("dblclick", (e) => {
  const row = e.target.closest(".file-row, .file-tile");
  if (!row || !fileCursor) return;
  const item = fileItems.find((i) => i.name === row.dataset.name);
  if (item) openItem(item);
});
$("files").addEventListener("contextmenu", (e) => {
  const row = e.target.closest(".file-row, .file-tile");
  if (!row || !fileCursor) return;
  const item = fileItems.find((i) => i.name === row.dataset.name);
  if (!item) return;
  if (!selected.has(item.name)) { selected.clear(); selected.add(item.name); paintFiles(); updateDownloadButton(); }
  showContextMenu(e, selectedItems());
});
$("files").addEventListener("dragstart", async (e) => {
  const row = e.target.closest(".file-row, .file-tile");
  if (!row || !fileCursor) return;
  const item = fileItems.find((i) => i.name === row.dataset.name);
  if (!item || item.dir) { e.preventDefault(); return; }
  if (!selected.has(item.name)) { selected.clear(); selected.add(item.name); paintFiles(); }
  const files = selectedItems().filter((i) => !i.dir);
  e.dataTransfer.effectAllowed = "copy";
  e.dataTransfer.setData("application/x-hearth-files", JSON.stringify(files.map((f) => itemRel(f.name))));
  e.dataTransfer.setData("text/plain", files.map((f) => f.name).join("\n"));
  const firstRel = itemRel(files[0].name);
  const href = `${location.origin}/api/download/${fileCursor.deviceId}/${fileCursor.share.id}?path=${encodeURIComponent(firstRel)}`;
  try { e.dataTransfer.setData("DownloadURL", `application/octet-stream:${files[0].name}:${href}`); } catch {}
  try { e.dataTransfer.setData("text/uri-list", href); } catch {}
  if (fileCursor.deviceId === state.device.id && window.pywebview?.api?.drag_files) {
    const paths = [];
    for (const f of files) {
      try {
        const info = await api(`/api/local-path/${fileCursor.share.id}?path=${encodeURIComponent(itemRel(f.name))}`);
        if (info.path) paths.push(info.path);
      } catch {}
    }
    if (paths.length) {
      e.preventDefault();
      try { await window.pywebview.api.drag_files(paths); } catch (err) { toast(err.message || "Could not start a drag"); }
    }
  } else if (fileCursor.deviceId !== state.device.id && window.pywebview?.api?.drag_files) {
    e.preventDefault();
    toast("Downloading first — drag it out once it finishes.");
    startDownload(files);
  }
});
$("files").addEventListener("scroll", () => {
  if (fileItems.length > 250 && fileView === "list") paintFiles();
});

$("ctxMenu").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ctx]");
  if (!btn || !ctxTarget) return;
  const act = btn.dataset.ctx;
  const items = ctxTarget.items;
  const extra = ctxTarget.extra || {};
  hideContextMenu();
  if (act === "open") {
    if (items[0]) await openItem(items[0]);
  } else if (act === "download") {
    await startDownload(items);
  } else if (act === "copy") {
    copyPaths(items);
  } else if (act === "unshare" && extra.unshareId) {
    state = await api(`/api/shares/${extra.unshareId}`, { method: "DELETE" });
    render();
  }
});

$("addHost").onclick = async () => {
  const host = $("manualHost").value.trim();
  if (!host) return;
  try { state = await api("/api/hosts", { method: "POST", body: { host } }); $("manualHost").value = ""; render(); } catch (err) { toast(err.message); }
};
$("bridge").onchange = async () => { state = await api("/api/settings", { method: "POST", body: { bridge: $("bridge").checked } }); render(); };
if ($("checkUpdates")) $("checkUpdates").onclick = () => checkUpdates(false);
if ($("installUpdate")) $("installUpdate").onclick = () => checkUpdates(true);
$("leave").onclick = async () => {
  if (!state?.group) { toast("You are not in a home group"); return; }
  if (!confirm("Leave this hearth on this PC?")) return;
  state = await api("/api/group/leave", { method: "POST", body: {} });
  closeSettings();
  render();
};

document.body.addEventListener("click", async (e) => {
  if (!e.target.closest("#appMenu, #menuBtn")) {
    $("appMenu").hidden = true;
    $("menuBtn").setAttribute("aria-expanded", "false");
  }
  if (!e.target.closest("#ctxMenu")) hideContextMenu();
  const tab = e.target.closest(".tab");
  if (tab) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + tab.dataset.view));
  }
  const themeBasic = e.target.closest("[data-theme-basic]");
  if (themeBasic) {
    applyTheme(themeBasic.dataset.themeBasic);
    state = await api("/api/theme", { method: "POST", body: { theme: themeBasic.dataset.themeBasic } });
    render();
  }
  const themeBtn = e.target.closest("#themes [data-theme]");
  if (themeBtn) { applyTheme(themeBtn.dataset.theme); state = await api("/api/theme", { method: "POST", body: { theme: themeBtn.dataset.theme } }); render(); }
  const mode = e.target.closest("[data-share-mode]");
  if (mode && e.target.tagName === "SELECT") {
    try {
      state = await api(`/api/shares/${mode.dataset.shareMode}`, { method: "PATCH", body: { mode: mode.value } });
      render();
    } catch (err) { toast(err.message); }
  }
  const unshare = e.target.closest("[data-unshare]");
  if (unshare) {
    e.stopPropagation();
    state = await api(`/api/shares/${unshare.dataset.unshare}`, { method: "DELETE" });
    render();
    return;
  }
  const mine = e.target.closest("[data-myshare]");
  if (mine && state) {
    const share = state.shares.find((s) => s.id === mine.dataset.myshare);
    if (share) openShare(state.device.id, share, "", state.shares);
  }
  const unhost = e.target.closest("[data-unhost]");
  if (unhost) { state = await api(`/api/hosts/${encodeURIComponent(unhost.dataset.unhost)}`, { method: "DELETE" }); render(); }
  const member = e.target.closest("[data-open]");
  if (member && state) {
    $("sidebar")?.classList.remove("open");
    openDeviceShares(member.dataset.open);
  }
  const shareTab = e.target.closest("#shareTabs [data-share]");
  if (shareTab && fileCursor) {
    const share = fileCursor.shares.find((s) => s.id === shareTab.dataset.share);
    if (share) openShare(fileCursor.deviceId, share, "", fileCursor.shares);
  }
  const crumb = e.target.closest("#crumb [data-path]");
  if (crumb && fileCursor) openShare(fileCursor.deviceId, fileCursor.share, crumb.dataset.path, fileCursor.shares);
  const cancel = e.target.closest("[data-cancel-xfer]");
  if (cancel) {
    try {
      const res = await api(`/api/transfers/${cancel.dataset.cancelXfer}/cancel`, { method: "POST", body: {} });
      if (res.transfers) state.transfers = res.transfers;
      renderTransfers();
    } catch (err) { toast(err.message); }
  }
  const reveal = e.target.closest("[data-reveal]");
  if (reveal) {
    try { await window.pywebview?.api?.reveal?.(reveal.dataset.reveal); } catch { toast("Saved to " + reveal.dataset.reveal); }
  }
});

$("myShares").addEventListener("contextmenu", (e) => {
  const card = e.target.closest("[data-myshare]");
  if (!card) return;
  const share = (state.shares || []).find((s) => s.id === card.dataset.myshare);
  if (!share) return;
  showContextMenu(e, [{ name: share.label, dir: true }], { unshareId: share.id });
});

document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (e.key === "Escape") {
    closeMenus();
    closeSettings();
    closeShareModal();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === ",") { e.preventDefault(); openSettings("general"); return; }
  if (typing) return;
  if (e.key === "Backspace" || (e.altKey && e.key === "ArrowLeft")) { e.preventDefault(); goHistory(-1); }
  else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); goHistory(1); }
  else if (e.key === "Enter" && selected.size) {
    const items = selectedItems();
    if (items[0]) openItem(items[0]);
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && fileCursor) {
    e.preventDefault();
    fileItems.forEach((i) => selected.add(i.name));
    paintFiles();
    updateDownloadButton();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && selected.size) {
    e.preventDefault();
    copyPaths(selectedItems());
  }
});

document.body.addEventListener("change", async (e) => {
  const mode = e.target.closest("[data-share-mode]");
  if (mode && e.target.tagName === "SELECT") {
    try {
      state = await api(`/api/shares/${mode.dataset.shareMode}`, { method: "PATCH", body: { mode: mode.value } });
      render();
    } catch (err) { toast(err.message); }
  }
});
$("filter").addEventListener("input", () => { render(); if (fileCursor) paintFiles(); });
if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if ((state?.theme || "system") === "system") applyTheme("system");
  });
}
(async function boot() {
  applyTheme(localStorage.getItem("hearth-theme") || "system");
  try {
    state = await api("/api/state");
    applyTheme(state.theme || "system");
    render();
    if (state.just_updated) {
      toast("Updated to v" + state.just_updated);
      api("/api/updates/ack", { method: "POST", body: {} }).catch(() => {});
    }
  } catch (err) { toast("Cannot reach Hearth: " + err.message); }
  connectWs();
})();
