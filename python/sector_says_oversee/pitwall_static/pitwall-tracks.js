/* ============================================================
 * Pit Wall — Circuits tab (track library CRUD)
 * ============================================================ */
(function () {
  const $ = (id) => document.getElementById(id);
  let editing = null; // current track being edited (null = new)

  async function loadAll() {
    const [library, unmapped] = await Promise.all([
      fetch('/api/tracks').then(r => r.json()),
      fetch('/api/tracks/unmapped').then(r => r.json()),
    ]);
    renderUnmapped(unmapped);
    renderLibrary(library);
  }

  function renderUnmapped(rows) {
    const tb = $('ck-unmapped-body');
    if (!Array.isArray(rows) || rows.length === 0) {
      tb.innerHTML = '<tr><td colspan="4" class="empty">All tracks in your race history are mapped. 🏁</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(r => `
      <tr>
        <td>${esc(r.track_name)}</td>
        <td>${r.race_count}</td>
        <td>${(r.last_seen || '').slice(0,10)}</td>
        <td>
          <button class="btn btn-sm" data-add="${esc(r.track_name)}">+ Add Map</button>
          <a class="btn btn-sm" href="/pitwall/track_editor.html?name=${encodeURIComponent(r.track_name)}" target="_blank">Draw</a>
        </td>
      </tr>
    `).join('');
    tb.querySelectorAll('[data-add]').forEach(btn => {
      btn.addEventListener('click', () => openEditor(null, btn.dataset.add));
    });
  }

  function renderLibrary(rows) {
    const tb = $('ck-library-body');
    if (!Array.isArray(rows) || rows.length === 0) {
      tb.innerHTML = '<tr><td colspan="4" class="empty">No tracks in the library yet. Add one above or click <strong>+ New Track</strong>.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(t => `
      <tr>
        <td>${esc(t.display_name || '—')}</td>
        <td><code>${esc(t.slug)}</code></td>
        <td>${esc((t.ir_names || []).join(', ')) || '<span style="color:#666">none</span>'}</td>
        <td>
          <button class="btn btn-sm" data-edit="${esc(t.slug)}">Edit</button>
          <a class="btn btn-sm" href="/pitwall/track_editor.html?slug=${encodeURIComponent(t.slug)}" target="_blank">Draw</a>
        </td>
      </tr>
    `).join('');
    tb.querySelectorAll('[data-edit]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const t = await fetch('/api/tracks/' + encodeURIComponent(btn.dataset.edit)).then(r => r.json());
        openEditor(t);
      });
    });
  }

  function openEditor(track, prefillIrName) {
    editing = track;
    $('ck-editor-card').style.display = '';
    $('ck-editor-title').textContent = track ? 'Edit Track — ' + (track.display_name || track.slug) : 'New Track';
    $('ck-display-name').value = track?.display_name || '';
    $('ck-slug').value         = track?.slug || '';
    $('ck-aliases').value      = (track?.ir_names || []).join('\n') || (prefillIrName || '');
    $('ck-viewbox').value      = track?.viewBox || '0 0 1260 720';
    $('ck-sf').value           = track?.sf_offset ?? 0;
    $('ck-path').value         = track?.centerline_path || '';
    $('ck-background').value   = track?.background || '';
    refreshBgPreview();
    $('ck-notes').value        = track?.notes || '';
    $('ck-delete-btn').style.display = track ? '' : 'none';
    $('ck-fetch-iracing-btn').style.display = track ? '' : 'none';

    if (prefillIrName && !track) {
      $('ck-display-name').value = prefillIrName.replace(/\s*-\s*.*$/, '');
    }
    refreshPreview();
    renderIracingCard(track);
    $('ck-editor-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function renderIracingCard(track) {
    const card = $('ck-iracing-card');
    if (!card) return;
    if (!track || !track.iracing_track_id) {
      card.style.display = 'none';
      return;
    }
    card.style.display = '';
    $('ck-ir-id').textContent = '· iRacing ID ' + track.iracing_track_id +
      (track.iracing_fetched_at ? ' · fetched ' + new Date(track.iracing_fetched_at * 1000).toLocaleString() : '');
    const imgs = $('ck-ir-images');
    imgs.innerHTML = '';
    [['Logo', track.iracing_logo], ['Hero', track.iracing_large_image], ['Map', track.iracing_track_map]].forEach(([label, url]) => {
      if (!url) return;
      imgs.insertAdjacentHTML('beforeend',
        '<figure style="margin:0;text-align:center"><img src="' + esc(url) + '" alt="' + esc(label) + '" style="max-height:120px;max-width:240px;border:1px solid var(--border);border-radius:4px;background:#111"><figcaption style="font-size:10px;color:var(--text-dim);margin-top:4px">' + esc(label) + '</figcaption></figure>');
    });
    $('ck-ir-detail').textContent    = track.iracing_detail_copy || '(none)';
    $('ck-ir-techspecs').textContent = track.iracing_techspecs_copy || '(none)';
  }

  async function fetchFromIracing() {
    if (!editing) return;
    const btn = $('ck-fetch-iracing-btn');
    const orig = btn.textContent;
    btn.textContent = 'Fetching…';
    btn.disabled = true;
    try {
      const res = await fetch('/api/tracks/' + encodeURIComponent(editing.slug) + '/fetch_iracing', { method: 'POST' });
      const body = await res.json();
      if (!res.ok || body.error) {
        alert(body.error || 'Fetch failed');
        return;
      }
      editing = body;
      renderIracingCard(body);
      loadAll();
    } catch (e) {
      alert('Fetch failed: ' + e);
    } finally {
      btn.textContent = orig;
      btn.disabled = false;
    }
  }

  function refreshPreview() {
    const d = $('ck-path').value.trim();
    const vb = $('ck-viewbox').value.trim() || '0 0 1260 720';
    $('ck-preview').setAttribute('viewBox', vb);
    ['ck-preview-glow', 'ck-preview-surface', 'ck-preview-stripe'].forEach(id => {
      $(id).setAttribute('d', d);
    });
  }

  function refreshBgPreview() {
    const url = $('ck-background').value.trim();
    const wrap = $('ck-bg-preview');
    const img = $('ck-bg-preview-img');
    if (!wrap || !img) return;
    if (!url) { wrap.style.display = 'none'; img.removeAttribute('src'); return; }
    img.onload = () => { wrap.style.display = ''; };
    img.onerror = () => { wrap.style.display = 'none'; };
    img.src = url;
  }

  // Pick a background from the media library. Reuses /api/media/files
  // so the thumbnail grid shows every registered image — the chosen
  // file's /api/media/file?path=... URL is what ends up on the track.
  async function pickBackgroundFromMedia() {
    let files = [];
    try {
      const res = await fetch('/api/media/files').then(r => r.json());
      files = (res.files || []).filter(f => f.type === 'image');
    } catch (e) { /* empty */ }
    if (!files.length) { alert('No images found in the media library.'); return; }

    const modal = document.getElementById('modal');
    if (!modal) { alert('Modal host missing.'); return; }
    const cards = files.map(f => {
      const src = '/api/media/file?path=' + encodeURIComponent(f.path);
      return (
        '<div class="bg-pick-thumb" data-url="' + esc(src) + '">' +
          '<img src="' + esc(src) + '" loading="lazy">' +
          '<div class="bg-pick-name">' + esc(f.name) + '</div>' +
        '</div>'
      );
    }).join('');
    const html =
      '<h3>Select Track Background</h3>' +
      '<div class="bg-pick-grid" id="ck-pick-grid">' + cards + '</div>' +
      '<div class="modal-actions">' +
        '<button class="btn" onclick="closeModal()">Cancel</button>' +
        '<button class="btn btn-primary" id="ck-pick-confirm">Set Background</button>' +
      '</div>';
    if (typeof showModal === 'function') {
      showModal(html);
    } else {
      modal.innerHTML = '<div class="modal-card">' + html + '</div>';
      modal.classList.add('open');
    }

    let selected = '';
    const grid = document.getElementById('ck-pick-grid');
    if (grid) {
      grid.addEventListener('click', (e) => {
        const thumb = e.target.closest('.bg-pick-thumb');
        if (!thumb) return;
        grid.querySelectorAll('.bg-pick-thumb').forEach(t => t.classList.remove('selected'));
        thumb.classList.add('selected');
        selected = thumb.dataset.url || '';
      });
    }
    const confirm = document.getElementById('ck-pick-confirm');
    if (confirm) {
      confirm.addEventListener('click', () => {
        if (!selected) { alert('Pick an image first.'); return; }
        $('ck-background').value = selected;
        refreshBgPreview();
        if (typeof closeModal === 'function') closeModal();
        else modal.classList.remove('open');
      });
    }
  }

  async function saveTrack() {
    const payload = {
      slug: $('ck-slug').value.trim() || undefined,
      display_name: $('ck-display-name').value.trim(),
      ir_names: $('ck-aliases').value.split('\n').map(s => s.trim()).filter(Boolean),
      viewBox: $('ck-viewbox').value.trim() || '0 0 1260 720',
      sf_offset: parseFloat($('ck-sf').value) || 0,
      centerline_path: $('ck-path').value.trim(),
      background: $('ck-background').value.trim(),
      notes: $('ck-notes').value.trim(),
    };
    if (!payload.display_name) { alert('Display Name is required'); return; }
    if (!payload.centerline_path) { alert('Centerline Path is required'); return; }

    const method = editing ? 'PUT' : 'POST';
    const url = editing ? '/api/tracks/' + encodeURIComponent(editing.slug) : '/api/tracks';
    const res = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const out = await res.json();
    if (out.error) { alert(out.error); return; }
    closeEditor();
    loadAll();
  }

  async function deleteTrack() {
    if (!editing) return;
    if (!confirm('Delete track "' + editing.display_name + '"?')) return;
    await fetch('/api/tracks/' + encodeURIComponent(editing.slug), { method: 'DELETE' });
    closeEditor();
    loadAll();
  }

  function closeEditor() {
    editing = null;
    $('ck-editor-card').style.display = 'none';
  }

  function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function init() {
    if (!$('ck-library-body')) return; // tab not present
    $('ck-new-btn').addEventListener('click', () => openEditor(null));
    $('ck-save-btn').addEventListener('click', saveTrack);
    $('ck-cancel-btn').addEventListener('click', closeEditor);
    $('ck-delete-btn').addEventListener('click', deleteTrack);
    $('ck-fetch-iracing-btn').addEventListener('click', fetchFromIracing);
    $('ck-path').addEventListener('input', refreshPreview);
    $('ck-viewbox').addEventListener('input', refreshPreview);
    $('ck-background').addEventListener('input', refreshBgPreview);
    $('ck-pick-bg-btn').addEventListener('click', pickBackgroundFromMedia);

    // Lazy-load when the Circuits tab is first activated
    let loaded = false;
    document.querySelectorAll('.sidebar-nav a[data-tab="circuits"]').forEach(a => {
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
