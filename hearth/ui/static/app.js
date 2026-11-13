const THEMES = [
  { id: "system", name: "System", note: "Follows Windows", colors: ["#121214", "#e39b5a", "#f4f1ec"] },
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
let pickerPath = "";
let pickerSelected = "";
let fileView = localStorage.getItem("hearth-file-view") || "grid";
let transfers = [];
const $ = (id) => document.getElementById(id);

function systemScheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
function applyTheme(id) {
  const theme = id || "system";
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.scheme = theme === "system" ? systemScheme() : (theme.endsWith("light") || theme === "remedy-bright" ? "light" : "dark");
  localStorage.setItem("hearth-theme", theme);
}
function toast(text) {
  const host = $("toasts");
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  host.appendChild(el);
  setTimeout(() => el.remove(), text && text.startsWith("Updated") ? 5000 : 2800);
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
    throw new Error(detail || res.statusText);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}
function renderThemes() {
  $("themes").innerHTML = THEMES.map((t) => `
    <button class="theme-card ${state?.theme === t.id ? "active" : ""}" data-theme="${t.id}" type="button">
      <span class="swatch" style="background:linear-gradient(90deg,${t.colors.join(",")})"></span>
      <span class="meta"><b>${t.name}</b><span>${t.note}</span></span>
    </button>`).join("");
}
function initials(name) {
  return (name || "PC").split(/\s+/).slice(0, 2).map((p) => p[0]).join("").toUpperCase();
}
function timeFmt(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function formatSize(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 ** 2) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 ** 3) return (n / 1024 ** 2).toFixed(1) + " MB";
  return (n / 1024 ** 3).toFixed(1) + " GB";
}
function canUpload() {
  return Boolean(fileCursor?.share) && fileCursor.share.mode === "readwrite";
}
function setFileView(mode) {
  fileView = mode;
  localStorage.setItem("hearth-file-view", mode);
  $("files").classList.toggle("grid", mode === "grid");
  $("files").classList.toggle("list", mode === "list");
  $("viewGrid").classList.toggle("active", mode === "grid");
  $("viewList").classList.toggle("active", mode === "list");
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
  if (!transfers.length) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = transfers.slice(-6).map((t) => `
    <div class="transfer ${t.status}">
      <b>${escapeHtml(t.name)}</b>
      <span>${t.status === "up" ? t.pct + "%" : t.status === "done" ? "Done" : t.message || "Failed"}</span>
      <div class="bar"><span style="width:${t.pct || (t.status === "done" ? 100 : 0)}%"></span></div>
    </div>`).join("");
}
function render() {
  if (!state) return;
  applyTheme(state.theme || "system");
  const inGroup = Boolean(state.group);
  $("gate").hidden = inGroup;
  $("app").hidden = !inGroup;
  if (!inGroup) {
    if (!$("deviceName").value) $("deviceName").value = state.device.name;
    renderNearby();
    return;
  }
  $("hearthTitle").textContent = state.group.name;
  $("hearthSub").textContent = `${state.members.filter((m) => m.online).length} on the network`;
  $("inviteBox").textContent = state.group.invite;
  $("passwordBox").textContent = state.group.password;
  $("bridge").checked = state.bridge;
  $("onlineCount").textContent = state.members.filter((m) => m.online).length;
  const q = ($("filter").value || "").toLowerCase();
  const selected = fileCursor?.deviceId;
  $("members").innerHTML = state.members.filter((m) => m.name.toLowerCase().includes(q)).map((m) => `
    <button class="member ${m.device_id === selected ? "active" : ""}" data-open="${m.device_id}" type="button">
      <div class="avatar">${initials(m.name)}</div>
      <div class="meta"><b>${escapeHtml(m.name)}${m.self ? " · you" : ""}</b><span>${(m.ips || []).filter(Boolean).slice(0, 2).join(" · ") || "local"}${m.bridge ? " · bridge" : ""}</span></div>
      <span class="dot ${m.online ? "on" : ""}"></span>
    </button>`).join("");
  $("myShares").innerHTML = (state.shares || []).length ? state.shares.map((s) => `
    <div class="share-card" data-myshare="${s.id}">
      <div class="meta"><b>${escapeHtml(s.label)}</b><span>${s.mode === "readwrite" ? "Read & write" : "Read only"}</span></div>
      <button class="btn ghost icon" data-unshare="${s.id}" type="button">×</button>
    </div>`).join("") : `<div class="empty">Share Documents or Pictures so the other PCs can see them.</div>`;
  $("hosts").innerHTML = (state.manual_hosts || []).map((h) => `
    <div class="share-card"><span class="meta"><b>${escapeHtml(h)}</b></span><button class="btn ghost icon" data-unhost="${escapeHtml(h)}" type="button">×</button></div>`).join("") || `<div class="empty">No extra hosts yet.</div>`;
  const log = $("chatLog");
  const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
  log.innerHTML = (state.chat || []).map((m) => `
    <div class="bubble ${m.from_id === state.device.id ? "me" : ""}">
      <div class="who">${escapeHtml(m.from_name)}${m.to ? " → private" : ""}</div>
      <div>${escapeHtml(m.text)}</div>
      <div class="when">${timeFmt(m.ts)}</div>
    </div>`).join("") || `<div class="empty">No one has spoken yet. Say hello to the other rooms.</div>`;
  if (stick || !log.dataset.ready) { log.scrollTop = log.scrollHeight; log.dataset.ready = "1"; }
  $("uploadBtn").hidden = !canUpload();
  renderThemes();
  renderTransfers();
  if (!fileCursor && (state.shares || []).length) {
    openShare(state.device.id, state.shares[0], "", state.shares);
  }
}
function fileItem(deviceId, share, path, item) {
  const rel = path ? path + "/" + item.name : item.name;
  const get = item.dir ? "" : `<a class="btn ghost get" href="/api/download/${deviceId}/${share.id}?path=${encodeURIComponent(rel)}">Get</a>`;
  const size = item.dir ? "Folder" : formatSize(item.size);
  if (fileView === "grid") {
    return `<button class="file-tile" type="button" data-dir="${item.dir}" data-name="${escapeHtml(item.name)}">
      <div class="avatar">${item.dir ? "▦" : "▨"}</div>
      <div class="meta"><b>${escapeHtml(item.name)}</b></div>
      <span class="size">${size}</span>
      ${get}
    </button>`;
  }
  return `<div class="file-row" data-dir="${item.dir}" data-name="${escapeHtml(item.name)}">
    <div class="avatar">${item.dir ? "▦" : "▨"}</div>
    <div class="meta"><b>${escapeHtml(item.name)}</b></div>
    <span class="size">${item.dir ? "" : formatSize(item.size)}</span>
    ${get}
  </div>`;
}
async function openShare(deviceId, share, path = "", shares = null) {
  fileCursor = { deviceId, share, path, shares: shares || fileCursor?.shares || [share] };
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === "files"));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-files"));
  $("shareTabs").innerHTML = (fileCursor.shares || []).map((s) => `
    <button class="share-tab ${s.id === share.id ? "active" : ""}" data-share="${s.id}" type="button">${escapeHtml(s.label)}</button>
  `).join("");
  $("crumb").innerHTML = `<button data-path="" type="button">${escapeHtml(share.label)}</button>` + path.split("/").filter(Boolean).map((_, i, arr) => {
    const p = arr.slice(0, i + 1).join("/");
    return `<span>/</span><button data-path="${escapeHtml(p)}" type="button">${escapeHtml(arr[i])}</button>`;
  }).join("");
  $("uploadBtn").hidden = share.mode !== "readwrite";
  setFileView(fileView);
  try {
    const data = await api(`/api/files/${deviceId}/${share.id}?path=${encodeURIComponent(path)}`);
    if (data.share) {
      fileCursor.share = { ...share, ...data.share };
      $("uploadBtn").hidden = fileCursor.share.mode !== "readwrite";
    }
    const items = data.items || [];
    const drop = canUpload() ? `<div class="drop-hint">Drag files here to add them</div>` : "";
    $("files").innerHTML = items.length
      ? items.map((item) => fileItem(deviceId, fileCursor.share, path, item)).join("")
      : `<div class="empty">${canUpload() ? "Empty folder. Drag files here to add them." : "Empty folder"}</div>` + (items.length ? "" : "");
    if (items.length && canUpload()) $("files").insertAdjacentHTML("beforeend", drop);
  } catch (err) {
    $("files").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
  render();
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

function readDroppedEntry(entry, prefix) {
  return new Promise((resolve) => {
    if (!entry) return resolve([]);
    if (entry.isFile) {
      entry.file((file) => {
        try { file.hearthDir = prefix || ""; } catch {}
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
function joinRel(base, extra) {
  return [base || "", extra || ""].filter(Boolean).join("/").replace(/\\/g, "/");
}
function uploadFiles(fileList) {
  if (!canUpload() || !fileList?.length) return Promise.resolve();
  const { deviceId, share, path } = fileCursor;
  if (fileList.length > 20) toast("Uploading " + fileList.length + " files");
  const jobs = [...fileList].map((file) => new Promise((resolve) => {
    const row = { name: file.name, pct: 0, status: "up" };
    transfers.push(row);
    renderTransfers();
    const body = new FormData();
    body.append("file", file);
    const dest = joinRel(path, file.hearthDir || "");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload/${deviceId}/${share.id}?path=${encodeURIComponent(dest)}`);
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) { row.pct = Math.round((ev.loaded / ev.total) * 100); renderTransfers(); }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { row.pct = 100; row.status = "done"; }
      else {
        let detail = xhr.responseText;
        try { detail = JSON.parse(detail).detail || detail; } catch {}
        row.status = "err";
        row.message = detail || xhr.statusText;
      }
      renderTransfers();
      resolve();
    };
    xhr.onerror = () => { row.status = "err"; row.message = "Network error"; renderTransfers(); resolve(); };
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
async function openShareModal() {
  $("shareModal").hidden = false;
  $("shareWrite").checked = false;
  const folders = state?.known_folders?.length ? state.known_folders : (await api("/api/known-folders")).folders;
  $("quickShares").innerHTML = (folders || []).map((f) => `
    <button class="quick-share" type="button" data-path="${escapeHtml(f.path)}">
      <b>${escapeHtml(f.name)}</b>
      <span>${escapeHtml(f.path)}</span>
    </button>
  `).join("") || `<div class="hint">No usual libraries found. Browse below.</div>`;
  const start = folders?.[0]?.path || "";
  try { await loadPicker(start); } catch { await loadPicker(""); }
  if (start) setPickerFolder(start);
}
function closeShareModal() { $("shareModal").hidden = true; }
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => { state = JSON.parse(ev.data).state; render(); };
  ws.onclose = () => setTimeout(connectWs, 1500);
}

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
$("shareFolder").onclick = () => openShareModal().catch((err) => toast(err.message));
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
    state = await api("/api/shares", { method: "POST", body: { path: pickerSelected, mode: $("shareWrite").checked ? "readwrite" : "read" } });
    render();
    closeShareModal();
    const share = state.shares[state.shares.length - 1];
    if (share) await openShare(state.device.id, share, "", state.shares);
    toast("Shared " + (share?.label || "folder") + " with the house");
  } catch (err) { toast(err.message); }
};
$("uploadBtn").onclick = () => $("uploadInput").click();
$("uploadInput").onchange = async (e) => {
  await uploadFiles(e.target.files);
  e.target.value = "";
};
$("viewGrid").onclick = () => { setFileView("grid"); if (fileCursor) openShare(fileCursor.deviceId, fileCursor.share, fileCursor.path, fileCursor.shares); };
$("viewList").onclick = () => { setFileView("list"); if (fileCursor) openShare(fileCursor.deviceId, fileCursor.share, fileCursor.path, fileCursor.shares); };
(() => {
  const wrap = $("filesWrap");
  const overlay = $("dropOverlay");
  let depth = 0;
  wrap.addEventListener("dragenter", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    depth += 1;
    overlay.hidden = !canUpload();
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
})();
$("addHost").onclick = async () => {
  const host = $("manualHost").value.trim();
  if (!host) return;
  try { state = await api("/api/hosts", { method: "POST", body: { host } }); $("manualHost").value = ""; render(); } catch (err) { toast(err.message); }
};
$("bridge").onchange = async () => { state = await api("/api/settings", { method: "POST", body: { bridge: $("bridge").checked } }); render(); };
async function checkUpdates(install) {
  const button = $("checkUpdates");
  if (button) button.disabled = true;
  try {
    const res = await api("/api/updates/check", { method: "POST", body: { install: !!install } });
    if ($("updateHint")) $("updateHint").textContent = res.message || "You're up to date.";
    if ($("installUpdate")) $("installUpdate").hidden = !(res.available && !res.installed && state?.frozen);
    if (res.installed) toast("Installing update… Hearth will reopen.");
    else if (res.available) toast("Version " + res.version + " is available");
    else toast(res.message || "You're up to date");
    if (res.html_url && res.available && !state?.frozen) {
      if ($("updateHint")) $("updateHint").textContent = res.message + " Open the release page from the message above if you want to install it yourself.";
    }
  } catch (err) {
    toast(err.message);
  } finally {
    if (button) button.disabled = false;
  }
}
if ($("checkUpdates")) $("checkUpdates").onclick = () => checkUpdates(false);
if ($("installUpdate")) $("installUpdate").onclick = () => checkUpdates(true);
$("leave").onclick = async () => {
  if (!confirm("Leave this hearth on this PC?")) return;
  state = await api("/api/group/leave", { method: "POST", body: {} }); render();
};
document.body.addEventListener("click", async (e) => {
  const tab = e.target.closest(".tab");
  if (tab) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + tab.dataset.view));
  }
  const themeBtn = e.target.closest("[data-theme]");
  if (themeBtn) { applyTheme(themeBtn.dataset.theme); state = await api("/api/theme", { method: "POST", body: { theme: themeBtn.dataset.theme } }); render(); }
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
  if (member && state) openDeviceShares(member.dataset.open);
  const shareTab = e.target.closest("#shareTabs [data-share]");
  if (shareTab && fileCursor) {
    const share = fileCursor.shares.find((s) => s.id === shareTab.dataset.share);
    if (share) openShare(fileCursor.deviceId, share, "", fileCursor.shares);
  }
  const crumb = e.target.closest("#crumb [data-path]");
  if (crumb && fileCursor) openShare(fileCursor.deviceId, fileCursor.share, crumb.dataset.path, fileCursor.shares);
  const row = e.target.closest(".file-row, .file-tile");
  if (row && fileCursor && !e.target.closest("a") && row.dataset.dir === "true") {
    openShare(fileCursor.deviceId, fileCursor.share, [fileCursor.path, row.dataset.name].filter(Boolean).join("/"), fileCursor.shares);
  }
});
$("filter").addEventListener("input", render);
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
