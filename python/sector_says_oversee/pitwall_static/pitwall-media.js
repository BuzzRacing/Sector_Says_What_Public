/* ============================================================
   pitwall-media.js — Media Gallery (Images Only)
   Sector Says What — Browse, preview, upload & manage image files
   ============================================================ */

let _mgFiles = [];
let _mgFiltered = [];
let _mgFolder = "";
let _mgCategory = "";
let _mgSearch = "";
let _mgView = "grid";
let _mgPreviewFile = null;
let _mgCommentators = [];
let _mgCategories = [];

// ─── Init ───────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".sidebar-nav a").forEach(a => {
    a.addEventListener("click", () => {
      if (a.dataset.tab === "media" && _mgFiles.length === 0) {
        mgLoadFiles();
      }
    });
  });
});

async function mgLoadFiles() {
  try {
    const res = await api("/api/media/files");
    _mgFiles = res.files || [];
    _mgCommentators = res.commentators || [];
    _mgCategories = res.categories || [];

    // Populate folder dropdown
    const sel = document.getElementById("mg-folder-select");
    sel.innerHTML = '<option value="">All Folders</option>';
    (res.folders || []).forEach(f => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f.replace("python/sector_says_oversee/", "");
      sel.appendChild(opt);
    });

    // Populate category dropdown
    const catSel = document.getElementById("mg-category-select");
    if (catSel) {
      catSel.innerHTML = '<option value="">All Categories</option>';
      const allCats = new Set(_mgCategories);
      _mgFiles.forEach(f => { if (f.category) allCats.add(f.category); });
      [...allCats].sort().forEach(c => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = mgCategoryLabel(c);
        catSel.appendChild(opt);
      });
    }

    mgApplyFilters();
  } catch (e) {
    console.warn("Media load:", e);
  }
}

function mgCategoryLabel(cat) {
  const labels = {
    persona_image: "Persona Image",
    hud_background: "HUD Background",
    pitwall_background: "Pit Wall Background",
    page_background: "Page Background",
    logo: "Logo",
    track_image: "Track Image",
    driver_image: "Driver Image",
    studio_backdrop: "Studio Backdrop",
    uncategorized: "Uncategorized",
  };
  return labels[cat] || cat;
}

// ─── Filtering ──────────────────────────────────────────────────

window.mgFilterFolder = function() {
  _mgFolder = document.getElementById("mg-folder-select").value;
  mgApplyFilters();
};

window.mgFilterCategory = function() {
  _mgCategory = document.getElementById("mg-category-select").value;
  mgApplyFilters();
};

window.mgSearch = function() {
  _mgSearch = (document.getElementById("mg-search").value || "").toLowerCase();
  mgApplyFilters();
};

function mgApplyFilters() {
  _mgFiltered = _mgFiles.filter(f => {
    if (_mgFolder && f.folder !== _mgFolder) return false;
    if (_mgCategory && (f.category || "uncategorized") !== _mgCategory) return false;
    if (_mgSearch && !f.name.toLowerCase().includes(_mgSearch)
        && !f.folder.toLowerCase().includes(_mgSearch)
        && !(f.commentator || "").toLowerCase().includes(_mgSearch)
        && !(f.category || "").toLowerCase().includes(_mgSearch)) return false;
    return true;
  });

  _mgFiltered.sort((a, b) => a.name.localeCompare(b.name));

  document.getElementById("mg-count").textContent = `${_mgFiltered.length} of ${_mgFiles.length} images`;
  document.getElementById("mg-folder-path").textContent = _mgFolder
    ? _mgFolder.replace("python/sector_says_oversee/", "")
    : "All folders";
  mgRender();
}

// ─── View Toggle ────────────────────────────────────────────────

window.mgSetView = function(view) {
  _mgView = view;
  document.getElementById("mg-view-grid").classList.toggle("active", view === "grid");
  document.getElementById("mg-view-list").classList.toggle("active", view === "list");
  mgRender();
};

// ─── Render ─────────────────────────────────────────────────────

function mgRender() {
  const gallery = document.getElementById("mg-gallery");
  gallery.className = `mg-gallery mg-${_mgView}`;

  if (_mgFiltered.length === 0) {
    gallery.innerHTML = '<div class="empty-state"><div class="empty-text">No images match your filters</div></div>';
    return;
  }

  if (_mgView === "grid") {
    gallery.innerHTML = _mgFiltered.map(f => mgRenderGridCard(f)).join("");
  } else {
    gallery.innerHTML = _mgFiltered.map(f => mgRenderListRow(f)).join("");
  }
}

function mgRenderGridCard(f) {
  const size = mgFormatSize(f.size);
  const catBadge = f.category && f.category !== "uncategorized"
    ? `<span class="mg-cat-badge">${mgCategoryLabel(f.category)}</span>` : "";
  const casterBadge = f.commentator
    ? `<span class="mg-caster-badge">${mgEscHtml(f.commentator)}</span>` : "";

  return `
    <div class="mg-card" onclick="mgOpenPreview('${mgEscAttr(f.path)}')">
      <div class="mg-thumb"><img src="/api/media/file?path=${encodeURIComponent(f.path)}" alt="${mgEscAttr(f.name)}" loading="lazy"></div>
      <div class="mg-info">
        <div class="mg-name" title="${mgEscAttr(f.name)}">${mgEscHtml(f.name)}</div>
        <div class="mg-meta">
          <span>${size}</span>
          <span>${f.ext}</span>
          ${catBadge}${casterBadge}
        </div>
      </div>
    </div>
  `;
}

function mgRenderListRow(f) {
  const size = mgFormatSize(f.size);

  return `
    <div class="mg-card" onclick="mgOpenPreview('${mgEscAttr(f.path)}')">
      <div class="mg-thumb"><img src="/api/media/file?path=${encodeURIComponent(f.path)}" alt="${mgEscAttr(f.name)}" loading="lazy"></div>
      <div class="mg-info">
        <div class="mg-name" title="${mgEscAttr(f.name)}">${mgEscHtml(f.name)}</div>
      </div>
      <div class="mg-list-folder">${mgEscHtml(f.folder.replace("python/sector_says_oversee/", ""))}</div>
      <div class="mg-list-size">${size}</div>
    </div>
  `;
}

// ─── Preview Panel ──────────────────────────────────────────────

window.mgOpenPreview = async function(path) {
  const f = _mgFiles.find(x => x.path === path);
  if (!f) return;
  _mgPreviewFile = f;

  const panel = document.getElementById("mg-preview");
  const title = document.getElementById("mg-preview-title");
  const body = document.getElementById("mg-preview-body");
  const meta = document.getElementById("mg-preview-meta");
  const actions = document.getElementById("mg-preview-actions");

  title.textContent = f.name;

  // Meta info
  const modified = new Date(f.modified * 1000);
  const catLabel = f.category && f.category !== "uncategorized"
    ? `<span class="mg-cat-badge">${mgCategoryLabel(f.category)}</span>` : "Uncategorized";
  const casterLabel = f.commentator || "\u2014";
  const locLabel = f.location || "\u2014";
  const descLabel = f.description ? mgEscHtml(f.description) : "\u2014";

  meta.innerHTML = `
    <span class="meta-label">Folder</span><span class="meta-value">${mgEscHtml(f.folder.replace("python/sector_says_oversee/", ""))}</span>
    <span class="meta-label">Size</span><span class="meta-value">${mgFormatSize(f.size)}</span>
    <span class="meta-label">Modified</span><span class="meta-value">${modified.toLocaleString()}</span>
    <span class="meta-label">Category</span><span class="meta-value">${catLabel}</span>
    <span class="meta-label">Commentator</span><span class="meta-value">${mgEscHtml(casterLabel)}</span>
    <span class="meta-label">Location</span><span class="meta-value">${mgEscHtml(locLabel)}</span>
    <span class="meta-label">Description</span><span class="meta-value">${descLabel}</span>
  `;

  // Actions
  const fileUrl = `/api/media/file?path=${encodeURIComponent(f.path)}`;
  actions.innerHTML = `
    <button class="btn btn-sm btn-primary" onclick="mgEditTags('${mgEscAttr(f.path)}')">Edit Tags</button>
    <a href="${fileUrl}" download="${mgEscAttr(f.name)}" class="btn btn-sm btn-secondary">Download</a>
    <button class="btn btn-sm btn-danger" onclick="mgDeleteFile('${mgEscAttr(f.path)}')">Delete</button>
  `;

  // Image preview
  body.innerHTML = `<img src="${fileUrl}" alt="${mgEscAttr(f.name)}">`;

  // Show panel
  panel.classList.remove("hidden");
  panel.offsetHeight;
  panel.classList.add("visible");
};

window.mgClosePreview = function() {
  const panel = document.getElementById("mg-preview");
  panel.classList.remove("visible");
  setTimeout(() => panel.classList.add("hidden"), 250);
  const body = document.getElementById("mg-preview-body");
  if (body) body.innerHTML = "";
  _mgPreviewFile = null;
};

// ─── Upload ─────────────────────────────────────────────────────

window.mgUpload = async function() {
  let folders, categories;
  try {
    [folders, categories] = await Promise.all([
      api("/api/media/folders"),
      api("/api/media/categories"),
    ]);
  } catch (e) { folders = []; categories = []; }

  const catOptions = (categories || []).map(c =>
    `<option value="${c}">${mgCategoryLabel(c)}</option>`
  ).join("");

  const casterOptions = _mgCommentators.map(c =>
    `<option value="${c}">${c}</option>`
  ).join("");

  const html = `
    <h3>Upload Image</h3>
    <div class="field">
      <label>Destination Folder</label>
      <select id="mg-upload-folder">
        ${folders.map(f => `<option value="${f.path}">${f.path.replace("python/sector_says_oversee/", "")}${f.exists ? "" : " (will create)"}</option>`).join("")}
      </select>
    </div>
    <div class="field">
      <label>Image File</label>
      <input type="file" id="mg-upload-file" accept="image/*" style="color:var(--text)">
    </div>
    <hr style="border-color:var(--border);margin:12px 0">
    <h4 style="margin:0 0 8px;color:var(--accent)">Asset Tags</h4>
    <div class="grid-2">
      <div class="field">
        <label>Category</label>
        <select id="mg-upload-category">
          <option value="uncategorized">Uncategorized</option>
          ${catOptions}
        </select>
      </div>
      <div class="field">
        <label>Commentator</label>
        <select id="mg-upload-commentator">
          <option value="">None</option>
          ${casterOptions}
        </select>
      </div>
      <div class="field">
        <label>Location</label>
        <select id="mg-upload-location">
          <option value="">None</option>
          <option>Studio</option>
          <option>PitLane</option>
          <option>Paddock</option>
          <option>FanZone</option>
          <option>Trackside</option>
          <option>Garage</option>
        </select>
      </div>
    </div>
    <div class="field">
      <label>Description</label>
      <input type="text" id="mg-upload-description" placeholder="Optional description...">
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="mgDoUpload()">Upload</button>
    </div>
  `;
  showModal(html);
};

window.mgDoUpload = async function() {
  const fileInput = document.getElementById("mg-upload-file");
  const folder = document.getElementById("mg-upload-folder").value;

  if (!fileInput.files.length) { toast("Select a file first"); return; }

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);
  formData.append("folder", folder);

  const category = document.getElementById("mg-upload-category").value;
  if (category) formData.append("category", category);
  const commentator = document.getElementById("mg-upload-commentator").value;
  if (commentator) formData.append("commentator", commentator);
  const location = document.getElementById("mg-upload-location").value;
  if (location) formData.append("location", location);
  const description = document.getElementById("mg-upload-description").value;
  if (description) formData.append("description", description);

  try {
    const res = await fetch("/api/media/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (data.ok) {
      closeModal();
      toast(`Uploaded: ${data.name}`);
      mgLoadFiles();
    } else {
      toast(data.error || "Upload failed");
    }
  } catch (e) {
    toast("Upload failed");
  }
};

// ─── Delete ─────────────────────────────────────────────────────

function mgSleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function mgReleaseMediaRefs(path) {
  const encoded = encodeURIComponent(path);
  document.querySelectorAll("img").forEach(img => {
    const src = img.getAttribute("src") || "";
    if (src.includes(encoded) || src.includes(path)) {
      img.removeAttribute("src");
      img.src = "";
    }
  });
  const body = document.getElementById("mg-preview-body");
  if (body) body.innerHTML = "";
  const title = document.getElementById("mg-preview-title");
  if (title) title.textContent = "Preview";
  const meta = document.getElementById("mg-preview-meta");
  if (meta) meta.innerHTML = "";
  const actions = document.getElementById("mg-preview-actions");
  if (actions) actions.innerHTML = "";
  const panel = document.getElementById("mg-preview");
  if (panel) panel.classList.remove("visible");
  _mgPreviewFile = null;
  await mgSleep(180);
}

window.mgDeleteFile = async function(path) {
  if (!confirm(`Delete ${path}?`)) return;
  try {
    await mgReleaseMediaRefs(path);
    const res = await api(`/api/media/delete?path=${encodeURIComponent(path)}`, "DELETE");
    if (res.ok) {
      toast("File deleted");
      mgLoadFiles();
    } else {
      toast(res.error || "Delete failed");
    }
  } catch (e) { toast("Delete failed"); }
};

// ─── Tag Editor ────────────────────────────────────────────────

window.mgEditTags = async function(path) {
  const f = _mgFiles.find(x => x.path === path);
  if (!f) return;

  let categories;
  try { categories = await api("/api/media/categories"); } catch(e) { categories = []; }

  const catOptions = (categories || []).map(c =>
    `<option value="${c}" ${(f.category || "uncategorized") === c ? "selected" : ""}>${mgCategoryLabel(c)}</option>`
  ).join("");

  const casterOptions = _mgCommentators.map(c =>
    `<option value="${c}" ${f.commentator === c ? "selected" : ""}>${c}</option>`
  ).join("");

  const locations = ["Studio", "PitLane", "Paddock", "FanZone", "Trackside", "Garage"];
  const locOptions = locations.map(l =>
    `<option value="${l}" ${f.location === l ? "selected" : ""}>${l}</option>`
  ).join("");

  // Normalize tags to a comma-separated string for the input. Legacy
  // rows may have stored tags as a bare string; the array form is the
  // canonical shape going forward.
  let tagsInit = "";
  if (Array.isArray(f.tags)) tagsInit = f.tags.filter(Boolean).join(", ");
  else if (typeof f.tags === "string") tagsInit = f.tags;

  const html = `
    <h3>Edit Tags — ${mgEscHtml(f.name)}</h3>
    <div class="grid-2">
      <div class="field">
        <label>Category</label>
        <select id="mg-tag-category">${catOptions}</select>
      </div>
      <div class="field">
        <label>Commentator</label>
        <select id="mg-tag-commentator">
          <option value="">None</option>
          ${casterOptions}
        </select>
      </div>
      <div class="field">
        <label>Location</label>
        <select id="mg-tag-location">
          <option value="">None</option>
          ${locOptions}
        </select>
      </div>
    </div>
    <div class="field">
      <label>Description</label>
      <input type="text" id="mg-tag-description" value="${mgEscAttr(f.description || "")}" placeholder="Optional description...">
    </div>
    <div class="field">
      <label>Drivers / Tags</label>
      <input type="text" id="mg-tag-tags" value="${mgEscAttr(tagsInit)}" placeholder="Comma-separated. Include driver names to show the photo on that driver's profile.">
    </div>
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="mgSaveTags('${mgEscAttr(path)}')">Save Tags</button>
    </div>
  `;
  showModal(html);
};

window.mgSaveTags = async function(path) {
  const rawTags = (document.getElementById("mg-tag-tags")?.value || "")
    .split(",").map(t => t.trim()).filter(Boolean);
  const payload = {
    file_path: path,
    category: document.getElementById("mg-tag-category").value,
    commentator: document.getElementById("mg-tag-commentator").value || null,
    location: document.getElementById("mg-tag-location").value || null,
    description: document.getElementById("mg-tag-description").value || null,
    tags: rawTags.length ? rawTags : null,
  };

  try {
    const res = await fetch("/api/media/tags", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      closeModal();
      toast("Tags saved");
      const f = _mgFiles.find(x => x.path === path);
      if (f) {
        f.category = payload.category;
        f.commentator = payload.commentator;
        f.location = payload.location;
        f.description = payload.description;
        f.tags = payload.tags;
        f.asset_id = data.id;
      }
      mgApplyFilters();
      if (_mgPreviewFile && _mgPreviewFile.path === path) {
        mgOpenPreview(path);
      }
    } else {
      toast(data.error || "Save failed");
    }
  } catch (e) {
    toast("Save failed");
  }
};

// ─── Helpers ────────────────────────────────────────────────────

function mgFormatSize(bytes) {
  if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + " MB";
  if (bytes > 1024) return (bytes / 1024).toFixed(1) + " KB";
  return bytes + " B";
}

function mgEscHtml(val) {
  if (val === null || val === undefined) return "";
  const d = document.createElement("div");
  d.textContent = String(val);
  return d.innerHTML;
}

function mgEscAttr(val) {
  return String(val || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
