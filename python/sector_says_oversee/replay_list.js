/* ============================================================
 * Race Replays — list page (GrumpyRobot-style cards)
 * Click a card to open /replay/<race_id>
 * ============================================================ */
(function () {
  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function focusDeltaBlock(grid, finish) {
    if (!grid || !finish) return '';
    const d = grid - finish; // positive = gained places
    if (d > 0) return `<span class="rc-focus-delta up">▲ +${d}</span>`;
    if (d < 0) return `<span class="rc-focus-delta down">▼ ${d}</span>`;
    return '<span class="rc-focus-delta flat">— even</span>';
  }

  function lastName(name) {
    if (!name) return '';
    const parts = String(name).trim().split(/\s+/);
    return parts.length > 1 ? parts[parts.length - 1] : parts[0];
  }

  function photoBg(url) {
    return url ? `background-image:url('${url.replace(/'/g, '%27')}');` : '';
  }

  function mediaUrl(path) {
    return `/api/media/file?path=${encodeURIComponent(path)}`;
  }

  function stableDriverImage(name, custId) {
    const seed = `${custId || ''}|${name || ''}`;
    let h = 0;
    for (let i = 0; i < seed.length; i++) {
      h = ((h * 31) + seed.charCodeAt(i)) >>> 0;
    }
    const n = String((h % 10) + 1).padStart(2, '0');
    return mediaUrl(`python/sector_says_oversee/drivers/default_male_${n}.png`);
  }

  function driverImage(url, name, custId) {
    return url || stableDriverImage(name, custId);
  }

  function initials(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    if (parts.length === 1) return esc(parts[0].slice(0, 2).toUpperCase());
    return esc((parts[0][0] + parts[parts.length - 1][0]).toUpperCase());
  }

  function cardHtml(r) {
    const track = r.track_name || 'Unknown Track';
    const config = r.track_config ? ` — ${r.track_config}` : '';
    const date = (r.race_date || '').slice(0, 10);
    const drivers = r.drivers_count || r.car_count || 0;
    const laps = r.total_laps || 0;
    const finishPos = r.owner_finish;
    const gridPos = r.owner_grid;

    const winnerRaw = r.winner_name || '';
    const winnerName = winnerRaw ? esc(winnerRaw.toUpperCase()) : '';
    const winnerImg = driverImage(r.winner_image, winnerRaw, r.winner_cust_id);
    const winnerTile = winnerImg
      ? `<div class="rc-photo-frame" style="${photoBg(winnerImg)}"></div>`
      : `<div class="rc-photo-frame no-photo">${initials(winnerRaw)}</div>`;

    const ownerName = r.owner_name ? esc(lastName(r.owner_name).toUpperCase()) : '';
    const ownerImg = driverImage(r.owner_image, r.owner_name, r.owner_cust_id);
    const focusRow = ownerName ? `
      <div class="rc-focus-row">
        <div class="rc-focus-photo" style="${photoBg(ownerImg)}"></div>
        <div class="rc-focus-info">
          <div class="rc-row-label">Focus Driver</div>
          <div class="rc-focus-name">${ownerName}</div>
        </div>
        ${finishPos ? `<div class="rc-focus-pill">P${finishPos}</div>` : ''}
        ${focusDeltaBlock(gridPos, finishPos)}
      </div>
    ` : '';

    const dotdName = r.dotd_name ? esc(lastName(r.dotd_name).toUpperCase()) : '';
    const dotdPct = (r.dotd_percent != null) ? Number(r.dotd_percent).toFixed(1) : null;
    const dotdImg = driverImage(r.dotd_image, r.dotd_name, r.dotd_cust_id);
    const dotdRow = dotdName ? `
      <div class="rc-dotd-row">
        <div class="rc-dotd-photo" style="${photoBg(dotdImg)}"></div>
        <div class="rc-dotd-info">
          <span class="rc-dotd-tag">DOTD</span>
          <span class="rc-dotd-name">${dotdName}</span>
        </div>
        ${dotdPct != null ? `<span class="rc-dotd-pct">${dotdPct}%</span>` : ''}
      </div>
    ` : '';

    return `
      <div class="replist-card" data-race-id="${r.race_id}">
        <div class="rc-body">
          <div class="rc-track">${esc(track)}${esc(config)}</div>
          <div class="rc-meta">
            <span><strong>${esc(date)}</strong></span>
            <span><strong>${drivers}</strong> drivers</span>
            <span><strong>${laps}</strong> laps</span>
          </div>
          ${winnerName ? `
            <div class="rc-winner-block">
              <div class="rc-row-label">Race Winner</div>
              <div class="rc-winner-name">${winnerName}</div>
            </div>` : ''
          }
          ${focusRow}
          ${dotdRow}
        </div>
        <div class="rc-photo-col">
          <div class="rc-photo-label">Winner</div>
          ${winnerTile}
        </div>
      </div>
    `;
  }

  async function load() {
    try {
      const races = await fetch('/api/replay/list').then(r => r.json());
      const grid = $('rl-grid');
      const count = $('rl-count');
      if (!Array.isArray(races) || races.length === 0) {
        count.textContent = 'No races on file';
        grid.innerHTML = '<div class="replist-empty">No races recorded yet. Run a race and it will appear here.</div>';
        return;
      }
      count.textContent = `${races.length} race${races.length === 1 ? '' : 's'} on file`;
      grid.innerHTML = races.map(cardHtml).join('');
      grid.querySelectorAll('.replist-card').forEach(card => {
        card.addEventListener('click', () => {
          window.location.href = '/replay/' + card.dataset.raceId;
        });
      });
    } catch (e) {
      $('rl-grid').innerHTML = `<div class="replist-empty">Failed to load races: ${esc(e.message)}</div>`;
    }
  }

  /* ── Page background (uses Pit Wall's media library) ── */
  const PAGE_KEY = 'replays';
  let _selectedBg = '';

  async function applyBackground() {
    try {
      const all = await fetch('/api/media/page-backgrounds').then(r => r.json());
      const path = all[PAGE_KEY];
      const layer = document.querySelector('.race-bg .bg-a');
      if (!layer) return;
      if (path) {
        layer.style.backgroundImage = `url(/api/media/file?path=${encodeURIComponent(path)})`;
      } else {
        layer.style.backgroundImage = '';
      }
    } catch (e) { /* silent */ }
  }

  async function openBgPicker() {
    let files = [];
    try {
      const res = await fetch('/api/media/files').then(r => r.json());
      files = (res.files || []).filter(f => f.type === 'image');
    } catch (e) {}
    const all = await fetch('/api/media/page-backgrounds').then(r => r.json()).catch(() => ({}));
    const current = all[PAGE_KEY] || '';
    _selectedBg = current;

    const grid = $('bgpick-grid');
    grid.innerHTML = files.length ? files.map(f => `
      <div class="bgpick-thumb ${f.path === current ? 'selected' : ''}" data-path="${esc(f.path)}">
        <img src="/api/media/file?path=${encodeURIComponent(f.path)}" loading="lazy">
        <div class="bgpick-name">${esc(f.name)}</div>
      </div>
    `).join('') : '<div style="color:#9A9AB0;font-family:Titillium Web,sans-serif">No images found. Upload some in the Pit Wall Media tab.</div>';

    grid.querySelectorAll('.bgpick-thumb').forEach(t => {
      t.addEventListener('click', () => {
        grid.querySelectorAll('.bgpick-thumb').forEach(x => x.classList.remove('selected'));
        t.classList.add('selected');
        _selectedBg = t.dataset.path;
      });
    });

    $('bgpick-overlay').classList.add('open');
  }

  function closeBgPicker() { $('bgpick-overlay').classList.remove('open'); }

  async function saveBackground() {
    if (!_selectedBg) { closeBgPicker(); return; }
    await fetch('/api/media/page-backgrounds', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: PAGE_KEY, file_path: _selectedBg }),
    });
    closeBgPicker();
    applyBackground();
  }

  async function clearBackground() {
    await fetch('/api/media/page-backgrounds', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: PAGE_KEY, file_path: null }),
    });
    closeBgPicker();
    applyBackground();
  }

  function init() {
    load();
    applyBackground();
    $('rl-bg-btn').addEventListener('click', openBgPicker);
    $('bgpick-cancel').addEventListener('click', closeBgPicker);
    $('bgpick-save').addEventListener('click', saveBackground);
    $('bgpick-clear').addEventListener('click', clearBackground);
    $('bgpick-overlay').addEventListener('click', (e) => {
      if (e.target.id === 'bgpick-overlay') closeBgPicker();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
