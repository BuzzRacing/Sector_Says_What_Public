/* ============================================================
   pitwall-data.js — Telemetry Data Viewer
   Sector Says What — Inspect live JSON exports
   ============================================================ */

let _tdActiveFile = null;
let _tdRefreshTimer = null;

// ─── Init ───────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  loadExportList();

  // Auto-refresh toggle
  const toggle = document.getElementById("td-auto-refresh");
  if (toggle) {
    toggle.addEventListener("change", () => {
      if (toggle.checked && _tdActiveFile) {
        _tdRefreshTimer = setInterval(() => loadExportFile(_tdActiveFile), 2000);
      } else {
        if (_tdRefreshTimer) { clearInterval(_tdRefreshTimer); _tdRefreshTimer = null; }
      }
    });
  }
});

async function loadExportList() {
  try {
    const files = await api("/api/data/exports");
    const list = document.getElementById("td-file-list");
    list.innerHTML = files.map(f => {
      const mod = new Date(f.modified * 1000).toLocaleTimeString();
      const size = f.size > 1024 ? (f.size / 1024).toFixed(1) + " KB" : f.size + " B";
      return `
        <div class="export-item" data-file="${f.name}" onclick="tdSelectFile('${f.name}')">
          <div class="export-icon">{ }</div>
          <div>
            <div class="export-name">${f.name}</div>
            <div class="export-meta">${size} &middot; ${mod}</div>
          </div>
        </div>
      `;
    }).join("");
  } catch (e) { console.warn("Export list:", e); }
}

// ─── Select File ────────────────────────────────────────────────

window.tdSelectFile = async function(filename) {
  _tdActiveFile = filename;

  // Update active state
  document.querySelectorAll(".export-item").forEach(el => {
    el.classList.toggle("active", el.dataset.file === filename);
  });

  document.getElementById("td-viewer-title").textContent = filename;
  await loadExportFile(filename);

  // Restart auto-refresh if enabled
  const toggle = document.getElementById("td-auto-refresh");
  if (toggle && toggle.checked) {
    if (_tdRefreshTimer) clearInterval(_tdRefreshTimer);
    _tdRefreshTimer = setInterval(() => loadExportFile(filename), 2000);
  }
};

async function loadExportFile(filename) {
  const container = document.getElementById("td-viewer-content");
  try {
    const result = await api(`/api/data/exports/${filename}`);
    if (result.error) {
      container.innerHTML = `<p style="color:var(--red)">${result.error}</p>`;
      return;
    }
    container.innerHTML = renderJson(result.data, 0);
  } catch (e) {
    container.innerHTML = '<p style="color:var(--red)">Error loading file</p>';
  }
}

// ─── JSON Renderer ──────────────────────────────────────────────

function renderJson(data, depth) {
  if (data === null) return '<span class="jt-null">null</span>';
  if (typeof data === "boolean") return `<span class="jt-bool">${data}</span>`;
  if (typeof data === "number") return `<span class="jt-number">${data}</span>`;
  if (typeof data === "string") {
    const escaped = data.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    if (escaped.length > 200) {
      return `<span class="jt-string">"${escaped.substring(0, 200)}..."</span>`;
    }
    return `<span class="jt-string">"${escaped}"</span>`;
  }

  if (Array.isArray(data)) {
    if (data.length === 0) return '<span class="jt-bracket">[]</span>';
    if (depth > 3) return `<span class="jt-bracket">[${data.length} items]</span>`;

    const items = data.map((item, i) => {
      const comma = i < data.length - 1 ? "," : "";
      return `<div style="padding-left:20px">${renderJson(item, depth + 1)}${comma}</div>`;
    }).join("");

    return `<details ${depth < 2 ? "open" : ""}>
      <summary><span class="jt-bracket">[</span> ${data.length} items</summary>
      ${items}
      <div><span class="jt-bracket">]</span></div>
    </details>`;
  }

  if (typeof data === "object") {
    const keys = Object.keys(data);
    if (keys.length === 0) return '<span class="jt-bracket">{}</span>';
    if (depth > 3) return `<span class="jt-bracket">{${keys.length} keys}</span>`;

    const entries = keys.map((key, i) => {
      const comma = i < keys.length - 1 ? "," : "";
      return `<div style="padding-left:20px"><span class="jt-key">"${key}"</span>: ${renderJson(data[key], depth + 1)}${comma}</div>`;
    }).join("");

    return `<details ${depth < 1 ? "open" : ""}>
      <summary><span class="jt-bracket">{</span> ${keys.length} keys</summary>
      ${entries}
      <div><span class="jt-bracket">}</span></div>
    </details>`;
  }

  return String(data);
}
