/* ============================================================
   pitwall-db.js — Database Explorer
   Sector Says What — Browse and edit SQLite tables
   ============================================================ */

let _dbState = { table: null, pk: null, columns: [], page: 0, limit: 50, total: 0, sort: null, order: "DESC" };

// ─── Init ───────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const tables = await api("/api/db/tables");
    const sel = document.getElementById("db-table-select");
    tables.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.name;
      opt.textContent = `${t.name} (${t.rows} rows)`;
      sel.appendChild(opt);
    });
  } catch (e) { console.warn("DB init:", e); }
});

// ─── Load Table ─────────────────────────────────────────────────

window.dbLoadTable = async function() {
  const table = document.getElementById("db-table-select").value;
  if (!table) return;
  _dbState.table = table;
  _dbState.page = 0;
  _dbState.sort = null;
  _dbState.order = "DESC";
  await dbFetchRows();
};

async function dbFetchRows() {
  const { table, page, limit, sort, order } = _dbState;
  const offset = page * limit;
  let url = `/api/db/tables/${table}/rows?limit=${limit}&offset=${offset}&order=${order}`;
  if (sort) url += `&sort=${sort}`;

  try {
    const data = await api(url);
    _dbState.columns = data.columns;
    _dbState.pk = data.pk;
    _dbState.total = data.total;
    renderDbGrid(data);
    document.getElementById("db-add-btn").style.display = "";
    document.getElementById("db-row-count").textContent = `${data.total} total rows`;

    // Pagination
    const pages = Math.ceil(data.total / limit);
    const pag = document.getElementById("db-pagination");
    pag.style.display = pages > 1 ? "flex" : "none";
    document.getElementById("db-page-info").textContent = `Page ${page + 1} of ${pages}`;
  } catch (e) {
    toast("Error loading table");
  }
}

function renderDbGrid(data) {
  const grid = document.getElementById("db-grid");
  if (!data.rows.length) {
    grid.innerHTML = '<div class="empty-state"><div class="empty-text">No rows in this table</div></div>';
    return;
  }

  const sortIcon = (col) => {
    if (_dbState.sort !== col) return "";
    return _dbState.order === "ASC" ? " &#9650;" : " &#9660;";
  };

  grid.innerHTML = `<table class="pw-table">
    <thead><tr>
      ${data.columns.map(c => `<th style="cursor:pointer" onclick="dbSort('${c}')">${c}${sortIcon(c)}</th>`).join("")}
      <th style="width:60px"></th>
    </tr></thead>
    <tbody>
      ${data.rows.map(row => `<tr>
        ${data.columns.map(c => {
          const val = row[c] !== null && row[c] !== undefined ? row[c] : "";
          const display = String(val).length > 80 ? String(val).substring(0, 80) + "..." : val;
          return `<td class="editable" data-col="${c}" data-pk="${row[_dbState.pk]}" ondblclick="dbEditCell(this)">${escHtml(display)}</td>`;
        }).join("")}
        <td><button class="btn btn-sm btn-danger" onclick="dbDeleteRow('${row[_dbState.pk]}')">Del</button></td>
      </tr>`).join("")}
    </tbody>
  </table>`;
}

// ─── Sorting ────────────────────────────────────────────────────

window.dbSort = function(col) {
  if (_dbState.sort === col) {
    _dbState.order = _dbState.order === "ASC" ? "DESC" : "ASC";
  } else {
    _dbState.sort = col;
    _dbState.order = "ASC";
  }
  _dbState.page = 0;
  dbFetchRows();
};

// ─── Pagination ─────────────────────────────────────────────────

window.dbPrevPage = function() {
  if (_dbState.page > 0) { _dbState.page--; dbFetchRows(); }
};

window.dbNextPage = function() {
  if ((_dbState.page + 1) * _dbState.limit < _dbState.total) { _dbState.page++; dbFetchRows(); }
};

// ─── Inline Cell Editing ────────────────────────────────────────

window.dbEditCell = function(td) {
  if (td.querySelector("input")) return;
  const col = td.dataset.col;
  const pk = td.dataset.pk;
  const original = td.textContent;

  const input = document.createElement("input");
  input.type = "text";
  input.value = original;
  td.textContent = "";
  td.appendChild(input);
  input.focus();
  input.select();

  const save = async () => {
    const newVal = input.value;
    td.textContent = newVal;
    if (newVal !== original) {
      try {
        await api(`/api/db/tables/${_dbState.table}/rows/${pk}`, "PUT", {
          _pk_col: _dbState.pk,
          [col]: newVal,
        });
        toast("Cell updated");
      } catch (e) { td.textContent = original; toast("Update failed"); }
    }
  };

  input.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    if (e.key === "Escape") { td.textContent = original; }
  });
  input.addEventListener("blur", save);
};

// ─── Add Row ────────────────────────────────────────────────────

window.dbAddRow = async function() {
  const schema = await api(`/api/db/tables/${_dbState.table}/schema`);
  const fields = schema.filter(c => !c.pk || c.name === "driver_name"); // skip auto-increment PKs

  const html = `
    <h3>Add Row to ${_dbState.table}</h3>
    ${fields.map(c => `
      <div class="field">
        <label>${c.name} <span style="color:var(--text-dim);font-weight:400">(${c.type || "TEXT"})</span></label>
        <input type="text" id="add-row-${c.name}" placeholder="${c.default || ""}">
      </div>
    `).join("")}
    <div class="modal-actions">
      <button class="btn" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="dbInsertRow()">Insert</button>
    </div>
  `;
  showModal(html);
};

window.dbInsertRow = async function() {
  const schema = await api(`/api/db/tables/${_dbState.table}/schema`);
  const data = {};
  schema.forEach(c => {
    const el = document.getElementById(`add-row-${c.name}`);
    if (el && el.value.trim()) data[c.name] = el.value.trim();
  });
  try {
    await api(`/api/db/tables/${_dbState.table}/rows`, "POST", data);
    closeModal();
    toast("Row inserted");
    dbFetchRows();
  } catch (e) { toast("Insert failed"); }
};

// ─── Delete Row ─────────────────────────────────────────────────

window.dbDeleteRow = async function(pkVal) {
  if (!confirm(`Delete row ${_dbState.pk}=${pkVal}?`)) return;
  try {
    await api(`/api/db/tables/${_dbState.table}/rows/${pkVal}?pk_col=${_dbState.pk}`, "DELETE");
    toast("Row deleted");
    dbFetchRows();
  } catch (e) { toast("Delete failed"); }
};

// ─── SQL Query ──────────────────────────────────────────────────

window.dbRunQuery = async function() {
  const sql = document.getElementById("db-sql").value.trim();
  if (!sql) return;
  const result = document.getElementById("db-query-result");
  try {
    const data = await api("/api/db/query", "POST", { sql });
    if (data.error) { result.innerHTML = `<p style="color:var(--red)">${escHtml(data.error)}</p>`; return; }
    if (!data.rows.length) { result.innerHTML = '<p style="color:var(--text-dim)">No results</p>'; return; }
    result.innerHTML = `<p style="color:var(--text-dim);font-size:12px;margin-bottom:8px">${data.count} rows returned</p>
      <table class="pw-table"><thead><tr>${data.columns.map(c => `<th>${c}</th>`).join("")}</tr></thead>
      <tbody>${data.rows.map(r => `<tr>${data.columns.map(c => `<td>${escHtml(r[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  } catch (e) { result.innerHTML = `<p style="color:var(--red)">Query error</p>`; }
};

// ─── Helpers ────────────────────────────────────────────────────

function escHtml(val) {
  if (val === null || val === undefined) return '<span style="color:var(--text-dim)">NULL</span>';
  const str = String(val);
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function showModal(html) {
  document.getElementById("modal-content").innerHTML = html;
  document.getElementById("modal-overlay").classList.remove("hidden");
}

window.closeModal = function() {
  document.getElementById("modal-overlay").classList.add("hidden");
};

// Close modal on overlay click
document.addEventListener("click", e => {
  if (e.target.id === "modal-overlay") closeModal();
});
