  // one-time migration from the app's old name: keep profiles, volume, screen ids
  try {
    for (const k of Object.keys(localStorage)) {
      if (k.startsWith("shaggyflix-")) {
        const nk = k.replace("shaggyflix-", "matinee-");
        if (localStorage.getItem(nk) === null) localStorage.setItem(nk, localStorage.getItem(k));
      }
    }
  } catch (e) { /* private mode */ }

  const $ = (sel, root = document) => root.querySelector(sel);
  const main = $("#main");
  const playerEl = $("#player");
  const toastEl = $("#toast");
  const modalEl = $("#modal");
  const topbar = $("#topbar");
  const searchInput = $("#search");

  const IS_IOS = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (IS_IOS) document.documentElement.classList.add("ios");
  const SAVE_EVERY_MS = 5000;
  const MIN_RESUME = 5;      // seconds — below this we start from 0
  const FINISHED_RATIO = 0.90;

  const PLAY_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
  const BACK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M20 11H7.8l5.6-5.6L12 4l-8 8 8 8 1.4-1.4L7.8 13H20z"/></svg>';

  // ----------------------------------------------------------- utilities
  let PROFILES = [];
  // No pinch-zoom on the phone: Safari fires gesture* for pinches; block them and any
  // multi-finger move (the player's own swipe handling is single-finger, so it is unaffected).
  for (const ev of ["gesturestart", "gesturechange", "gestureend"]) {
    document.addEventListener(ev, (e) => e.preventDefault(), { passive: false });
  }
  document.addEventListener("touchmove", (e) => {
    if (e.touches.length > 1 || (typeof e.scale === "number" && e.scale !== 1)) e.preventDefault();
  }, { passive: false });
  let lastTouchEnd = 0;   // double-tap zoom guard for non-button areas
  document.addEventListener("touchend", (e) => {
    const now = Date.now();
    if (now - lastTouchEnd < 300 && e.touches.length === 0 && !e.target.closest("video, .player, .remotebar, button, a, input, select")) e.preventDefault();
    lastTouchEnd = now;
  }, { passive: false });

  // app-wide settings from the server (name, skip length); defaults until fetched
  const APPCFG = { app_name: "Matinee", skip_seconds: 30, first_run: false };
  const applyBranding = () => {
    const name = APPCFG.app_name || "Matinee";
    const logo = $(".logo");
    const head = name.length > 2 ? name.slice(0, -2) : name;
    const tail = name.length > 2 ? name.slice(-2) : "";
    logo.innerHTML = `${esc(head.toUpperCase())}<span>${esc(tail.toUpperCase())}</span><small class="tagline">home cinema for your own files</small>`;
    document.title = name;
  };

  let PROFILE = localStorage.getItem("matinee-profile") || "";

  const api = async (path, opts) => {
    if (PROFILE && path.startsWith("/api/")) path += (path.includes("?") ? "&" : "?") + "p=" + encodeURIComponent(PROFILE);
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  };

  const fmtTime = (s) => {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  };
  const fmtDuration = (s) => {
    if (!s) return "";
    if (s < 60) return `${Math.round(s)} sec`;
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return h ? `${h}h ${m}m` : `${m} min`;
  };
  const timeLeft = (v) => v.duration && v.position ? `${fmtDuration(v.duration - v.position)} left` : "";

  const hue = (str) => { let h = 0; for (const c of str) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h % 360; };
  const tileBg = (v) => {
    const h = hue(v.folder + "/" + v.title);
    return `linear-gradient(135deg, hsl(${h} 55% 30%), hsl(${(h + 45) % 360} 60% 16%))`;
  };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };

  let toastTimer = null;
  const toast = (text, action) => {
    clearTimeout(toastTimer);
    toastEl.innerHTML = `<span>${esc(text)}</span>`;
    if (action) {
      const b = el(`<button type="button">${esc(action.label)}</button>`);
      b.onclick = () => { action.onClick(); hideToast(); };
      toastEl.appendChild(b);
    }
    toastEl.hidden = false;
    toastTimer = setTimeout(hideToast, action ? 6000 : 3000);
  };
  const hideToast = () => { toastEl.hidden = true; };

  // ---------------------------------------------------- progress saving
  const saveProgress = (id, position, duration, { beacon = false } = {}) => {
    // under the resume threshold there is nothing worth saving (the server ignores it too)
    if (!(position >= MIN_RESUME) && !(duration && position / duration >= FINISHED_RATIO)) return Promise.resolve();
    const body = JSON.stringify({ id, position, duration: duration || null, profile: PROFILE || null });
    if (beacon && navigator.sendBeacon) {
      navigator.sendBeacon("/api/progress", new Blob([body], { type: "application/json" }));
      return Promise.resolve();
    }
    return fetch("/api/progress", {
      method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true,
    }).catch(() => {});
  };


  // Trickplay sprite math shared by the player scrubber and the phone remote:
  // point the tile <div> at the right cell of /trickplay/<id>.jpg for time t.
  const applyTrickTile = (img, meta, videoId, t) => {
    if (!meta) { img.hidden = true; return; }
    const idx = Math.max(0, Math.min(meta.count - 1, Math.floor(t / meta.interval)));
    const col = idx % meta.cols, row = Math.floor(idx / meta.cols);
    img.style.width = meta.tile_w + "px";
    img.style.height = meta.tile_h + "px";
    img.style.backgroundImage = `url('/trickplay/${videoId}.jpg')`;
    img.style.backgroundPosition = `-${col * meta.tile_w}px -${row * meta.tile_h}px`;
    img.hidden = false;
  };
