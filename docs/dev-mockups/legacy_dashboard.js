// ======================================================================
//  SECTOR SAYS WHAT — BROADCAST HUD (V2)
// ======================================================================
"use strict";

/* ================================================================
   FETCH JSON HELPER
================================================================ */
async function fetchJson(path) {
  try {
    const r = await fetch(path + "?t=" + Date.now());
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } catch (err) {
    console.warn("[JSON] Failed:", path, err);
    return null;
  }
}

/* ================================================================
   DOM HOOKS
================================================================ */
const towerBody        = document.getElementById("tower-body");
const towerLapEl       = document.getElementById("tower-lap");
const activeCommentary = document.getElementById("active-commentary");
const commentaryHistory = document.getElementById("commentary-history");
const dotdHeroEl       = document.getElementById("dotd-hero");
const dotdRunnersEl    = document.getElementById("dotd-runners");
const flagGlow         = document.getElementById("flag-glow");
const flagCardEl       = document.getElementById("flag-card");
const rightCol         = document.getElementById("right-col");
const tickerTrack      = document.getElementById("ticker-track");

// Masthead
const mhTrack   = document.getElementById("mh-track");
const mhAir     = document.getElementById("mh-air");
const mhTrk     = document.getElementById("mh-trk");
const mhHum     = document.getElementById("mh-hum");

// Settings
const settingsOverlay = document.getElementById("settings-overlay");
const settingsBtn     = document.getElementById("settings-btn");
const settingsClose   = document.getElementById("settings-close");
const focusSelect     = document.getElementById("focus-driver-select");
const audioSelect     = document.getElementById("audio-device");
const saveBtn         = document.getElementById("settings-save");

// Background
const bgA = document.getElementById("bg-a");
const bgB = document.getElementById("bg-b");

/* ================================================================
   GLOBAL STATE
================================================================ */
let commentatorImages = {};
let raceGrid = [];
let lastStreamEvent = null;
let lastPhotoKey = null;
let lastCommentatorName = "";
let lastCommentatorLocation = "";
let lastPhotoChange = 0;
const PHOTO_COOLDOWN = 8000;
let lastCommentatorKey = "";
let driverImages = {};
let driverPhotos = {};
let driverProfiles = {};  // CarIdx -> {color_id, color_hex, gender, icon, ...}
let focusDriverIdx = null;
let currentFocusDriver = null;
let _ownerDriverImages = {};

/* ================================================================
   BACKGROUND ROTATION
================================================================ */
let backgroundImages = [
  "backgrounds/open_track_day.png",
  "backgrounds/grid_starting.png",
  "backgrounds/pit_lane.png",
  "backgrounds/fanzone.png"
];
let bgIndex = 0;
let activeBg = "a";

async function loadHudBackgrounds() {
  try {
    const res = await fetchJson("/api/media/hud-backgrounds");
    if (res && res.backgrounds && res.backgrounds.length > 0) {
      backgroundImages = res.backgrounds.map(
        p => `/api/media/file?path=${encodeURIComponent(p)}`
      );
    }
  } catch (e) { /* keep defaults */ }
  if (backgroundImages.length > 0 && bgA) {
    bgA.style.backgroundImage = `url('${backgroundImages[0]}')`;
    bgA.style.opacity = "1";
    bgIndex = 1;
  }
}

function rotateBackground() {
  if (backgroundImages.length === 0) return;
  const nextUrl = backgroundImages[bgIndex % backgroundImages.length];
  bgIndex++;
  if (activeBg === "a") {
    bgB.style.backgroundImage = `url('${nextUrl}')`;
    bgB.style.opacity = "1";
    bgA.style.opacity = "0";
    activeBg = "b";
  } else {
    bgA.style.backgroundImage = `url('${nextUrl}')`;
    bgA.style.opacity = "1";
    bgB.style.opacity = "0";
    activeBg = "a";
  }
}

loadHudBackgrounds();
setInterval(rotateBackground, 30000);

/* ================================================================
   FLAG GLOW OVERLAY
================================================================ */
const flagColorMap = {
  green: "green", yellow: "yellow", white: "white",
  blue: "blue", meatball: "yellow", red: "red",
  checkered: "checkered", "safety car": "safety-car",
  "safety_car": "safety-car", caution: "yellow"
};
const allFlagClasses = ["green","yellow","red","blue","white","safety-car","checkered","active"];
let lastFlag = null;

function showFlagGlow(flagRaw) {
  if (!flagGlow || !flagRaw) return;
  const raw = flagRaw.toLowerCase().trim();

  let flag = null;
  for (const [key, cls] of Object.entries(flagColorMap)) {
    if (raw.includes(key)) { flag = cls; break; }
  }

  if (!flag || flag === "none") {
    flagGlow.classList.remove(...allFlagClasses);
    lastFlag = null;
    return;
  }

  if (flag === lastFlag) return;
  lastFlag = flag;

  flagGlow.classList.remove(...allFlagClasses);
  flagGlow.classList.add(flag, "active");
}

/* ================================================================
   UTILS
================================================================ */
function shortName(full) {
  if (!full) return "---";
  const parts = String(full).trim().split(/\s+/);
  return parts[parts.length - 1]
    .replace("[PIT]", "PIT")
    .slice(0, 3)
    .toUpperCase();
}

function lastNameFull(name) {
  if (!name) return "---";
  name = String(name).trim();
  if (name.includes(",")) return name.split(",")[0].trim().toUpperCase();
  const parts = name.split(" ").filter(Boolean);
  return (parts.length > 1 ? parts[parts.length - 1] : parts[0]).toUpperCase();
}

function fmtGap(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n >= 0 ? `+${n.toFixed(3)}` : n.toFixed(3);
}

function fmtLap(v) {
  if (v == null || v === "" || Number(v) <= 0 || isNaN(Number(v))) return "—";
  const secs = Number(v);
  const mins = Math.floor(secs / 60);
  const rem = secs - mins * 60;
  return mins > 0 ? `${mins}:${rem.toFixed(3).padStart(6, "0")}` : rem.toFixed(3);
}

function caseInsensitiveGet(obj, key) {
  if (!obj || !key) return undefined;
  const low = key.toLowerCase();
  for (const k of Object.keys(obj)) {
    if (k.toLowerCase() === low) return obj[k];
  }
  return undefined;
}

/* ================================================================
   ASSET LOADING
================================================================ */
async function loadCommentatorImages() {
  const js = await fetchJson("/sector_said_exports/commentator_images.json");
  if (js) commentatorImages = js;
}
async function loadDriverImages() {
  const js = await fetchJson("/sector_said_exports/driver_images.json");
  if (js) driverImages = js;
}
async function loadDriverPhotos() {
  const js = await fetchJson("/sector_said_exports/driver_photos.json");
  if (js) driverPhotos = js;
}
async function loadOwnerDriverImages() {
  try {
    const drivers = await fetchJson("/api/owner-drivers");
    if (!drivers) return;
    _ownerDriverImages = {};
    for (const [name, d] of Object.entries(drivers)) {
      if (d.profile_image) {
        _ownerDriverImages[d.display_name || name] = d.profile_image;
      }
    }
  } catch (e) { /* silent */ }
}

async function loadDriverProfiles() {
  const js = await fetchJson("/sector_said_exports/driver_profiles.json");
  if (js) driverProfiles = js;
}

/* ================================================================
   DRIVER ICON SVGs — Male / Female / Unknown silhouettes
   Colored with the assigned F1 team color
================================================================ */
function driverIconSvg(gender, colorHex, size = 28) {
  const bg = colorHex || "#555";
  // Male silhouette
  if (gender === "male") {
    return `<svg width="${size}" height="${size}" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="6" fill="${bg}"/>
      <circle cx="20" cy="14" r="7" fill="#fff" opacity="0.9"/>
      <path d="M8 36 Q8 24 20 24 Q32 24 32 36" fill="#fff" opacity="0.9"/>
    </svg>`;
  }
  // Female silhouette
  if (gender === "female") {
    return `<svg width="${size}" height="${size}" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="6" fill="${bg}"/>
      <circle cx="20" cy="13" r="7" fill="#fff" opacity="0.9"/>
      <ellipse cx="20" cy="12" rx="9" ry="4" fill="#fff" opacity="0.4"/>
      <path d="M9 36 Q10 23 20 23 Q30 23 31 36" fill="#fff" opacity="0.9"/>
    </svg>`;
  }
  // Unknown / neutral
  return `<svg width="${size}" height="${size}" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
    <rect width="40" height="40" rx="6" fill="${bg}"/>
    <circle cx="20" cy="14" r="6.5" fill="#fff" opacity="0.9"/>
    <rect x="15" y="10" width="10" height="3" rx="1.5" fill="${bg}" opacity="0.5"/>
    <path d="M9 36 Q9 24 20 24 Q31 24 31 36" fill="#fff" opacity="0.9"/>
  </svg>`;
}

function getDriverProfile(carIdx) {
  return driverProfiles[String(carIdx)] || null;
}

function getDriverColorClass(carIdx) {
  const p = getDriverProfile(carIdx);
  return p ? `tc-${p.color_id}` : "tc-generic";
}

/* ================================================================
   COMMENTATOR PHOTO — returns image URL for a commentator
================================================================ */
function getCommentatorPhotoUrl(name, location) {
  if (!name) return "";
  const person = caseInsensitiveGet(commentatorImages, name);
  if (!person) return "";

  let pool = [];
  const locMatch = caseInsensitiveGet(person, location || "Studio") ||
                   caseInsensitiveGet(person, "Studio");
  if (Array.isArray(locMatch)) {
    pool = locMatch;
  }
  if (!Array.isArray(pool) || pool.length === 0) return "";

  const choice = pool[Math.floor(Math.random() * pool.length)];
  return `commentators/${choice}`;
}

/* ================================================================
   TIMING TOWER — F1 Broadcast Style
================================================================ */
function renderTower(data) {
  if (!towerBody || !data) return;

  const rawCars = Array.isArray(data.cars) ? data.cars :
                  Array.isArray(data.Cars) ? data.Cars : [];
  if (rawCars.length === 0) return;

  // Resolve focus driver
  let fd = null;
  if (currentFocusDriver) {
    fd = rawCars.find(c => String(c.CarNumber) === String(currentFocusDriver.CarNumber)) ||
         rawCars.find(c => (c.Name || "").trim().toLowerCase() === (currentFocusDriver.Name || "").trim().toLowerCase());
  }

  // Top 10
  let topCars = rawCars
    .filter(c => c.LivePos != null && c.LivePos > 0)
    .sort((a, b) => a.LivePos - b.LivePos)
    .slice(0, 10);

  const fdInTop10 = fd ? topCars.some(c => c.CarIdx === fd.CarIdx) : false;

  // Update lap counter
  if (towerLapEl && topCars.length > 0) {
    const lap = topCars[0].Lap || 0;
    towerLapEl.innerHTML = `${lap} <span class="lap-dim">/ ${rawCars.length > 0 ? "—" : "—"}</span>`;
  }

  // Build HTML
  let html = "";

  topCars.forEach((car, idx) => {
    const pos = car.LivePos;
    const name = shortName(car.Name || car.Driver || "");
    const gap = pos === 1 ? "Interval" : fmtGap(car.GapAhead);
    const isFocus = fd && !fdInTop10 ? false : (fd && fd.CarIdx === car.CarIdx);
    const colorClass = getDriverColorClass(car.CarIdx);

    html += `<div class="tower-row${isFocus ? ' focus-driver' : ''}">
      <div class="tower-pos">${pos}</div>
      <div class="tower-color ${colorClass}"></div>
      <div class="tower-name">${name}</div>
      <div class="tower-gap${pos === 1 ? ' leader' : ''}">${gap}</div>
    </div>`;
  });

  // Owner driver outside top 10
  if (fd && !fdInTop10 && fd.LivePos > 10) {
    const gap = fmtGap(fd.GapToLeader);
    const fdColor = getDriverColorClass(fd.CarIdx);
    html += `<div class="tower-sep"></div>
    <div class="tower-row focus-driver">
      <div class="tower-pos">${fd.LivePos}</div>
      <div class="tower-color ${fdColor}"></div>
      <div class="tower-name">${shortName(fd.Name || "")}</div>
      <div class="tower-gap">${gap}</div>
    </div>`;
  }

  // OUT rows (cars with TrackSurface indicating pit/retired, if any)
  const outCars = rawCars.filter(c =>
    c.TrackSurface != null && (c.TrackSurface === 1 || c.TrackSurface === -1) &&
    !topCars.some(t => t.CarIdx === c.CarIdx) &&
    (!fd || fd.CarIdx !== c.CarIdx)
  ).slice(0, 2);

  outCars.forEach(car => {
    const outColor = getDriverColorClass(car.CarIdx);
    html += `<div class="tower-row out-row">
      <div class="tower-pos">—</div>
      <div class="tower-color ${outColor}"></div>
      <div class="tower-name">${shortName(car.Name || "")}</div>
      <div class="tower-out-label">OUT</div>
    </div>`;
  });

  towerBody.innerHTML = html;
}

/* ================================================================
   COMMENTARY STREAM — Active + History
================================================================ */
let prevStreamLen = 0;

function renderStream(data) {
  if (!data || !Array.isArray(data.events)) return;

  const events = data.events.slice(-30).reverse();
  if (events.length === 0) return;

  // Active (most recent)
  const active = events[0];
  if (activeCommentary) {
    const type = (active.type || "").replace(/[_-]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    const commentator = active.commentator || "";
    const location = active.location || "Studio";
    const photoUrl = getCommentatorPhotoUrl(commentator, location);

    // Waveform bars
    let waveBars = "";
    for (let i = 0; i < 25; i++) {
      const h = 8 + Math.floor(Math.random() * 20);
      const delay = (i * 0.04).toFixed(2);
      waveBars += `<div class="wave-bar" style="--h:${h}px;animation-delay:${delay}s;"></div>`;
    }

    activeCommentary.style.display = "";
    activeCommentary.innerHTML = `
      <div class="active-bg-photo" style="background-image:url('${photoUrl}');"></div>
      <div class="active-content">
        <div class="active-meta">
          <span class="active-type">${type}</span>
          <span class="active-commentator">${commentator}</span>
          <span class="active-location">${location}</span>
        </div>
        <div class="active-waveform">${waveBars}</div>
        <div class="active-text">${active.desc || active.short || ""}</div>
      </div>
    `;
  }

  // History (previous items)
  if (commentaryHistory) {
    const historyEvents = events.slice(1, 4); // Show 3 past items
    commentaryHistory.innerHTML = historyEvents.map(ev => {
      const type = (ev.type || "").replace(/[_-]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      const photoUrl = getCommentatorPhotoUrl(ev.commentator, ev.location);
      return `<div class="history-item">
        <div class="history-photo" style="background-image:url('${photoUrl}');"></div>
        <div class="history-body">
          <div class="history-meta">
            <span class="history-type">${type}</span>
            <span class="history-who">${ev.commentator || ""}</span>
            <span class="history-where">${ev.location || ""}</span>
          </div>
          <div class="history-text">${ev.desc || ev.short || ""}</div>
        </div>
      </div>`;
    }).join("");
  }

  // Update last stream event for photo tracking
  lastStreamEvent = active;
}

/* ================================================================
   DRIVER OF THE DAY
================================================================ */
function renderDOTD(data) {
  if (!data || !Array.isArray(data.top3)) return;

  const top3 = data.top3.slice(0, 3);

  const colorFor = (d) => {
    const p = driverProfiles[String(d.driver_id ?? d.car_idx ?? "")] || null;
    return (p && p.color_hex) ? p.color_hex : "#222";
  };

  // Hero (1st place)
  if (dotdHeroEl && top3.length > 0) {
    const d = top3[0];
    const pct = Math.round(d.percent || 0);
    const carColor = colorFor(d);
    dotdHeroEl.style.display = "";
    dotdHeroEl.style.setProperty("background", carColor, "important");
    console.log("[DOTD] hero", d.name, "driver_id", d.driver_id, "→ color", carColor);
    dotdHeroEl.innerHTML = `
      <div class="dotd-hero-photo" style="background:#000;border-right-color:#000"></div>
      <div class="dotd-hero-body">
        <div class="dotd-hero-tag">Driver of the Day</div>
        <div class="dotd-hero-name">${lastNameFull(d.name)}</div>
        <div class="dotd-hero-team">${d.pos ? "P" + d.pos : ""}</div>
        <div class="dotd-hero-pct">${pct}%</div>
        <div class="dotd-hero-pct-label">Votes</div>
        <div class="dotd-hero-bar" style="background:#000"><div class="dotd-hero-bar-fill" style="width:${pct}%;background:#000"></div></div>
      </div>
    `;
  }

  // Runners (2nd & 3rd)
  if (dotdRunnersEl && top3.length >= 2) {
    dotdRunnersEl.innerHTML = top3.slice(1, 3).map((d, i) => {
      const rank = i === 0 ? 2 : 3;
      const pct = Math.round(d.percent || 0);
      const carColor = colorFor(d);
      return `<div class="dotd-runner rank-${rank}" style="background:${carColor}">
        <div class="dotd-runner-photo" style="background:#000;border-right-color:#000"></div>
        <div class="dotd-runner-body">
          <div class="dotd-runner-rank">${rank}${rank === 2 ? "nd" : "rd"}</div>
          <div class="dotd-runner-name">${lastNameFull(d.name)}</div>
          <div class="dotd-runner-pct">${pct}%</div>
        </div>
      </div>`;
    }).join("");
  }
}

/* ================================================================
   MASTHEAD / META
================================================================ */
function renderMeta(leaderboard, weekendInfo) {
  if (!weekendInfo) weekendInfo = {};

  const trackName = weekendInfo.track_display_name || weekendInfo.track_name || "—";
  if (mhTrack) mhTrack.textContent = trackName;

  const w = weekendInfo.weekend_options || {};
  if (mhAir) mhAir.textContent = w.WeatherTemp || "—";
  if (mhTrk) {
    const trackTemp = weekendInfo.track_temp;
    mhTrk.textContent = trackTemp ? `${trackTemp}°C` : "—";
  }
  if (mhHum) mhHum.textContent = w.RelativeHumidity || "—";

  // Update lap counter from leaderboard
  if (towerLapEl && leaderboard && Array.isArray(leaderboard.cars) && leaderboard.cars.length) {
    const lap = leaderboard.cars[0].Lap || 0;
    towerLapEl.innerHTML = `${lap} <span class="lap-dim">/ —</span>`;
  }
}

/* ================================================================
   BOTTOM TICKER
================================================================ */
function renderTicker(leaderboard, dotdData) {
  if (!tickerTrack) return;

  const cars = (leaderboard && Array.isArray(leaderboard.cars)) ? leaderboard.cars : [];
  const leader = cars.length > 0 ? cars.sort((a,b) => (a.LivePos||999) - (b.LivePos||999))[0] : null;
  const leaderName = leader ? shortName(leader.Name) : "—";

  const gap12 = cars.length >= 2 ? fmtGap(cars[1].GapAhead) : "—";

  // Best lap across all cars
  let bestLapDriver = "—";
  let bestLapTime = "—";
  let bestTime = Infinity;
  cars.forEach(c => {
    const t = Number(c.Best || c.BestLapTime);
    if (t > 0 && t < bestTime) {
      bestTime = t;
      bestLapDriver = shortName(c.Name);
      bestLapTime = fmtLap(t);
    }
  });

  const dotdName = (dotdData && dotdData.top3 && dotdData.top3[0]) ?
    lastNameFull(dotdData.top3[0].name) : "—";
  const dotdPct = (dotdData && dotdData.top3 && dotdData.top3[0]) ?
    Math.round(dotdData.top3[0].percent || 0) + "%" : "";

  const fdLabel = currentFocusDriver ?
    `P${currentFocusDriver.LivePos || "?"} ${shortName(currentFocusDriver.Name)}` : "—";

  const items = [
    { l: "Leader", v: leaderName, cls: "tv" },
    { l: "Gap 1-2", v: gap12, cls: "tv" },
    { l: "Fastest Lap", v: `${bestLapDriver} ${bestLapTime}`, cls: "tg" },
    { l: "Cars", v: `${cars.length}`, cls: "tv" },
    { l: "Focus Driver", v: fdLabel, cls: "tg" },
    { l: "DOTD", v: `${dotdName} ${dotdPct}`, cls: "tg" },
  ];

  // Duplicate for seamless scroll
  const html = [...items, ...items].map(i =>
    `<span class="ticker-item"><span class="tl">${i.l}</span> <span class="${i.cls}">${i.v}</span></span>`
  ).join("");

  tickerTrack.innerHTML = html;
}

/* ================================================================
   SETTINGS PANEL
================================================================ */
if (settingsBtn) {
  settingsBtn.onclick = async () => {
    await loadSettings();
    await loadRaceGrid();
    await loadAudioDevices();
    settingsOverlay.classList.add("open");
  };
}

if (settingsClose) {
  settingsClose.onclick = () => settingsOverlay.classList.remove("open");
}

if (settingsOverlay) {
  settingsOverlay.onclick = (e) => {
    if (e.target === settingsOverlay) settingsOverlay.classList.remove("open");
  };
}

async function loadSettings() {
  const js = await fetchJson("/api/settings");
  if (!js) return;
  if (audioSelect && typeof js.audio_output_index === "number") {
    audioSelect.value = String(js.audio_output_index);
  }
}

/* ================================================================
   RACE GRID + FOCUS DRIVER
================================================================ */
async function loadRaceGrid() {
  const grid = await fetchJson("/sector_said_exports/race_grid.json");
  const settings = await fetchJson("/api/settings");
  if (!Array.isArray(grid)) return;

  raceGrid = grid;
  const currentFocus = settings?.FocusDriver ?? null;

  if (focusSelect) {
    focusSelect.innerHTML = "";
    grid.forEach(d => {
      const opt = document.createElement("option");
      const num = d.CarNumber || d.CarNumberRaw || d.Number || "--";
      const name = d.Name || d.Driver || "Unknown";
      opt.value = d.CarIdx;
      opt.textContent = `#${num} ${name}`;
      if (String(d.CarIdx) === String(currentFocus)) opt.selected = true;
      focusSelect.appendChild(opt);
    });

    focusSelect.onchange = () => {
      const d = raceGrid.find(x => String(x.CarIdx) === String(focusSelect.value));
      if (d) currentFocusDriver = d;
    };
  }
}

async function loadAndUpdateFocusDriver() {
  const fd = await fetchJson("/sector_said_exports/focus_driver_of_the_day.json");
  const lb = await fetchJson("/sector_said_exports/leaderboard.json");
  if (!fd || !lb || !Array.isArray(lb.cars)) return;

  const match = lb.cars.find(c => String(c.CarNumber).trim() === String(fd.CarNumber || "").trim()) ||
                lb.cars.find(c => String(c.Name).trim().toLowerCase() === String(fd.Name || "").trim().toLowerCase());
  if (match) currentFocusDriver = match;
}

/* ================================================================
   AUDIO DEVICES
================================================================ */
async function loadAudioDevices() {
  if (!audioSelect) return;
  const listJson = await fetchJson("/api/audio/devices");
  const settings = await fetchJson("/api/settings");
  audioSelect.innerHTML = "";
  const list = listJson?.AudioDevices || [];
  const savedIdx = Number(settings?.audio_output_index ?? 0);
  list.forEach(dev => {
    const opt = document.createElement("option");
    opt.value = String(dev.id);
    opt.textContent = dev.name;
    audioSelect.appendChild(opt);
  });
  audioSelect.value = String(savedIdx);
}

/* ================================================================
   SAVE SETTINGS
================================================================ */
if (saveBtn) {
  saveBtn.onclick = async () => {
    const idx = Number(audioSelect?.value);

    await fetch("/api/settings/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audio_output_index: idx
      })
    });

    await fetch("/api/audio/reload", { method: "POST" });

    const fd = currentFocusDriver;
    if (fd && fd.CarIdx != null) {
      await fetch("/api/settings/focus_driver", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          Name: (fd.Name || fd.Driver || "").trim(),
          CarNumber: String(fd.CarNumber || fd.CarNumberRaw || fd.Number || "--")
        })
      });
    }

    settingsOverlay.classList.remove("open");
  };
}

/* ================================================================
   TICK LOOP
================================================================ */
async function tick() {
  const [leaderboard, weekendInfo, streamData, dotdData] = await Promise.all([
    fetchJson("/sector_said_exports/leaderboard.json"),
    fetchJson("/sector_said_exports/weekend_info.json"),
    fetchJson("/sector_said_exports/trigger_stream.json"),
    fetchJson("/sector_said_exports/dotd_top3.json")
  ]);

  renderTower(leaderboard);
  renderStream(streamData);
  renderDOTD(dotdData);
  renderMeta(leaderboard, weekendInfo);
  renderTicker(leaderboard, dotdData);

  // Flag glow from leaderboard flags
  if (leaderboard && leaderboard.flag) {
    showFlagGlow(leaderboard.flag);
  }

  // Also check stream for flag events
  if (streamData && Array.isArray(streamData.events)) {
    const recent = streamData.events.slice(-5).reverse();
    for (const ev of recent) {
      const type = (ev.type || "").toLowerCase();
      if (type.includes("flag") || type.includes("safety") || type.includes("caution")) {
        showFlagGlow(ev.type);
        break;
      }
    }
  }
}

/* ================================================================
   SLOW REFRESH
================================================================ */
async function slowRefresh() {
  await loadRaceGrid();
  await loadAudioDevices();
  await loadDriverImages();
  await loadDriverPhotos();
  await loadDriverProfiles();
  await loadOwnerDriverImages();
  await loadCommentatorImages();
  await loadAndUpdateFocusDriver();
}
setInterval(slowRefresh, 20000);

/* ================================================================
   INIT
================================================================ */
function scheduleTick() {
  tick().then(() => {
    requestAnimationFrame(() => {
      setTimeout(scheduleTick, 1000);
    });
  });
}

(async function init() {
  await loadCommentatorImages();
  await loadRaceGrid();
  await loadAudioDevices();
  await loadDriverImages();
  await loadDriverPhotos();
  await loadDriverProfiles();
  await loadOwnerDriverImages();
  await loadAndUpdateFocusDriver();

  const savedFD = await fetchJson("/sector_said_exports/focus_driver_of_the_day.json");
  if (savedFD && raceGrid.length) {
    const match = raceGrid.find(x => String(x.CarNumber) === String(savedFD.CarNumber)) ||
                  raceGrid.find(x => (x.Name || "").trim().toLowerCase() === (savedFD.Name || "").trim().toLowerCase());
    if (match) currentFocusDriver = match;
  }

  scheduleTick();
})();
