(function () {
  const $ = (id) => document.getElementById(id);

  function tokenParam() {
    const params = new URLSearchParams(window.location.search);
    return params.get("admin_token") || "";
  }

  function withParams(path, extra) {
    const token = tokenParam();
    const [baseAndQuery, hashPart] = String(path).split("#", 2);
    const url = new URL(baseAndQuery, window.location.origin);
    if (token) url.searchParams.set("admin_token", token);
    Object.entries(extra || {}).forEach(([key, value]) => {
      if (value != null) url.searchParams.set(key, value);
    });
    return url.pathname + url.search + (hashPart ? "#" + hashPart : "");
  }

  function setFrame(frame, src) {
    if (!frame || !src) return;
    frame.onload = () => activateEmbeddedHash(frame, src);
    frame.src = withParams(src, { preview_t: Date.now() });
  }

  function activateEmbeddedHash(frame, src) {
    const hash = String(src || "").split("#", 2)[1];
    if (!hash || !frame.contentDocument) return;
    try {
      const parentY = window.location.hash === "#top" ? 0 : (window.scrollY || 0);
      const doc = frame.contentDocument;
      const tabLink = doc.querySelector("[data-tab='" + CSS.escape(hash) + "']");
      const target = doc.getElementById(hash);
      if (tabLink && target) {
        doc.querySelectorAll(".tab-section").forEach((section) => section.classList.remove("active"));
        target.classList.add("active");
        doc.querySelectorAll("[data-tab]").forEach((link) => {
          link.classList.toggle("active", link.getAttribute("data-tab") === hash);
        });
        if (frame.contentWindow) frame.contentWindow.scrollTo(0, 0);
      } else {
        if (target && frame.contentWindow) frame.contentWindow.scrollTo(0, target.offsetTop || 0);
      }
      window.requestAnimationFrame(() => window.scrollTo(0, parentY));
    } catch (err) {
      console.warn("Could not activate embedded preview hash:", err);
    }
  }

  function fmtInt(value) {
    const n = Number(value) || 0;
    return n.toLocaleString();
  }

  function shortTrack(name) {
    return String(name || "Race Replay")
      .replace("Autodromo Internazionale Enzo e Dino Ferrari", "Imola")
      .replace("Suzuka International Racing Course", "Suzuka")
      .replace("Nordschleife Industriefahrten", "Nordschleife")
      .replace("Nürburgring Grand Prix No Chicane", "Nurburgring GP")
      .replace("Nürburgring Grand-Prix-Strecke", "Nurburgring GP");
  }

  function updateStats(summary) {
    const stats = (summary && summary.stats) || {};
    const boxes = $("hero-stats");
    if (!boxes) return;
    const values = boxes.querySelectorAll("strong");
    if (values[0]) values[0].textContent = fmtInt(stats.races);
    if (values[1]) values[1].textContent = fmtInt(stats.commentary_lines);
    if (values[2]) values[2].textContent = fmtInt(stats.lap_samples);
  }

  function updateReplayExample(summary) {
    const examples = (summary && summary.examples) || {};
    const latest = examples.latest || examples.commentary_rich || examples.lap_sample_rich;
    const deep = examples.commentary_rich || latest;
    if (latest && latest.race_id) {
      const href = "/replay/" + latest.race_id;
      const label = $("latest-label");
      const link = $("latest-link");
      const frame = $("latest-frame");
      const copy = $("latest-copy");
      if (label) label.textContent = "Latest: " + shortTrack(latest.track_name);
      if (link) link.href = href;
      if (copy) {
        const bits = [
          latest.total_laps ? latest.total_laps + " laps" : "",
          latest.car_count ? latest.car_count + " cars" : "",
          latest.owner_finish ? "Focus Driver P" + latest.owner_finish : "",
          latest.dotd_name ? "DOTD " + latest.dotd_name : "",
        ].filter(Boolean);
        copy.textContent = bits.length
          ? bits.join(" / ") + ". Animated map, timing tower, callouts, finish card, results, and report."
          : "Animated map, timing tower, callouts, finish card, results, and report.";
      }
      setFrame(frame, href);
    }
    if (deep && deep.race_id) {
      const deepLink = $("deep-replay-link");
      if (deepLink) deepLink.href = "/replay/" + deep.race_id;
    }
  }

  function refreshPublicFrames() {
    document.querySelectorAll("iframe[data-src]").forEach((frame) => {
      setFrame(frame, frame.dataset.src);
    });
    const latest = $("latest-link");
    if (latest && latest.getAttribute("href") && latest.getAttribute("href") !== "/replay") {
      setFrame($("latest-frame"), latest.getAttribute("href"));
    }
  }

  function updateAdminPreviews(summary) {
    const canAdmin = Boolean(summary && summary.can_admin_preview);
    const note = $("admin-note");
    if (note) {
      note.textContent = canAdmin
        ? "Admin screen captures are live because this request is localhost or includes a valid admin token."
        : "Pit Wall screen captures are protected. Remote visitors see summaries instead of admin controls.";
    }
    document.querySelectorAll("[data-admin-preview]").forEach((card) => {
      const frameWrap = card.querySelector(".preview-frame");
      const frame = card.querySelector("iframe");
      const src = card.getAttribute("data-admin-preview");
      if (frameWrap) frameWrap.classList.toggle("is-live", canAdmin);
      if (canAdmin && frame) {
        setFrame(frame, src);
      } else if (frame) {
        frame.removeAttribute("src");
      }
      card.querySelectorAll("a[href^='/pitwall']").forEach((link) => {
        link.href = withParams(link.getAttribute("href") || "/pitwall");
      });
    });
  }

  async function loadSummary() {
    const token = tokenParam();
    const url = token ? "/api/product/summary?admin_token=" + encodeURIComponent(token) : "/api/product/summary";
    try {
      const summary = await fetch(url, { cache: "no-store" }).then((r) => r.json());
      updateStats(summary);
      updateReplayExample(summary);
      updateAdminPreviews(summary);
      refreshPublicFrames();
      if (window.location.hash === "#top") window.requestAnimationFrame(() => window.scrollTo(0, 0));
    } catch (err) {
      updateStats(null);
      updateAdminPreviews(null);
      refreshPublicFrames();
      if (window.location.hash === "#top") window.requestAnimationFrame(() => window.scrollTo(0, 0));
      console.warn("Product summary unavailable:", err);
    }
  }

  function init() {
    document.querySelectorAll("a[href^='/pitwall']").forEach((link) => {
      link.href = withParams(link.getAttribute("href") || "/pitwall");
    });
    const refresh = $("refresh-previews");
    if (refresh) {
      refresh.addEventListener("click", () => {
        refreshPublicFrames();
        document.querySelectorAll(".preview-frame.is-live iframe").forEach((frame) => {
          if (frame.src) frame.src = withParams(frame.getAttribute("src") || frame.src, { preview_t: Date.now() });
        });
      });
    }
    loadSummary();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
