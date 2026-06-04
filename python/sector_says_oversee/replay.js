/* ============================================================
 * Sector Says What — Race Replay (standalone window)
 * Uses the shared HUD timing-tower / tower-row classes so it
 * matches the live broadcast HUD exactly.
 * ============================================================ */
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { running: false, animId: 0, race: null };

  /* Tighten the SVG viewBox to fit the centerline path bbox + padding,
   * and update the element's aspect-ratio so the stage doesn't letterbox. */
  function fitTrackViewBox(svg) {
    const path = $('rp-raceline');
    if (!path || !path.getAttribute('d')) return;
    let bb;
    try { bb = path.getBBox(); } catch (e) { return; }
    if (!bb || !bb.width || !bb.height) return;
    const padX = bb.width * 0.06;
    const padY = bb.height * 0.10;
    const x = bb.x - padX, y = bb.y - padY;
    const w = bb.width + padX * 2, h = bb.height + padY * 2;
    svg.setAttribute('viewBox', `${x.toFixed(1)} ${y.toFixed(1)} ${w.toFixed(1)} ${h.toFixed(1)}`);
    // Don't set svg.style.aspectRatio — it forces tall tracks (e.g. Road
    // Atlanta) to push the slider/control rows off-screen. Letting the SVG
    // size to its container with preserveAspectRatio="xMidYMid meet" scales
    // the track to fit instead.
    svg.style.aspectRatio = '';
  }

  /* ── Initial race id from URL (/replay/<id>) ── */
  function urlRaceId() {
    const m = location.pathname.match(/\/replay\/(\d+)/);
    return m ? m[1] : null;
  }

  /* ── Race picker ── */
  async function loadRaceList() {
    const sel = $('rp-race-picker');
    try {
      const races = await fetch('/api/history/races?limit=100').then(r => r.json());
      if (!Array.isArray(races) || races.length === 0) {
        sel.innerHTML = '<option value="">No races in database</option>';
        return;
      }
      sel.innerHTML = '<option value="">— Select a race —</option>' +
        races.map(r => {
          const date = (r.race_date || '').slice(0, 10);
          const track = r.track_name || 'Unknown';
          const winner = r.winner_name ? ` · 🏆 ${r.winner_name}` : '';
          return `<option value="${r.race_id}">#${r.race_id} · ${date} · ${track}${winner}</option>`;
        }).join('');
      const initial = urlRaceId();
      if (initial) {
        sel.value = initial;
        loadRaceFromDb(initial);
      }
    } catch (e) {
      sel.innerHTML = '<option value="">Failed to load races</option>';
    }
  }

  async function loadRaceFromDb(raceId) {
    try {
      const [data, timeline, profiles] = await Promise.all([
        fetch('/api/replay/' + raceId).then(r => r.json()),
        fetch('/api/replay/' + raceId + '/timeline').then(r => r.json()).catch(() => null),
        fetch('/sector_said_exports/driver_profiles.json').then(r => r.json()).catch(() => ({})),
      ]);
      if (data.error) { alert(data.error); return; }
      data._timeline = (timeline && Array.isArray(timeline.events)) ? timeline.events : [];
      data._driverProfiles = profiles || {};
      setupAnimation(data);
    } catch (e) {
      alert('Failed to load race: ' + e.message);
    }
  }

  /* ── Helpers ── */
  function abbrev(name) {
    const parts = (name || '').trim().split(/\s+/);
    if (parts.length >= 2) return parts[parts.length - 1].substring(0, 3).toUpperCase();
    return (name || '').substring(0, 3).toUpperCase();
  }
  function fmtTime(sec) { const m = Math.floor(sec / 60), s = Math.floor(sec % 60); return m + ':' + (s < 10 ? '0' : '') + s; }
  function lastName(name) {
    const parts = (name || '').trim().split(/\s+/);
    return parts.length >= 2 ? parts[parts.length - 1] : (name || '—');
  }
  function ordinal(n) {
    const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }
  function formatLapTime(sec) {
    if (!sec || sec <= 0) return '—';
    const m = Math.floor(sec / 60);
    const s = sec - m * 60;
    return m + ':' + (s < 10 ? '0' : '') + s.toFixed(3);
  }
  function fmtInterval(sec) {
    if (sec < 60) return '+' + sec.toFixed(3);
    const m = Math.floor(sec / 60); let s = (sec - m * 60).toFixed(3);
    if (parseFloat(s) < 10) s = '0' + s;
    return '+' + m + ':' + s;
  }

  /* ── Bottom ticker — populated from the current race data on load
   *    and refreshed every frame for the live lap counter. Same chrome
   *    as the broadcast HUD ticker. ── */
  function buildTickerItems(data, liveLap) {
    const items = [];
    const push = (label, value, highlight) => {
      items.push(
        '<span class="ticker-item"><span class="tl">' + label + '</span> ' +
        '<span class="' + (highlight ? 'tg' : 'tv') + '">' + value + '</span></span>'
      );
    };
    if (!data) return '';
    const track = (data.track_name || '') + (data.track_config ? ' — ' + data.track_config : '');
    if (track) push('Track', track);
    if (data.total_laps) push('Laps', (liveLap != null ? liveLap + ' / ' : '') + data.total_laps);
    if (Array.isArray(data.drivers)) push('Cars', String(data.drivers.length));
    if (data.winner_name) push('Winner', data.winner_name, true);
    if (data.fastest_lap && data.fastest_lap.time_s > 0) {
      push('Fastest Lap',
        (data.fastest_lap.driver_name || '—') + ' ' + formatLapTime(data.fastest_lap.time_s),
        true);
    }
    if (data.dotd && data.dotd.name) {
      push('DOTD', data.dotd.name + (data.dotd.score ? ' — ' + data.dotd.score.toFixed(0) : ''), true);
    }
    // Focus Driver — drivers with is_us set get a ticker chip showing
    // their grid->finish position delta. Positive means places gained.
    if (Array.isArray(data.drivers)) {
      const fd = data.drivers.find(d => d.is_us);
      if (fd) {
        const start = (fd.race_start_pos || 0) + 1;
        const finish = (fd.race_finish_pos || 0) + 1;
        const delta = start - finish;
        const deltaStr = delta > 0 ? ' ▲' + delta
                       : delta < 0 ? ' ▼' + Math.abs(delta)
                       : '';
        push('Focus Driver', fd.name + ' P' + finish + deltaStr, true);
      }
    }
    return items.join('');
  }

  function updateReplayTicker(data, liveLap) {
    const track = $('ticker-track');
    if (!track) return;
    const inner = buildTickerItems(data, liveLap);
    if (!inner) {
      track.innerHTML = '<span style="padding:0 24px;color:var(--text-secondary);' +
        'font-family:var(--font-display);font-size:13px;letter-spacing:1px;">' +
        'PICK A RACE FROM THE RIGHT PANEL TO BEGIN</span>';
      return;
    }
    // Duplicate for seamless scroll — same technique as broadcast HUD.
    track.innerHTML = inner + inner;
  }

  /* ── Finish card (Phase D) ── */
  function _resolveDriverPhoto(name, profiles, iracingUrl) {
    const prof = Object.values(profiles).find(pr =>
      (pr.name || '').toLowerCase() === (name || '').toLowerCase()
    );
    return iracingUrl
      || (prof && prof.iracing_image_url)
      || (prof && prof.profile_image
            ? '/api/media/file?path=' + encodeURIComponent(prof.profile_image)
            : null);
  }

  /* Decode an image fully before we ask the DOM to display it, so the
   * finish podium / DOTD photos don't pop in a frame or two after the
   * card appears. Cached in memory so repeat calls are instant. */
  const _preloadedImgs = new Set();
  function _preloadImage(url) {
    if (!url) return Promise.resolve();
    if (_preloadedImgs.has(url)) return Promise.resolve();
    return new Promise(resolve => {
      const img = new Image();
      img.decoding = "async";
      img.onload = img.onerror = () => { _preloadedImgs.add(url); resolve(); };
      img.src = url;
      if (typeof img.decode === "function") {
        img.decode().then(() => { _preloadedImgs.add(url); resolve(); }).catch(() => {});
      }
    });
  }

  async function buildFinishCard(data) {
    const finishEl = $('rp-finish');
    if (!finishEl) return;
    finishEl.classList.remove('is-visible');
    finishEl.setAttribute('aria-hidden', 'true');

    const profiles = data._driverProfiles || {};

    // Gather every URL up front and decode them in parallel. The finish
    // card doesn't appear until the race ends, so spending a few hundred
    // ms here at load time costs nothing visible to the user.
    const podium = Array.isArray(data.podium) ? data.podium : [];
    const podiumPhotos = podium.map(p =>
      _resolveDriverPhoto(p.name, profiles, p.iracing_image_url)
    );
    const dotdUrl = (data.dotd && data.dotd.name)
      ? _resolveDriverPhoto(data.dotd.name, profiles, data.dotd.iracing_image_url)
      : null;
    await Promise.all([...podiumPhotos, dotdUrl].filter(Boolean).map(_preloadImage));

    // ── Podium: gold/silver/bronze cards with F1 color name blocks ──
    const podiumEl = $('rp-podium');
    if (podiumEl) {
      podiumEl.innerHTML = '';
      podium.forEach((p, i) => {
        const block = document.createElement('div');
        block.className = 'finish-podium-block fp-' + p.pos + (p.is_owner ? ' is-owner' : '');

        const photoUrl = podiumPhotos[i];
        const photoStyle = photoUrl
          ? 'background-image:url(\'' + photoUrl + '\')'
          : '';
        const color = '#' + (p.color || '555');

        block.innerHTML =
          '<div class="fp-pos-label">' + p.pos + '</div>' +
          '<div class="fp-photo" style="' + photoStyle + '"></div>' +
          '<div class="fp-info" style="--driver-color:' + color + '">' +
            '<div class="fp-name">' + lastName(p.name || '\u2014') + '</div>' +
          '</div>';
        podiumEl.appendChild(block);
      });
    }

    // ── DOTD: photo flush left, F1 color body ──
    const dotdCard = $('rp-dotd-card');
    if (dotdCard) {
      if (data.dotd && data.dotd.name) {
        dotdCard.style.display = '';
        $('rp-dotd-name').textContent = data.dotd.name;
        $('rp-dotd-pts').textContent = (data.dotd.percent || 0).toFixed(1) + '%';
        const dColor = '#' + (data.dotd.color || 'D4AF37');
        const dotdBody = $('rp-dotd-body');
        if (dotdBody) dotdBody.style.setProperty('--hero-team-color', dColor);

        const dotdPhoto = $('rp-dotd-photo');
        if (dotdPhoto && dotdUrl) {
          dotdPhoto.style.backgroundImage = "url('" + dotdUrl + "')";
          dotdPhoto.style.backgroundSize = 'cover';
          dotdPhoto.style.backgroundPosition = 'center top';
        }
      } else {
        dotdCard.style.display = 'none';
      }
    }
  }
  function showFinish() {
    const el = $('rp-finish');
    if (el) { el.classList.add('is-visible'); el.setAttribute('aria-hidden', 'false'); }
  }
  function hideFinish() {
    const el = $('rp-finish');
    if (el) { el.classList.remove('is-visible'); el.setAttribute('aria-hidden', 'true'); }
  }

  /* ── Race Results Table ── */
  function _licClass(level) {
    if (!level || level <= 0) return { cls: '?', letter: '?' };
    if (level >= 18) return { cls: 'A', letter: 'A' };
    if (level >= 14) return { cls: 'B', letter: 'B' };
    if (level >= 10) return { cls: 'C', letter: 'C' };
    if (level >= 6)  return { cls: 'D', letter: 'D' };
    return { cls: 'R', letter: 'R' };
  }

  window.openResultsTable = function() {
    const data = state.race;
    if (!data || !data.drivers) return;

    const sub = document.getElementById('results-sub');
    if (sub) {
      sub.textContent = (data.track_name || '') +
        (data.track_config ? ' — ' + data.track_config : '') +
        ' · ' + (data.total_laps || '?') + ' Laps';
    }

    // Sort by finish position
    const sorted = data.drivers.slice().sort((a, b) =>
      (a.finish_position || 999) - (b.finish_position || 999)
    );

    // Find fastest lap across field
    let fastestLap = Infinity;
    sorted.forEach(d => { if (d.best_lap && d.best_lap > 0 && d.best_lap < fastestLap) fastestLap = d.best_lap; });

    // Find top DOTD vote-share so we can highlight the leader
    let topDotdPct = 0;
    sorted.forEach(d => { if ((d.dotd_percent || 0) > topDotdPct) topDotdPct = d.dotd_percent; });

    const tbody = document.getElementById('results-tbody');
    tbody.innerHTML = '';

    sorted.forEach(d => {
      const pos = d.finish_position || '?';
      const grid = d.grid_position || '?';
      const posClass = pos === 1 ? 'p1' : pos === 2 ? 'p2' : pos === 3 ? 'p3' : 'pn';
      const color = '#' + (d.livery ? d.livery.color1 : '555');

      // Grid delta
      let gridHtml = '';
      if (typeof grid === 'number' && typeof pos === 'number') {
        const delta = grid - pos;
        if (delta > 0) gridHtml = '<span class="grid-up">▲' + delta + '</span>';
        else if (delta < 0) gridHtml = '<span class="grid-dn">▼' + Math.abs(delta) + '</span>';
        else gridHtml = '<span class="grid-eq">—</span>';
      }

      // License badge
      const lic = _licClass(d.license_level);
      const sr = d.license_sub_level ? (d.license_sub_level / 100).toFixed(2) : '?.??';
      const licHtml = '<span class="lic-badge lic-' + lic.cls + '">' + lic.letter + ' ' + sr + '</span>';

      // Best lap
      let bestHtml = '—';
      if (d.best_lap && d.best_lap > 0) {
        const isPurple = Math.abs(d.best_lap - fastestLap) < 0.001;
        bestHtml = '<span class="' + (isPurple ? 'lap-purple' : '') + '">' + formatLapTime(d.best_lap) + '</span>';
      }

      // DOTD vote-share. Show every driver's percent so you can see your
      // relative rank even at 0%. The winner gets the highlighted style.
      const dotdPct = d.dotd_percent || 0;
      const isTopDotd = dotdPct > 0 && Math.abs(dotdPct - topDotdPct) < 0.05;
      const dotdHtml = '<span class="' + (isTopDotd ? 'dotd-hi' : '') + '">' +
        dotdPct.toFixed(1) + '%</span>';

      // Career — pulled from the iRacing /data API cache server-side.
      // Old races (pre-Phase 1, no cust_id) silently render as "—".
      let careerHtml = '<span class="career-st">—</span>';
      if (d.career && d.career.starts > 0) {
        const c = d.career;
        careerHtml =
          '<div class="career-cell">' +
            '<span class="career-w">' + c.wins + 'W</span>' +
            '<span class="career-t">' + c.top5 + 'T5</span>' +
            '<span class="career-st">/' + c.starts + '</span>' +
          '</div>' +
          (c.category ? '<span class="career-cat">' + c.category + '</span>' : '');
      }

      const tr = document.createElement('tr');
      if (d.is_us) tr.className = 'is-owner';
      tr.innerHTML =
        '<td class="col-pos"><span class="pos-badge ' + posClass + '">' + pos + '</span></td>' +
        '<td class="col-driver"><div class="drv-cell"><div class="drv-color" style="background:' + color + '"></div><span class="drv-name">' + (d.name || '—') + '</span></div></td>' +
        '<td class="col-num">' + (d.car_number || '—') + '</td>' +
        '<td class="col-lic">' + licHtml + '</td>' +
        '<td class="col-ir">' + (d.irating || '—') + '</td>' +
        '<td class="col-grid">' + grid + ' ' + gridHtml + '</td>' +
        '<td class="col-laps">' + (d.laps_led || '—') + '</td>' +
        '<td class="col-best">' + bestHtml + '</td>' +
        '<td class="col-inc">' + (d.incidents || 0) + '</td>' +
        '<td class="col-dotd">' + dotdHtml + '</td>' +
        '<td class="col-career">' + careerHtml + '</td>';
      tbody.appendChild(tr);
    });

    document.getElementById('results-overlay').classList.add('open');
  };

  window.closeResultsTable = function() {
    document.getElementById('results-overlay').classList.remove('open');
  };

  // Esc closes whichever overlay is currently open (results + report).
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape' && e.key !== 'Esc') return;
    const results = document.getElementById('results-overlay');
    const report  = document.getElementById('report-overlay');
    if (results && results.classList.contains('open')) {
      window.closeResultsTable();
      e.preventDefault();
    } else if (report && report.classList.contains('open')) {
      window.closeRaceReport();
      e.preventDefault();
    }
  });

  /* ── Race Report ── */
  window.openRaceReport = function() {
    const overlay = document.getElementById('report-overlay');
    if (!overlay) return;
    overlay.classList.add('open');
    const content = document.getElementById('report-content');
    const raceId = state.race && state.race.race_id;
    if (!raceId) { content.innerHTML = '<p>No race loaded.</p>'; return; }
    // Skip if already loaded for this race
    if (content.dataset.loaded === String(raceId)) return;
    content.innerHTML = '<div class="report-loading">Generating race report\u2026</div>';
    fetch('/api/replay/' + raceId + '/report')
      .then(r => r.json())
      .then(data => {
        if (data.error) { content.innerHTML = '<p>' + data.error + '</p>'; return; }
        content.dataset.loaded = String(raceId);
        _renderReport(content, data.report);
      })
      .catch(e => { content.innerHTML = '<p>Failed: ' + e.message + '</p>'; });
  };

  window.closeRaceReport = function() {
    document.getElementById('report-overlay').classList.remove('open');
  };

  window.regenerateReport = function() {
    const content = document.getElementById('report-content');
    const raceId = state.race && state.race.race_id;
    if (!raceId) return;
    content.innerHTML = '<div class="report-loading">Regenerating\u2026</div>';
    content.dataset.loaded = '';
    fetch('/api/replay/' + raceId + '/report?regenerate=1')
      .then(r => r.json())
      .then(data => {
        if (data.error) { content.innerHTML = '<p>' + data.error + '</p>'; return; }
        content.dataset.loaded = String(raceId);
        _renderReport(content, data.report);
      })
      .catch(e => { content.innerHTML = '<p>Failed: ' + e.message + '</p>'; });
  };

  function _renderReport(el, text) {
    const lines = (text || '').split('\n');
    // First non-empty line is the headline
    let headlineIdx = 0;
    while (headlineIdx < lines.length && !lines[headlineIdx].trim()) headlineIdx++;
    const headline = (lines[headlineIdx] || '').replace(/^#+\s*/, '').replace(/^\*\*/, '').replace(/\*\*$/, '');
    const body = lines.slice(headlineIdx + 1).join('\n').trim();
    // Convert markdown-ish to HTML
    const bodyHtml = body
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .split(/\n\n+/)
      .map(p => '<p>' + p.replace(/\n/g, '<br>') + '</p>')
      .join('');
    el.innerHTML = '<h2 class="report-headline">' + headline + '</h2>' +
      '<div class="report-body">' + bodyHtml + '</div>';
  }

  /* ── Setup tower + start animation ── */
  function setupAnimation(data) {
    state.race = data;
    buildFinishCard(data);
    if (state.animId) cancelAnimationFrame(state.animId);
    state.running = false;
    $('rp-play-btn').textContent = '\u25B6 Start Race';

    // Defensive: drop sentinel/garbage lap counts (iRacing INT16 max etc).
    let totalLaps = data.total_laps || 0;
    if (totalLaps > 999) totalLaps = 0;
    // Time-limited and tourist-layout races can under-report the lap target;
    // the classified lap count is the safer replay distance.
    if (Array.isArray(data.drivers)) {
      let maxDrvLaps = 0;
      data.drivers.forEach(d => { if ((d.race_laps || 0) > maxDrvLaps) maxDrvLaps = d.race_laps; });
      if (maxDrvLaps > totalLaps) totalLaps = maxDrvLaps;
    }
    data.total_laps = totalLaps;
    if (data.duration_s && data.duration_s > 14400) data.duration_s = 0;
    $('rp-track').textContent = data.track_name + (data.track_config ? ' — ' + data.track_config : '');
    $('rp-race-lap').textContent = 'Lap 0 / ' + totalLaps;
    $('tower-lap').innerHTML = '0 <span class="lap-dim">/ ' + totalLaps + '</span>';
    $('rp-lap-chip').textContent = '0/' + totalLaps;
    $('rp-cars-chip').textContent = data.drivers.length;

    /* Apply track library entry if matched */
    state.useCenterlineDirect = false;
    const svg = $('rp-track-svg');
    if (data.track && data.track.centerline_path) {
      $('rp-raceline').setAttribute('d', data.track.centerline_path);
      if (data.track.viewBox) svg.setAttribute('viewBox', data.track.viewBox);
      state.useCenterlineDirect = true;
      // Auto-fit viewBox to the actual path bbox so the trace fills the stage
      // (avoids huge whitespace when the editor canvas is bigger than the lap).
      requestAnimationFrame(() => fitTrackViewBox(svg));
    } else if (data.raceline_path) {
      $('rp-raceline').setAttribute('d', data.raceline_path);
    } else {
      // Reset to legacy default viewBox in case the previous race had a custom one
      svg.setAttribute('viewBox', '0 60 1260 660');
      svg.style.aspectRatio = '1260 / 660';
    }

    /* Page-level background photo: fills the area between the masthead and
     * the ticker, behind all three columns. Per-track override or shared
     * default. Hidden on 404. */
    const bgImg = $('rp-page-bg');
    if (bgImg) {
      // Priority: editor-chosen background, iRacing hero image fetched
      // via the /data API, then the shared default. Keeps the field
      // optional while still giving every track a real-looking visual.
      const t = data.track || {};
      const bgUrl = t.background || t.iracing_large_image || '/tracks/_default_bg.jpg';
      bgImg.style.display = 'none';
      bgImg.onload = () => { bgImg.style.display = ''; };
      bgImg.onerror = () => { bgImg.style.display = 'none'; };
      bgImg.src = bgUrl;
    }

    $('rp-dots-group').innerHTML = '';

    /* Fastest Lap card */
    if (data.fastest_lap && data.fastest_lap.time_s > 0) {
      $('rp-fl-card').style.display = '';
      $('rp-fl-driver').textContent = data.fastest_lap.driver_name;
      $('rp-fl-time').textContent = formatLapTime(data.fastest_lap.time_s);
    } else {
      $('rp-fl-card').style.display = 'none';
    }
    $('rp-leader-card').style.display = '';
    $('rp-battle-card').style.display = '';

    /* Laps Led card */
    const lapsLedCard = $('rp-laps-led-card');
    if (lapsLedCard && data.drivers) {
      const topLed = data.drivers.slice().sort((a, b) => (b.laps_led || 0) - (a.laps_led || 0))[0];
      if (topLed && topLed.laps_led > 0) {
        lapsLedCard.style.display = '';
        $('rp-laps-led-driver').textContent = lastName(topLed.name);
        $('rp-laps-led-count').textContent = topLed.laps_led;
      }
    }

    /* Biggest Mover card */
    const moverCard = $('rp-biggest-mover-card');
    if (moverCard && data.drivers) {
      let bestMover = null, bestDelta = 0;
      data.drivers.forEach(d => {
        const delta = (d.grid_position || 0) - (d.finish_position || 0);
        if (delta > bestDelta) { bestDelta = delta; bestMover = d; }
      });
      if (bestMover) {
        moverCard.style.display = '';
        $('rp-biggest-mover-driver').textContent = lastName(bestMover.name);
        $('rp-biggest-mover-delta').textContent = '+' + bestDelta;
      }
    }

    /* Cars card */
    const carsCard = $('rp-most-stops-card');
    if (carsCard && data.drivers) {
      const running = data.drivers.filter(d => d.reason_out === 'Running' || d.race_laps > 0).length;
      carsCard.style.display = '';
      $('rp-cars-running').textContent = running + ' running';
      $('rp-cars-total').textContent = '/ ' + data.drivers.length;
    }

    /* Populate bottom ticker with race meta — track, laps, cars,
     * winner, fastest lap, DOTD, focus driver. Refreshed each lap
     * during playback via updateReplayTicker(data, currentLap). */
    updateReplayTicker(data, 0);

    /* Build tower rows using shared `.tower-row` markup */
    const towerBody = $('tower-body');
    towerBody.innerHTML = '';
    towerBody.style.position = 'relative';
    towerBody.style.height = (data.drivers.length * 32) + 'px';

    const sortedByStart = data.drivers.slice().sort((a, b) => a.race_start_pos - b.race_start_pos);
    sortedByStart.forEach((drv, i) => {
      const row = document.createElement('div');
      row.className = 'tower-row' + (drv.is_us ? ' focus-driver' : '');
      row.style.position = 'absolute';
      row.style.left = '0'; row.style.right = '0';
      row.style.transform = 'translateY(' + (i * 32) + 'px)';
      row.innerHTML =
        '<div class="tower-pos">' + (i + 1) + '</div>' +
        '<div class="tower-color" style="background:#' + drv.livery.color1 + '"></div>' +
        '<div class="tower-name">' + abbrev(drv.name) + '</div>' +
        '<div class="tower-delta">—</div>' +
        '<div class="tower-gap">—</div>';
      towerBody.appendChild(row);
    });

    initRaceAnim(totalLaps, data.drivers);
  }

  function initRaceAnim(totalLaps, drivers) {
    const raceline = $('rp-raceline');
    if (!raceline) return;
    const dotsG = $('rp-dots-group');
    const playBtn = $('rp-play-btn');
    const timerEl = $('rp-race-timer');
    const lapEl = $('rp-race-lap');
    const legendEl = $('rp-legend');
    const towerLapEl = $('tower-lap');
    const towerBody = $('tower-body');
    const rateSlider = $('rp-rate-slider');
    const rateDisplay = $('rp-rate-display');
    const rateNow = $('rp-rate-now');
    const scrubSlider = $('rp-scrub-slider');
    const scrubCur = $('rp-scrub-cur');
    const scrubEnd = $('rp-scrub-end');
    const calloutEl = $('rp-callout');
    const calloutFrom = $('rp-callout-from');
    const calloutHeadline = $('rp-callout-headline');
    const calloutBody = $('rp-callout-body');
    const calloutWave = $('rp-callout-wave');

    /* Paint the animated wave bars once. */
    if (calloutWave && !calloutWave.children.length) {
      for (let i = 0; i < 28; i++) {
        const bar = document.createElement('div');
        bar.className = 'ssw-callout-wave-bar';
        bar.style.animationDelay = (i * 60) + 'ms';
        calloutWave.appendChild(bar);
      }
    }
    const F1_COLORS = [
      '#FF8000', // McLaren
      '#DC0000', // Ferrari
      '#27F4D2', // Mercedes
      '#3671C6', // Red Bull
      '#229971', // Aston Martin
      '#0093CC', // Alpine
      '#64C4FF', // Williams
      '#52E252', // Sauber
      '#B6BABD', // Haas
      '#6692FF', // RB
    ];
    /* Track which dot (if any) the active callout is following. When set,
     * renderFrame re-positions the callout next to that dot each frame. */
    let calloutTargetDot = null;

    function resolveTargetDot(evt) {
      // Prefer explicit car_idx match — covers commentary_log events and
      // synth events that now carry car_idx on the server.
      if (evt && typeof evt.car_idx === "number") {
        const hit = dots.find(d => d.drv && d.drv.car_idx === evt.car_idx);
        if (hit) return hit;
      }
      // Fallback: scan the headline + body for a driver's last name.
      const txt = ((evt.headline || "") + " " + (evt.body || "")).toLowerCase();
      if (!txt.trim()) return null;
      let best = null, bestPos = Infinity;
      for (const d of dots) {
        const ln = lastName(d.drv.name || "").toLowerCase();
        if (ln.length < 3) continue;
        const pos = txt.indexOf(ln);
        if (pos >= 0 && pos < bestPos) { best = d; bestPos = pos; }
      }
      return best;
    }

    function showCallout(evt) {
      if (!calloutEl) return;
      calloutFrom.textContent = (evt.commentator || 'SECTOR SAYS').toUpperCase();
      calloutHeadline.textContent = evt.headline || '';
      calloutBody.textContent = evt.body || '';
      const c = F1_COLORS[Math.floor(Math.random() * F1_COLORS.length)];
      calloutEl.style.setProperty('--callout-color', c);
      calloutEl.style.borderColor = c;
      calloutEl.classList.toggle('is-owner', !!evt.is_owner);
      calloutTargetDot = resolveTargetDot(evt);
      calloutEl.classList.toggle('is-tracking', !!calloutTargetDot);
      calloutEl.classList.add('is-visible');
      calloutEl.setAttribute('aria-hidden', 'false');
    }
    function hideCallout() {
      if (!calloutEl) return;
      calloutEl.classList.remove('is-visible');
      calloutEl.classList.remove('is-tracking');
      calloutEl.setAttribute('aria-hidden', 'true');
      calloutTargetDot = null;
      // Clear inline positioning so the default CSS placement takes over next time.
      calloutEl.style.left = "";
      calloutEl.style.top = "";
      calloutEl.style.right = "";
    }

    /* Position the callout near its target dot. The dot's SVG-user-space
     * transform is mapped to CSS pixels via getScreenCTM, then expressed
     * relative to .replay-stage (the callout's offset parent). */
    function updateCalloutPosition() {
      if (!calloutTargetDot || !calloutEl) return;
      const svg = raceline && raceline.ownerSVGElement;
      const stage = calloutEl.offsetParent;
      if (!svg || !stage) return;
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      // Read the dot's translate(x,y) from its transform attribute.
      const m = (calloutTargetDot.el.getAttribute("transform") || "")
        .match(/translate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)/);
      if (!m) return;
      const svgPt = svg.createSVGPoint();
      svgPt.x = parseFloat(m[1]);
      svgPt.y = parseFloat(m[2]);
      const screen = svgPt.matrixTransform(ctm);
      const stageRect = stage.getBoundingClientRect();
      const calloutW = calloutEl.offsetWidth || 320;
      const calloutH = calloutEl.offsetHeight || 120;
      // Default: place to the upper-right of the dot. Flip when near edges.
      const OFFSET_X = 42;
      const OFFSET_Y = 24;
      let x = screen.x - stageRect.left + OFFSET_X;
      let y = screen.y - stageRect.top - calloutH - OFFSET_Y;
      if (x + calloutW > stageRect.width - 8) {
        x = screen.x - stageRect.left - calloutW - OFFSET_X;
      }
      if (y < 8) {
        y = screen.y - stageRect.top + OFFSET_Y;
      }
      x = Math.max(8, Math.min(stageRect.width - calloutW - 8, x));
      y = Math.max(8, Math.min(stageRect.height - calloutH - 8, y));
      calloutEl.style.left = x + "px";
      calloutEl.style.top = y + "px";
      calloutEl.style.right = "auto";
    }

    /* Real-time playback clock — race seconds, advanced by dt * playbackRate.
     * playbackRate ramps between 1.0 (during highlight windows, Phase B) and
     * `maxFF` (the user-controlled "Max FF" slider). Phase A: rate is just
     * pinned to maxFF the whole time. */
    // Pick a sensible total race time:
    //   1. races.duration_s if we trust it
    //   2. totalLaps × 90s if we know the lap count
    //   3. fastest_lap × ~30 laps as a last-ditch guess
    //   4. 30 minutes flat
    let DURATION_S;
    if (state.race && state.race.duration_s && state.race.duration_s > 0) {
      DURATION_S = state.race.duration_s;
    } else if (totalLaps > 0) {
      DURATION_S = Math.max(60, totalLaps * 90);
    } else if (state.race && state.race.fastest_lap && state.race.fastest_lap.time_s > 0) {
      DURATION_S = state.race.fastest_lap.time_s * 30;
    } else {
      DURATION_S = 30 * 60;
    }
    let simTime = 0;            // current race seconds
    let maxFF = parseInt(rateSlider.value);
    let playbackRate = maxFF;

    /* ── Focus Driver overtake callouts ──
     * Keep replay popups limited to synthetic Focus Driver rank gains.
     * The server timeline still exists for future modes/reports, but the
     * visible callout card should not show generic race highlights here.
     */
    const timeline = [];
    let nextIdx = 0;
    let activeEvent = null;          // currently inside its slow window
    const SLOWDOWN_LEAD_S = 3.0;      // race-seconds of approach to ease into 1×
    const SPEEDUP_LEAD_S  = 1.5;      // ease back up after the window
    let prevOwnerRank = -1;           // for overtake detection
    let lastOvertakeAt = -999;
    const OVERTAKE_COOLDOWN_S = 8.0;

    function findNextIdx(fromTime) {
      for (let i = 0; i < timeline.length; i++) {
        if (timeline[i].t > fromTime) return i;
      }
      return timeline.length;
    }

    function injectOvertake(t, newRank) {
      if (t - lastOvertakeAt < OVERTAKE_COOLDOWN_S) return;
      lastOvertakeAt = t;
      const evt = {
        t: t,
        kind: "owner_overtake",
        is_owner: true,
        headline: "OVERTAKE — P" + (newRank + 1),
        body: "You move up to P" + (newRank + 1) + ".",
        commentator: "",
        duration: 4.0,
      };
      // Insert in sorted position so the cursor still works.
      let i = nextIdx;
      while (i < timeline.length && timeline[i].t <= t) i++;
      timeline.splice(i, 0, evt);
    }

    const fullLen = raceline.getTotalLength();

    /* Sample the raceline.
     *  - Library tracks store an actual centerline, so sample directly.
     *  - Legacy default (Road Atlanta) traces both road edges, so average
     *    opposite samples to recover an approximate centerline. */
    const RAW_SAMPLES = 200;
    let rawPts = [];
    if (state.useCenterlineDirect) {
      for (let i = 0; i < RAW_SAMPLES; i++) {
        const p = raceline.getPointAtLength((i / RAW_SAMPLES) * fullLen);
        rawPts.push({ x: p.x, y: p.y });
      }
    } else {
      const halfLen = fullLen * 0.5;
      for (let i = 0; i < RAW_SAMPLES; i++) {
        const t = i / RAW_SAMPLES;
        const p1 = raceline.getPointAtLength(halfLen * t);
        const p2 = raceline.getPointAtLength(fullLen - halfLen * t);
        rawPts.push({ x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 });
      }
    }
    function chaikin(pts) {
      const out = []; const n = pts.length;
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        out.push({ x: pts[i].x * 0.75 + pts[j].x * 0.25, y: pts[i].y * 0.75 + pts[j].y * 0.25 });
        out.push({ x: pts[i].x * 0.25 + pts[j].x * 0.75, y: pts[i].y * 0.25 + pts[j].y * 0.75 });
      }
      return out;
    }
    function resample(pts, count) {
      const dists = [0];
      for (let i = 1; i < pts.length; i++) {
        const dx = pts[i].x - pts[i - 1].x, dy = pts[i].y - pts[i - 1].y;
        dists.push(dists[i - 1] + Math.sqrt(dx * dx + dy * dy));
      }
      const cdx = pts[0].x - pts[pts.length - 1].x, cdy = pts[0].y - pts[pts.length - 1].y;
      const totalD = dists[pts.length - 1] + Math.sqrt(cdx * cdx + cdy * cdy);
      const out = [];
      for (let i = 0; i < count; i++) {
        const target = (i / count) * totalD;
        let lo = 0;
        for (let k = 1; k < pts.length; k++) { if (dists[k] <= target) lo = k; else break; }
        if (target >= dists[pts.length - 1]) {
          const remain = target - dists[pts.length - 1];
          const cl = totalD - dists[pts.length - 1];
          const tt = cl > 0 ? remain / cl : 0;
          out.push({
            x: pts[pts.length - 1].x + (pts[0].x - pts[pts.length - 1].x) * tt,
            y: pts[pts.length - 1].y + (pts[0].y - pts[pts.length - 1].y) * tt,
          });
        } else {
          const hi = (lo + 1) % pts.length;
          const seg = dists[hi] - dists[lo];
          const tt = seg > 0 ? (target - dists[lo]) / seg : 0;
          out.push({
            x: pts[lo].x + (pts[hi].x - pts[lo].x) * tt,
            y: pts[lo].y + (pts[hi].y - pts[lo].y) * tt,
          });
        }
      }
      return out;
    }
    let smoothed = rawPts;
    for (let p = 0; p < 3; p++) smoothed = chaikin(smoothed);
    const centerPts = resample(smoothed, 400);
    const N = centerPts.length;
    const centerDists = [0];
    for (let i = 1; i < N; i++) {
      const dx = centerPts[i].x - centerPts[i - 1].x, dy = centerPts[i].y - centerPts[i - 1].y;
      centerDists.push(centerDists[i - 1] + Math.sqrt(dx * dx + dy * dy));
    }
    const closeDx = centerPts[0].x - centerPts[N - 1].x, closeDy = centerPts[0].y - centerPts[N - 1].y;
    const lapLen = centerDists[N - 1] + Math.sqrt(closeDx * closeDx + closeDy * closeDy);

    function svgUserUnitPx() {
      const svg = raceline && raceline.ownerSVGElement;
      const ctm = svg && svg.getScreenCTM ? svg.getScreenCTM() : null;
      if (!ctm) return 1;
      const sx = Math.sqrt(ctm.a * ctm.a + ctm.b * ctm.b);
      const sy = Math.sqrt(ctm.c * ctm.c + ctm.d * ctm.d);
      return Math.max(0.01, (sx + sy) / 2);
    }

    let pathD = 'M' + centerPts[0].x.toFixed(1) + ',' + centerPts[0].y.toFixed(1);
    for (let i = 1; i < N; i++) pathD += 'L' + centerPts[i].x.toFixed(1) + ',' + centerPts[i].y.toFixed(1);
    pathD += 'Z';
    $('rp-track-glow-path').setAttribute('d', pathD);
    $('rp-track-surface').setAttribute('d', pathD);
    $('rp-track-asphalt-inner').setAttribute('d', pathD);
    $('rp-track-stripe').setAttribute('d', pathD);

    /* S/F marker */
    const sfPt = centerPts[0], sfPt2 = centerPts[1];
    const sfAngle = Math.atan2(sfPt2.y - sfPt.y, sfPt2.x - sfPt.x) + Math.PI / 2;
    const sfLen = 18;
    const sl1 = $('rp-sf-line1'), sl2 = $('rp-sf-line2'), sfTxt = $('rp-sf-text');
    [sl1, sl2].forEach(l => {
      l.setAttribute('x1', sfPt.x - Math.cos(sfAngle) * sfLen);
      l.setAttribute('y1', sfPt.y - Math.sin(sfAngle) * sfLen);
      l.setAttribute('x2', sfPt.x + Math.cos(sfAngle) * sfLen);
      l.setAttribute('y2', sfPt.y + Math.sin(sfAngle) * sfLen);
    });
    sfTxt.setAttribute('x', sfPt.x);
    sfTxt.setAttribute('y', sfPt.y - sfLen - 5);

    function getCenterPoint(frac) {
      frac = ((frac % 1.0) + 1.0) % 1.0;
      const target = frac * lapLen;
      let lo = 0, hi = N - 1;
      while (lo < hi - 1) { const mid = (lo + hi) >> 1; if (centerDists[mid] <= target) lo = mid; else hi = mid; }
      if (target >= centerDists[N - 1]) {
        const remain = target - centerDists[N - 1];
        const cl = lapLen - centerDists[N - 1];
        const t2 = cl > 0 ? remain / cl : 0;
        return {
          x: centerPts[N - 1].x + (centerPts[0].x - centerPts[N - 1].x) * t2,
          y: centerPts[N - 1].y + (centerPts[0].y - centerPts[N - 1].y) * t2,
        };
      }
      const seg = centerDists[hi] - centerDists[lo];
      const t2 = seg > 0 ? (target - centerDists[lo]) / seg : 0;
      return {
        x: centerPts[lo].x + (centerPts[hi].x - centerPts[lo].x) * t2,
        y: centerPts[lo].y + (centerPts[hi].y - centerPts[lo].y) * t2,
      };
    }

    let leaderAvg = 999999;
    // Find the fastest avg_lap among drivers who completed a meaningful number of laps
    const minLapsForAvg = totalLaps > 0 ? Math.floor(totalLaps * 0.5) : 1;
    drivers.forEach(d => {
      if (d.race_avg_lap > 0 && d.race_avg_lap < leaderAvg && d.race_laps >= minLapsForAvg)
        leaderAvg = d.race_avg_lap;
    });
    // Fallback: use any driver with a valid avg_lap
    if (leaderAvg >= 999999) {
      drivers.forEach(d => {
        if (d.race_avg_lap > 0 && d.race_avg_lap < leaderAvg) leaderAvg = d.race_avg_lap;
      });
    }
    const leaderAvgSec = (leaderAvg > 0 && leaderAvg < 999999) ? leaderAvg / 10000 : 80;

    /* Scale factor for the synthetic (no-samples) distance curve. The DB's
     * duration_s reflects the full race; the leader's natural finish time is
     * totalLaps × leaderAvgSec. If those drift (because avg_lap was computed
     * across the whole field, not just the leader) the synthetic leader
     * otherwise lands at the finish well before the timeline events that
     * reference duration_s. Stretching every driver's pace by this ratio
     * keeps relative gaps intact while anchoring the leader's finish to
     * duration_s — so "Race Winner" and "Chequered Flag" callouts align. */
    let syntheticPaceScale = 1.0;
    if (totalLaps > 0 && DURATION_S > 0 && leaderAvgSec > 0) {
      const naturalFinish = totalLaps * leaderAvgSec;
      if (naturalFinish > 0) syntheticPaceScale = DURATION_S / naturalFinish;
    }

    /* Per-driver totalDist(t) curve plus the sim time at which they were
     * done racing (crash / pit retirement / flag).
     *
     *  - Lap samples present (live-captured engine races): piecewise-linear
     *    between (session_time_s, lap_number) crossings. Prepend an implicit
     *    (0, 0) anchor so pre-first-crossing motion interpolates at the
     *    driver's first-lap pace instead of jumping. `retiredAt` = last
     *    sample's t when reason_out != 'Running'; null for the winner and
     *    anyone still classified as running.
     *  - No samples (imported races): dist(t) = t / (avg_lap_s × scale),
     *    capped at race_laps. `retiredAt` = maxLaps × avg_lap_s × scale for
     *    non-Running drivers, null otherwise.
     *
     * Output: { fn(t), retiredAt }. */
    /* Grid-slot spacing (in laps). At race start each driver sits this
     * fraction of a lap behind the car in front, mirroring how iRacing
     * lays out the grid just before the lights. Fades out by the end of
     * lap 1 so the natural racing spacing takes over.
     *
     * 0.0015 ≈ 8m per slot at a typical 5km road course (~210m for a
     * 28-car grid). Earlier 0.012 stretched the grid almost halfway
     * around the track and read as cars-already-mid-race at t=0. */
    const GRID_STEP = 0.0015;

    function buildDistFn(drv) {
      const samples = Array.isArray(drv.lap_samples) ? drv.lap_samples : [];
      const rawAvgLapSec = (drv.race_avg_lap > 0) ? drv.race_avg_lap / 10000 : leaderAvgSec;
      const avgLapSec = rawAvgLapSec * syntheticPaceScale;
      const maxLaps = (drv.race_laps && drv.race_laps > 0) ? drv.race_laps : null;
      const isRunning = drv.reason_out === "Running";
      const gridOffset = Math.max(0, (drv.race_start_pos || 0)) * GRID_STEP;

      if (samples.length > 0) {
        // Anchor at t=0 with the driver parked at their grid slot (negative
        // distance wraps around to the right spot on the racing line). The
        // first real crossing fills in the lap-1 segment — that first
        // interpolation window naturally carries them through the grid
        // distance plus one lap.
        const pts = [{ t: 0, lap: -gridOffset }];
        for (const s of samples) {
          if (typeof s.t === "number" && typeof s.lap === "number") {
            pts.push({ t: s.t, lap: s.lap });
          }
        }
        pts.sort((a, b) => a.t - b.t);
        const lastT = pts[pts.length - 1].t;
        const lastLap = pts[pts.length - 1].lap;
        const fn = function (t) {
          if (t <= 0) return pts[0].lap;
          if (t >= lastT) return lastLap;
          let lo = 0, hi = pts.length - 1;
          while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (pts[mid].t <= t) lo = mid; else hi = mid;
          }
          const a = pts[lo], b = pts[hi];
          const dt = b.t - a.t;
          if (dt <= 0) return b.lap;
          return a.lap + (b.lap - a.lap) * (t - a.t) / dt;
        };
        // Inverse: given a target distance (in laps), what session time
        // did this driver pass through it? Used by the timing tower to
        // compute true gap-to-leader instead of multiplying lap distance
        // by an average. Returns null if the target distance is outside
        // the recorded range (driver hasn't reached it yet, or retired).
        const invFn = function (targetLap) {
          if (targetLap <= pts[0].lap) return 0;
          if (targetLap >= lastLap) return lastT;
          // Binary search by lap. pts is sorted by t but lap is monotonic
          // for a running driver (laps only increase) so it's also lap-
          // sorted. Defensive guard: bail to linear scan if not.
          let lo = 0, hi = pts.length - 1;
          while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (pts[mid].lap <= targetLap) lo = mid; else hi = mid;
          }
          const a = pts[lo], b = pts[hi];
          const dlap = b.lap - a.lap;
          if (dlap <= 0) return b.t;
          return a.t + (b.t - a.t) * (targetLap - a.lap) / dlap;
        };
        // Implicit retirement: a driver flagged "Running" whose last
        // sample is more than 3 laps short of the race total almost
        // certainly crashed / parked / was disqualified. The engine
        // doesn't always update reason_out for these. Without this
        // override they stay full-color frozen at lap N (often the S/F
        // line for a 1-lap exit), looking like ghost cars sitting
        // ahead of the active field.
        let effectiveRetiredAt = isRunning ? null : lastT;
        if (effectiveRetiredAt === null && totalLaps > 0
            && lastLap < totalLaps - 3) {
          effectiveRetiredAt = lastT;
        }
        return { fn: fn, invFn: invFn, retiredAt: effectiveRetiredAt };
      }

      // No samples: pace-accurate synthesis. The grid offset fades out
      // linearly across lap 1 so the driver lands on the S/F line at
      // roughly t = avgLapSec × (1 + gridOffset), matching their natural
      // first crossing.
      const fn = function (t) {
        if (t <= 0) return -gridOffset;
        const raw = t / avgLapSec;
        const fade = Math.max(0, 1 - raw);           // 1 at t=0, 0 after lap 1
        const d = raw - gridOffset * fade;
        return maxLaps !== null ? Math.min(d, maxLaps) : d;
      };
      // Synthetic inverse: solve d = (t/avg) - gridOffset*max(0,1-t/avg).
      // For t >= avgLapSec the fade term is zero, so t = (d + 0) * avgLapSec.
      // That covers everything past lap 1 (where the gap math actually
      // matters). Lap-1 inverses fall back to the same linear approx.
      const invFn = function (targetLap) {
        if (targetLap <= -gridOffset) return 0;
        return (targetLap + gridOffset) * avgLapSec;
      };
      const retiredAt = (!isRunning && maxLaps !== null)
        ? maxLaps * avgLapSec
        : null;
      return { fn: fn, invFn: invFn, retiredAt: retiredAt };
    }

    rateSlider.oninput = function () {
      maxFF = parseInt(this.value);
      rateDisplay.textContent = maxFF + '\u00d7';
      // Phase A: rate is pinned to maxFF the entire run.
      playbackRate = maxFF;
      rateNow.textContent = '@ ' + maxFF + '\u00d7';
    };
    rateDisplay.textContent = maxFF + '\u00d7';
    rateNow.textContent = '@ ' + maxFF + '\u00d7';
    scrubEnd.textContent = fmtTime(DURATION_S);
    scrubSlider.value = 0;
    scrubCur.textContent = '0:00';

    const sorted = drivers.slice().sort((a, b) => a.race_start_pos - b.race_start_pos);
    const dots = [];
    let focusDotEl = null;
    const towerRows = Array.from(towerBody.querySelectorAll('.tower-row'));

    /* Glow + shadow filters for the focus driver */
    const svgEl = raceline.parentElement;
    let defs = svgEl.querySelector('defs');
    if (!defs.querySelector('#rp-dot-glow')) {
      const gf = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
      gf.setAttribute('id', 'rp-dot-glow');
      gf.setAttribute('x', '-50%'); gf.setAttribute('y', '-50%');
      gf.setAttribute('width', '200%'); gf.setAttribute('height', '200%');
      gf.innerHTML =
        '<feDropShadow dx="0" dy="0" stdDeviation="6" flood-color="#D4AF37" flood-opacity="0.9"/>' +
        '<feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#000" flood-opacity="0.6"/>';
      defs.appendChild(gf);
    }

    sorted.forEach((drv, idx) => {
      const ns = 'http://www.w3.org/2000/svg';
      const abr = abbrev(drv.name);
      const color = '#' + drv.livery.color1;
      const g = document.createElementNS(ns, 'g'); g.setAttribute('opacity', '0');
      if (drv.is_us) {
        // Focus driver: larger dot with black outline ring + gold ring + glow
        g.setAttribute('filter', 'url(#rp-dot-glow)');
        const outerRing = document.createElementNS(ns, 'circle');
        outerRing.setAttribute('r', '30'); outerRing.setAttribute('cx', '0'); outerRing.setAttribute('cy', '0');
        outerRing.setAttribute('fill', 'none');
        outerRing.setAttribute('stroke', '#000'); outerRing.setAttribute('stroke-width', '4');
        g.appendChild(outerRing);
        const c = document.createElementNS(ns, 'circle');
        c.setAttribute('r', '27'); c.setAttribute('cx', '0'); c.setAttribute('cy', '0');
        c.setAttribute('fill', color);
        c.setAttribute('stroke', '#D4AF37'); c.setAttribute('stroke-width', '3.5');
        g.appendChild(c);
      } else {
        const c = document.createElementNS(ns, 'circle');
        c.setAttribute('r', '18'); c.setAttribute('cx', '0'); c.setAttribute('cy', '0');
        c.setAttribute('fill', color);
        c.setAttribute('stroke', '#000'); c.setAttribute('stroke-width', '3.5');
        g.appendChild(c);
      }

      if (drv.is_us) {
        // Fixed-above tag — F1 broadcast style. Sits above the dot
        // with a thin leader line. No orbiting, no lerp, just clean.
        const TAG_W = 110;
        const TAG_H = 34;
        const TAG_SCREEN_W = 116;
        const TAG_SCREEN_GAP = 42;
        const TAG_GAP = 42;      // gap between dot top and tag bottom
        const DOT_R = 27;

        // Leader line (vertical, dot top → tag bottom)
        const line = document.createElementNS(ns, 'line');
        line.setAttribute('x1', '0'); line.setAttribute('y1', String(-DOT_R));
        line.setAttribute('x2', '0'); line.setAttribute('y2', String(-DOT_R - TAG_GAP));
        line.setAttribute('stroke', '#D4AF37');
        line.setAttribute('stroke-width', '2');
        line.setAttribute('stroke-dasharray', '4,3');
        g.appendChild(line);

        // Tag group — fixed at (0, -DOT_R - TAG_GAP - TAG_H/2)
        const tagY = -DOT_R - TAG_GAP - TAG_H / 2;
        const calloutG = document.createElementNS(ns, 'g');
        calloutG.setAttribute('transform', 'translate(0,' + tagY + ')');

        // Shadow rect (offset down+right slightly)
        const shadow = document.createElementNS(ns, 'rect');
        shadow.setAttribute('x', String(-TAG_W / 2 + 2));
        shadow.setAttribute('y', String(-TAG_H / 2 + 2));
        shadow.setAttribute('width', String(TAG_W));
        shadow.setAttribute('height', String(TAG_H));
        shadow.setAttribute('rx', '6');
        shadow.setAttribute('fill', 'rgba(0,0,0,0.5)');
        calloutG.appendChild(shadow);

        // Main plate — dark with gold border, pill shape
        const plate = document.createElementNS(ns, 'rect');
        plate.setAttribute('x', String(-TAG_W / 2));
        plate.setAttribute('y', String(-TAG_H / 2));
        plate.setAttribute('width', String(TAG_W));
        plate.setAttribute('height', String(TAG_H));
        plate.setAttribute('rx', '6');
        plate.setAttribute('fill', 'rgba(0,0,0,0.85)');
        plate.setAttribute('stroke', '#D4AF37');
        plate.setAttribute('stroke-width', '2');
        calloutG.appendChild(plate);

        // Team-color accent bar on the left
        const accent = document.createElementNS(ns, 'rect');
        accent.setAttribute('x', String(-TAG_W / 2 + 4));
        accent.setAttribute('y', String(-TAG_H / 2 + 5));
        accent.setAttribute('width', '5');
        accent.setAttribute('height', String(TAG_H - 10));
        accent.setAttribute('rx', '2');
        accent.setAttribute('fill', color);
        calloutG.appendChild(accent);

        // Driver name text
        const nameTxt = document.createElementNS(ns, 'text');
        nameTxt.setAttribute('x', String(-TAG_W / 2 + 16));
        nameTxt.setAttribute('y', '1');
        nameTxt.setAttribute('text-anchor', 'start');
        nameTxt.setAttribute('dominant-baseline', 'middle');
        nameTxt.setAttribute('fill', '#fff');
        nameTxt.setAttribute('font-size', '16');
        nameTxt.setAttribute('font-weight', '800');
        nameTxt.setAttribute('font-family', "'Titillium Web', sans-serif");
        nameTxt.setAttribute('letter-spacing', '1');
        nameTxt.textContent = abr;
        calloutG.appendChild(nameTxt);

        // Position badge (right side of tag) — updated each frame
        const posTxt = document.createElementNS(ns, 'text');
        posTxt.setAttribute('x', String(TAG_W / 2 - 10));
        posTxt.setAttribute('y', '1');
        posTxt.setAttribute('text-anchor', 'end');
        posTxt.setAttribute('dominant-baseline', 'middle');
        posTxt.setAttribute('fill', '#D4AF37');
        posTxt.setAttribute('font-size', '16');
        posTxt.setAttribute('font-weight', '900');
        posTxt.setAttribute('font-family', "'Titillium Web', sans-serif");
        posTxt.textContent = '';
        calloutG.appendChild(posTxt);

        g.appendChild(calloutG);

        // Stash — keep the nameplate readable even when a large track
        // viewBox makes SVG user units tiny on screen.
        g.__callout = {
          line: line,
          posTxt: posTxt,
          plate: calloutG,
          tagW: TAG_W,
          tagH: TAG_H,
          tagScreenW: TAG_SCREEN_W,
          tagScreenGap: TAG_SCREEN_GAP,
          dotR: DOT_R,
        };

        // Also keep the 3-letter abbreviation centered on the dot itself.
        const dotTxt = document.createElementNS(ns, 'text');
        dotTxt.setAttribute('x', '0'); dotTxt.setAttribute('y', '6');
        dotTxt.setAttribute('text-anchor', 'middle');
        dotTxt.setAttribute('fill', '#fff');
        dotTxt.setAttribute('font-size', '15');
        dotTxt.setAttribute('font-weight', '900');
        dotTxt.setAttribute('font-family', "'Titillium Web', sans-serif");
        dotTxt.setAttribute('letter-spacing', '0.5');
        dotTxt.setAttribute('stroke', 'rgba(0,0,0,0.7)');
        dotTxt.setAttribute('stroke-width', '0.8');
        dotTxt.setAttribute('paint-order', 'stroke');
        dotTxt.textContent = abr;
        g.appendChild(dotTxt);
      } else {
        // Regular cars: just the 3-letter abbreviation centered in the dot.
        const txt = document.createElementNS(ns, 'text');
        txt.setAttribute('x', '0'); txt.setAttribute('y', '5');
        txt.setAttribute('text-anchor', 'middle');
        txt.setAttribute('fill', '#fff');
        txt.setAttribute('font-size', '13');
        txt.setAttribute('font-weight', '900');
        txt.setAttribute('font-family', "'Titillium Web', sans-serif");
        txt.setAttribute('letter-spacing', '0.5');
        txt.setAttribute('stroke', 'rgba(0,0,0,0.6)');
        txt.setAttribute('stroke-width', '0.6');
        txt.setAttribute('paint-order', 'stroke');
        txt.textContent = abr;
        g.appendChild(txt);
      }
      dotsG.appendChild(g);
      if (drv.is_us) focusDotEl = g;
      const isDns = (drv.reason_out === "DNS") ||
                    (drv.race_laps === 0 && drv.reason_out !== "Running");
      const built = buildDistFn(drv);
      dots.push({
        el: g,
        drv: drv,
        totalDist: 0,
        towerRow: towerRows[idx] || null,
        abbr: abr,
        distFn: built.fn,
        invDistFn: built.invFn,
        retiredAt: built.retiredAt,
        isDns: isDns,
        hasSamples: Array.isArray(drv.lap_samples) && drv.lap_samples.length > 0,
      });

      const li = document.createElement('div');
      li.className = 'replay-legend-item' + (drv.is_us ? ' us' : '') + (drv.reason_out !== 'Running' ? ' dnf' : '');
      li.innerHTML = '<span class="dot" style="background:#' + drv.livery.color1 + '"></span>#' + drv.car_number + ' ' + drv.name;
      if (legendEl) legendEl.appendChild(li);
    });
    if (focusDotEl) dotsG.appendChild(focusDotEl);

    const startPt = getCenterPoint(0);
    dots.forEach(d => d.el.setAttribute('transform', 'translate(' + startPt.x + ',' + startPt.y + ')'));

    let lastFrame = 0;

    function updateFocusNameplateScale(dot, dotY) {
      const cb = dot && dot.el && dot.el.__callout;
      if (!cb || !cb.plate) return;
      const unitPx = svgUserUnitPx();
      const scale = cb.tagScreenW / (cb.tagW * unitPx);
      const gap = cb.tagScreenGap / unitPx;
      const halfTagH = cb.tagH * scale / 2;
      let tagY = -cb.dotR - gap - halfTagH;
      let lineStartY = -cb.dotR;
      let lineEndY = tagY + halfTagH;
      const svg = raceline && raceline.ownerSVGElement;
      const vb = svg && svg.viewBox && svg.viewBox.baseVal;
      if (vb && typeof dotY === 'number') {
        const marginY = vb.y + (10 / unitPx);
        const tagTop = dotY + tagY - halfTagH;
        const belowTagY = cb.dotR + gap + halfTagH;
        const belowBottom = dotY + belowTagY + halfTagH;
        if (tagTop < marginY && belowBottom < vb.y + vb.height - (10 / unitPx)) {
          tagY = belowTagY;
          lineStartY = cb.dotR;
          lineEndY = tagY - halfTagH;
        }
      }
      cb.plate.setAttribute(
        'transform',
        'matrix(' + scale.toFixed(4) + ' 0 0 ' + scale.toFixed(4) + ' 0 ' + tagY.toFixed(2) + ')'
      );
      if (cb.line) {
        cb.line.setAttribute('y1', String(lineStartY.toFixed(2)));
        cb.line.setAttribute('x2', '0');
        cb.line.setAttribute('y2', String(lineEndY.toFixed(2)));
      }
    }

    function animate(ts) {
      if (ts - lastFrame < 33) { state.animId = requestAnimationFrame(animate); return; }
      const dt = lastFrame ? Math.min(0.1, (ts - lastFrame) / 1000) : 0;
      lastFrame = ts;

      // ── Rate ramp from highlight cursor ──
      let targetRate = maxFF;
      // Pop expired events.
      if (activeEvent && simTime > activeEvent.t + activeEvent.duration + SPEEDUP_LEAD_S) {
        activeEvent = null;
        hideCallout();
      }
      if (!activeEvent && nextIdx < timeline.length && simTime >= timeline[nextIdx].t) {
        activeEvent = timeline[nextIdx];
        // Enforce minimum on-screen time of 4 race-seconds.
        activeEvent.duration = Math.max(4.0, activeEvent.duration || 0);
        nextIdx++;
        showCallout(activeEvent);
      }
      if (activeEvent) {
        const inWin = simTime <= activeEvent.t + activeEvent.duration;
        if (inWin) {
          targetRate = 1.0;          // hard 1× during the slow window
        } else {
          // Ease back up to maxFF.
          const k = (simTime - (activeEvent.t + activeEvent.duration)) / SPEEDUP_LEAD_S;
          targetRate = 1.0 + (maxFF - 1.0) * Math.min(1, k);
        }
      } else if (nextIdx < timeline.length) {
        const lead = timeline[nextIdx].t - simTime;
        if (lead < SLOWDOWN_LEAD_S) {
          // Ease down to 1× as we approach.
          const k = lead / SLOWDOWN_LEAD_S;
          targetRate = 1.0 + (maxFF - 1.0) * Math.max(0, Math.min(1, k));
        }
      }
      // Smooth the rate so it never jumps abruptly.
      playbackRate += (targetRate - playbackRate) * Math.min(1, dt * 4);
      rateNow.textContent = '@ ' + playbackRate.toFixed(1) + '\u00d7';

      simTime = Math.min(simTime + dt * playbackRate, DURATION_S);
      renderFrame();

      // ── Owner-overtake auto-detect (uses tower rank set by renderFrame) ──
      if (state.ownerRank >= 0) {
        if (prevOwnerRank >= 0 && state.ownerRank < prevOwnerRank) {
          injectOvertake(simTime + 0.001, state.ownerRank);
        }
        prevOwnerRank = state.ownerRank;
      }

      if (simTime < DURATION_S) state.animId = requestAnimationFrame(animate);
      else {
        state.running = false;
        playBtn.textContent = '\u25B6 Replay';
        hideCallout();
        showFinish();
      }
    }

    /* Paint the world for the current simTime — used by both the animate
     * loop and the scrub-bar seek handler. Pure function of simTime. */
    function renderFrame() {
      /* Each driver's distance (in laps) is sampled from their distFn at
       * simTime. Rank, intervals, leader lap count, and dot position are
       * all derived from those distances — no synthetic grid→finish ramp. */
      dots.forEach(d => { d.totalDist = d.distFn(simTime); });

      // Sort by distance travelled (DNS cars always at the back so they
      // still occupy their tower row).
      const ranked = dots.slice().sort((a, b) => {
        if (a.isDns !== b.isDns) return a.isDns ? 1 : -1;
        return b.totalDist - a.totalDist;
      });
      const firstActive = ranked.find(d => !d.isDns);
      const leaderDist = firstActive ? firstActive.totalDist : 0;
      const lapTimeSec = leaderAvgSec;

      /* Lap counter reflects the leader's actual completed laps. If
       * totalLaps is known, floor the leader's distance; otherwise show a
       * rolling count derived from motion. */
      const displayTotal = totalLaps > 0 ? totalLaps : '—';
      const leaderLapFloat = leaderDist;
      const lap = totalLaps > 0
        ? Math.min(Math.floor(leaderLapFloat), totalLaps)
        : Math.floor(leaderLapFloat);
      const progress = DURATION_S > 0 ? Math.min(simTime / DURATION_S, 1.0) : 0;

      timerEl.textContent = fmtTime(Math.floor(simTime)) + ' / ' + fmtTime(DURATION_S);
      if (!scrubDragging) {
        scrubSlider.value = Math.round(progress * 1000);
        scrubCur.textContent = fmtTime(Math.floor(simTime));
      }
      lapEl.textContent = 'Lap ' + lap + ' / ' + displayTotal;
      towerLapEl.innerHTML = lap + ' <span class="lap-dim">/ ' + displayTotal + '</span>';
      $('rp-lap-chip').textContent = lap + '/' + displayTotal;
      $('rp-time-chip').textContent = fmtTime(Math.floor(simTime));
      if (lap !== state._tickerLap) {
        state._tickerLap = lap;
        updateReplayTicker(state.race, lap);
      }

      let __trackBB = null;
      try { __trackBB = raceline.getBBox(); } catch (e) {}

      /* Paint dots at their current distance.
       *
       *   - DNS cars: shown faded + grayscale at their grid box so the
       *     viewer can see they're entered but never made the start.
       *   - In-pit cars: dimmed + nudged perpendicular to the racing line
       *     so they're visibly off-track for the duration of the stop.
       *   - Retired (crashed / disconnected / DNF): park at the last
       *     sampled position, fade to a grayscale ghost so they're
       *     locatable but obviously out.
       */
      const RETIRE_FADE_SEC = 3.0;
      const RETIRED_OPACITY = 0.30;
      const DNS_OPACITY     = 0.25;
      const PIT_OPACITY     = 0.55;
      const PIT_OFFSET_PX   = 14;   // perpendicular nudge from racing line
      dots.forEach(d => {
        const frac = ((d.totalDist % 1.0) + 1.0) % 1.0;
        const pt = getCenterPoint(frac);

        if (d.isDns) {
          // Park at grid spot (their initial totalDist already encodes it)
          d.el.setAttribute('transform', 'translate(' + pt.x + ',' + pt.y + ')');
          d.el.setAttribute('opacity', DNS_OPACITY.toFixed(2));
          d.el.setAttribute('filter', 'url(#rp-grayscale)');
          return;
        }

        // In-pit detection: replay payload may carry per-driver pit_stops
        // {entered_time, exited_time, ...}. Cheap linear scan over a
        // typically small array.
        let inPit = false;
        if (Array.isArray(d.drv.pit_stops)) {
          for (const ps of d.drv.pit_stops) {
            if (typeof ps.entered_time !== 'number') continue;
            const out = (typeof ps.exited_time === 'number') ? ps.exited_time : ps.entered_time + 60;
            if (simTime >= ps.entered_time && simTime <= out) { inPit = true; break; }
          }
        }

        // Apply perpendicular nudge for in-pit cars so they sit off the
        // racing line. Pull tangent from a slightly-ahead point and
        // rotate 90° to get the normal.
        let drawX = pt.x, drawY = pt.y;
        if (inPit) {
          const ahead = getCenterPoint(((frac + 0.0008) % 1.0));
          const tx = ahead.x - pt.x, ty = ahead.y - pt.y;
          const len = Math.sqrt(tx * tx + ty * ty) || 1;
          // Rotate tangent 90° clockwise (pit lane is conventionally on
          // the right of the start/finish line — swap sign per track if
          // needed).
          drawX = pt.x + (ty / len) * PIT_OFFSET_PX;
          drawY = pt.y - (tx / len) * PIT_OFFSET_PX;
        }
        d.el.setAttribute('transform', 'translate(' + drawX + ',' + drawY + ')');
        updateFocusNameplateScale(d, drawY);

        if (d.retiredAt !== null && simTime > d.retiredAt) {
          // Hold a faded ghost at the last point so the viewer can see
          // where the driver dropped out, rather than fading them off
          // the screen entirely.
          const since = simTime - d.retiredAt;
          const fade = Math.max(RETIRED_OPACITY, 1 - since / RETIRE_FADE_SEC * (1 - RETIRED_OPACITY));
          d.el.setAttribute('opacity', fade.toFixed(2));
          if (since > RETIRE_FADE_SEC) {
            d.el.setAttribute('filter', 'url(#rp-grayscale)');
          }
        } else if (inPit) {
          d.el.setAttribute('opacity', PIT_OPACITY.toFixed(2));
          d.el.removeAttribute('filter');
        } else {
          d.el.setAttribute('opacity', '1');
          d.el.removeAttribute('filter');
        }
      });

      /* Leader card */
      if (ranked[0]) {
        $('rp-leader-name').textContent = lastName(ranked[0].drv.name);
        $('rp-leader-lap').textContent = 'L' + lap + '/' + displayTotal;
      }

      /* Battle card — find the closest pair by actual distance gap */
      let bestPair = null, bestGap = Infinity;
      for (let i = 0; i < ranked.length - 1; i++) {
        const a = ranked[i], b = ranked[i + 1];
        if (a.isDns || b.isDns) continue;
        // Gap in seconds = distance difference (laps) × lap time
        const gap = Math.abs(a.totalDist - b.totalDist) * lapTimeSec;
        if (gap >= 0 && gap < bestGap) { bestGap = gap; bestPair = { a, b, posA: i + 1, posB: i + 2 }; }
      }
      if (bestPair) {
        $('rp-battle-label').textContent = 'Battle for ' + ordinal(bestPair.posB);
        $('rp-battle-pos1').textContent = bestPair.posA;
        $('rp-battle-pos2').textContent = bestPair.posB;
        $('rp-battle-name1').textContent = lastName(bestPair.a.drv.name);
        $('rp-battle-name2').textContent = lastName(bestPair.b.drv.name);
        $('rp-battle-name1').style.color = '#' + bestPair.a.drv.livery.color1;
        $('rp-battle-name2').style.color = '#' + bestPair.b.drv.livery.color1;
        $('rp-battle-team1').textContent = '#' + bestPair.a.drv.car_number;
        $('rp-battle-team2').textContent = '#' + bestPair.b.drv.car_number;
        $('rp-battle-gap').textContent = '+' + bestGap.toFixed(3);
      }

      // Record owner rank for the overtake detector in animate().
      state.ownerRank = -1;
      for (let i = 0; i < ranked.length; i++) {
        if (ranked[i].drv.is_us) {
          state.ownerRank = i;
          // Update the fixed-above tag with current position
          const cb = ranked[i].el.__callout;
          if (cb && cb.posTxt) cb.posTxt.textContent = 'P' + (i + 1);
          break;
        }
      }

      ranked.forEach((d, idx) => {
        if (!d.towerRow) return;
        const curRank = idx + 1;

        // Detect position change vs previous frame
        if (d._lastRank !== undefined && d._lastRank !== curRank && progress > 0.01) {
          const gained = curRank < d._lastRank;
          // Remove old flash class (restart animation if same direction)
          d.towerRow.classList.remove('pos-gained', 'pos-lost');
          // Force reflow so re-adding the same class restarts the animation
          void d.towerRow.offsetWidth;
          d.towerRow.classList.add(gained ? 'pos-gained' : 'pos-lost');
          // Clean up after animation completes (2s)
          clearTimeout(d._flashTimer);
          d._flashTimer = setTimeout(() => {
            d.towerRow.classList.remove('pos-gained', 'pos-lost');
          }, 2000);
        }
        d._lastRank = curRank;

        d.towerRow.style.transform = 'translateY(' + (idx * 32) + 'px)';
        d.towerRow.querySelector('.tower-pos').textContent = curRank;
        d.towerRow.querySelector('.tower-name').textContent = d.abbr;
        // Position delta vs grid: + = gained, - = lost
        const deltaEl = d.towerRow.querySelector('.tower-delta');
        if (deltaEl) {
          const startPos = (d.drv.race_start_pos || 0) + 1;  // 0-based → 1-based
          const delta = startPos - curRank;
          if (!startPos) {
            deltaEl.innerHTML = '<span class="d-num">—</span>';
            deltaEl.className = 'tower-delta';
          } else if (delta > 0) {
            // Gain: caret on top, number below
            deltaEl.innerHTML = '<span class="d-car">▲</span><span class="d-num">' + delta + '</span>';
            deltaEl.className = 'tower-delta gained';
          } else if (delta < 0) {
            // Loss: number on top, caret below
            deltaEl.innerHTML = '<span class="d-num">' + Math.abs(delta) + '</span><span class="d-car">▼</span>';
            deltaEl.className = 'tower-delta lost';
          } else {
            deltaEl.innerHTML = '<span class="d-num">0</span>';
            deltaEl.className = 'tower-delta even';
          }
        }
        const gapEl = d.towerRow.querySelector('.tower-gap');
        const isRetired = d.retiredAt !== null && simTime > d.retiredAt;
        if (d.isDns) {
          gapEl.textContent = 'DNS';
          gapEl.className = 'tower-gap';
          d.towerRow.classList.add('out-row');
        } else if (isRetired) {
          gapEl.textContent = 'OUT';
          gapEl.className = 'tower-gap';
          d.towerRow.classList.add('out-row');
        } else if (idx === 0) {
          gapEl.textContent = 'LEADER';
          gapEl.className = 'tower-gap leader';
          d.towerRow.classList.remove('out-row');
        } else {
          // True gap = simTime − leader's session_time when the leader was
          // at this car's current distance. Uses the leader's own
          // lap_samples (or synthetic inverse) so the interval reflects
          // the actual pace at that point in the race, not a flat
          // multiplication by leader_avg_lap. Falls back to the old
          // distance×avg approximation if the leader's invDistFn returns
          // null (target outside recorded range).
          let gapSec;
          if (firstActive && typeof firstActive.invDistFn === 'function') {
            const tLeaderHere = firstActive.invDistFn(d.totalDist);
            if (typeof tLeaderHere === 'number' && tLeaderHere >= 0) {
              gapSec = Math.max(0, simTime - tLeaderHere);
            } else {
              gapSec = Math.max(0, (leaderDist - d.totalDist) * lapTimeSec);
            }
          } else {
            gapSec = Math.max(0, (leaderDist - d.totalDist) * lapTimeSec);
          }
          gapEl.textContent = fmtInterval(gapSec);
          gapEl.className = 'tower-gap';
          d.towerRow.classList.remove('out-row');
        }
      });

      // Track the active callout's target car (if any) — runs last so it
      // reads the transform we just applied to the target dot.
      updateCalloutPosition();
    }   // end renderFrame

    /* ── Scrub bar ── */
    let scrubDragging = false;
    function seekTo(newSimTime) {
      simTime = Math.max(0, Math.min(newSimTime, DURATION_S));
      // Reveal dots that were hidden by the pre-start opacity=0 state.
      dots.forEach(d => d.el.setAttribute('opacity', '1'));
      // Re-anchor the highlight cursor and clear any active window —
      // skipping past events should not trigger banners that already passed.
      activeEvent = null;
      nextIdx = findNextIdx(simTime);
      prevOwnerRank = -1;
      hideCallout();
      hideFinish();
      renderFrame();
    }
    scrubSlider.addEventListener('mousedown',  () => { scrubDragging = true; });
    scrubSlider.addEventListener('touchstart', () => { scrubDragging = true; });
    scrubSlider.addEventListener('input', function () {
      scrubDragging = true;
      const frac = parseInt(this.value, 10) / 1000;
      seekTo(frac * DURATION_S);
    });
    const stopDrag = () => { scrubDragging = false; };
    scrubSlider.addEventListener('mouseup',   stopDrag);
    scrubSlider.addEventListener('touchend',  stopDrag);
    scrubSlider.addEventListener('change',    stopDrag);

    playBtn.onclick = function () {
      if (state.running) {
        cancelAnimationFrame(state.animId);
        state.running = false;
        playBtn.textContent = '\u25B6 Resume';
        return;
      }
      // If we're at the end, restart from zero.
      if (simTime >= DURATION_S) {
        simTime = 0;
        dots.forEach(d => { d.el.setAttribute('opacity', '0'); d.totalDist = 0; });
        activeEvent = null;
        nextIdx = 0;
        prevOwnerRank = -1;
        lastOvertakeAt = -999;
        hideCallout();
        hideFinish();
      }
      state.running = true;
      playBtn.textContent = '\u23F8 Pause';
      lastFrame = 0;
      state.animId = requestAnimationFrame(animate);
    };

    // Paint the initial frame so the dots/tower aren't blank before play.
    renderFrame();
  }

  /* ── Wire up controls ── */
  function init() {
    loadRaceList();
    const finishReplayBtn = $('rp-finish-replay');
    if (finishReplayBtn) {
      finishReplayBtn.addEventListener('click', () => {
        hideFinish();
        // playBtn handles end-of-race restart already.
        $('rp-play-btn').click();
      });
    }
    $('rp-race-picker').addEventListener('change', (e) => {
      if (e.target.value) loadRaceFromDb(e.target.value);
    });
    $('rp-json-file').addEventListener('change', (e) => {
      const f = e.target.files[0]; if (!f) return;
      const r = new FileReader();
      r.onload = ev => {
        try { setupAnimation(JSON.parse(ev.target.result)); }
        catch (err) { alert('Bad JSON: ' + err.message); }
      };
      r.readAsText(f);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
