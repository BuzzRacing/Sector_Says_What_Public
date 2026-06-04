/* ============================================================
   pitwall-broadcast.js — Broadcast Control Panel
   Sector Says What — Start/stop engine, live monitoring
   ============================================================ */

let _bcPollTimer = null;

// ─── Broadcast Start / Stop ─────────────────────────────────────

window.broadcastStart = async function() {
  const btn = document.getElementById("bc-start");
  btn.disabled = true;
  btn.textContent = "Starting...";
  try {
    const res = await api("/api/broadcast/start", "POST");
    if (res.error) {
      toast(res.error);
      btn.disabled = false;
      btn.textContent = "Lights Out";
      return;
    }
    toast("Engine started — PID " + res.pid);
    startBroadcastPolling();
  } catch (e) {
    toast("Failed to start engine");
    btn.disabled = false;
    btn.textContent = "Lights Out";
  }
};

window.broadcastStop = async function() {
  if (!confirm("Red Flag — Stop the broadcast engine?")) return;
  try {
    await api("/api/broadcast/stop", "POST");
    toast("Engine stopped");
    stopBroadcastPolling();
    updateBroadcastUI({status: "idle"});
  } catch (e) {
    toast("Failed to stop engine");
  }
};

// ─── Polling ────────────────────────────────────────────────────

function startBroadcastPolling() {
  if (_bcPollTimer) return;
  pollBroadcast();
  _bcPollTimer = setInterval(pollBroadcast, 2000);
}

function stopBroadcastPolling() {
  if (_bcPollTimer) { clearInterval(_bcPollTimer); _bcPollTimer = null; }
}

async function pollBroadcast() {
  try {
    const state = await api("/api/broadcast/status");
    updateBroadcastUI(state);

    if (state.status !== "idle") {
      const triggers = await api("/api/broadcast/triggers?limit=30");
      updateTriggerFeed(triggers);
    }
  } catch (e) {
    // Silently retry
  }
}

// ─── UI Updates ─────────────────────────────────────────────────

function updateBroadcastUI(state) {
  const dot = document.getElementById("bc-dot");
  const text = document.getElementById("bc-status-text");
  const sub = document.getElementById("bc-status-sub");
  const startBtn = document.getElementById("bc-start");
  const stopBtn = document.getElementById("bc-stop");

  // Status dot
  dot.className = "status-dot " + (state.status || "idle");

  // Status text
  const labels = {
    idle: "IDLE",
    connecting: "CONNECTING",
    pre_race: "PRE-RACE",
    racing: "RACING",
    cooldown: "COOLDOWN",
    finished: "FINISHED",
  };
  text.textContent = labels[state.status] || state.status.toUpperCase();

  if (state.status === "idle") {
    sub.textContent = "No active broadcast";
    startBtn.style.display = "";
    startBtn.disabled = false;
    startBtn.textContent = "Lights Out";
    stopBtn.style.display = "none";
  } else {
    sub.textContent = state.started_at
      ? "Started " + new Date(state.started_at).toLocaleTimeString()
      : "PID " + (state.engine_pid || "?");
    startBtn.style.display = "none";
    stopBtn.style.display = "";
  }

  // Stat cards
  document.getElementById("bc-lap").textContent =
    state.total_laps ? `${state.lap || 0}/${state.total_laps}` : (state.lap || "--");
  document.getElementById("bc-cars").textContent = state.car_count || "--";
  document.getElementById("bc-commentary").textContent = state.commentary_count || 0;

  const elapsed = state.elapsed_s || 0;
  const min = Math.floor(elapsed / 60);
  const sec = Math.floor(elapsed % 60);
  document.getElementById("bc-elapsed").textContent =
    `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;

  // Commentary rate
  const rate = elapsed > 60 ? ((state.commentary_count || 0) / (elapsed / 60)).toFixed(1) : "--";
  document.getElementById("bc-rate").textContent = rate;

  // Last trigger
  const lastTrig = state.last_trigger || "--";
  document.getElementById("bc-last-trigger").textContent = lastTrig.replace(/_/g, " ").substring(0, 12);

  // Progress bar
  const progWrap = document.getElementById("bc-progress-wrap");
  if (state.total_laps && state.lap && state.status === "racing") {
    const pct = Math.min(100, Math.round((state.lap / state.total_laps) * 100));
    progWrap.style.display = "";
    document.getElementById("bc-progress-fill").style.width = pct + "%";
    document.getElementById("bc-progress-label").textContent = `Lap ${state.lap} of ${state.total_laps} (${pct}%)`;
  } else {
    progWrap.style.display = "none";
  }

  // Live indicators
  document.querySelectorAll(".stat-card").forEach(c => c.classList.remove("stat-live"));
  if (state.status === "racing") {
    document.querySelectorAll(".stat-card").forEach(c => c.classList.add("stat-live"));
  }

  // Frozen engine warning
  let warn = document.getElementById("bc-frozen-warning");
  if (!warn) {
    warn = document.createElement("div");
    warn.id = "bc-frozen-warning";
    warn.style.cssText = "background:#e74c3c;color:#fff;padding:8px 12px;border-radius:6px;margin-top:8px;font-weight:700;display:none;";
    sub.parentElement.appendChild(warn);
  }
  if (state.warning) {
    warn.textContent = "⚠ " + state.warning;
    warn.style.display = "block";
  } else {
    warn.style.display = "none";
  }
}

const _bcF1Colors = [
  '#2ecc71', '#00d2be', '#3671C6', '#FF8000', '#0093cc',
  '#64C4FF', '#00e701', '#B6BABD', '#6692FF', '#279f57',
];

function updateTriggerFeed(triggers) {
  const feed = document.getElementById("bc-feed");
  if (!triggers || triggers.length === 0) return;

  const reversed = [...triggers].reverse();
  document.getElementById("bc-feed-count").textContent = `${triggers.length} events`;

  // Last commentary highlight (radio card style)
  const latest = reversed[0];
  if (latest) {
    const color = _bcF1Colors[Math.floor(Math.random() * _bcF1Colors.length)];
    const lc = document.getElementById("bc-last-commentary");
    lc.style.display = "";
    lc.style.setProperty("--radio-color", color);
    lc.innerHTML = `
      <div class="prompt-radio-header">
        <div class="prompt-radio-name">${(latest.type || "").replace(/_/g, " ")}</div>
        <div class="prompt-radio-label">RADIO</div>
      </div>
      <div class="prompt-radio-stripe"></div>
      <div style="padding:14px 18px;font-size:14px;color:rgba(255,255,255,0.85);line-height:1.5">${latest.desc || latest.short || ""}</div>
      <div style="padding:6px 18px 10px;font-size:11px;color:var(--text-dim)">${latest.commentator || ""} ${latest.location ? "@ " + latest.location : ""}</div>
    `;
  }

  // Feed list
  feed.innerHTML = reversed.map(t => {
    const color = _bcF1Colors[Math.floor(Math.random() * _bcF1Colors.length)];
    return `
      <div class="trigger-feed-item">
        <span class="tf-time">${t.ts ? new Date(t.ts).toLocaleTimeString() : ""}</span>
        <span class="tf-type" style="color:${color}">${(t.type || "").replace(/_/g, " ")}</span>
        <span class="tf-desc">${t.short || t.desc || ""}</span>
        <span class="tf-caster">${t.commentator || ""}</span>
      </div>`;
  }).join("");
}

// ─── Auto-start polling if on Broadcast tab ─────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Poll once on load to get initial state
  pollBroadcast().then(state => {
    // If not idle, start continuous polling
    api("/api/broadcast/status").then(s => {
      if (s.status && s.status !== "idle") startBroadcastPolling();
    }).catch(() => {});
  });

  // Start/stop polling when switching to/from broadcast tab
  document.querySelectorAll(".sidebar-nav a").forEach(a => {
    a.addEventListener("click", () => {
      if (a.dataset.tab === "broadcast") {
        pollBroadcast();
        api("/api/broadcast/status").then(s => {
          if (s.status && s.status !== "idle") startBroadcastPolling();
        }).catch(() => {});
      }
    });
  });
});
