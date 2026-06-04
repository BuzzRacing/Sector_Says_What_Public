/* ============================================================
 * Pit Wall — Garage tab (car library + iRacing sync)
 * ============================================================ */
(function () {
  const $ = (id) => document.getElementById(id);
  let editing = null;

  async function loadAll() {
    const cars = await fetch('/api/cars').then(r => r.json());
    renderLibrary(cars);
  }

  function renderLibrary(rows) {
    const tb = $('cg-library-body');
    if (!Array.isArray(rows) || rows.length === 0) {
      tb.innerHTML = '<tr><td colspan="6" class="empty">No cars in the library yet. Click <strong>Sync from iRacing</strong> to populate.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(c => {
      const logo = c.iracing_logo
        ? `<img src="${esc(c.iracing_logo)}" alt="" style="height:28px;max-width:80px;object-fit:contain">`
        : '<span style="color:#666">—</span>';
      return `
        <tr>
          <td>${logo}</td>
          <td>${esc(c.display_name || '—')}</td>
          <td><code>${esc(c.slug)}</code></td>
          <td>${c.iracing_car_id ?? '<span style="color:#666">—</span>'}</td>
          <td>${esc((c.ir_names || []).join(', ')) || '<span style="color:#666">none</span>'}</td>
          <td><button class="btn btn-sm" data-edit="${esc(c.slug)}">Edit</button></td>
        </tr>
      `;
    }).join('');
    tb.querySelectorAll('[data-edit]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const c = await fetch('/api/cars/' + encodeURIComponent(btn.dataset.edit)).then(r => r.json());
        openEditor(c);
      });
    });
  }

  function openEditor(car) {
    editing = car;
    $('cg-editor-card').style.display = '';
    $('cg-editor-title').textContent = car ? 'Edit Car — ' + (car.display_name || car.slug) : 'New Car';
    $('cg-display-name').value = car?.display_name || '';
    $('cg-slug').value         = car?.slug || '';
    $('cg-aliases').value      = (car?.ir_names || []).join('\n');
    $('cg-notes').value        = car?.notes || '';
    $('cg-delete-btn').style.display = car ? '' : 'none';
    renderIracingCard(car);
    $('cg-editor-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderIracingCard(car) {
    const card = $('cg-iracing-card');
    if (!card) return;
    if (!car || !car.iracing_car_id) {
      card.style.display = 'none';
      return;
    }
    card.style.display = '';
    $('cg-ir-id').textContent = '· iRacing ID ' + car.iracing_car_id +
      (car.iracing_fetched_at ? ' · fetched ' + new Date(car.iracing_fetched_at * 1000).toLocaleString() : '');
    const imgs = $('cg-ir-images');
    imgs.innerHTML = '';
    [['Logo', car.iracing_logo],
     ['Sponsor', car.iracing_sponsor_logo],
     ['Hero', car.iracing_large_image],
     ['Group', car.iracing_group_image]].forEach(([label, url]) => {
      if (!url) return;
      imgs.insertAdjacentHTML('beforeend',
        '<figure style="margin:0;text-align:center"><img src="' + esc(url) + '" alt="' + esc(label) + '" style="max-height:120px;max-width:240px;border:1px solid var(--border);border-radius:4px;background:#111"><figcaption style="font-size:10px;color:var(--text-dim);margin-top:4px">' + esc(label) + '</figcaption></figure>');
    });
    $('cg-ir-detail').textContent    = car.iracing_detail_copy || '(none)';
    $('cg-ir-techspecs').textContent = car.iracing_techspecs_copy || '(none)';
  }

  async function saveCar() {
    const payload = {
      slug: $('cg-slug').value.trim() || undefined,
      display_name: $('cg-display-name').value.trim(),
      ir_names: $('cg-aliases').value.split('\n').map(s => s.trim()).filter(Boolean),
      notes: $('cg-notes').value.trim(),
    };
    if (!payload.display_name) { alert('Display Name is required'); return; }

    const method = editing ? 'PUT' : 'POST';
    const url = editing ? '/api/cars/' + encodeURIComponent(editing.slug) : '/api/cars';
    const res = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const out = await res.json();
    if (out.error) { alert(out.error); return; }
    closeEditor();
    loadAll();
  }

  async function deleteCar() {
    if (!editing) return;
    if (!confirm('Delete car "' + editing.display_name + '"?')) return;
    await fetch('/api/cars/' + encodeURIComponent(editing.slug), { method: 'DELETE' });
    closeEditor();
    loadAll();
  }

  async function syncFromIracing() {
    const btn = $('cg-sync-btn');
    const orig = btn.textContent;
    btn.textContent = 'Syncing…';
    btn.disabled = true;
    try {
      const res = await fetch('/api/cars/sync_iracing', { method: 'POST' });
      const body = await res.json();
      if (!res.ok || body.error) {
        alert(body.error || 'Sync failed');
        return;
      }
      const errs = (body.errors || []).length;
      const msg = `Synced — ${body.created} created, ${body.updated} updated` + (errs ? `, ${errs} errors` : '');
      if (typeof toast === 'function') toast(msg); else alert(msg);
      loadAll();
    } catch (e) {
      alert('Sync failed: ' + e);
    } finally {
      btn.textContent = orig;
      btn.disabled = false;
    }
  }

  function closeEditor() {
    editing = null;
    $('cg-editor-card').style.display = 'none';
  }

  function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function init() {
    if (!$('cg-library-body')) return;
    $('cg-new-btn').addEventListener('click', () => openEditor(null));
    $('cg-save-btn').addEventListener('click', saveCar);
    $('cg-cancel-btn').addEventListener('click', closeEditor);
    $('cg-delete-btn').addEventListener('click', deleteCar);
    $('cg-sync-btn').addEventListener('click', syncFromIracing);

    // Lazy-load when the Garage tab is first activated
    let loaded = false;
    document.querySelectorAll('.sidebar-nav a[data-tab="garage"]').forEach(a => {
      a.addEventListener('click', () => {
        if (!loaded) { loadAll(); loaded = true; }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
