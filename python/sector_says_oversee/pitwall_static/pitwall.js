/* ============================================================
   pitwall.js — Pit Wall Control Panel
   Sector Says What — F1-inspired Admin Dashboard
   ============================================================ */

let CONFIG = {};
let _editingCommentator = null;

// ─── Init ────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  initTabs();
  initRangeSliders();
  loadLogo();
  loadPageBackgrounds();
  await loadConfig();
  loadHistory();
  loadOwnerHistory();
});

async function loadLogo() {
  try {
    const res = await api("/api/media/logo");
    if (res.path) {
      const img = document.getElementById("sidebar-logo");
      if (img) {
        img.src = "/api/media/file?path=" + encodeURIComponent(res.path);
        img.style.display = "";
        document.getElementById("sidebar-brand-fallback").style.display = "none";
      }
    }
  } catch (e) { /* fallback text stays visible */ }
}

// ─── Page Backgrounds ──────────────────────────────────────────

let _pageBackgrounds = {};

async function loadPageBackgrounds() {
  try {
    _pageBackgrounds = await api("/api/media/page-backgrounds");
    applyPageBackgrounds();
  } catch (e) { /* silent */ }
}

async function loadAudioDevices(selectedIdx) {
  const sel = document.getElementById("rr-audio-device");
  if (!sel) return;
  try {
    const data = await api("/api/audio/devices");
    const list = (data && data.AudioDevices) || [];
    // Prefer the index from the live device endpoint (reads system_settings.json)
    // so Pit Wall and HUD always agree on the current device.
    const cur = (data && data.audio_output_index != null)
      ? data.audio_output_index
      : (selectedIdx != null ? selectedIdx : 0);
    if (!list.length) {
      sel.innerHTML = `<option value="0">No audio devices found</option>`;
      return;
    }
    sel.innerHTML = list.map(d =>
      `<option value="${d.id}"${d.id === cur ? " selected" : ""}>${d.id}: ${d.name}</option>`
    ).join("");
  } catch (e) {
    sel.innerHTML = `<option value="0">Error loading devices</option>`;
  }
}

function applyPageBackgrounds() {
  document.querySelectorAll(".tab-section").forEach(section => {
    const tabId = section.id;
    const path = _pageBackgrounds[tabId];
    if (path) {
      const url = `/api/media/file?path=${encodeURIComponent(path)}`;
      // Fully decode before applying so the paint is atomic — no jump.
      const img = new Image();
      img.src = url;
      const apply = () => {
        section.style.setProperty("--page-bg", `url("${url}")`);
        section.classList.add("has-bg");
      };
      (img.decode ? img.decode().then(apply, apply) : new Promise(r => { img.onload = r; img.onerror = r; }).then(apply));
    } else {
      section.style.removeProperty("--page-bg");
      section.classList.remove("has-bg");
    }
  });
}

window.openSetBackground = async function(tabId) {
  // Fetch all image files from gallery
  let files;
  try {
    const res = await api("/api/media/files");
    files = (res.files || []).filter(f => f.type === "image");
  } catch (e) { files = []; }

  const current = _pageBackgrounds[tabId] || "";
  const thumbs = files.map(f => {
    const sel = f.path === current ? "selected" : "";
    return `<div class="bg-pick-thumb ${sel}" data-path="${f.path}" onclick="pickBgThumb(this, '${tabId}')">
      <img src="/api/media/file?path=${encodeURIComponent(f.path)}" loading="lazy">
      <div class="bg-pick-name">${f.name}</div>
    </div>`;
  }).join("");

  const html = `
    <h3>Set Background — ${tabId}</h3>
    <p style="color:var(--text-dim);font-size:12px;margin-bottom:12px">Pick an image from your Media Gallery. Upload new images in the Media tab first.</p>
    <div class="bg-pick-grid">${thumbs || '<div style="color:var(--text-dim)">No images found. Upload some in the Media Gallery.</div>'}</div>
    <div class="modal-actions">
      <button class="btn btn-danger btn-sm" onclick="clearPageBg('${tabId}')">Clear</button>
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="bg-pick-save" onclick="savePageBg('${tabId}')">Set Background</button>
    </div>
  `;
  showModal(html);
};

let _bgPickSelected = "";

window.pickBgThumb = function(el, tabId) {
  document.querySelectorAll(".bg-pick-thumb").forEach(t => t.classList.remove("selected"));
  el.classList.add("selected");
  _bgPickSelected = el.dataset.path;
};

window.savePageBg = async function(tabId) {
  if (!_bgPickSelected) { toast("Select an image first"); return; }
  try {
    await fetch("/api/media/page-backgrounds", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: tabId, file_path: _bgPickSelected }),
    });
    _pageBackgrounds[tabId] = _bgPickSelected;
    applyPageBackgrounds();
    closeModal();
    toast("Background set");
  } catch (e) { toast("Failed to set background"); }
  _bgPickSelected = "";
};

window.clearPageBg = async function(tabId) {
  try {
    await fetch("/api/media/page-backgrounds", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: tabId, file_path: null }),
    });
    delete _pageBackgrounds[tabId];
    applyPageBackgrounds();
    closeModal();
    toast("Background cleared");
  } catch (e) { toast("Failed to clear background"); }
};

// ─── Tab navigation ──────────────────────────────────────────────
function initTabs() {
  document.querySelectorAll(".sidebar-nav a").forEach(a => {
    a.addEventListener("click", e => {
      const tab = a.dataset.tab;
      if (!tab) return; // external link (e.g. Race Replay) — let the browser handle it
      e.preventDefault();
      document.querySelectorAll(".sidebar-nav a").forEach(x => x.classList.remove("active"));
      a.classList.add("active");
      document.querySelectorAll(".tab-section").forEach(s => s.classList.remove("active"));
      document.getElementById(tab).classList.add("active");
      document.querySelectorAll(".nav-group").forEach(g => {
        g.classList.toggle("has-active", !!g.querySelector("a.active"));
      });
    });
  });

  // Collapsible nav groups
  document.querySelectorAll(".nav-group").forEach(g => {
    g.classList.add("collapsed");
    const toggle = g.querySelector(".nav-group-toggle");
    if (toggle) toggle.addEventListener("click", () => g.classList.toggle("collapsed"));
  });
}

// ─── Range slider live values ────────────────────────────────────
function initRangeSliders() {
  document.querySelectorAll("input[type=range]").forEach(r => {
    const valEl = document.getElementById(r.id + "-val");
    if (valEl) {
      r.addEventListener("input", () => { valEl.textContent = parseFloat(r.value).toFixed(r.step < 1 ? 2 : 0); });
    }
  });
}

// ─── Lap-time formatter (m:ss.sss) ───────────────────────────────
function fmtLapTime(sec) {
  if (!sec || sec <= 0) return "—";
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return m + ":" + (s < 10 ? "0" : "") + s.toFixed(3);
}

function htmlEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function fmtOne(value) {
  return Number.isFinite(value) ? value.toFixed(1) : "—";
}

function fmtPos(value) {
  return value ? `P${value}` : "—";
}

function fmtSigned(value) {
  if (!Number.isFinite(value)) return "—";
  if (value > 0) return `+${value}`;
  return `${value}`;
}

// ─── Toast ───────────────────────────────────────────────────────
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2500);
}

// ─── API helpers ─────────────────────────────────────────────────
async function api(url, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  return res.json();
}

// ─── Load full config ────────────────────────────────────────────
async function loadConfig() {
  CONFIG = await api("/api/config");
  populateRaceRoom();
  populateOwnerDriver();
  populateBroadcastTeam();
  populateRaceControl();
  populateStewards();
  populateMarshalling();
  populatePowerUnit();
}

// ─── RACE ROOM ───────────────────────────────────────────────────
function populateRaceRoom() {
  const ct = CONFIG.commentary_team || {};
  const casters = ct.commentators || {};
  const sys = CONFIG.system || {};
  const pu = CONFIG.power_unit || {};
  const oai = pu.openai || {};

  // Commentator rows — names on left, preview on right
  const container = document.getElementById("rr-commentators");
  container.innerHTML = Object.entries(casters).map(([name, c]) => {
    const isActive = c.is_active !== false;
    return `
      <div class="rr-caster-row" onmouseenter="rrShowPreview('${name}')">
        <label class="toggle"><input type="checkbox" data-caster="${name}" ${isActive ? "checked" : ""}><span class="slider"></span></label>
        <div class="rr-caster-info">
          <div class="rr-caster-name">${name}</div>
          <div class="rr-caster-loc">${c.location || 'Studio'}</div>
        </div>
      </div>`;
  }).join("");

  // Rotation — quarter host selectors
  const rot = ct.host_rotation_quarters || [];
  const qLabels = ["Q1 (0–25%)", "Q2 (25–50%)", "Q3 (50–75%)", "Q4 (75–100%)"];
  document.getElementById("rr-rotation").innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
      ${rot.map((h, i) => `
        <div class="field">
          <label>${qLabels[i] || 'Q' + (i+1)}</label>
          <select class="rr-rot-q" data-q="${i}">
            ${Object.entries(casters).map(([n, c]) =>
              `<option value="${n}" ${n===h?"selected":""}>${n} (${c.location || ''})</option>`
            ).join("")}
          </select>
        </div>
      `).join("")}
    </div>
  `;

  // Location assignments — build from unique locations in active commentators
  const locations = ["Studio", "PitLane", "Paddock", "FanZone", "Trackside"];
  const activeCasters = Object.entries(casters).filter(([, c]) => c.is_active !== false);
  const locEl = document.getElementById("rr-locations");
  if (locEl) {
    locEl.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        ${locations.map(loc => {
          const current = activeCasters.find(([, c]) => (c.location || "").toLowerCase() === loc.toLowerCase());
          const currentName = current ? current[0] : "";
          return `
            <div class="field">
              <label>${loc}</label>
              <select class="rr-loc-assign" data-loc="${loc}">
                <option value="">— None —</option>
                ${activeCasters.map(([n]) =>
                  `<option value="${n}" ${n===currentName?"selected":""}>${n}</option>`
                ).join("")}
              </select>
            </div>`;
        }).join("")}
      </div>
    `;
  }

  // System toggles
  document.getElementById("rr-tts").checked = sys.enable_tts !== false;
  loadAudioDevices(sys.audio_output_index || 0);

  // AI & Audio settings
  const aiTemp = oai.temperature || 0.5;
  document.getElementById("rr-ai-temp").value = aiTemp;
  document.getElementById("rr-ai-temp-val").textContent = aiTemp.toFixed(2);
  document.getElementById("rr-ai-temp").addEventListener("input", function() {
    document.getElementById("rr-ai-temp-val").textContent = parseFloat(this.value).toFixed(2);
  });

  const maxTok = oai.max_tokens || 250;
  document.getElementById("rr-max-tokens").value = maxTok;
  document.getElementById("rr-max-tokens-val").textContent = maxTok;
  document.getElementById("rr-max-tokens").addEventListener("input", function() {
    document.getElementById("rr-max-tokens-val").textContent = this.value;
  });

  const ambVol = sys.ambient_volume || 0.5;
  document.getElementById("rr-ambient-vol").value = ambVol;
  document.getElementById("rr-ambient-vol-val").textContent = ambVol.toFixed(2);
  document.getElementById("rr-ambient-vol").addEventListener("input", function() {
    document.getElementById("rr-ambient-vol-val").textContent = parseFloat(this.value).toFixed(2);
  });

  const model = oai.model || "gpt-4o-mini";
  document.getElementById("rr-ai-model").value = model;

  // Focus Driver — populate from live leaderboard
  populateFocusDriver();
}

// ─── FOCUS DRIVER PICKER ────────────────────────────────────────
async function populateFocusDriver() {
  const sel = document.getElementById("rr-focus-driver");
  if (!sel) return;

  // Load current FDOTD to show who's selected
  let currentName = "";
  try {
    const settings = await api("/api/settings");
    const fd = settings["focus-driver-list"];
    if (fd && fd.Name) currentName = fd.Name.replace(/\s*\[PIT\]/, "").trim();
  } catch (_) {}

  // Load live leaderboard for driver list
  try {
    const res = await fetch("/sector_said_exports/leaderboard.json");
    const lb = await res.json();
    const cars = lb.cars || [];
    sel.innerHTML = '<option value="">— Not set —</option>';
    cars
      .filter(c => c.Name && !c.Name.startsWith("Pace Car"))
      .sort((a, b) => (a.LivePos || 999) - (b.LivePos || 999))
      .forEach(c => {
        const name = c.Name.replace(/\s*\[PIT\]/, "").trim();
        const ir = c.IRating ? ` (iR ${c.IRating})` : "";
        const opt = document.createElement("option");
        opt.value = c.CarIdx;
        opt.textContent = `P${c.LivePos || "?"} ${name}${ir}`;
        opt.dataset.name = name;
        opt.dataset.custId = c.CustId || "";
        opt.dataset.carNumber = c.CarNumber || "";
        if (name.toLowerCase() === currentName.toLowerCase()) opt.selected = true;
        sel.appendChild(opt);
      });
  } catch (_) {
    sel.innerHTML = '<option value="">— No leaderboard —</option>';
  }

  // Show current badge
  updateFocusBadge(currentName);
}

function updateFocusBadge(name) {
  const badge = document.getElementById("rr-focus-badge");
  const info = document.getElementById("rr-focus-info");
  if (!badge || !info) return;
  if (name) {
    badge.textContent = name;
    info.style.display = "";
  } else {
    info.style.display = "none";
  }
}

window.setFocusDriver = async function() {
  const sel = document.getElementById("rr-focus-driver");
  if (!sel) return;
  const opt = sel.selectedOptions[0];
  if (!opt || !opt.value) {
    toast("No driver selected");
    return;
  }
  const name = opt.dataset.name || "";
  const fdPayload = {
    CarIdx: parseInt(opt.value, 10),
    CustId: opt.dataset.custId ? parseInt(opt.dataset.custId, 10) : null,
    Name: name,
    CarNumber: opt.dataset.carNumber || "",
  };
  try {
    const res = await api("/api/settings/focus_driver", "POST", fdPayload);
    if (res.error) { toast(res.error); return; }
    toast("Focus Driver: " + name);
    updateFocusBadge(name);
  } catch (e) {
    toast("Failed to set Focus Driver");
  }
};

window.rrShowPreview = function(name) {
  const casters = (CONFIG.commentary_team || {}).commentators || {};
  const c = casters[name];
  if (!c) return;
  const preview = document.getElementById("rr-caster-preview");
  const portrait = c.profile_image
    ? `<img src="/api/media/file?path=${encodeURIComponent(c.profile_image)}" alt="${name}">`
    : `<div class="rr-preview-portrait-placeholder">${name.charAt(0)}</div>`;
  const bio = c.bio || c.style || "";
  preview.innerHTML = `
    <div class="rr-preview-portrait">${portrait}</div>
    <div class="rr-preview-overlay">
      <div class="rr-preview-spacer"></div>
      <div class="rr-preview-nameplate">
        <div class="rr-preview-firstname">${c.nationality || ''}</div>
        <div class="rr-preview-lastname">${name}</div>
      </div>
      <div class="rr-preview-stats">
        <div class="rr-preview-stat"><span class="rr-preview-stat-label">Location</span><span class="rr-preview-stat-value">${c.location || 'Studio'}</span></div>
        <div class="rr-preview-stat"><span class="rr-preview-stat-label">Energy</span><span class="rr-preview-stat-value">${c.energy || '—'}</span></div>
        <div class="rr-preview-stat"><span class="rr-preview-stat-label">Style</span><span class="rr-preview-stat-value">${c.style || '—'}</span></div>
      </div>
      ${bio ? `<div class="rr-preview-bio">${bio}</div>` : ''}
    </div>
  `;
};

// ─── OWNER DRIVER ────────────────────────────────────────────────
let _odSelectedDriver = null;

function populateOwnerDriver() {
  odRenderGrid();
}

async function odRenderGrid() {
  let drivers;
  try { drivers = await api("/api/owner-drivers"); } catch(e) { drivers = {}; }
  // Also store on CONFIG for career lookup
  CONFIG.owner_drivers = drivers;

  const grid = document.getElementById("od-grid");
  const cards = Object.entries(drivers).map(([name, d], idx) => {
    const isActive = d.is_active !== false;
    const portrait = d.profile_image
      ? `<img src="/api/media/file?path=${encodeURIComponent(d.profile_image)}" alt="${name}">`
      : `<div class="profile-card-portrait-placeholder">${name.charAt(0)}</div>`;
    const nameParts = name.split(" ");
    const firstName = nameParts.length > 1 ? nameParts.slice(0, -1).join(" ") : "";
    const lastName = nameParts.length > 1 ? nameParts[nameParts.length - 1] : name;
    const tilt = ((idx % 5) - 2) * 0.8 + (Math.random() - 0.5) * 1.2;
    return `
      <div class="profile-card ${isActive ? 'active-profile' : 'inactive-profile'} ${_odSelectedDriver === name ? 'selected-profile' : ''}" style="--card-tilt:${tilt.toFixed(2)}deg" onclick="odOpenDetail('${name}')">
        <span class="profile-status-badge ${isActive ? 'active' : 'inactive'}">${isActive ? 'Active' : 'Inactive'}</span>
        <div class="profile-card-portrait">
          ${portrait}
          <div class="profile-card-overlay">
            <div class="profile-card-nameplate">
              ${firstName ? `<div class="profile-card-firstname">${firstName}</div>` : ''}
              <div class="profile-card-lastname">${lastName}</div>
            </div>
            <div class="profile-card-stats">
              <div class="profile-card-stat"><span class="profile-card-stat-label">Team</span><span class="profile-card-stat-value">${d.team_name || '—'}</span></div>
              <div class="profile-card-stat"><span class="profile-card-stat-label">Car</span><span class="profile-card-stat-value">${d.car_number || '—'}</span></div>
              <div class="profile-card-stat"><span class="profile-card-stat-label">Style</span><span class="profile-card-stat-value">${d.driving_style || '—'}</span></div>
            </div>
          </div>
        </div>
      </div>`;
  }).join("");

  grid.innerHTML = cards + `
    <div class="profile-card profile-card-add" onclick="odAddNew()">
      <div class="profile-card-add-icon">+</div>
      <div class="profile-card-add-label">Add Driver</div>
    </div>`;
}

window.odOpenDetail = function(name) {
  const drivers = CONFIG.owner_drivers || {};
  const d = drivers[name];
  if (!d) return;
  _odSelectedDriver = name;

  document.getElementById("od-grid").style.display = "none";
  document.getElementById("od-detail").style.display = "block";

  // Name split
  const nameParts = name.split(" ");
  const firstName = nameParts.length > 1 ? nameParts.slice(0, -1).join(" ") : "";
  const lastName = nameParts.length > 1 ? nameParts[nameParts.length - 1] : name;
  document.getElementById("od-detail-firstname").textContent = firstName;
  document.getElementById("od-detail-lastname").textContent = lastName;
  document.getElementById("od-detail-bio").textContent = d.bio || "";

  // Photo — portrait goes in the wrap, Change Photo pill is a sibling
  // of the wrap so it lands above the sidebar overlay (z-index 1).
  // Inside the wrap (z-index 0) the pill was invisible behind the
  // nameplate gradient.
  const wrap = document.getElementById("od-photo-wrap");
  const portraitHtml = d.profile_image
    ? `<img src="/api/media/file?path=${encodeURIComponent(d.profile_image)}" onclick="odPickPhoto()" title="Click to change photo">`
    : `<div class="profile-detail-portrait-placeholder" onclick="odPickPhoto()" title="Click to set photo">${name.charAt(0)}</div>`;
  wrap.innerHTML = portraitHtml;

  const sidebar = wrap.parentElement;
  sidebar.querySelectorAll(".profile-photo-change").forEach(b => b.remove());
  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = "profile-photo-change";
  pill.onclick = () => odPickPhoto();
  pill.innerHTML = `<span class="pc-icon">\u{1F4F7}</span><span>${d.profile_image ? "Change Photo" : "Set Photo"}</span>`;
  sidebar.appendChild(pill);

  // Sidebar stats
  document.getElementById("od-detail-stats").innerHTML = `
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Team</span><span class="profile-detail-stat-value">${d.team_name || '—'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Car Number</span><span class="profile-detail-stat-value">${d.car_number || '—'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">iRacing ID</span><span class="profile-detail-stat-value">${d.iracing_customer_id || '—'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Driving Style</span><span class="profile-detail-stat-value">${d.driving_style || '—'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Commentary</span><span class="profile-detail-stat-value">${d.commentary_frequency || 'normal'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Nickname</span><span class="profile-detail-stat-value">${d.nickname || '—'}</span></div>
  `;

  // Form fields
  document.getElementById("od-name").value = d.display_name || name;
  document.getElementById("od-nickname").value = d.nickname || "";
  document.getElementById("od-team").value = d.team_name || "";
  document.getElementById("od-bio").value = d.bio || "";
  document.getElementById("od-iracing-id").value = d.iracing_customer_id || "";
  document.getElementById("od-car-number").value = d.car_number || "";
  document.getElementById("od-style").value = d.driving_style || "";
  document.getElementById("od-frequency").value = d.commentary_frequency || "normal";
  document.getElementById("od-phrases").value = (d.featured_phrases || []).join(", ");

  odRenderGrid();
  loadOwnerHistory();
  loadOwnerPhotos(name);
};

/* Fetch and render every image tagged with this driver's display name.
 * Each card clicks through to the shared media-gallery preview modal. */
async function loadOwnerPhotos(name) {
  const gallery = document.getElementById("od-photos");
  const countEl = document.getElementById("od-photos-count");
  if (!gallery) return;
  gallery.innerHTML = '<div class="empty-state" style="padding:24px"><div class="empty-text" style="color:var(--text-dim)">Loading…</div></div>';
  if (countEl) countEl.textContent = "";
  try {
    const resp = await api("/api/media/by-driver/" + encodeURIComponent(name));
    const files = (resp && Array.isArray(resp.files)) ? resp.files : [];
    if (countEl) countEl.textContent = files.length
      ? `— ${files.length} photo${files.length === 1 ? "" : "s"}`
      : "";
    if (!files.length) {
      gallery.innerHTML =
        '<div class="empty-state" style="padding:24px">' +
          '<div class="empty-text" style="color:var(--text-dim)">' +
            'No photos tagged with "' + _odEsc(name) + '" yet. Open the Media gallery, edit a photo\'s tags, and add the driver\'s name.' +
          '</div>' +
        '</div>';
      return;
    }
    gallery.innerHTML = files.map(f => {
      const safePath = _odEsc(f.path);
      const safeName = _odEsc(f.name);
      return (
        '<div class="mg-card" onclick="mgOpenPreview(\'' + safePath.replace(/'/g, "\\'") + '\')">' +
          '<div class="mg-thumb"><img src="/api/media/file?path=' +
            encodeURIComponent(f.path) + '" alt="' + safeName +
            '" loading="lazy"></div>' +
          '<div class="mg-info"><div class="mg-name" title="' + safeName +
            '">' + safeName + '</div></div>' +
        '</div>'
      );
    }).join("");
  } catch (e) {
    gallery.innerHTML = '<div class="empty-state" style="padding:24px"><div class="empty-text" style="color:var(--text-dim)">Failed to load photos.</div></div>';
  }
}

function _odEsc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

window.odCloseDetail = function() {
  document.getElementById("od-detail").style.display = "none";
  document.getElementById("od-grid").style.display = "";
  _odSelectedDriver = null;
  odRenderGrid();
};

window.odSave = async function() {
  if (!_odSelectedDriver) return;
  const phrases = document.getElementById("od-phrases").value;
  const data = {
    display_name: document.getElementById("od-name").value,
    nickname: document.getElementById("od-nickname").value,
    team_name: document.getElementById("od-team").value,
    bio: document.getElementById("od-bio").value,
    iracing_customer_id: document.getElementById("od-iracing-id").value,
    car_number: document.getElementById("od-car-number").value,
    driving_style: document.getElementById("od-style").value,
    commentary_frequency: document.getElementById("od-frequency").value,
    featured_phrases: phrases ? phrases.split(",").map(s => s.trim()).filter(Boolean) : [],
  };
  await api(`/api/owner-drivers/${encodeURIComponent(_odSelectedDriver)}`, "PUT", data);
  toast("Profile saved");
  odRenderGrid();
};

window.odSetActive = async function() {
  if (!_odSelectedDriver) return;
  await api(`/api/owner-drivers/${encodeURIComponent(_odSelectedDriver)}/activate`, "POST");
  toast(`${_odSelectedDriver} set as active driver`);
  odRenderGrid();
};

window.odDelete = async function() {
  if (!_odSelectedDriver) return;
  if (!confirm(`Delete driver profile "${_odSelectedDriver}"?`)) return;
  await api(`/api/owner-drivers/${encodeURIComponent(_odSelectedDriver)}`, "DELETE");
  toast("Profile deleted");
  _odSelectedDriver = null;
  document.getElementById("od-detail").style.display = "none";
  odRenderGrid();
};

window.odAddNew = function() {
  const html = `
    <h3>Add Owner Driver</h3>
    <div class="field"><label>Display Name</label><input type="text" id="od-new-name" placeholder="Your iRacing name"></div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="odCreateNew()">Create</button>
    </div>`;
  showModal(html);
};

window.odCreateNew = async function() {
  const name = document.getElementById("od-new-name").value.trim();
  if (!name) { toast("Name required"); return; }
  await api(`/api/owner-drivers/${encodeURIComponent(name)}`, "PUT", {
    display_name: name, is_active: true,
  });
  closeModal();
  toast(`${name} created`);
  await odRenderGrid();
  odOpenDetail(name);
};

window.odPickPhoto = async function() {
  if (!_odSelectedDriver) return;
  let files;
  try { const res = await api("/api/media/files"); files = (res.files || []).filter(f => f.type === "image"); } catch(e) { files = []; }
  const thumbs = files.map(f => `
    <div class="bg-pick-thumb" data-path="${f.path}" onclick="pickBgThumb(this, 'od-photo')">
      <img src="/api/media/file?path=${encodeURIComponent(f.path)}" loading="lazy">
      <div class="bg-pick-name">${f.name}</div>
    </div>`).join("");
  const html = `
    <h3>Select Profile Photo</h3>
    <div class="bg-pick-grid">${thumbs || '<div style="color:var(--text-dim)">No images found.</div>'}</div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="odSavePhoto()">Set Photo</button>
    </div>`;
  _bgPickSelected = "";
  showModal(html);
};

window.odSavePhoto = async function() {
  if (!_bgPickSelected || !_odSelectedDriver) { toast("Select an image"); return; }
  await api(`/api/owner-drivers/${encodeURIComponent(_odSelectedDriver)}`, "PUT", { profile_image: _bgPickSelected });
  // Same in-memory-staleness trap as btSavePhoto — refresh CONFIG
  // before re-opening the detail, otherwise the UI re-renders the
  // old portrait even though the save hit disk.
  try {
    const fresh = await api("/api/owner-drivers");
    CONFIG.owner_drivers = fresh || {};
  } catch (_) {}
  closeModal();
  toast("Photo updated");
  odOpenDetail(_odSelectedDriver);
};

async function loadOwnerHistory() {
  try {
    const history = await api("/api/history/owner");
    const container = document.getElementById("od-recent-races");
    if (!history || history.length === 0) {
      container.innerHTML = '<p style="color:var(--text-dim)">No race history yet.</p>';
    } else {
      container.innerHTML = `<table class="pw-table"><thead><tr><th>Track</th><th>Grid</th><th>Finish</th><th>Best Lap</th></tr></thead><tbody>
        ${history.map(r => `<tr>
          <td>${r.track_name || "?"}</td>
          <td>${r.grid_position || "-"}</td>
          <td class="num">P${r.finish_position || "?"}</td>
          <td>${fmtLapTime(r.best_lap)}</td>
        </tr>`).join("")}
      </tbody></table>`;
    }

    const name = _odSelectedDriver;
    if (!name) return;
    const careers = await api("/api/history/drivers");
    const owner = careers.find(d => d.driver_name === name);
    if (owner) {
      document.getElementById("od-career-stats").innerHTML = `
        <div class="grid-3">
          <div><div style="font-size:24px;font-weight:700;color:var(--green)">${owner.wins}</div><div style="font-size:10px;color:var(--text-dim)">WINS</div></div>
          <div><div style="font-size:24px;font-weight:700;color:var(--gold)">${owner.podiums}</div><div style="font-size:10px;color:var(--text-dim)">PODIUMS</div></div>
          <div><div style="font-size:24px;font-weight:700">${owner.races_entered}</div><div style="font-size:10px;color:var(--text-dim)">RACES</div></div>
          <div><div style="font-size:24px;font-weight:700;color:var(--green)">${owner.poles}</div><div style="font-size:10px;color:var(--text-dim)">POLES</div></div>
          <div><div style="font-size:24px;font-weight:700;color:var(--gold)">${owner.dotd_wins}</div><div style="font-size:10px;color:var(--text-dim)">DOTD</div></div>
          <div><div style="font-size:24px;font-weight:700">P${owner.best_finish || "-"}</div><div style="font-size:10px;color:var(--text-dim)">BEST FINISH</div></div>
        </div>`;
    }
  } catch (e) { console.warn("Owner history load:", e); }
}

// ─── BROADCAST TEAM ──────────────────────────────────────────────
let _btSelectedCaster = null;

function populateBroadcastTeam() {
  btRenderGrid();
}

async function btRenderGrid() {
  let casters;
  try { casters = await api("/api/commentators"); } catch(e) { casters = {}; }
  // Cache on CONFIG
  if (!CONFIG.commentary_team) CONFIG.commentary_team = {};
  CONFIG.commentary_team.commentators = casters;

  const grid = document.getElementById("bt-grid");
  const cards = Object.entries(casters).map(([name, c], idx) => {
    const isActive = c.is_active !== false;
    const portrait = c.profile_image
      ? `<img src="/api/media/file?path=${encodeURIComponent(c.profile_image)}" alt="${name}">`
      : `<div class="profile-card-portrait-placeholder">${name.charAt(0)}</div>`;
    const tilt = ((idx % 7) - 3) * 0.7 + (Math.random() - 0.5) * 1.4;
    return `
      <div class="profile-card ${isActive ? 'active-profile' : 'inactive-profile'} ${_btSelectedCaster === name ? 'selected-profile' : ''}" style="--card-tilt:${tilt.toFixed(2)}deg" onclick="btOpenDetail('${name}')">
        <span class="profile-status-badge ${isActive ? 'active' : 'inactive'}">${isActive ? 'Active' : 'Inactive'}</span>
        <div class="profile-card-portrait">
          ${portrait}
          <div class="profile-card-overlay">
            <div class="profile-card-nameplate">
              <div class="profile-card-firstname">${c.nationality || ''}</div>
              <div class="profile-card-lastname">${name}</div>
            </div>
            <div class="profile-card-stats">
              <div class="profile-card-stat"><span class="profile-card-stat-label">Location</span><span class="profile-card-stat-value">${c.location || 'Studio'}</span></div>
              <div class="profile-card-stat"><span class="profile-card-stat-label">Energy</span><span class="profile-card-stat-value">${c.energy || '—'}</span></div>
              <div class="profile-card-stat"><span class="profile-card-stat-label">Style</span><span class="profile-card-stat-value">${c.style || '—'}</span></div>
            </div>
          </div>
        </div>
      </div>`;
  }).join("");

  grid.innerHTML = cards + `
    <div class="profile-card profile-card-add" onclick="btAddNew()">
      <div class="profile-card-add-icon">+</div>
      <div class="profile-card-add-label">Add Commentator</div>
    </div>`;
}

window.btOpenDetail = function(name) {
  const casters = (CONFIG.commentary_team || {}).commentators || {};
  const c = casters[name];
  if (!c) return;
  _btSelectedCaster = name;
  _editingCommentator = name;

  document.getElementById("bt-grid").style.display = "none";
  document.getElementById("bt-detail").style.display = "block";

  // Name — only the character name, no think/persona line
  document.getElementById("bt-detail-lastname").textContent = name;
  document.getElementById("bt-detail-bio").textContent = c.bio || "";

  // Toggle button label
  const isActive = c.is_active !== false;
  document.getElementById("bt-toggle-btn").textContent = isActive ? "Deactivate" : "Activate";

  // Photo — portrait goes in the wrap, but the Change Photo pill has
  // to live as a sibling of the wrap so it lands above the sidebar
  // overlay (z-index 1). Inside the wrap (z-index 0) the pill was
  // hidden behind the gradient + nameplate — invisible to the user.
  const wrap = document.getElementById("bt-photo-wrap");
  const btPortraitHtml = c.profile_image
    ? `<img src="/api/media/file?path=${encodeURIComponent(c.profile_image)}" onclick="btPickPhoto()" title="Click to change photo">`
    : `<div class="profile-detail-portrait-placeholder" onclick="btPickPhoto()" title="Click to set photo">${name.charAt(0)}</div>`;
  wrap.innerHTML = btPortraitHtml;

  const sidebar = wrap.parentElement;
  sidebar.querySelectorAll(".profile-photo-change").forEach(b => b.remove());
  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = "profile-photo-change";
  pill.onclick = () => btPickPhoto();
  pill.innerHTML = `<span class="pc-icon">\u{1F4F7}</span><span>${c.profile_image ? "Change Photo" : "Set Photo"}</span>`;
  sidebar.appendChild(pill);

  // Sidebar stats
  document.getElementById("bt-detail-stats").innerHTML = `
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Location</span><span class="profile-detail-stat-value">${c.location || 'Studio'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Voice ID</span><span class="profile-detail-stat-value">${c.voice_id || '—'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Rate</span><span class="profile-detail-stat-value">${c.speaking_rate || 1.3}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Nationality</span><span class="profile-detail-stat-value">${c.nationality || '—'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Age</span><span class="profile-detail-stat-value">${c.age || '—'}</span></div>
    <div class="profile-detail-stat"><span class="profile-detail-stat-label">Energy</span><span class="profile-detail-stat-value">${c.energy || '—'}</span></div>
    ${c.catchphrase ? `<div class="profile-detail-stat"><span class="profile-detail-stat-label">Catchphrase</span><span class="profile-detail-stat-value">${c.catchphrase}</span></div>` : ''}
  `;

  // Form fields
  document.getElementById("bt-think").value = c.think || "";
  document.getElementById("bt-nationality").value = c.nationality || "";
  document.getElementById("bt-age").value = c.age || "";
  document.getElementById("bt-bio").value = c.bio || "";
  document.getElementById("bt-catchphrase").value = c.catchphrase || "";
  document.getElementById("bt-location").value = c.location || "Studio";
  document.getElementById("bt-style").value = c.style || "";
  document.getElementById("bt-energy").value = c.energy || "";
  document.getElementById("bt-voice").value = c.voice || "";
  document.getElementById("bt-voiceid").value = c.voice_id || "";
  document.getElementById("bt-rate").value = c.speaking_rate || 1.3;
  document.getElementById("bt-rate-val").textContent = (c.speaking_rate || 1.3).toFixed(1);
  document.getElementById("bt-physical").value = c.physical_description || "";

  // Load tagged images for this commentator
  btLoadTaggedImages(name);

  btRenderGrid();
};

window.btCloseDetail = function() {
  document.getElementById("bt-detail").style.display = "none";
  document.getElementById("bt-grid").style.display = "";
  _btSelectedCaster = null;
  _editingCommentator = null;
  btRenderGrid();
};

// ── Tagged Image Gallery for Commentator ──────────────────────
async function btLoadTaggedImages(commentatorName) {
  const gallery = document.getElementById("bt-image-gallery");
  if (!gallery) return;

  gallery.innerHTML = '<div class="bt-gallery-empty">Loading images...</div>';

  try {
    const res = await api("/api/media/files");
    const files = res.files || [];

    // Filter to images tagged with this commentator
    const tagged = files.filter(f => {
      if (f.type !== "image") return false;
      if (!f.commentator) return false;
      if (f.commentator.toLowerCase() !== commentatorName.toLowerCase()) return false;
      return true;
    });

    if (tagged.length === 0) {
      gallery.innerHTML = '<div class="bt-gallery-empty">No images tagged for this commentator.</div>';
      return;
    }

    gallery.innerHTML = tagged.map(f => {
      const url = `/api/media/file?path=${encodeURIComponent(f.path)}`;
      const locBadge = f.location ? `<span class="bt-gallery-loc">${f.location}</span>` : '';
      return `<div class="bt-gallery-item" onclick="btGalleryPreview('${url.replace(/'/g, "\\'")}', '${f.name.replace(/'/g, "\\'")}')">
        <img src="${url}" alt="${f.name}" loading="lazy">
        <div class="bt-gallery-badges">${locBadge}</div>
      </div>`;
    }).join("");
  } catch (e) {
    gallery.innerHTML = '<div class="bt-gallery-empty">Failed to load images.</div>';
  }
}

// Preview a gallery image in a lightbox
window.btGalleryPreview = function(url, name) {
  const overlay = document.createElement("div");
  overlay.className = "bt-gallery-lightbox";
  overlay.onclick = () => overlay.remove();
  overlay.innerHTML = `
    <div class="bt-gallery-lightbox-inner">
      <img src="${url}" alt="${name}">
      <div class="bt-gallery-lightbox-name">${name}</div>
    </div>
  `;
  document.body.appendChild(overlay);
};

window.btSave = async function() {
  if (!_btSelectedCaster) return;
  const data = {
    think: document.getElementById("bt-think").value,
    nationality: document.getElementById("bt-nationality").value,
    age: document.getElementById("bt-age").value,
    bio: document.getElementById("bt-bio").value,
    catchphrase: document.getElementById("bt-catchphrase").value,
    location: document.getElementById("bt-location").value,
    style: document.getElementById("bt-style").value,
    energy: document.getElementById("bt-energy").value,
    voice: document.getElementById("bt-voice").value,
    voice_id: document.getElementById("bt-voiceid").value,
    speaking_rate: parseFloat(document.getElementById("bt-rate").value),
    physical_description: document.getElementById("bt-physical").value,
  };
  await api(`/api/commentators/${encodeURIComponent(_btSelectedCaster)}`, "PUT", data);
  toast(`${_btSelectedCaster} saved`);
  btRenderGrid();
};

window.btToggleActive = async function() {
  if (!_btSelectedCaster) return;
  const res = await api(`/api/commentators/${encodeURIComponent(_btSelectedCaster)}/toggle`, "POST");
  toast(`${_btSelectedCaster} ${res.is_active ? 'activated' : 'deactivated'}`);
  btOpenDetail(_btSelectedCaster);
};

window.btDelete = async function() {
  if (!_btSelectedCaster) return;
  if (!confirm(`Delete commentator "${_btSelectedCaster}"? This cannot be undone.`)) return;
  await api(`/api/commentators/${encodeURIComponent(_btSelectedCaster)}`, "DELETE");
  toast(`${_btSelectedCaster} deleted`);
  _btSelectedCaster = null;
  _editingCommentator = null;
  document.getElementById("bt-detail").style.display = "none";
  btRenderGrid();
};

window.btAddNew = function() {
  const html = `
    <h3>Add Commentator</h3>
    <div class="grid-2">
      <div class="field"><label>Name</label><input type="text" id="bt-new-name" placeholder="Character name"></div>
      <div class="field"><label>Persona (Think...)</label><input type="text" id="bt-new-think" placeholder="e.g. David Attenborough"></div>
    </div>
    <div class="grid-2">
      <div class="field"><label>Location</label>
        <select id="bt-new-location">
          <option>Studio</option><option>PitLane</option><option>Paddock</option><option>FanZone</option><option>Trackside</option><option>Garage</option>
        </select>
      </div>
      <div class="field"><label>Voice ID</label><input type="text" id="bt-new-voiceid" placeholder="Inworld voice ID"></div>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="btCreateNew()">Create</button>
    </div>`;
  showModal(html);
};

window.btCreateNew = async function() {
  const name = document.getElementById("bt-new-name").value.trim();
  if (!name) { toast("Name required"); return; }
  const data = {
    think: document.getElementById("bt-new-think").value,
    location: document.getElementById("bt-new-location").value,
    voice_id: document.getElementById("bt-new-voiceid").value,
    voice: "", style: "", energy: "medium", speaking_rate: 1.3,
    bio: "", nationality: "", age: "", catchphrase: "",
    physical_description: "", profile_image: "", is_active: true,
  };
  await api(`/api/commentators/${encodeURIComponent(name)}`, "PUT", data);
  closeModal();
  toast(`${name} created`);
  await btRenderGrid();
  btOpenDetail(name);
};

window.btPickPhoto = async function() {
  if (!_btSelectedCaster) return;
  let files;
  try { const res = await api("/api/media/files"); files = (res.files || []).filter(f => f.type === "image"); } catch(e) { files = []; }
  const thumbs = files.map(f => `
    <div class="bg-pick-thumb" data-path="${f.path}" onclick="pickBgThumb(this, 'bt-photo')">
      <img src="/api/media/file?path=${encodeURIComponent(f.path)}" loading="lazy">
      <div class="bg-pick-name">${f.name}</div>
    </div>`).join("");
  const html = `
    <h3>Select Profile Photo</h3>
    <div class="bg-pick-grid">${thumbs || '<div style="color:var(--text-dim)">No images found.</div>'}</div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="btSavePhoto()">Set Photo</button>
    </div>`;
  _bgPickSelected = "";
  showModal(html);
};

window.btSavePhoto = async function() {
  if (!_bgPickSelected || !_btSelectedCaster) { toast("Select an image"); return; }
  await api(`/api/commentators/${encodeURIComponent(_btSelectedCaster)}`, "PUT", { profile_image: _bgPickSelected });
  // Refresh the in-memory CONFIG from the server — btOpenDetail reads
  // CONFIG.commentary_team.commentators[name], not the API, so without
  // this re-render it would redraw the stale portrait.
  try {
    const fresh = await api("/api/commentators");
    CONFIG.commentary_team = CONFIG.commentary_team || {};
    CONFIG.commentary_team.commentators = fresh || {};
  } catch (_) {}
  closeModal();
  toast("Photo updated");
  btOpenDetail(_btSelectedCaster);
};

window.btPreviewVoice = async function() {
  if (!_btSelectedCaster) return;
  const btn = document.getElementById("bt-preview-btn");
  btn.disabled = true;
  btn.textContent = "Generating...";
  try {
    const res = await api(`/api/commentators/${encodeURIComponent(_btSelectedCaster)}/preview`, "POST", {
      text: `And we're back live at the track! This is ${_btSelectedCaster}, ready for action.`,
    });
    if (res.ok && res.clip) {
      const audio = document.getElementById("bt-audio-preview");
      audio.src = res.clip;
      audio.play();
      toast("Playing preview...");
    } else {
      toast(res.error || "Preview failed");
    }
  } catch (e) {
    toast("Preview failed");
  } finally {
    btn.disabled = false;
    btn.textContent = "Preview Voice";
  }
};

// ─── RACE CONTROL ────────────────────────────────────────────────
let _rcHostPopupOpen = null;

function populateRaceControl() {
  const rc = CONFIG.race_control || {};
  const ts = (CONFIG.commentary_team || {}).trigger_settings || {};
  const casters = (CONFIG.commentary_team || {}).commentators || {};

  // Global sliders
  rcSetSlider("rc-cooldown", rc.cooldown || 30, "s");
  rcSetSlider("rc-flag-cd", rc.flag_cooldown || 30, "s");
  rcSetSlider("rc-midfield", rc.midfield_battle_cooldown || 260, "s");
  rcSetSlider("rc-summary", rc.summary_race_interval || 60, "s");

  // Split triggers into live vs scheduled
  const live = [], scheduled = [];
  for (const [name, t] of Object.entries(ts)) {
    if (t.fractions && t.fractions.length > 0) {
      scheduled.push([name, t]);
    } else {
      live.push([name, t]);
    }
  }

  document.getElementById("rc-live-triggers").innerHTML = live.map(([n, t]) => rcRenderTriggerCard(n, t, casters)).join("");
  document.getElementById("rc-scheduled-triggers").innerHTML = scheduled.map(([n, t]) => rcRenderTriggerCard(n, t, casters)).join("");

  // Bind slider events
  document.querySelectorAll(".rc-slider").forEach(s => {
    s.addEventListener("input", () => {
      const valEl = document.getElementById(s.id + "-val");
      if (valEl) valEl.textContent = s.value + "s";
    });
  });

  // Close host popup on outside click
  document.addEventListener("click", (e) => {
    if (_rcHostPopupOpen && !e.target.closest(".rc-host-pick") && !e.target.closest(".rc-host-popup")) {
      const popup = document.getElementById(_rcHostPopupOpen);
      if (popup) popup.remove();
      _rcHostPopupOpen = null;
    }
  });
}

function rcSetSlider(id, val, suffix) {
  const el = document.getElementById(id);
  if (el) { el.value = val; }
  const valEl = document.getElementById(id + "-val");
  if (valEl) valEl.textContent = val + (suffix || "");
}

const _rcF1Colors = [
  '#e10600', '#00d2be', '#3671C6', '#FF8000', '#0093cc',
  '#64C4FF', '#00e701', '#B6BABD', '#6692FF', '#279f57',
];

function rcRenderTriggerCard(name, t, casters) {
  // `_disabled` is the ONLY enabled flag the engine actually reads
  // (trigger_utils._is_trigger_disabled). `real_time` is structural
  // metadata — whether a trigger fires on live events vs at scheduled
  // fractions — and must not gate the toggle, otherwise pre_race_pick
  // and pre_race_focus (both real_time: false) look permanently gray.
  const enabled = !t._disabled;
  const color = _rcF1Colors[Math.floor(Math.random() * _rcF1Colors.length)];
  const hostName = t.host || "";
  const hostData = hostName ? casters[hostName] : null;
  const hostPhoto = hostData && hostData.profile_image
    ? `<img class="rc-host-photo" src="/api/media/file?path=${encodeURIComponent(hostData.profile_image)}">`
    : "";
  const hostDisplay = hostName
    ? `${hostPhoto}<span class="rc-host-name">${hostName}</span>`
    : `<span class="rc-host-none">Rotation</span>`;

  const fractions = t.fractions
    ? `<div class="rc-fractions">${t.fractions.map(f => `<span class="rc-frac-chip">${Math.round(f * 100)}%</span>`).join("")}</div>`
    : "";

  const prettyName = name.replace(/_/g, " ").replace(/-/g, " ");
  const waveBars = Array.from({length: 20}, (_, i) =>
    `<div class="prompt-radio-wave-bar" style="animation-delay:${(i * 0.08).toFixed(2)}s;height:${3 + Math.random() * 8}px"></div>`
  ).join('');

  return `
    <div class="rc-trigger-card ${enabled ? '' : 'rc-disabled'}" data-trig="${name}" style="--radio-color:${color}">
      <div class="prompt-radio-header">
        <div class="prompt-radio-name">${prettyName}</div>
        <div class="prompt-radio-label">RADIO</div>
        <div class="prompt-radio-actions">
          <button class="rc-trigger-toggle ${enabled ? 'on' : ''}" data-trig="${name}" onclick="rcToggleTrigger(this)" title="Enable / Disable"></button>
        </div>
      </div>
      <div class="prompt-radio-stripe"></div>
      <div class="prompt-radio-wave">${waveBars}</div>
      <div class="rc-trigger-style">${t.style || ''}</div>
      <div class="rc-trigger-controls">
        <div class="field">
          <label>Characters</label>
          <input type="number" class="trig-chars" data-trig="${name}" value="${t.characters || 100}" min="20" max="500">
        </div>
        <div class="field">
          <label>Cooldown (s)</label>
          <input type="number" class="trig-cd" data-trig="${name}" value="${t.cooldown || 0}" step="0.5" min="0" max="300">
        </div>
        <div class="field" style="grid-column: span 2">
          <label>Host Commentator</label>
          <div class="rc-host-pick" onclick="rcOpenHostPicker('${name}', event)">
            ${hostDisplay}
          </div>
        </div>
        ${t.fractions ? `
        <div class="field" style="grid-column: span 2">
          <label>Race Progress Points</label>
          <input type="text" class="trig-frac" data-trig="${name}" value="${t.fractions.join(', ')}" placeholder="e.g. 0.25, 0.50, 0.75">
          ${fractions}
        </div>` : ""}
        ${t.ambient_file ? `
        <div class="field">
          <label>Ambient Audio</label>
          <input type="text" class="trig-ambient" data-trig="${name}" value="${t.ambient_file || ''}" placeholder="filename.mp3">
        </div>
        <div class="field">
          <label>Ambient Vol</label>
          <input type="number" class="trig-ambient-vol" data-trig="${name}" value="${t.ambient_volume || 0.5}" step="0.1" min="0" max="1">
        </div>` : ""}
      </div>
    </div>`;
}

window.rcToggleTrigger = function(btn) {
  const card = btn.closest(".rc-trigger-card");
  btn.classList.toggle("on");
  card.classList.toggle("rc-disabled");
};

window.rcOpenHostPicker = function(trigName, event) {
  event.stopPropagation();
  // Close existing
  if (_rcHostPopupOpen) {
    const old = document.getElementById(_rcHostPopupOpen);
    if (old) old.remove();
    if (_rcHostPopupOpen === "rc-host-popup-" + trigName) { _rcHostPopupOpen = null; return; }
  }

  const casters = (CONFIG.commentary_team || {}).commentators || {};
  const ts = (CONFIG.commentary_team || {}).trigger_settings || {};
  const currentHost = ts[trigName]?.host || "";

  const popupId = "rc-host-popup-" + trigName;
  let html = `<div class="rc-host-popup" id="${popupId}">`;
  // "Rotation" option (no fixed host)
  html += `<div class="rc-host-option ${!currentHost ? 'selected' : ''}" onclick="rcSelectHost('${trigName}', '')">
    <div style="width:40px;height:40px;border-radius:50%;background:var(--border);display:flex;align-items:center;justify-content:center;font-size:16px;color:var(--text-dim)">&#8634;</div>
    <div class="rc-host-opt-name">Rotation</div>
  </div>`;
  for (const [name, c] of Object.entries(casters)) {
    if (c.is_active === false) continue;
    const photo = c.profile_image
      ? `<img src="/api/media/file?path=${encodeURIComponent(c.profile_image)}" style="width:40px;height:40px;border-radius:50%;object-fit:cover">`
      : `<div style="width:40px;height:40px;border-radius:50%;background:var(--surface-alt);display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;color:var(--text-dim)">${name.charAt(0)}</div>`;
    html += `<div class="rc-host-option ${currentHost === name ? 'selected' : ''}" onclick="rcSelectHost('${trigName}', '${name}')">
      ${photo}
      <div class="rc-host-opt-name">${name}</div>
    </div>`;
  }
  html += `</div>`;

  const card = event.target.closest(".rc-trigger-card");
  card.style.position = "relative";
  card.insertAdjacentHTML("beforeend", html);
  _rcHostPopupOpen = popupId;
};

window.rcSelectHost = function(trigName, hostName) {
  // Update config in memory. Use `null` (not undefined) for Rotation
  // so JSON.stringify keeps the key — otherwise the PUT body drops it
  // and the server-side deep-merge preserves the old host.
  const ts = (CONFIG.commentary_team || {}).trigger_settings || {};
  if (ts[trigName]) ts[trigName].host = hostName || null;

  // Close popup
  if (_rcHostPopupOpen) {
    const popup = document.getElementById(_rcHostPopupOpen);
    if (popup) popup.remove();
    _rcHostPopupOpen = null;
  }

  // Re-render just that card's host pick
  const card = document.querySelector(`.rc-trigger-card[data-trig="${trigName}"]`);
  if (card) {
    const casters = (CONFIG.commentary_team || {}).commentators || {};
    const hostData = hostName ? casters[hostName] : null;
    const hostPhoto = hostData && hostData.profile_image
      ? `<img class="rc-host-photo" src="/api/media/file?path=${encodeURIComponent(hostData.profile_image)}">`
      : "";
    const pick = card.querySelector(".rc-host-pick");
    if (pick) {
      pick.innerHTML = hostName
        ? `${hostPhoto}<span class="rc-host-name">${hostName}</span>`
        : `<span class="rc-host-none">Rotation</span>`;
    }
  }
};

window.saveRaceControl = async function() {
  // Save global timing
  await api("/api/config/race_control", "PUT", {
    cooldown: parseInt(document.getElementById("rc-cooldown").value),
    flag_cooldown: parseInt(document.getElementById("rc-flag-cd").value),
    midfield_battle_cooldown: parseInt(document.getElementById("rc-midfield").value),
    summary_race_interval: parseFloat(document.getElementById("rc-summary").value),
  });

  // Collect trigger settings
  const ts = {};
  const existingTs = (CONFIG.commentary_team || {}).trigger_settings || {};

  document.querySelectorAll(".rc-trigger-card").forEach(card => {
    const name = card.dataset.trig;
    const existing = existingTs[name] || {};
    ts[name] = { ...existing };

    const charsEl = card.querySelector(".trig-chars");
    if (charsEl) ts[name].characters = parseInt(charsEl.value);

    const cdEl = card.querySelector(".trig-cd");
    if (cdEl) ts[name].cooldown = parseFloat(cdEl.value);

    const fracEl = card.querySelector(".trig-frac");
    if (fracEl) {
      const parsed = fracEl.value.split(",").map(v => parseFloat(v.trim())).filter(v => !isNaN(v));
      // Only overwrite when the user typed something valid. An empty
      // parse means the input was blanked / malformed — fall back to
      // the existing fractions via deep-merge rather than nuking them.
      if (parsed.length > 0) {
        ts[name].fractions = parsed;
      } else {
        delete ts[name].fractions;
      }
    }

    const ambientEl = card.querySelector(".trig-ambient");
    if (ambientEl) ts[name].ambient_file = ambientEl.value;

    const ambientVolEl = card.querySelector(".trig-ambient-vol");
    if (ambientVolEl) ts[name].ambient_volume = parseFloat(ambientVolEl.value);

    // Host from in-memory config (set by rcSelectHost). Always send an
    // explicit value — `null` means "Rotation" — so the server-side
    // deep-merge can actually remove a previously-assigned host. If we
    // left the key out, merge would keep the stale host forever.
    const h = existingTs[name]?.host;
    ts[name].host = (h === undefined || h === "") ? null : h;

    // Enabled state. Only `_disabled` gates the engine runtime; we don't
    // touch `real_time` here — it's structural (live vs scheduled).
    const toggle = card.querySelector(".rc-trigger-toggle");
    if (toggle && !toggle.classList.contains("on")) {
      ts[name]._disabled = true;
    } else {
      delete ts[name]._disabled;
    }

    // Guard numeric inputs — parseInt("") is NaN which stringify-renders
    // as null and lets the engine fall back to defaults. Skip writes
    // when we couldn't parse a valid number.
    if (Number.isNaN(ts[name].characters)) delete ts[name].characters;
    if (Number.isNaN(ts[name].cooldown))   delete ts[name].cooldown;
    if (Number.isNaN(ts[name].ambient_volume)) delete ts[name].ambient_volume;
  });

  await api("/api/config/commentary_team", "PUT", { trigger_settings: ts });
  toast("Race Control saved");
};

window.rcReloadConfig = async function() {
  await api("/api/config/reload", "POST");
  CONFIG = await api("/api/config");
  populateRaceControl();
  toast("Config reloaded from disk");
};

// ─── STEWARDS' OFFICE ────────────────────────────────────────────
function populateStewards() {
  const dotd = CONFIG.dotd || {};
  const activeTriggers = dotd.triggers || {};
  const disabledTriggers = dotd.disabled_triggers || {};
  const dim = dotd.diminishing || {};

  // Scoring limits sliders
  rcSetSlider("st-cap", dotd.cap_per_lap || 300, "");
  const dimToggle = document.getElementById("st-dim-toggle");
  if (dimToggle) dimToggle.classList.toggle("on", dim.enabled !== false);
  document.getElementById("st-decay").value = (dim.decay_steps || [1, 0.8, 0.6, 0.4]).join(", ");

  // Bind slider events for stewards
  document.getElementById("st-cap").addEventListener("input", function() {
    document.getElementById("st-cap-val").textContent = this.value;
  });

  // Render trigger cards
  document.getElementById("st-active-triggers").innerHTML =
    Object.entries(activeTriggers).map(([n, t]) => stRenderTriggerCard(n, t, true)).join("");
  document.getElementById("st-disabled-triggers").innerHTML =
    Object.entries(disabledTriggers).map(([n, t]) => stRenderTriggerCard(n, t, false)).join("") ||
    '<div style="color:var(--text-dim);font-size:12px;padding:8px">No disabled triggers</div>';

  // Bind all mini sliders
  document.querySelectorAll(".st-slider-mini").forEach(s => {
    s.addEventListener("input", () => {
      const valEl = s.parentElement.querySelector(".st-slider-val");
      if (valEl) valEl.textContent = s.value;
    });
  });
}

function stRenderTriggerCard(name, t, isActive) {
  const pts = t.points || 0;
  const ptsClass = pts > 0 ? "positive" : pts < 0 ? "negative" : "neutral";
  const borderClass = pts > 0 ? "st-positive" : pts < 0 ? "st-negative" : "st-neutral";
  const prettyName = name.replace(/_/g, " ");

  let badges = "";
  if (t.per_lap) badges += '<span class="st-badge st-badge-perlap">Per Lap</span>';
  if (t.cap_per_race) badges += `<span class="st-badge st-badge-capped">Cap: ${t.cap_per_race}/race</span>`;
  if (t.diminishing_window_s) badges += `<span class="st-badge st-badge-diminishing">Diminish: ${t.diminishing_window_s}s</span>`;

  return `
    <div class="st-trigger-card ${borderClass}" data-st="${name}" data-active="${isActive}">
      <div class="st-trigger-header">
        <div class="st-trigger-name">${prettyName}</div>
        <div class="st-points-display ${ptsClass}">${pts > 0 ? '+' : ''}${pts}</div>
      </div>
      ${badges ? `<div class="st-trigger-badges">${badges}</div>` : ""}
      <div class="st-trigger-sliders">
        <div class="field">
          <label>Points</label>
          <div class="st-slider-row">
            <input type="range" class="st-slider-mini st-pts" data-st="${name}" min="-100" max="150" step="5" value="${pts}">
            <span class="st-slider-val">${pts}</span>
          </div>
        </div>
        <div class="field">
          <label>Min Interval (s)</label>
          <div class="st-slider-row">
            <input type="range" class="st-slider-mini st-int" data-st="${name}" min="0" max="10" step="0.5" value="${t.min_interval_s || 0}">
            <span class="st-slider-val">${t.min_interval_s || 0}</span>
          </div>
        </div>
        <div class="field">
          <label>Cap Per Race</label>
          <div class="st-slider-row">
            <input type="range" class="st-slider-mini st-cap-race" data-st="${name}" min="0" max="20" step="1" value="${t.cap_per_race || 0}">
            <span class="st-slider-val">${t.cap_per_race || 0}</span>
          </div>
          <div style="font-size:9px;color:var(--text-dim);margin-top:2px">0 = unlimited</div>
        </div>
        <div class="field">
          <label>Options</label>
          <div style="display:flex;flex-direction:column;gap:6px;margin-top:4px">
            <label style="display:flex;align-items:center;gap:6px;font-size:11px;text-transform:none;letter-spacing:0;cursor:pointer">
              <input type="checkbox" class="st-perlap" data-st="${name}" ${t.per_lap ? "checked" : ""}> Score per lap
            </label>
          </div>
        </div>
      </div>
      <div class="st-trigger-actions">
        ${isActive
          ? `<button class="btn btn-sm" onclick="stDisableTrigger('${name}')" title="Move to disabled">Disable</button>`
          : `<button class="btn btn-sm btn-primary" onclick="stEnableTrigger('${name}')" title="Move to active">Enable</button>`}
        <button class="btn btn-sm btn-danger" onclick="stDeleteTrigger('${name}', ${isActive})" title="Delete trigger">Delete</button>
      </div>
    </div>`;
}

window.stEnableTrigger = function(name) {
  const dotd = CONFIG.dotd || {};
  const disabled = dotd.disabled_triggers || {};
  if (!disabled[name]) return;
  dotd.triggers = dotd.triggers || {};
  dotd.triggers[name] = disabled[name];
  delete disabled[name];
  populateStewards();
  toast(`${name.replace(/_/g, " ")} enabled`);
};

window.stDisableTrigger = function(name) {
  const dotd = CONFIG.dotd || {};
  const triggers = dotd.triggers || {};
  if (!triggers[name]) return;
  dotd.disabled_triggers = dotd.disabled_triggers || {};
  dotd.disabled_triggers[name] = triggers[name];
  delete triggers[name];
  populateStewards();
  toast(`${name.replace(/_/g, " ")} disabled`);
};

window.stDeleteTrigger = function(name, isActive) {
  if (!confirm(`Delete DOTD trigger "${name.replace(/_/g, " ")}"?`)) return;
  const dotd = CONFIG.dotd || {};
  if (isActive) { delete (dotd.triggers || {})[name]; }
  else { delete (dotd.disabled_triggers || {})[name]; }
  populateStewards();
  toast(`${name.replace(/_/g, " ")} deleted`);
};

window.stAddTrigger = function() {
  const html = `
    <h3>Add DOTD Scoring Trigger</h3>
    <div class="grid-2">
      <div class="field"><label>Trigger Name</label><input type="text" id="st-new-name" placeholder="e.g. clean_overtake"></div>
      <div class="field"><label>Points</label><input type="number" id="st-new-pts" value="10" step="5"></div>
      <div class="field"><label>Min Interval (s)</label><input type="number" id="st-new-int" value="1" step="0.5"></div>
      <div class="field"><label>Cap Per Race (0=unlimited)</label><input type="number" id="st-new-cap" value="0"></div>
    </div>
    <div style="margin-top:8px">
      <label style="display:flex;align-items:center;gap:6px;font-size:12px;text-transform:none;letter-spacing:0;cursor:pointer">
        <input type="checkbox" id="st-new-perlap"> Score per lap
      </label>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="stCreateTrigger()">Add Trigger</button>
    </div>`;
  showModal(html);
};

window.stCreateTrigger = function() {
  const name = document.getElementById("st-new-name").value.trim().replace(/\s+/g, "_").toLowerCase();
  if (!name) { toast("Name required"); return; }
  const dotd = CONFIG.dotd || {};
  dotd.triggers = dotd.triggers || {};
  if (dotd.triggers[name] || (dotd.disabled_triggers || {})[name]) { toast("Trigger already exists"); return; }
  dotd.triggers[name] = {
    points: parseFloat(document.getElementById("st-new-pts").value) || 0,
    min_interval_s: parseFloat(document.getElementById("st-new-int").value) || 0,
    cap_per_race: parseInt(document.getElementById("st-new-cap").value) || undefined,
    per_lap: document.getElementById("st-new-perlap").checked || undefined,
  };
  closeModal();
  populateStewards();
  toast(`${name.replace(/_/g, " ")} added`);
};

window.saveStewards = async function() {
  // Walk every rendered card and build a complete replacement payload.
  // We send the whole dotd section with ?replace=true so removals, 0
  // values, and unchecked flags actually persist — the default merge
  // semantics silently kept old values for any field we omitted.
  //
  // Unmanaged per-trigger fields (_comment, diminishing_window_s, etc.)
  // are preserved by reading from CONFIG.dotd and overlaying the UI-
  // controlled fields on top.
  const existingActive = (CONFIG.dotd && CONFIG.dotd.triggers) || {};
  const existingDisabled = (CONFIG.dotd && CONFIG.dotd.disabled_triggers) || {};

  const triggers = {};
  const disabledTriggers = {};

  document.querySelectorAll(".st-trigger-card").forEach(card => {
    const name = card.dataset.st;
    const isActive = card.dataset.active === "true";
    const base = existingActive[name] || existingDisabled[name] || {};

    // Start from the prior definition so fields like `_comment` or
    // `diminishing_window_s` survive the save. Then overlay every
    // UI-controlled field explicitly, including falsy values.
    const data = Object.assign({}, base);
    data.points = parseFloat(card.querySelector(".st-pts").value) || 0;
    data.min_interval_s = parseFloat(card.querySelector(".st-int").value) || 0;

    const capVal = parseInt(card.querySelector(".st-cap-race").value);
    if (capVal > 0) data.cap_per_race = capVal;
    else delete data.cap_per_race;

    const perLapEl = card.querySelector(".st-perlap");
    if (perLapEl && perLapEl.checked) data.per_lap = true;
    else delete data.per_lap;

    if (isActive) triggers[name] = data;
    else disabledTriggers[name] = data;
  });

  const dimToggle = document.getElementById("st-dim-toggle");
  const payload = {
    triggers,
    disabled_triggers: disabledTriggers,
    cap_per_lap: parseInt(document.getElementById("st-cap").value),
    diminishing: {
      enabled: dimToggle ? dimToggle.classList.contains("on") : true,
      decay_steps: document.getElementById("st-decay").value
        .split(",").map(v => parseFloat(v.trim())).filter(v => !isNaN(v)),
    },
  };

  await api("/api/config/dotd?replace=true", "PUT", payload);

  // Mirror the saved state into the in-memory CONFIG so subsequent UI
  // interactions (re-renders, navigations, further saves) see the same
  // thing the file now holds.
  CONFIG.dotd = payload;

  // Re-render the cards so each header badge reflects the new points
  // value (the badge is baked into the card HTML at render time and
  // doesn't track slider changes on its own).
  populateStewards();

  toast("Stewards' Office config saved");
};

// ─── MARSHALLING ─────────────────────────────────────────────────
function populateMarshalling() {
  const m = CONFIG.marshalling || {};
  const sb = m.sector_boundaries_range || {};
  const col = m.collision || {};
  const ot = m.offtrack || {};

  const maSliders = [
    ["ma-s1-lo", (sb.s1 || [0.30])[0]],
    ["ma-s1-hi", (sb.s1 || [0, 0.36])[1]],
    ["ma-s2-lo", (sb.s2 || [0.63])[0]],
    ["ma-s2-hi", (sb.s2 || [0, 0.69])[1]],
    ["ma-col-cd", col.cooldown_seconds || 30],
    ["ma-col-yaw", col.compound_yaw_threshold || 2.0],
    ["ma-col-ryaw", col.recovery_yaw_threshold || 3.0],
    ["ma-ot-cd", ot.cooldown_seconds || 30],
    ["ma-ot-yaw", ot.spin_yaw_threshold || 5.0],
    ["ma-ot-rpm", ot.spin_rpm_threshold || 500],
    ["ma-ot-multi", ot.multi_car_threshold || 3],
  ];
  maSliders.forEach(([id, val]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = val;
    const valEl = document.getElementById(id + "-val");
    const step = parseFloat(el.step) || 1;
    const suffix = id.includes("-cd") ? "s" : "";
    if (valEl) valEl.textContent = (step < 1 ? parseFloat(val).toFixed(2) : val) + suffix;
    el.addEventListener("input", function() {
      if (valEl) valEl.textContent = (step < 1 ? parseFloat(this.value).toFixed(2) : this.value) + suffix;
    });
  });
}

window.saveMarshalling = async function() {
  await api("/api/config/marshalling", "PUT", {
    sector_boundaries_range: {
      s1: [parseFloat(document.getElementById("ma-s1-lo").value), parseFloat(document.getElementById("ma-s1-hi").value)],
      s2: [parseFloat(document.getElementById("ma-s2-lo").value), parseFloat(document.getElementById("ma-s2-hi").value)],
    },
    collision: {
      cooldown_seconds: parseInt(document.getElementById("ma-col-cd").value),
      compound_yaw_threshold: parseFloat(document.getElementById("ma-col-yaw").value),
      recovery_yaw_threshold: parseFloat(document.getElementById("ma-col-ryaw").value),
    },
    offtrack: {
      cooldown_seconds: parseInt(document.getElementById("ma-ot-cd").value),
      spin_yaw_threshold: parseFloat(document.getElementById("ma-ot-yaw").value),
      spin_rpm_threshold: parseInt(document.getElementById("ma-ot-rpm").value),
      multi_car_threshold: parseInt(document.getElementById("ma-ot-multi").value),
    }
  });
  toast("Marshalling config saved");
};

// ─── POWER UNIT ──────────────────────────────────────────────────
function _puHighlightProvider() {
  const prov = document.getElementById("pu-provider").value;
  const oCard = document.getElementById("pu-openai-card");
  const lCard = document.getElementById("pu-local-card");
  if (oCard) {
    oCard.classList.toggle("card-accent", prov === "openai");
    oCard.style.opacity = prov === "openai" ? "1" : "0.5";
  }
  if (lCard) {
    lCard.classList.toggle("card-accent", prov === "local_llm");
    lCard.style.opacity = prov === "local_llm" ? "1" : "0.5";
  }
}

function populatePowerUnit() {
  const pu = CONFIG.power_unit || {};
  const ai = pu.openai || {};
  const loc = pu.local_llm || {};
  const tts = pu.inworld || {};
  const ir = pu.iracing_api || {};

  // Provider toggle
  document.getElementById("pu-provider").value = pu.provider || "openai";
  document.getElementById("pu-provider").addEventListener("change", _puHighlightProvider);

  // OpenAI fields
  document.getElementById("pu-model").value = ai.model || "gpt-4o-mini";

  // Local LLM fields
  document.getElementById("pu-local-url").value = loc.base_url || "http://192.168.1.100:1234/v1";
  document.getElementById("pu-local-key").value = loc.api_key || "not-needed";
  document.getElementById("pu-local-model").value = loc.model || "local-model";

  // TTS fields
  document.getElementById("pu-tts-model").value = tts.model_id || "inworld-tts-1.5-mini";
  document.getElementById("pu-tts-enc").value = tts.audio_encoding || "MP3";

  // iRacing /data API fields
  const irEnabledEl = document.getElementById("pu-ir-enabled");
  if (irEnabledEl) irEnabledEl.checked = !!ir.enabled;

  const puSliders = [
    ["pu-temp", ai.temperature || 0.5, 2],
    ["pu-tokens", ai.max_tokens || 250, 0],
    ["pu-local-temp", loc.temperature || 0.5, 2],
    ["pu-local-tokens", loc.max_tokens || 250, 0],
    ["pu-tts-temp", tts.temperature || 0.79, 2],
    ["pu-tts-rate", tts.default_speaking_rate || 1.3, 1],
    ["pu-ir-ttl", ir.cache_ttl_hours || 24, 0],
  ];
  puSliders.forEach(([id, val, decimals]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = val;
    const valEl = document.getElementById(id + "-val");
    if (valEl) valEl.textContent = parseFloat(val).toFixed(decimals);
    el.addEventListener("input", function() {
      if (valEl) valEl.textContent = parseFloat(this.value).toFixed(decimals);
    });
  });

  _puHighlightProvider();
  refreshIRacingStatus();
}

// ── iRacing /data API helpers ───────────────────────────────────
async function refreshIRacingStatus() {
  const dot = document.getElementById("pu-ir-dot");
  const txt = document.getElementById("pu-ir-status");
  if (!dot || !txt) return;
  try {
    const s = await api("/api/iracing/status");
    let color = "#666", label = "disabled";
    if (s.auth_locked_out) { color = "#e74c3c"; label = "auth lockout — fix creds & reset"; }
    else if (s.available)  { color = "#2ecc71"; label = "ready"; }
    else if (s.enabled && !s.creds_present) { color = "#f39c12"; label = "enabled, no credentials"; }
    else if (s.enabled)    { color = "#f39c12"; label = "enabled"; }
    dot.style.background = color;
    txt.textContent = label;
  } catch (e) {
    dot.style.background = "#666";
    txt.textContent = "status unavailable";
  }
}

window.testIRacing = async function() {
  try {
    const res = await fetch("/api/iracing/test");
    const body = await res.json();
    if (body.ok) {
      toast(`iRacing OK — ${body.categories_count} categories returned`);
    } else {
      toast(`iRacing test failed: ${body.error || "unknown"}`);
    }
  } catch (e) {
    toast(`iRacing test error: ${e}`);
  }
  refreshIRacingStatus();
};

window.resetIRacingLockout = async function() {
  try {
    await api("/api/iracing/reset_lockout", "POST", {});
    toast("Lockout cleared");
  } catch (e) {
    toast(`Reset failed: ${e}`);
  }
  refreshIRacingStatus();
};

window.savePowerUnit = async function() {
  await api("/api/config/power_unit", "PUT", {
    provider: document.getElementById("pu-provider").value,
    openai: {
      model: document.getElementById("pu-model").value,
      temperature: parseFloat(document.getElementById("pu-temp").value),
      max_tokens: parseInt(document.getElementById("pu-tokens").value),
    },
    local_llm: {
      base_url: document.getElementById("pu-local-url").value,
      api_key: document.getElementById("pu-local-key").value || "not-needed",
      model: document.getElementById("pu-local-model").value,
      temperature: parseFloat(document.getElementById("pu-local-temp").value),
      max_tokens: parseInt(document.getElementById("pu-local-tokens").value),
    },
    inworld: {
      model_id: document.getElementById("pu-tts-model").value,
      audio_encoding: document.getElementById("pu-tts-enc").value,
      temperature: parseFloat(document.getElementById("pu-tts-temp").value),
      default_speaking_rate: parseFloat(document.getElementById("pu-tts-rate").value),
    },
    iracing_api: {
      enabled: document.getElementById("pu-ir-enabled").checked,
      cache_ttl_hours: parseInt(document.getElementById("pu-ir-ttl").value),
    }
  });
  toast("Power Unit config saved");
  refreshIRacingStatus();
};

// ─── RACE ARCHIVES ───────────────────────────────────────────────
const _raF1Colors = [
  '#2ecc71', '#00d2be', '#3671C6', '#FF8000', '#0093cc',
  '#64C4FF', '#00e701', '#B6BABD', '#6692FF', '#279f57',
];
const _raStandingsSortTypes = {
  finish_position: "number",
  driver_name: "text",
  car_number: "number",
  grid_position: "number",
  best_lap: "number",
  laps_led: "number",
  dotd_percent: "number",
  incidents: "number",
};
let _raStandingsRows = [];
let _raStandingsSort = { key: "finish_position", dir: "asc" };

function _raSortValue(row, key) {
  const value = row ? row[key] : null;
  if (_raStandingsSortTypes[key] === "text") {
    return String(value || "").toLowerCase();
  }
  if (key === "car_number") {
    const parsed = parseInt(String(value || "").replace(/[^\d-]/g, ""), 10);
    return Number.isFinite(parsed) ? parsed : null;
  }
  const num = Number(value);
  if ((key === "best_lap" || key === "finish_position" || key === "grid_position") && num <= 0) {
    return null;
  }
  return Number.isFinite(num) ? num : null;
}

function _raCompareStandings(a, b, key, dir) {
  const av = _raSortValue(a, key);
  const bv = _raSortValue(b, key);
  if (av == null && bv == null) return (a.finish_position || 9999) - (b.finish_position || 9999);
  if (av == null) return 1;
  if (bv == null) return -1;
  let cmp = 0;
  if (_raStandingsSortTypes[key] === "text") {
    cmp = av.localeCompare(bv);
  } else {
    cmp = av - bv;
  }
  if (cmp === 0) cmp = (a.finish_position || 9999) - (b.finish_position || 9999);
  return dir === "asc" ? cmp : -cmp;
}

function _raRenderStandings() {
  const body = document.getElementById("ra-standings-body");
  if (!body) return;
  const { key, dir } = _raStandingsSort;
  const drivers = [..._raStandingsRows].sort((a, b) => _raCompareStandings(a, b, key, dir));
  body.innerHTML = drivers.map(d => `
    <tr${d.is_owner ? ' style="background:rgba(46,204,113,0.08)"' : ''}>
      <td class="num">${d.finish_position || "—"}</td>
      <td style="font-weight:600">${d.driver_name || "—"}</td>
      <td>${d.car_number || ""}</td>
      <td>${d.grid_position || "—"}</td>
      <td>${fmtLapTime(d.best_lap)}</td>
      <td>${d.laps_led || 0}</td>
      <td>${(d.dotd_percent || 0).toFixed(1)}%</td>
      <td>${d.incidents || 0}</td>
    </tr>
  `).join("");

  document.querySelectorAll("#ra-standings th[data-ra-sort]").forEach(th => {
    const active = th.dataset.raSort === key;
    th.setAttribute("aria-sort", active ? (dir === "asc" ? "ascending" : "descending") : "none");
    const mark = th.querySelector(".ra-sort-mark");
    if (mark) mark.textContent = active ? (dir === "asc" ? "▲" : "▼") : "";
  });
}

window.raSortStandings = function(key) {
  if (!_raStandingsSortTypes[key]) return;
  if (_raStandingsSort.key === key) {
    _raStandingsSort.dir = _raStandingsSort.dir === "asc" ? "desc" : "asc";
  } else {
    _raStandingsSort = { key, dir: "asc" };
  }
  _raRenderStandings();
};

function _raFocusDriverName() {
  const drivers = CONFIG.owner_drivers || {};
  const entries = Object.entries(drivers);
  const active = entries.find(([, d]) => d && d.is_active);
  const picked = active || entries[0];
  if (!picked) return "";
  const [key, data] = picked;
  return (data && (data.display_name || data.name)) || key || "";
}

function _raCareerTrackLabel(name) {
  const clean = String(name || "Unknown Track")
    .replace("Autodromo Internazionale Enzo e Dino Ferrari", "Imola")
    .replace("Nürburgring", "Nurburgring")
    .replace("Nordschleife Industriefahrten", "Nordschleife")
    .replace("Grand Prix No Chicane", "GP No Chicane")
    .replace("Grand-Prix-Strecke", "GP-Strecke");
  return clean.length > 28 ? clean.slice(0, 25) + "..." : clean;
}

function _raCareerDateLabel(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function _raCareerKpi(label, value, sub = "") {
  return `
    <div class="ra-career-kpi">
      <div class="ra-career-kpi-value">${htmlEsc(value)}</div>
      <div class="ra-career-kpi-label">${htmlEsc(label)}</div>
      ${sub ? `<div class="ra-career-kpi-sub">${htmlEsc(sub)}</div>` : ""}
    </div>
  `;
}

function _raCareerPositionChart(trend) {
  const rows = (trend || []).filter(r => r && r.finish_position);
  if (!rows.length) return '<div class="ra-career-empty">No finish-position history yet.</div>';
  const width = 760;
  const height = 270;
  const pad = { left: 44, right: 18, top: 18, bottom: 42 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const maxPos = Math.max(10, ...rows.map(r => Math.max(r.car_count || 0, r.grid_position || 0, r.finish_position || 0)));
  const xAt = i => rows.length === 1 ? pad.left + plotW / 2 : pad.left + (plotW * i / (rows.length - 1));
  const yAt = pos => pad.top + ((Math.max(1, Math.min(maxPos, pos)) - 1) / Math.max(1, maxPos - 1)) * plotH;
  const pathFor = key => rows
    .filter(r => r[key])
    .map(r => `${xAt(rows.indexOf(r)).toFixed(1)},${yAt(r[key]).toFixed(1)}`)
    .map((pt, i) => `${i ? "L" : "M"}${pt}`)
    .join(" ");
  const axis = [1, Math.ceil(maxPos / 2), maxPos].map(pos => {
    const y = yAt(pos);
    return `<g><line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" class="ra-career-gridline"/><text x="10" y="${y + 4}" class="ra-career-axis">P${pos}</text></g>`;
  }).join("");
  const markers = rows.map((r, i) => {
    const x = xAt(i);
    const y = yAt(r.finish_position);
    const gainClass = (r.position_delta || 0) >= 0 ? "positive" : "negative";
    return `
      <g class="ra-career-point" style="animation-delay:${(i * 60 + 600)}ms">
        <circle cx="${x}" cy="${y}" r="4.5" class="${gainClass}"></circle>
        <title>Race ${r.race_id}: ${htmlEsc(_raCareerTrackLabel(r.track_name))} - Grid ${fmtPos(r.grid_position)}, Finish ${fmtPos(r.finish_position)}</title>
      </g>
    `;
  }).join("");
  const raceTicks = rows.map((r, i) => {
    const x = xAt(i);
    return `<text x="${x}" y="${height - 14}" class="ra-career-tick">#${r.race_id}</text>`;
  }).join("");
  return `
    <svg class="ra-career-chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Focus Driver finish trend">
      ${axis}
      <path d="${pathFor("grid_position")}" class="ra-career-line grid" pathLength="1"></path>
      <path d="${pathFor("finish_position")}" class="ra-career-line finish" pathLength="1"></path>
      ${markers}
      ${raceTicks}
      <g class="ra-career-legend">
        <circle cx="${width - 148}" cy="18" r="4" class="finish-dot"></circle><text x="${width - 138}" y="22">Finish</text>
        <circle cx="${width - 74}" cy="18" r="4" class="grid-dot"></circle><text x="${width - 64}" y="22">Grid</text>
      </g>
    </svg>
  `;
}

function _raCareerSlopeChart(trend) {
  const rows = (trend || []).filter(r => r && r.grid_position && r.finish_position).slice(-10);
  if (!rows.length) return '<div class="ra-career-empty">No grid-to-finish data yet.</div>';
  const width = 760;
  const height = 230;
  const pad = { left: 38, right: 18, top: 18, bottom: 38 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const maxPos = Math.max(10, ...rows.map(r => Math.max(r.car_count || 0, r.grid_position || 0, r.finish_position || 0)));
  const xAt = i => rows.length === 1 ? pad.left + plotW / 2 : pad.left + (plotW * i / (rows.length - 1));
  const yAt = pos => pad.top + ((Math.max(1, Math.min(maxPos, pos)) - 1) / Math.max(1, maxPos - 1)) * plotH;
  const pairs = rows.map((r, i) => {
    const x = xAt(i);
    const y1 = yAt(r.grid_position);
    const y2 = yAt(r.finish_position);
    const delta = r.position_delta || 0;
    const cls = delta > 0 ? "gain" : (delta < 0 ? "loss" : "even");
    return `
      <g class="ra-career-slope" style="animation-delay:${i * 70}ms">
        <line x1="${x}" y1="${y1}" x2="${x}" y2="${y2}" class="${cls}"></line>
        <circle cx="${x}" cy="${y1}" r="4" class="grid"></circle>
        <circle cx="${x}" cy="${y2}" r="5" class="${cls}"></circle>
        <text x="${x}" y="${height - 14}" class="ra-career-tick">#${r.race_id}</text>
        <text x="${x}" y="${Math.min(y1, y2) - 7}" class="ra-career-delta ${cls}">${fmtSigned(delta)}</text>
      </g>
    `;
  }).join("");
  return `
    <svg class="ra-career-chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Focus Driver grid to finish change">
      <line x1="${pad.left}" y1="${yAt(1)}" x2="${width - pad.right}" y2="${yAt(1)}" class="ra-career-gridline"></line>
      <line x1="${pad.left}" y1="${yAt(maxPos)}" x2="${width - pad.right}" y2="${yAt(maxPos)}" class="ra-career-gridline"></line>
      <text x="10" y="${yAt(1) + 4}" class="ra-career-axis">P1</text>
      <text x="10" y="${yAt(maxPos) + 4}" class="ra-career-axis">P${maxPos}</text>
      ${pairs}
    </svg>
  `;
}

function _raCareerBarPanel(title, rows, key, formatter, className) {
  const visible = (rows || []).slice(-8);
  if (!visible.length) return '<div class="ra-career-empty">No race data yet.</div>';
  const max = Math.max(1, ...visible.map(r => Math.abs(Number(r[key]) || 0)));
  return `
    <div class="ra-career-bar-panel">
      <div class="ra-career-panel-title">${htmlEsc(title)}</div>
      ${visible.map((r, i) => {
        const value = Number(r[key]) || 0;
        const w = Math.max(2, Math.min(100, Math.abs(value) / max * 100));
        return `
          <div class="ra-career-bar-row" style="--delay:${i * 65}ms">
            <span>#${r.race_id}</span>
            <div class="ra-career-bar-track"><i class="${className}" style="--w:${w}%"></i></div>
            <strong>${htmlEsc(formatter(value))}</strong>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function _raRenderFocusCareerReplay(host, data) {
  const summary = data.summary || {};
  const trend = data.trend || [];
  const latest = trend[trend.length - 1] || {};
  const tracks = (data.tracks || []).slice(0, 4);
  const moments = data.moments || [];
  const iratingValue = summary.latest_irating
    ? `${summary.latest_irating}${Number.isFinite(summary.irating_delta) ? " (" + fmtSigned(summary.irating_delta) + ")" : ""}`
    : "—";

  host.classList.remove("is-ready");
  host.innerHTML = `
    <div class="ra-career-hero">
      <div>
        <div class="ra-career-eyebrow">Focus Driver</div>
        <div class="ra-career-driver">${htmlEsc(data.driver_name || "Focus Driver")}</div>
        <div class="ra-career-latest">
          ${latest.race_id ? `Latest: Race ${latest.race_id} at ${htmlEsc(_raCareerTrackLabel(latest.track_name))} - ${fmtPos(latest.finish_position)} from ${fmtPos(latest.grid_position)}` : ""}
        </div>
      </div>
      <div class="ra-career-record">
        <span>${summary.wins || 0}</span> wins
        <b>${summary.podiums || 0}</b> podiums
        <b>${summary.dotd_wins || 0}</b> DOTD
      </div>
    </div>

    <div class="ra-career-kpi-grid">
      ${_raCareerKpi("Races", summary.races || 0, `${summary.sample_races || 0} with lap samples`)}
      ${_raCareerKpi("Best Finish", fmtPos(summary.best_finish), `${fmtOne(summary.avg_finish)} avg finish`)}
      ${_raCareerKpi("Net Positions", fmtSigned(summary.net_positions || 0), `${summary.comeback_races || 0} comeback races`)}
      ${_raCareerKpi("Clean Races", summary.clean_races || 0, `${summary.total_incidents || 0} total incidents`)}
      ${_raCareerKpi("DOTD Peak", `${fmtOne(summary.best_dotd_percent || 0)}%`, `${summary.dotd_wins || 0} wins`)}
      ${_raCareerKpi("iRating", iratingValue, "latest archive value")}
    </div>

    <div class="ra-career-chart-card wide">
      <div class="ra-career-panel-title">Finish Form</div>
      ${_raCareerPositionChart(trend)}
    </div>

    <div class="ra-career-chart-grid">
      <div class="ra-career-chart-card">
        <div class="ra-career-panel-title">Grid To Finish</div>
        ${_raCareerSlopeChart(trend)}
      </div>
      <div class="ra-career-chart-card">
        ${_raCareerBarPanel("DOTD Vote", trend, "dotd_percent", v => `${v.toFixed(1)}%`, "dotd")}
        ${_raCareerBarPanel("Incidents", trend, "incidents", v => `${Math.round(v)}x`, "incidents")}
      </div>
    </div>

    <div class="ra-career-lower-grid">
      <div class="ra-career-chart-card">
        <div class="ra-career-panel-title">Best Tracks</div>
        <div class="ra-career-track-grid">
          ${tracks.map(t => `
            <div class="ra-career-track-card">
              <div class="ra-career-track-name" title="${htmlEsc(t.track_name)}">${htmlEsc(_raCareerTrackLabel(t.track_name))}</div>
              <div class="ra-career-track-stats">
                <span>${t.races} races</span>
                <span>${fmtPos(t.best_finish)}</span>
                <span>${fmtOne(t.avg_finish)} avg</span>
                <span>${fmtSigned(Math.round(t.avg_gain || 0))} avg gain</span>
              </div>
            </div>
          `).join("") || '<div class="ra-career-empty">No track history yet.</div>'}
        </div>
      </div>
      <div class="ra-career-chart-card">
        <div class="ra-career-panel-title">Career Moments</div>
        <div class="ra-career-moments">
          ${moments.map(m => `
            <div class="ra-career-moment">
              <div>
                <span>${htmlEsc(m.label)}</span>
                <strong>${htmlEsc(m.value)}</strong>
              </div>
              <a href="/replay/${m.race_id}" target="_blank" rel="noopener">Race ${m.race_id}</a>
              <small>${htmlEsc(_raCareerTrackLabel(m.track_name))}</small>
            </div>
          `).join("") || '<div class="ra-career-empty">No moments yet.</div>'}
        </div>
      </div>
    </div>
  `;
  requestAnimationFrame(() => host.classList.add("is-ready"));
}

async function _raLoadFocusDriverCareer() {
  const host = document.getElementById("ra-career-replay");
  if (!host) return;
  const name = _raFocusDriverName();
  if (!name) {
    host.innerHTML = '<div class="ra-career-empty">No active Focus Driver configured.</div>';
    return;
  }
  try {
    host.innerHTML = '<div class="ra-career-empty">Loading Focus Driver stats...</div>';
    const d = await api(`/api/history/drivers/${encodeURIComponent(name)}/career-replay`);
    if (!d || d.error) {
      host.innerHTML = `<div class="ra-career-empty">No career stats yet for ${htmlEsc(name)}.</div>`;
      return;
    }
    _raRenderFocusCareerReplay(host, d);
  } catch (e) {
    host.innerHTML = `<div class="ra-career-empty">No career stats yet for ${htmlEsc(name)}.</div>`;
  }
}

// ─── iRacing eventresult import ─────────────────────────────────

window.raImportFileChanged = function() {
  const input = document.getElementById("ra-import-file");
  const label = document.getElementById("ra-import-filename");
  const btn = document.getElementById("ra-import-btn");
  const file = input.files && input.files[0];
  if (file) {
    label.textContent = `${file.name} (${Math.round(file.size / 1024)} KB)`;
    btn.disabled = false;
  } else {
    label.textContent = "";
    btn.disabled = true;
  }
  // Clear any prior status when a new file is picked
  const status = document.getElementById("ra-import-status");
  status.style.display = "none";
  status.textContent = "";
  status.className = "ra-import-status";
};

window.raImportEventresult = async function() {
  const input = document.getElementById("ra-import-file");
  const btn = document.getElementById("ra-import-btn");
  const status = document.getElementById("ra-import-status");
  const forceNew = document.getElementById("ra-import-force-new").checked;
  const file = input.files && input.files[0];
  if (!file) return;

  btn.disabled = true;
  status.style.display = "block";
  status.className = "ra-import-status pending";
  status.textContent = `Reading ${file.name}…`;

  let payload;
  try {
    const text = await file.text();
    payload = JSON.parse(text);
  } catch (e) {
    status.className = "ra-import-status error";
    status.textContent = `Could not parse JSON: ${e.message}`;
    btn.disabled = false;
    return;
  }

  status.textContent = "Importing…";
  try {
    const url = forceNew ? "/api/replay/import?force_new=1" : "/api/replay/import";
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      status.className = "ra-import-status error";
      status.textContent = data.error || `HTTP ${res.status}`;
      btn.disabled = false;
      return;
    }
    status.className = "ra-import-status success";
    const trackBits = data.track_config ? `${data.track_name} — ${data.track_config}` : data.track_name;
    const verb = data.merged ? "Merged into" : "Imported as new race";
    let line = `${verb} race #${data.race_id}: ${trackBits} — ${data.drivers_updated} of ${data.drivers_count} driver rows enriched`;
    if (data.unmatched && data.unmatched.length) {
      line += ` · ${data.unmatched.length} unmatched: ${data.unmatched.slice(0, 5).join(", ")}`;
      if (data.unmatched.length > 5) line += ` +${data.unmatched.length - 5} more`;
    }
    status.textContent = line;
    toast(data.merged ? "Race merged" : "Race imported");
    // Reset picker, refresh list
    input.value = "";
    document.getElementById("ra-import-filename").textContent = "";
    document.getElementById("ra-import-force-new").checked = false;
    btn.disabled = true;
    loadHistory();
  } catch (e) {
    status.className = "ra-import-status error";
    status.textContent = `Import failed: ${e.message || e}`;
    btn.disabled = false;
  }
};

async function loadHistory() {
  try {
    const races = await api("/api/history/races");
    const tbody = document.getElementById("ra-races");
    const empty = document.getElementById("ra-empty");

    if (!races || races.length === 0) { empty.style.display = "block"; tbody.innerHTML = ""; return; }
    empty.style.display = "none";

    tbody.innerHTML = races.map(r => {
      const dur = r.duration_s ? `${Math.floor(r.duration_s/60)}m` : "-";
      return `
        <tr class="ra-race-row" onclick="raOpenDetail(${r.race_id})">
          <td>${r.race_date ? new Date(r.race_date).toLocaleDateString() : "-"}</td>
          <td>${r.track_name || "-"}</td>
          <td>${r.winner_name || "-"}</td>
          <td>${r.dotd_name || "-"}</td>
          <td class="num">${r.owner_finish ? "P" + r.owner_finish : "-"}</td>
          <td>${r.car_count || "-"}</td>
          <td>${dur}</td>
          <td><span class="ra-race-view-btn">View</span></td>
        </tr>`;
    }).join("");

    await _raLoadFocusDriverCareer();
  } catch (e) { console.warn("History load:", e); }
}

// ─── Race Detail View ────────────────────────────────────────────

window.raOpenDetail = async function(raceId) {
  try {
    const detail = await api(`/api/history/races/${raceId}`);
    if (!detail || !detail.race) { toast("Race not found"); return; }

    // Hide list, show detail
    document.getElementById("ra-list-card").style.display = "none";
    document.getElementById("ra-careers-card").style.display = "none";
    document.getElementById("ra-detail").style.display = "";

    const r = detail.race;
    const dur = r.duration_s ? `${Math.floor(r.duration_s/60)}m ${Math.floor(r.duration_s%60)}s` : "—";
    document.getElementById("ra-detail-title").textContent = `${r.track_name || "Race"} — ${r.race_date ? new Date(r.race_date).toLocaleDateString() : ""}`;

    // Summary stats
    document.getElementById("ra-summary-stats").innerHTML = `
      <div class="stat-card"><div class="stat-value">${r.winner_name || "—"}</div><div class="stat-label">Winner</div></div>
      <div class="stat-card"><div class="stat-value">${r.dotd_name || "—"}</div><div class="stat-label">DOTD</div></div>
      <div class="stat-card"><div class="stat-value">${r.owner_finish ? "P"+r.owner_finish : "—"}</div><div class="stat-label">Your Finish</div></div>
      <div class="stat-card"><div class="stat-value">${r.car_count || "—"}</div><div class="stat-label">Cars</div></div>
      <div class="stat-card"><div class="stat-value">${r.total_laps || "—"}</div><div class="stat-label">Laps</div></div>
      <div class="stat-card"><div class="stat-value">${dur}</div><div class="stat-label">Duration</div></div>
    `;

    // Final standings
    _raStandingsRows = detail.drivers || [];
    _raStandingsSort = { key: "finish_position", dir: "asc" };
    _raRenderStandings();

    // Commentary replay — radio style cards
    const commentary = detail.commentary || [];
    if (commentary.length === 0) {
      document.getElementById("ra-commentary-feed").innerHTML = '<div style="color:var(--text-dim);padding:16px">No commentary logged for this race</div>';
    } else {
      document.getElementById("ra-commentary-feed").innerHTML = commentary.map(c => {
        const color = _raF1Colors[Math.floor(Math.random() * _raF1Colors.length)];
        const time = c.timestamp_s ? `${Math.floor(c.timestamp_s/60)}:${String(Math.floor(c.timestamp_s%60)).padStart(2,'0')}` : "";
        return `
          <div class="ra-commentary-card" style="--radio-color:${color}">
            <div class="ra-commentary-header">
              <span class="ra-commentary-type">${(c.trigger_type || "").replace(/_/g, " ")}</span>
              <span class="ra-commentary-time">${time}</span>
            </div>
            <div class="ra-commentary-caster">${c.commentator || ""} ${c.location ? "@ " + c.location : ""}</div>
            <div class="ra-commentary-text">${c.ai_text || c.prompt_text || ""}</div>
          </div>`;
      }).join("");
    }

    // Incidents timeline
    const incidents = detail.incidents || [];
    if (incidents.length === 0) {
      document.getElementById("ra-incidents-feed").innerHTML = '<div style="color:var(--text-dim);padding:16px">No incidents recorded</div>';
    } else {
      document.getElementById("ra-incidents-feed").innerHTML = incidents.map(inc => {
        const time = inc.timestamp_s ? `${Math.floor(inc.timestamp_s/60)}:${String(Math.floor(inc.timestamp_s%60)).padStart(2,'0')}` : "";
        return `
          <div class="ra-incident-card">
            <span class="ra-incident-time">${time}</span>
            <span class="ra-incident-type">${(inc.incident_type || "").replace(/_/g, " ")}</span>
            <span class="ra-incident-desc">${inc.car_name || ""}${inc.other_car_name ? " vs " + inc.other_car_name : ""} ${inc.description || ""}</span>
          </div>`;
      }).join("");
    }

    // Show standings tab by default
    raTab(document.querySelector('.ra-tab.active'), 'standings');
  } catch (e) {
    console.warn("Race detail:", e);
    toast("Failed to load race detail");
  }
};

window.raCloseDetail = function() {
  document.getElementById("ra-detail").style.display = "none";
  document.getElementById("ra-list-card").style.display = "";
  document.getElementById("ra-careers-card").style.display = "";
};

window.raTab = function(btn, tabName) {
  document.querySelectorAll('.ra-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.ra-tab-content').forEach(c => c.style.display = 'none');
  document.getElementById('ra-' + tabName).style.display = '';
};

// ─── SAVE CONFIG (Race Room) ─────────────────────────────────────
window.saveConfig = async function() {
  // Rotation
  const rotSelects = document.querySelectorAll(".rr-rot-q");
  const rotation = Array.from(rotSelects).map(s => s.value);

  await api("/api/config/commentary_team", "PUT", { host_rotation_quarters: rotation });

  // Location assignments — update each commentator's location
  const locSelects = document.querySelectorAll(".rr-loc-assign");
  for (const sel of locSelects) {
    const loc = sel.dataset.loc;
    const name = sel.value;
    if (name) {
      await api(`/api/commentators/${encodeURIComponent(name)}`, "PUT", { location: loc });
    }
  }

  // System — only include audio fields when the dropdown actually has
  // a selection, so a save that was meant for unrelated toggles (TTS,
  // ambient) never collapses audio_output_index to 0.
  const rrAudioSel = document.getElementById("rr-audio-device");
  const rrAudioOpt = rrAudioSel && rrAudioSel.selectedOptions[0];
  const rrAudioRaw = rrAudioSel ? rrAudioSel.value : "";
  const rrAudioIdx = rrAudioRaw === "" ? null : parseInt(rrAudioRaw, 10);
  const rrAudioName = rrAudioOpt ? (rrAudioOpt.textContent || "").replace(/^\d+:\s*/, "").trim() : "";
  const sysPayload = {
    enable_tts: document.getElementById("rr-tts").checked,
    ambient_volume: parseFloat(document.getElementById("rr-ambient-vol").value),
  };
  if (Number.isInteger(rrAudioIdx)) {
    sysPayload.audio_output_index = rrAudioIdx;
    if (rrAudioName) sysPayload.audio_output_name = rrAudioName;
  }
  await api("/api/config/system", "PUT", sysPayload);

  // AI / Power Unit
  await api("/api/config/power_unit", "PUT", {
    openai: {
      model: document.getElementById("rr-ai-model").value,
      temperature: parseFloat(document.getElementById("rr-ai-temp").value),
      max_tokens: parseInt(document.getElementById("rr-max-tokens").value),
    }
  });

  toast("Race Room configuration saved");
};

// ─── Helpers ─────────────────────────────────────────────────────
function setRange(id, value) {
  const el = document.getElementById(id);
  if (el) {
    el.value = value;
    const valEl = document.getElementById(id + "-val");
    if (valEl) valEl.textContent = parseFloat(value).toFixed(el.step < 1 ? 2 : 0);
  }
}
