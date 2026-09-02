  // ---------------------------------------------------------- remote play
  // This device is a potential "screen" (something a phone can play onto) when
  // it's not an iPhone/iPad and has a pointer — i.e. the laptop's browser.
  const IS_SCREEN = !IS_IOS && matchMedia("(hover: hover)").matches;
  // One screen identity per BROWSER (localStorage, shared by all its tabs), so the
  // laptop never shows up as two screens. A lease below picks exactly one tab to
  // act on that identity, and a tab that is actually playing always wins it.
  let SCREEN_ID = localStorage.getItem("matinee-screen-id");
  if (!SCREEN_ID) { SCREEN_ID = Math.random().toString(36).slice(2, 12); localStorage.setItem("matinee-screen-id", SCREEN_ID); }
  let CAST = null;   // {id, name} of the screen this device is controlling
  try { CAST = JSON.parse(sessionStorage.getItem("matinee-cast") || "null"); } catch (e) { CAST = null; }
  const castBtn = $("#castBtn");
  const remoteBar = $("#remoteBar");
  let screensCache = [];

  const sendCommand = (command) =>
    fetch("/api/remote/command", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ screen_id: CAST.id, command }) }).then((r) => r.json()).catch(() => ({ ok: false }));

  const castPlay = async (v) => {
    const r = await sendCommand({ action: "play", video_id: v.id, profile: PROFILE || null });
    toast(r.ok ? `Playing on ${CAST.name}` : `${CAST.name} is not reachable`);
    if (!r.ok) setCast(null);
  };

  const setCast = (target) => {
    CAST = target;
    sessionStorage.setItem("matinee-cast", JSON.stringify(target));
    document.body.classList.toggle("casting", !!target);
    if (!target) remoteBar.hidden = true;
    updateCastUi();
  };

  // Executed on the SCREEN when a phone sends a command.
  const handleRemoteCommand = async (cmd) => {
    const v = current && current.el;
    switch (cmd.action) {
      case "play": {
        if (cmd.profile && PROFILES.includes(cmd.profile) && cmd.profile !== PROFILE) {
          PROFILE = cmd.profile;
          localStorage.setItem("matinee-profile", PROFILE);
          homeCache = null;
          updateAvatarBtn();
        }
        const gate = $("#pgate");
        if (gate) gate.remove();
        try {
          openPlayer(await api(`/api/video/${cmd.video_id}`), { fromApp: false, viaRemote: true });
          if (remoteBc) remoteBc.postMessage({ t: "remote-play", token: TAB_TOKEN });
        } catch (e) { /* gone */ }
        break;
      }
      case "pause": if (v) v.pause(); break;
      case "resume":
        if (v) { v.play().catch(() => {}); }
        else {
          // nothing open on this screen: start the profile's continue-watching pick
          try {
            const home = await api("/api/home");
            if (home.hero) {
              openPlayer(home.hero, { fromApp: false, viaRemote: true });
              if (remoteBc) remoteBc.postMessage({ t: "remote-play", token: TAB_TOKEN });
            }
          } catch (e) { /* offline */ }
        }
        break;
      case "seek": if (v && isFinite(cmd.position)) v.currentTime = Math.max(0, cmd.position); break;
      case "skip": if (v) v.currentTime = Math.max(0, v.currentTime + (Number(cmd.delta) || 0)); break;
      case "volume":
        if (v && current && current.setVolume) current.setVolume(v.volume + (Number(cmd.delta) || 0), { silent: true });
        break;
      case "next":
        if (current && current.upnextShowing && current.upnextGo) { current.upnextGo(); break; }
        if (current) {
          try {
            const d = await api(`/api/video/${current.video.id}`);
            if (d.next) {
              const nv = current.el;
              const dur = (nv && isFinite(nv.duration) && nv.duration) || current.duration || 0;
              if (nv && dur && nv.currentTime / dur >= 0.85) {
                saveProgress(current.video.id, dur, dur);
                current.suppressCloseSave = true;
              }
              openPlayer(d.next, { fromApp: false, viaRemote: true });
              if (remoteBc) remoteBc.postMessage({ t: "remote-play", token: TAB_TOKEN });
            }
          } catch (e) { /* ignore */ }
        }
        break;
      case "fullscreen": {
        if (!current) break;
        const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
        if (fsEl) {
          (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        } else {
          const req = playerEl.requestFullscreen || playerEl.webkitRequestFullscreen;
          // Safari (and unconfigured Chrome) only allow fullscreen from a LOCAL
          // click — so arm it: the next click/tap on this machine goes fullscreen.
          const armFsOnClick = () => {
            toast("Click or tap anywhere on this screen to go fullscreen", { label: "Go", onClick: () => {} });
            disarmFs();
            const once = () => {
              disarmFs();
              if (!current || playerEl.hidden) return;   // stopped meanwhile: nothing to fullscreen
              try {
                const r2 = req && req.call(playerEl);
                if (r2 && r2.catch) r2.catch(() => {});
              } catch (err) { /* give up quietly */ }
            };
            armedFsOnce = once;
            document.addEventListener("pointerdown", once, true);
            setTimeout(() => { if (armedFsOnce === once) disarmFs(); }, 120000);
          };
          try {
            const r = req && req.call(playerEl);
            if (r && r.catch) r.catch(armFsOnClick);
          } catch (e) {
            armFsOnClick();
          }
        }
        break;
      }
      case "cc":
        if (current && current.toggleCc) toast(current.toggleCc() ? "Subtitles on" : "Subtitles off");
        break;
      case "suboffset":
        if (current && current.nudgeSubs) {
          current.nudgeSubs(Number(cmd.delta) || 0);
          const so = current.subOffset || 0;
          toast(so === 0 ? "Subtitles in sync" : `Subtitles ${Math.abs(so)}s ${so > 0 ? "later" : "earlier"}`);
        }
        break;
      case "stop":
        closePlayer();
        if (location.hash.startsWith("#/watch")) { history.replaceState(null, "", "#/"); route(); }
        break;
    }
  };

  // Any tab of this browser hears when another one starts remote playback and
  // stops its own video — no two videos ever play at once on the laptop.
  let remoteBc = null;
  const TAB_TOKEN = Math.random().toString(36).slice(2, 12);
  try {
    remoteBc = new BroadcastChannel("matinee-remote");
    remoteBc.onmessage = (e) => {
      if (!e.data || e.data.token === TAB_TOKEN) return;
      if (e.data.t === "remote-play" && current) {
        closePlayer();
        if (location.hash.startsWith("#/watch")) { history.replaceState(null, "", "#/"); route(); }
      }
    };
  } catch (e) { /* very old browser */ }

  if (IS_SCREEN) {
    const LEASE_KEY = "matinee-screen-lease";
    const writeLease = () =>
      localStorage.setItem(LEASE_KEY, JSON.stringify({ token: TAB_TOKEN, ts: Date.now(), playing: !!current }));
    const iAmLeader = () => {
      let lease = null;
      try { lease = JSON.parse(localStorage.getItem(LEASE_KEY) || "null"); } catch (e) { lease = null; }
      if (!lease || lease.token === TAB_TOKEN || Date.now() - lease.ts > 6000) { writeLease(); return true; }
      if (current && !lease.playing) { writeLease(); return true; }   // the playing tab outranks an idle leader
      return false;
    };

    const reportCycle = async () => {
      if (!iAmLeader()) return;
      const v = current && current.el;
      const state = current ? {
        video_id: current.video.id, title: current.video.title, folder: current.video.folder,
        position: v && isFinite(v.currentTime) ? v.currentTime : 0,
        duration: (v && isFinite(v.duration) && v.duration) || current.video.duration || 0,
        paused: !v || v.paused, profile: PROFILE || null,
        volume: v ? Math.round((v.muted ? 0 : v.volume) * 100) : null,
        fullscreen: !!(document.fullscreenElement || document.webkitFullscreenElement),
        blocked: !!current.blocked,
        series: !!current.series,
        subs: !!(v && v.textTracks && v.textTracks[0]),
        cc_on: !!(v && v.textTracks && v.textTracks[0] && v.textTracks[0].mode === "showing"),
        sub_offset: current.subOffset || 0,
        upnext: !!current.upnextShowing,
        next_id: current.next ? current.next.id : null,
        next_title: current.next ? current.next.title : null,
      } : null;
      try {
        const r = await fetch("/api/remote/state", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ screen_id: SCREEN_ID, name: "Laptop", state }) }).then((x) => x.json());
        for (const cmd of r.commands || []) await handleRemoteCommand(cmd);
      } catch (e) { /* server briefly away */ }
    };
    // A Worker's timer is not throttled when the tab is hidden — a backgrounded
    // laptop tab must stay reachable from the phone.
    try {
      const w = new Worker(URL.createObjectURL(new Blob(["setInterval(() => postMessage(1), 2000);"], { type: "text/javascript" })));
      w.onmessage = reportCycle;
    } catch (e) {
      setInterval(reportCycle, 2000);
    }
    reportCycle();
  }

  // Controller side: keep the cast button and remote bar fresh.
  const rbTrickCache = {};   // video_id -> trickplay meta (or "loading"/"none")
  const rbTrick = (videoId) => {
    const m = rbTrickCache[videoId];
    if (m && m !== "loading") return m === "none" ? null : m;
    if (!m) {
      rbTrickCache[videoId] = "loading";
      api(`/api/trickplay/${videoId}`)
        .then((meta) => { rbTrickCache[videoId] = meta || "none"; })
        .catch(() => { rbTrickCache[videoId] = "none"; });
    }
    return null;
  };

  let rbScrub = false;   // finger on the remote's seek bar: don't repaint under it
  const rbPreviewEl = () => {
    let pv = remoteBar.querySelector(".seekpreview");
    if (!pv) {
      pv = el(`<div class="seekpreview rb-preview" hidden><div class="sp-img" hidden></div><span class="sp-time"></span></div>`);
      remoteBar.appendChild(pv);
    }
    return pv;
  };
  remoteBar.addEventListener("input", (e) => {
    if (!e.target.classList.contains("rb-seek")) return;
    rbScrub = true;
    const target = screensCache.find((sc) => CAST && sc.id === CAST.id);
    const st = target && target.state;
    if (!st || !st.duration) return;
    const frac = e.target.value / 1000;
    const t = frac * st.duration;
    const timeEl = remoteBar.querySelector(".rb-time");
    if (timeEl) timeEl.textContent = `${fmtTime(t)} / ${fmtTime(st.duration)}`;
    const pv = rbPreviewEl();
    $(".sp-time", pv).textContent = fmtTime(t);
    applyTrickTile($(".sp-img", pv), rbTrick(st.video_id), st.video_id, t);
    const rect = e.target.getBoundingClientRect();
    const bar = remoteBar.getBoundingClientRect();
    const w = pv.offsetWidth || 176;
    const h = pv.offsetHeight || 110;
    pv.style.left = `${Math.max(8, Math.min(rect.left - bar.left + frac * rect.width - w / 2, remoteBar.clientWidth - w - 8))}px`;
    pv.style.top = `${rect.top - bar.top - h - 10}px`;
    pv.hidden = false;
  });
  remoteBar.addEventListener("change", (e) => {
    if (!e.target.classList.contains("rb-seek")) return;
    const target = screensCache.find((sc) => CAST && sc.id === CAST.id);
    const st = target && target.state;
    if (st && st.duration) sendCommand({ action: "seek", position: (e.target.value / 1000) * st.duration });
    const pv = remoteBar.querySelector(".seekpreview");
    if (pv) pv.hidden = true;
    setTimeout(() => { rbScrub = false; }, 2500);   // wait for the laptop's next state report
  });

  const rbEpCache = {};   // folder -> {id -> episode}, for the Up Next card
  const rbEpisode = (folder, id) => {
    const m = rbEpCache[folder];
    if (m && m !== "loading") return m[id] || null;
    if (!m) {
      rbEpCache[folder] = "loading";
      api(`/api/show/${encodeURIComponent(folder)}`).then((sd) => {
        const map = {};
        for (const se of sd.seasons_list || []) for (const e2 of se.episodes || []) map[e2.id] = e2;
        rbEpCache[folder] = map;
      }).catch(() => { delete rbEpCache[folder]; });
    }
    return null;
  };

  const updateCastUi = () => {
    const others = screensCache.filter((sc) => sc.id !== SCREEN_ID && sc.online);
    castBtn.hidden = !others.length && !CAST;
    castBtn.classList.toggle("active", !!CAST);
    if (!CAST) { remoteBar.hidden = true; return; }
    const target = screensCache.find((sc) => sc.id === CAST.id);
    if (!target || !target.online) { remoteBar.hidden = true; return; }
    const st = target.state;
    remoteBar.hidden = false;
    if (!st) {
      remoteBar.innerHTML = `<div class="rb-info"><span class="rb-name">${esc(CAST.name)}</span><span class="rb-title">Nothing playing — tap any title to play it there</span></div>
        <div class="rb-controls"><button class="pbtn" data-rb="disconnect" aria-label="Disconnect">\u2715</button></div>`;
      return;
    }
    if (rbScrub) return;   // mid-drag: leave the DOM alone
    remoteBar.innerHTML = `
      <div class="rb-info">
        <span class="rb-name">Playing on ${esc(CAST.name)}${st.profile ? " \u00b7 " + esc(st.profile) : ""}</span>
        <span class="rb-title">${esc(st.title)}</span>
        <span class="rb-time">${fmtTime(st.position)}${st.duration ? " / " + fmtTime(st.duration) : ""}${st.volume != null ? ` \u00b7 \ud83d\udd0a ${st.volume}%` : ""}</span>
        ${st.blocked ? '<span class="rb-note">\u26a0\ufe0e Laptop needs one click to start \u2014 or allow Auto-Play for the site in Safari settings (one time)</span>' : ""}
      </div>
      <input type="range" class="rb-seek" min="0" max="1000" step="1" aria-label="Seek on the laptop"
        value="${st.duration ? Math.round((st.position / st.duration) * 1000) : 0}">
      ${(() => {
        if (!st.upnext || !st.next_id) return "";
        const ep = rbEpisode(st.folder, st.next_id);
        const img = (ep && ep.ep_image) || `/poster/${encodeURIComponent(st.folder)}`;
        return `<div class="rb-upnext">
          <img class="rbu-img" src="${img}" alt="" onerror="this.hidden = true">
          <div class="rbu-body">
            <div class="rbu-k">Up next on ${esc(CAST.name)}</div>
            <div class="rbu-t">${esc((ep && ep.label) || st.next_title || "")}</div>
            ${ep && ep.ep_name ? `<div class="rbu-n">${esc(ep.ep_name)}</div>` : ""}
            ${ep && ep.ep_summary ? `<div class="rbu-s">${esc(ep.ep_summary)}</div>` : ""}
            <div class="rbu-actions">
              <button class="btn" data-rb="next">\u25b6 Play now</button>
              <button class="btn secondary" data-rb="stop">\u2715 Close</button>
            </div>
          </div>
        </div>`;
      })()}
      <div class="rb-controls">
        <div class="rb-g rb-g1">
        <button class="pbtn" data-rb="back30" aria-label="Skip back"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg><span class="skiplbl">${APPCFG.skip_seconds}</span></button>
        <button class="pbtn rb-play" data-rb="${st.paused ? "resume" : "pause"}" aria-label="Play or pause">${st.paused ? PLAY_SVG : '<svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>'}</button>
        <button class="pbtn" data-rb="fwd30" aria-label="Skip forward"><svg viewBox="0 0 24 24" width="20" height="20" style="transform:scaleX(-1)"><path fill="currentColor" d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg><span class="skiplbl">${APPCFG.skip_seconds}</span></button>
        </div>
        <div class="rb-g rb-g2">
        <button class="pbtn" data-rb="next" aria-label="Next episode"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M6 18l8.5-6L6 6v12zm10-12v12h2V6h-2z"/></svg></button>
        ${st.series ? `<button class="pbtn" data-rb="eps" aria-label="Episodes"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M4 6h13v2H4V6zm0 5h13v2H4v-2zm0 5h13v2H4v-2zm15-9h2v2h-2V7zm0 5h2v2h-2v-2zm0 5h2v2h-2v-2z"/></svg></button>` : ""}
        <button class="pbtn" data-rb="fs" aria-label="Fullscreen on the laptop">${st.fullscreen
          ? '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>'
          : '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>'}</button>
        <button class="pbtn" data-rb="voldown" aria-label="Volume down"><svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3z"/></svg>\u2212</button>
        <button class="pbtn" data-rb="volup" aria-label="Volume up"><svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4z"/></svg>+</button>
        </div>
        ${st.subs ? `<div class="rb-g rb-gsub">
        <button class="pbtn ccbtn ${st.cc_on ? "active" : ""}" data-rb="cc" aria-label="Subtitles on the laptop">CC</button>
        <button class="pbtn rb-sync" data-rb="subminus" aria-label="Subtitles earlier">\u2212\u00bd</button>
        <span class="rb-off">${st.sub_offset ? (st.sub_offset > 0 ? "+" : "") + st.sub_offset + "s" : "sync"}</span>
        <button class="pbtn rb-sync" data-rb="subplus" aria-label="Subtitles later">+\u00bd</button>
        </div>` : ""}
        <div class="rb-g rb-g3">
        <button class="pbtn" data-rb="stop" aria-label="Stop">\u25a0</button>
        <button class="pbtn" data-rb="disconnect" aria-label="Disconnect">\u2715</button>
        </div>
      </div>`;
  };

  // swipe up on the remote bar for big controls, down for the slim bar
  let rbSwipe = null;
  remoteBar.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") { rbSwipe = null; return; }
    rbSwipe = { x: e.clientX, y: e.clientY };
  });
  remoteBar.addEventListener("pointercancel", () => { rbSwipe = null; });
  remoteBar.addEventListener("pointerup", (e) => {
    if (!rbSwipe || e.pointerType !== "touch") return;
    const dy = e.clientY - rbSwipe.y, dx = e.clientX - rbSwipe.x;
    rbSwipe = null;
    if (Math.abs(dy) < 40 || Math.abs(dx) > Math.abs(dy)) return;
    remoteBar.classList.toggle("expanded", dy < 0);
  });

  remoteBar.addEventListener("click", (e) => {
    const b = e.target.closest("[data-rb]");
    if (!b) return;
    const act = b.dataset.rb;
    if (act === "disconnect") { setCast(null); toast("Playing on this device again"); return; }
    if (act === "back30") sendCommand({ action: "skip", delta: -APPCFG.skip_seconds });
    else if (act === "fwd30") sendCommand({ action: "skip", delta: APPCFG.skip_seconds });
    else if (act === "eps") {
      const target = screensCache.find((sc) => CAST && sc.id === CAST.id);
      const st = target && target.state;
      if (st && st.folder) openEpisodePanel({
        folder: st.folder, currentId: st.video_id, profile: st.profile || PROFILE, mount: document.body,
        onPick: (ep) => { sendCommand({ action: "play", video_id: ep.id, profile: st.profile || PROFILE }); toast(`Playing on ${CAST.name}`); },
      });
    }
    else if (act === "cc") sendCommand({ action: "cc" });
    else if (act === "subminus") sendCommand({ action: "suboffset", delta: -0.5 });
    else if (act === "subplus") sendCommand({ action: "suboffset", delta: 0.5 });
    else if (act === "fs") {
      const target = screensCache.find((sc) => CAST && sc.id === CAST.id);
      const wanted = !(target && target.state && target.state.fullscreen);
      sendCommand({ action: "fullscreen" });
      // If the laptop doesn't change state, say why instead of failing silently.
      setTimeout(() => {
        const t2 = screensCache.find((sc) => CAST && sc.id === CAST.id);
        if (t2 && t2.state && !!t2.state.fullscreen !== wanted) {
          toast("Laptop is waiting \u2014 click or tap anywhere on the laptop once and it goes fullscreen (its browser requires one local click)");
        }
      }, 6000);
    }
    else if (act === "voldown") sendCommand({ action: "volume", delta: -0.1 });
    else if (act === "volup") sendCommand({ action: "volume", delta: 0.1 });
    else sendCommand({ action: act });
    if (act === "pause" || act === "resume") { b.dataset.rb = act === "pause" ? "resume" : "pause"; }
  });

  castBtn.addEventListener("click", () => {
    const others = screensCache.filter((sc) => sc.id !== SCREEN_ID && sc.online);
    modalEl.innerHTML = "";
    const box = el(`
      <div class="box" role="dialog" aria-modal="true">
        <h3>Play on\u2026</h3>
        <div class="castlist"></div>
        <button class="btn close" type="button">Done</button>
      </div>`);
    const list = $(".castlist", box);
    const mkItem = (label, sub, active, onPick) => {
      const item = el(`<button class="castitem ${active ? "active" : ""}" type="button">
        <span class="ci-main">${esc(label)}</span>
        ${sub ? `<span class="ci-sub">${esc(sub)}</span>` : ""}
        ${active ? "<span class=\"on\">\u2713</span>" : ""}</button>`);
      item.addEventListener("click", () => { onPick(); modalEl.hidden = true; });
      list.appendChild(item);
    };
    mkItem("This device", null, !CAST, () => { setCast(null); });
    others.forEach((sc) => {
      const st = sc.state;
      const sub = st ? `Playing: ${st.title} \u00b7 ${fmtTime(st.position)}${st.duration ? " / " + fmtTime(st.duration) : ""}` : "Nothing playing";
      mkItem(sc.name, sub, CAST && CAST.id === sc.id, () => {
        setCast({ id: sc.id, name: sc.name });
        toast(st ? `Controlling ${sc.name}` : `Tap any title to play it on ${sc.name}`);
      });
    });
    if (!others.length) list.appendChild(el(`<p class="muted small">No other screen is online. Open Matinee in Chrome on the laptop first.</p>`));
    $(".close", box).addEventListener("click", () => { modalEl.hidden = true; });
    modalEl.appendChild(box);
    modalEl.hidden = false;
  });

  // "Laptop is already watching X in another window" — close it or watch both.
  const confirmTakeover = (v, opts, busy) => {
    modalEl.innerHTML = "";
    const box = el(`
      <div class="box" role="dialog" aria-modal="true">
        <h3>Already watching</h3>
        <p>${esc(busy.name)} is already watching <b>${esc(busy.state.title)}</b> in another window.</p>
        <div class="takeover-actions">
          <button class="btn" type="button" data-tk="take">Close it and watch here</button>
          <button class="btn secondary" type="button" data-tk="both">Watch both</button>
          <button class="btn secondary" type="button" data-tk="cancel">Cancel</button>
        </div>
      </div>`);
    box.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-tk]");
      if (!b) return;
      modalEl.hidden = true;
      if (b.dataset.tk === "cancel") return;
      if (b.dataset.tk === "take") {
        try {
          await fetch("/api/remote/command", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ screen_id: busy.id, command: { action: "stop" } }) });
        } catch (err) { /* other session may already be gone */ }
      }
      openPlayer(v, { ...opts, confirmed: true });
    });
    modalEl.appendChild(box);
    modalEl.hidden = false;
  };

  const pollScreens = async () => {
    try { screensCache = (await fetch("/api/remote/screens").then((r) => r.json())).screens || []; } catch (e) { screensCache = []; }
    updateCastUi();
    // On the phone: if the laptop is mid-session, offer to attach as its remote (once).
    if (!IS_SCREEN && !CAST && !sessionStorage.getItem("matinee-attach-offered")) {
      const playing = screensCache.find((sc) => sc.id !== SCREEN_ID && sc.online && sc.state);
      if (playing) {
        sessionStorage.setItem("matinee-attach-offered", "1");
        toast(`${playing.name} is watching ${playing.state.title}`, {
          label: "Control it",
          onClick: () => setCast({ id: playing.id, name: playing.name }),
        });
      }
    }
  };
  setInterval(pollScreens, 2500);
  pollScreens();

