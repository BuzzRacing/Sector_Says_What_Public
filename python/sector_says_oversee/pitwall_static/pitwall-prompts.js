/* ============================================================
   pitwall-prompts.js — Commentary Prompts Editor
   Sector Says What — Edit prompt templates per trigger type
   ============================================================ */

let _prActiveTrigger = null;

// ─── Init ───────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const types = await api("/api/prompts/trigger-types");
    const list = document.getElementById("pr-type-list");
    list.innerHTML = types.map(t => `
      <div class="prompt-type-item" data-type="${t}" onclick="prSelectType('${t}')">
        ${t.replace(/_/g, " ")}
      </div>
    `).join("");
  } catch (e) { console.warn("Prompts init:", e); }
});

// ─── Select Trigger Type ────────────��───────────────────────────

window.prSelectType = async function(type) {
  _prActiveTrigger = type;

  // Update active state
  document.querySelectorAll(".prompt-type-item").forEach(el => {
    el.classList.toggle("active", el.dataset.type === type);
  });

  document.getElementById("pr-active-type").textContent = type.replace(/_/g, " ").toUpperCase();
  document.getElementById("pr-add-btn").style.display = "";

  await prLoadTemplates(type);
};

async function prLoadTemplates(type) {
  const container = document.getElementById("pr-templates");
  try {
    const templates = await api(`/api/prompts/${type}`);
    if (!templates.length) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-text">No custom templates for this trigger yet</div>
          <button class="btn btn-sm btn-primary mt" onclick="addPrompt()">Create First Template</button>
        </div>`;
      return;
    }

    // Generate varied wave bar delays for each card
    // F1 team colours pool
    const f1Colors = [
      '#e10600', // Ferrari red
      '#00d2be', // Mercedes teal
      '#3671C6', // Red Bull blue
      '#FF8000', // McLaren papaya
      '#0093cc', // Alpine blue
      '#64C4FF', // Williams blue
      '#00e701', // Kick Sauber green
      '#B6BABD', // Haas silver
      '#6692FF', // RB blue
      '#279f57', // Aston Martin green
    ];

    container.innerHTML = `<div class="pr-radio-grid">${templates.map((t, idx) => {
      const color = f1Colors[Math.floor(Math.random() * f1Colors.length)];
      const truncated = t.prompt_text.length > 120
        ? t.prompt_text.substring(0, 120).replace(/\s+\S*$/, '') + '...'
        : t.prompt_text;
      const waveBars = Array.from({length: 24}, (_, i) =>
        `<div class="prompt-radio-wave-bar" style="animation-delay:${(i * 0.07).toFixed(2)}s;height:${3 + Math.random() * 8}px"></div>`
      ).join('');
      return `
        <div class="prompt-card" style="--radio-color:${color}">
          <div class="prompt-radio-header">
            <div class="prompt-radio-name">${escHtml(t.template_name)}</div>
            <div class="prompt-radio-label">RADIO</div>
            <div class="prompt-radio-actions">
              <button class="btn btn-sm" onclick="prEditTemplate(${t.id})">Edit</button>
              <button class="btn btn-sm btn-danger" onclick="prDeleteTemplate(${t.id})">Del</button>
            </div>
          </div>
          <div class="prompt-radio-stripe"></div>
          <div class="prompt-radio-wave">${waveBars}</div>
          <div class="prompt-radio-text" onclick="prToggleExpand(this)" title="Click to expand/collapse">${escHtml(truncated)}</div>
          <div class="prompt-radio-meta">
            <span class="prompt-radio-chars">${t.char_limit} chars</span>
            <span class="prompt-radio-status ${t.is_active ? 'active' : 'inactive'}">${t.is_active ? 'On Air' : 'Off Air'}</span>
          </div>
          <div class="prompt-radio-full" style="display:none">${escHtml(t.prompt_text)}</div>
        </div>`;
    }).join('')}</div>`;
  } catch (e) {
    container.innerHTML = '<p style="color:var(--red)">Error loading templates</p>';
  }
}

window.prToggleExpand = function(el) {
  const card = el.closest('.prompt-card');
  const full = card.querySelector('.prompt-radio-full').textContent;
  const truncated = full.length > 120
    ? full.substring(0, 120).replace(/\s+\S*$/, '') + '...'
    : full;
  const isExpanded = el.classList.contains('expanded');
  if (isExpanded) {
    el.textContent = truncated;
    el.classList.remove('expanded');
  } else {
    el.textContent = full;
    el.classList.add('expanded');
  }
};

// ─── Add Prompt ───��─────────────────────────────────────────────

window.addPrompt = function() {
  if (!_prActiveTrigger) return;
  const html = `
    <h3>New Prompt Template</h3>
    <div class="field"><label>Trigger Type</label><input type="text" id="pr-new-type" value="${_prActiveTrigger}" readonly></div>
    <div class="field"><label>Template Name</label><input type="text" id="pr-new-name" placeholder="e.g. Overtake Hype v2"></div>
    <div class="field"><label>Prompt Text</label><textarea id="pr-new-text" rows="5" placeholder="Generate a live commentary snippet for an overtake. {driver} has just passed {other_driver} for P{position}."></textarea></div>
    <div class="grid-2">
      <div class="field"><label>Character Limit</label><input type="number" id="pr-new-chars" value="150"></div>
      <div class="field"><label>Active</label><label class="toggle"><input type="checkbox" id="pr-new-active" checked><span class="slider"></span></label></div>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="prSaveNew()">Create</button>
    </div>
  `;
  showModal(html);
};

window.prSaveNew = async function() {
  const data = {
    trigger_type: document.getElementById("pr-new-type").value,
    template_name: document.getElementById("pr-new-name").value,
    prompt_text: document.getElementById("pr-new-text").value,
    char_limit: parseInt(document.getElementById("pr-new-chars").value) || 150,
    is_active: document.getElementById("pr-new-active").checked ? 1 : 0,
  };
  if (!data.template_name || !data.prompt_text) { toast("Name and prompt text required"); return; }
  try {
    await api("/api/prompts", "POST", data);
    closeModal();
    toast("Template created");
    prLoadTemplates(_prActiveTrigger);
  } catch (e) { toast("Create failed"); }
};

// ─── Edit Prompt ────────────��───────────────────────────────────

window.prEditTemplate = async function(id) {
  const templates = await api(`/api/prompts/${_prActiveTrigger}`);
  const t = templates.find(x => x.id === id);
  if (!t) return;

  const html = `
    <h3>Edit Template</h3>
    <div class="field"><label>Template Name</label><input type="text" id="pr-edit-name" value="${escAttr(t.template_name)}"></div>
    <div class="field"><label>Prompt Text</label><textarea id="pr-edit-text" rows="5">${escHtml(t.prompt_text)}</textarea></div>
    <div class="grid-2">
      <div class="field"><label>Character Limit</label><input type="number" id="pr-edit-chars" value="${t.char_limit}"></div>
      <div class="field"><label>Active</label><label class="toggle"><input type="checkbox" id="pr-edit-active" ${t.is_active ? "checked" : ""}><span class="slider"></span></label></div>
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="prSaveEdit(${id})">Save</button>
    </div>
  `;
  showModal(html);
};

window.prSaveEdit = async function(id) {
  const data = {
    template_name: document.getElementById("pr-edit-name").value,
    prompt_text: document.getElementById("pr-edit-text").value,
    char_limit: parseInt(document.getElementById("pr-edit-chars").value) || 150,
    is_active: document.getElementById("pr-edit-active").checked ? 1 : 0,
  };
  try {
    await api(`/api/prompts/${id}`, "PUT", data);
    closeModal();
    toast("Template updated");
    prLoadTemplates(_prActiveTrigger);
  } catch (e) { toast("Update failed"); }
};

// ─── Delete Prompt ──────���───────────────────────────────────────

window.prDeleteTemplate = async function(id) {
  if (!confirm("Delete this prompt template?")) return;
  try {
    await api(`/api/prompts/${id}`, "DELETE");
    toast("Template deleted");
    prLoadTemplates(_prActiveTrigger);
  } catch (e) { toast("Delete failed"); }
};

// ─── Helpers ────────────────────────────────��───────────────────

function escHtml(val) {
  if (val === null || val === undefined) return "";
  const div = document.createElement("div");
  div.textContent = String(val);
  return div.innerHTML;
}

function escAttr(val) {
  return String(val || "").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
