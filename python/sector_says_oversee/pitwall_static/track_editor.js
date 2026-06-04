// track_editor.js — Phase 1 + 2: trace centerlines over a backdrop image.
// Saves to POST /api/tracks (track_library.save schema).

const $ = (id) => document.getElementById(id);
const svg = $("svg");
const stage = $("stage");
const backdrop = $("backdrop");
const finalPath = $("centerlineFinal");
const previewPath = $("centerlinePreview");
const anchorLayer = $("anchorLayer");
const sfLayer = $("sfLayer");

const VIEW_W = 1600, VIEW_H = 900;

const state = {
  anchors: [],          // [[x,y], ...]
  closed: false,
  sfIndex: null,
  bgSrc: "",
  bgScale: 1,
  bgDx: 0,
  bgDy: 0,
  bgOpacity: 0.4,
  bgVisible: true,
  dragging: null,       // index being dragged
  history: [],
  future: [],
  currentSlug: "",
};

// ─── History ────────────────────────────────────────────────────
function snap() {
  state.history.push(JSON.stringify({
    a: state.anchors, c: state.closed, s: state.sfIndex,
  }));
  if (state.history.length > 100) state.history.shift();
  state.future.length = 0;
}
function undo() {
  if (!state.history.length) return;
  state.future.push(JSON.stringify({ a: state.anchors, c: state.closed, s: state.sfIndex }));
  const prev = JSON.parse(state.history.pop());
  state.anchors = prev.a; state.closed = prev.c; state.sfIndex = prev.s;
  render();
}
function redo() {
  if (!state.future.length) return;
  state.history.push(JSON.stringify({ a: state.anchors, c: state.closed, s: state.sfIndex }));
  const nxt = JSON.parse(state.future.pop());
  state.anchors = nxt.a; state.closed = nxt.c; state.sfIndex = nxt.s;
  render();
}

// ─── Coords ─────────────────────────────────────────────────────
function svgPoint(evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  const ctm = svg.getScreenCTM().inverse();
  const p = pt.matrixTransform(ctm);
  return [Math.round(p.x * 10) / 10, Math.round(p.y * 10) / 10];
}

// ─── Catmull-Rom → cubic Bezier path ───────────────────────────
function buildPath(pts, closed) {
  if (pts.length < 2) return "";
  if (pts.length === 2) return `M ${pts[0][0]} ${pts[0][1]} L ${pts[1][0]} ${pts[1][1]}`;
  const get = (i) => {
    if (closed) return pts[(i + pts.length) % pts.length];
    return pts[Math.max(0, Math.min(pts.length - 1, i))];
  };
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  const last = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < last; i++) {
    const p0 = get(i - 1), p1 = get(i), p2 = get(i + 1), p3 = get(i + 2);
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)} ${c2x.toFixed(2)} ${c2y.toFixed(2)} ${p2[0]} ${p2[1]}`;
  }
  if (closed) d += " Z";
  return d;
}

// ─── Render ─────────────────────────────────────────────────────
function render() {
  const d = buildPath(state.anchors, state.closed);
  finalPath.setAttribute("d", state.closed || state.anchors.length > 2 ? d : "");
  previewPath.setAttribute("d", !state.closed && state.anchors.length > 1 ? d : "");

  // anchors
  anchorLayer.innerHTML = "";
  state.anchors.forEach(([x, y], i) => {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", x); c.setAttribute("cy", y);
    c.setAttribute("r", 5); c.setAttribute("class", "anchor");
    c.dataset.idx = i;
    anchorLayer.appendChild(c);
  });

  // SF marker
  sfLayer.innerHTML = "";
  if (state.sfIndex != null && state.anchors[state.sfIndex]) {
    const [x, y] = state.anchors[state.sfIndex];
    const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    r.setAttribute("x", x - 8); r.setAttribute("y", y - 8);
    r.setAttribute("width", 16); r.setAttribute("height", 16);
    r.setAttribute("class", "sf");
    sfLayer.appendChild(r);
  }

  // stats
  $("statCount").textContent = state.anchors.length;
  $("statClosed").textContent = state.closed ? "yes" : "no";
  $("statSf").textContent = state.sfIndex == null ? "—" : state.sfIndex;
}

// ─── Backdrop ───────────────────────────────────────────────────
function applyBackdrop() {
  if (!state.bgSrc) {
    backdrop.style.display = "none";
    return;
  }
  backdrop.setAttribute("href", state.bgSrc);
  backdrop.style.display = state.bgVisible ? "" : "none";
  backdrop.setAttribute("opacity", state.bgOpacity);
  // Fit-then-transform: anchor at viewbox center, scale, then offset.
  const w = VIEW_W * state.bgScale, h = VIEW_H * state.bgScale;
  const x = (VIEW_W - w) / 2 + state.bgDx;
  const y = (VIEW_H - h) / 2 + state.bgDy;
  backdrop.setAttribute("x", x); backdrop.setAttribute("y", y);
  backdrop.setAttribute("width", w); backdrop.setAttribute("height", h);
}

// ─── Pointer logic ──────────────────────────────────────────────
let downAt = null, downIdx = null, didDrag = false;

svg.addEventListener("pointerdown", (e) => {
  if (e.button === 2) return; // right click handled by contextmenu
  const tgt = e.target;
  if (tgt.classList && tgt.classList.contains("anchor")) {
    downIdx = +tgt.dataset.idx;
    downAt = svgPoint(e);
    didDrag = false;
    svg.setPointerCapture(e.pointerId);
    return;
  }
  // empty canvas click → handled on pointerup so we can detect drag-vs-click
  downIdx = null;
  downAt = svgPoint(e);
});

svg.addEventListener("pointermove", (e) => {
  if (downIdx == null) return;
  const p = svgPoint(e);
  if (!didDrag) {
    snap();
    didDrag = true;
  }
  state.anchors[downIdx] = p;
  render();
});

svg.addEventListener("pointerup", (e) => {
  if (downIdx != null) {
    downIdx = null; downAt = null; didDrag = false;
    return;
  }
  if (!downAt) return;
  const p = svgPoint(e);
  // alt+click on existing centerline → insert at nearest segment
  if (e.altKey && state.anchors.length >= 2) {
    snap();
    const i = nearestSegmentIndex(p);
    state.anchors.splice(i + 1, 0, p);
    render();
  } else {
    snap();
    state.anchors.push(p);
    render();
  }
  downAt = null;
});

svg.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const tgt = e.target;
  if (tgt.classList && tgt.classList.contains("anchor")) {
    const i = +tgt.dataset.idx;
    snap();
    state.anchors.splice(i, 1);
    if (state.sfIndex != null) {
      if (state.sfIndex === i) state.sfIndex = null;
      else if (state.sfIndex > i) state.sfIndex--;
    }
    render();
  }
});

function nearestSegmentIndex(p) {
  let best = 0, bestD = Infinity;
  const n = state.anchors.length;
  const limit = state.closed ? n : n - 1;
  for (let i = 0; i < limit; i++) {
    const a = state.anchors[i], b = state.anchors[(i + 1) % n];
    const d = pointSegDist(p, a, b);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}
function pointSegDist(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy + 1e-9)));
  const cx = a[0] + t * dx, cy = a[1] + t * dy;
  return Math.hypot(p[0] - cx, p[1] - cy);
}

// ─── SF set mode ────────────────────────────────────────────────
let sfMode = false;
$("sfBtn").addEventListener("click", () => {
  sfMode = !sfMode;
  stage.classList.toggle("mode-sf", sfMode);
  $("sfBtn").classList.toggle("primary", sfMode);
  $("hint").textContent = sfMode ? "Click an anchor to mark Start/Finish" : "";
});
anchorLayer.addEventListener("click", (e) => {
  if (!sfMode) return;
  const tgt = e.target;
  if (tgt.classList && tgt.classList.contains("anchor")) {
    snap();
    state.sfIndex = +tgt.dataset.idx;
    sfMode = false;
    stage.classList.remove("mode-sf");
    $("sfBtn").classList.remove("primary");
    render();
  }
}, true);

// ─── Keys ───────────────────────────────────────────────────────
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "z" || e.key === "Z") { e.preventDefault(); undo(); }
  else if (e.key === "y" || e.key === "Y") { e.preventDefault(); redo(); }
  else if (e.key === "c" || e.key === "C") { toggleClose(); }
});

function toggleClose() {
  if (state.anchors.length < 3) return;
  snap();
  state.closed = !state.closed;
  render();
}
$("closeBtn").addEventListener("click", toggleClose);
$("undoBtn").addEventListener("click", undo);
$("redoBtn").addEventListener("click", redo);
$("clearBtn").addEventListener("click", () => {
  if (!confirm("Clear all anchors?")) return;
  snap();
  state.anchors = []; state.closed = false; state.sfIndex = null;
  render();
});

// ─── Backdrop UI ────────────────────────────────────────────────
$("bgUrl").addEventListener("change", (e) => { state.bgSrc = e.target.value.trim(); applyBackdrop(); });
$("bgFile").addEventListener("change", (e) => {
  const f = e.target.files[0]; if (!f) return;
  const r = new FileReader();
  r.onload = () => { state.bgSrc = r.result; applyBackdrop(); };
  r.readAsDataURL(f);
});
$("bgOpacity").addEventListener("input", (e) => {
  state.bgOpacity = +e.target.value / 100;
  $("opVal").textContent = e.target.value + "%";
  applyBackdrop();
});
$("bgScale").addEventListener("input", (e) => {
  state.bgScale = +e.target.value;
  $("scVal").textContent = (+e.target.value).toFixed(2) + "×";
  applyBackdrop();
});
$("bgDx").addEventListener("input", (e) => {
  state.bgDx = +e.target.value;
  $("dxVal").textContent = e.target.value;
  applyBackdrop();
});
$("bgDy").addEventListener("input", (e) => {
  state.bgDy = +e.target.value;
  $("dyVal").textContent = e.target.value;
  applyBackdrop();
});
$("bgClear").addEventListener("click", () => {
  state.bgSrc = ""; $("bgUrl").value = ""; $("bgFile").value = "";
  applyBackdrop();
});
$("bgToggle").addEventListener("click", () => {
  state.bgVisible = !state.bgVisible;
  $("bgToggle").textContent = state.bgVisible ? "Hide" : "Show";
  applyBackdrop();
});

// drag-drop image
stage.addEventListener("dragover", (e) => e.preventDefault());
stage.addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => { state.bgSrc = r.result; applyBackdrop(); };
  r.readAsDataURL(f);
});

// ─── Save / Load ────────────────────────────────────────────────
function toast(msg, ok = true) {
  const t = $("toast");
  t.textContent = msg;
  t.style.background = ok ? "var(--emerald-dim)" : "#6b1d1d";
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}

async function loadTrackList() {
  try {
    const r = await fetch("/api/tracks");
    const tracks = await r.json();
    const sel = $("trackSelect");
    sel.innerHTML = '<option value="">— New track —</option>';
    tracks.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.slug; o.textContent = t.display_name || t.slug;
      sel.appendChild(o);
    });
  } catch (e) { console.warn(e); }
}

$("trackSelect").addEventListener("change", async (e) => {
  const slug = e.target.value;
  if (!slug) {
    state.currentSlug = "";
    $("displayName").value = ""; $("irNames").value = "";
    state.anchors = []; state.closed = false; state.sfIndex = null;
    render();
    return;
  }
  const r = await fetch("/api/tracks/" + slug);
  if (!r.ok) { toast("Failed to load track", false); return; }
  const t = await r.json();
  state.currentSlug = t.slug || slug;
  $("displayName").value = t.display_name || "";
  $("irNames").value = (t.ir_names || []).join("\n");
  // load editor anchors if present
  if (t.editor && Array.isArray(t.editor.anchors)) {
    state.anchors = t.editor.anchors;
    state.closed = !!t.editor.closed;
    state.sfIndex = t.editor.sf_index ?? null;
  } else {
    state.anchors = [];
    state.closed = false;
    state.sfIndex = null;
    toast("No editor anchors saved — start fresh", true);
  }
  // viewBox
  if (t.viewBox) svg.setAttribute("viewBox", t.viewBox);
  render();
});

$("saveBtn").addEventListener("click", async () => {
  const name = $("displayName").value.trim();
  if (!name) return toast("Display name required", false);
  if (state.anchors.length < 3) return toast("Need at least 3 anchors", false);

  const centerline_path = buildPath(state.anchors, state.closed);
  const ir_names = $("irNames").value.split("\n").map((s) => s.trim()).filter(Boolean);

  // sf_offset = sfIndex / anchorCount (0..1) — track_library uses this for SF location
  const sf_offset = state.sfIndex != null && state.anchors.length
    ? state.sfIndex / state.anchors.length : 0;

  const payload = {
    slug: state.currentSlug || undefined,
    display_name: name,
    ir_names,
    viewBox: svg.getAttribute("viewBox"),
    centerline_path,
    sf_offset,
    notes: "",
    editor: {
      anchors: state.anchors,
      closed: state.closed,
      sf_index: state.sfIndex,
      backdrop_meta: {
        scale: state.bgScale, dx: state.bgDx, dy: state.bgDy, opacity: state.bgOpacity,
      },
    },
  };

  const method = state.currentSlug ? "PUT" : "POST";
  const url = state.currentSlug ? "/api/tracks/" + state.currentSlug : "/api/tracks";
  try {
    const r = await fetch(url, {
      method, headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const err = await r.text();
      toast("Save failed: " + err, false);
      return;
    }
    const saved = await r.json();
    state.currentSlug = saved.slug;
    toast("Saved " + saved.slug);
    loadTrackList();
  } catch (e) {
    toast("Save error: " + e.message, false);
  }
});

// ─── Init ───────────────────────────────────────────────────────
applyBackdrop();
render();
loadTrackList().then(() => {
  const params = new URLSearchParams(location.search);
  const slug = params.get("slug");
  const name = params.get("name");
  if (slug) {
    const sel = $("trackSelect");
    sel.value = slug;
    sel.dispatchEvent(new Event("change"));
  } else if (name) {
    $("displayName").value = name.replace(/\s*-\s*.*$/, "");
    $("irNames").value = name;
  }
});
