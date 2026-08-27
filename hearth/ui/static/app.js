const THEMES = [
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
const $ = (id) => document.getElementById(id);
function applyTheme(id) { document.documentElement.dataset.theme = id; }
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.style.display = "block";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.style.display = "none"; }, 2400);
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
    <button class="theme-card ${state?.theme === t.id ? "active" : ""}" data-theme="${t.id}">
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
function render() {
  if (!state) return;
  applyTheme(state.theme || "grove-dark");
  const inGroup = Boolean(state.group);
  $("gate").hidden = inGroup;
  $("app").hidden = !inGroup;
  if (!inGroup) {
    if (!$("deviceName").value) $("deviceName").value = state.device.name;
    return;
  }
  $("hearthTitle").textContent = state.group.name;
  $("hearthSub").textContent = `${state.members.filter((m) => m.online).length} on the network`;
  $("inviteBox").textContent = state.group.invite;
  $("passwordBox").textContent = state.group.password;
  $("bridge").checked = state.bridge;
  $("onlineCount").textContent = state.members.filter((m) => m.online).length;
  const q = ($("filter").value || "").toLowerCase();
  $("members").innerHTML = state.members.filter((m) => m.name.toLowerCase().includes(q)).map((m) => `
    <button class="member" data-open="${m.device_id}">
      <div class="avatar">${initials(m.name)}</div>
      <div class="meta"><b>${m.name}${m.self ? " · you" : ""}</b><span>${(m.ips || []).slice(0, 2).join(" · ") || "local"}${m.bridge ? " · bridge" : ""}</span></div>
      <span class="dot ${m.online ? "on" : ""}"></span>
    </button>`).join("");
  $("myShares").innerHTML = (state.shares || []).length ? state.shares.map((s) => `
    <div class="share-card"><div class="meta"><b>${s.label}</b><span>${s.mode === "readwrite" ? "Read & write" : "Read"} · ${s.path}</span></div>
    <button class="btn ghost" data-unshare="${s.id}">×</button></div>`).join("") : `<div class="empty">Share Documents, Photos, or a drop folder.</div>`;
  $("hosts").innerHTML = (state.manual_hosts || []).map((h) => `
    <div class="share-card"><span class="meta"><b>${h}</b></span><button class="btn ghost" data-unhost="${h}">×</button></div>`).join("") || `<div class="empty">No extra hosts yet.</div>`;
  const log = $("chatLog");
  const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
  log.innerHTML = (state.chat || []).map((m) => `
    <div class="bubble ${m.from_id === state.device.id ? "me" : ""}">
      <div class="who">${m.from_name}${m.to ? " → private" : ""}</div>
      <div>${escapeHtml(m.text)}</div>
      <div class="when">${timeFmt(m.ts)}</div>
    </div>`).join("") || `<div class="empty">No one has spoken yet. Say hello to the other rooms.</div>`;
  if (stick || !log.dataset.ready) { log.scrollTop = log.scrollHeight; log.dataset.ready = "1"; }
  renderThemes();
}
async function openShare(deviceId, share, path = "") {
  fileCursor = { deviceId, share, path };
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === "files"));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-files"));
  $("crumb").innerHTML = `<button data-path="">${share.label}</button>` + path.split("/").filter(Boolean).map((_, i, arr) => {
    const p = arr.slice(0, i + 1).join("/");
    return `<span>/</span><button data-path="${p}">${arr[i]}</button>`;
  }).join("");
  try {
    const data = await api(`/api/files/${deviceId}/${share.id}?path=${encodeURIComponent(path)}`);
    $("files").innerHTML = data.items.map((item) => `
      <div class="file-row" data-dir="${item.dir}" data-name="${item.name}">
        <div class="avatar">${item.dir ? "▦" : "▨"}</div>
        <div class="meta"><b>${item.name}</b></div>
        <span class="size">${item.dir ? "" : formatSize(item.size)}</span>
        ${item.dir ? "" : `<a class="btn ghost" href="/api/download/${deviceId}/${share.id}?path=${encodeURIComponent(path ? path + "/" + item.name : item.name)}">Get</a>`}
      </div>`).join("") || `<div class="empty">Empty folder</div>`;
  } catch (err) {
    $("files").innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => { state = JSON.parse(ev.data).state; render(); };
  ws.onclose = () => setTimeout(connectWs, 1500);
}
document.querySelectorAll("[data-mode]").forEach((btn) => {
  btn.onclick = () => {
    const join = btn.dataset.mode === "join";
    $("joinPane").hidden = !join;
    $("createPane").hidden = join;
  };
});
$("createBtn").onclick = async () => {
  try {
    state = await api("/api/group/create", { method: "POST", body: { name: $("groupName").value, password: $("groupPass").value || null, device_name: $("deviceName").value } });
    render(); toast("Hearth is lit. Copy the invite for the other PCs.");
  } catch (err) { toast(err.message); }
};
$("joinBtn").onclick = async () => {
  try {
    state = await api("/api/group/invite", { method: "POST", body: { invite: $("invite").value, device_name: $("deviceName").value } });
    render(); toast("Joined. Discovery is probing the house.");
  } catch (err) { toast(err.message); }
};
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
$("shareFolder").onclick = async () => {
  const path = prompt("Folder path on this PC", "Documents");
  if (!path) return;
  const mode = confirm("Allow others to add files? OK = read & write, Cancel = read only") ? "readwrite" : "read";
  try { state = await api("/api/shares", { method: "POST", body: { path, mode } }); render(); } catch (err) { toast(err.message); }
};
$("addHost").onclick = async () => {
  const host = $("manualHost").value.trim();
  if (!host) return;
  try { state = await api("/api/hosts", { method: "POST", body: { host } }); $("manualHost").value = ""; render(); } catch (err) { toast(err.message); }
};
$("bridge").onchange = async () => { state = await api("/api/settings", { method: "POST", body: { bridge: $("bridge").checked } }); render(); };
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
  if (unshare) { state = await api(`/api/shares/${unshare.dataset.unshare}`, { method: "DELETE" }); render(); }
  const unhost = e.target.closest("[data-unhost]");
  if (unhost) { state = await api(`/api/hosts/${encodeURIComponent(unhost.dataset.unhost)}`, { method: "DELETE" }); render(); }
  const member = e.target.closest("[data-open]");
  if (member && state) {
    const peer = state.members.find((m) => m.device_id === member.dataset.open);
    if (!peer) return;
    try {
      const data = await api(`/api/remote-shares/${peer.device_id}`);
      if (!data.shares?.length) { toast(peer.self ? "Share a folder first" : peer.name + " has not shared a folder yet"); return; }
      openShare(peer.device_id, data.shares[0]);
    } catch (err) { toast(err.message); }
  }
  const crumb = e.target.closest("#crumb [data-path]");
  if (crumb && fileCursor) openShare(fileCursor.deviceId, fileCursor.share, crumb.dataset.path);
  const row = e.target.closest(".file-row[data-dir='true']");
  if (row && fileCursor) openShare(fileCursor.deviceId, fileCursor.share, [fileCursor.path, row.dataset.name].filter(Boolean).join("/"));
});
$("filter").addEventListener("input", render);
(async function boot() {
  applyTheme(localStorage.getItem("hearth-theme") || "grove-dark");
  try { state = await api("/api/state"); applyTheme(state.theme); render(); }
  catch (err) { toast("Cannot reach Hearth: " + err.message); }
  connectWs();
})();
