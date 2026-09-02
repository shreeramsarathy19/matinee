  // -------------------------------------------------------------- player
  let current = null; // { video, el, lastSave, idleTimer, cleanup }

  const openPlayer = (v, opts = {}) => {
    const { fromApp = false, viaRemote = false, confirmed = false } = opts;
    if (CAST) { castPlay(v); return; }
    // One session per screen: starting to watch here while another window/browser
    // on this machine is already watching prompts to close that session first.
    if (IS_SCREEN && !viaRemote && !confirmed) {
      const busy = screensCache.find((sc) => sc.id !== SCREEN_ID && sc.online && sc.state);
      if (busy) { confirmTakeover(v, opts, busy); return; }
    }
    if (current && current.video.id === v.id) return;
    closePlayer({ switching: true });   // keep fullscreen across episode changes

    const wantHash = `#/watch/${v.id}`;
    if (location.hash !== wantHash) {
      if (fromApp) history.pushState({ watch: v.id }, "", wantHash);
      else history.replaceState({ watch: v.id }, "", wantHash);
    }

    playerEl.innerHTML = "";
    const video = document.createElement("video");
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "");
    video.controls = false;   // fully custom controls (Netflix-style) — no native UI to collide with
    video.autoplay = true;
    video.preload = "metadata";
    video.src = `/stream/${v.id}`;
    playerEl.appendChild(video);

    // Video details: sidecar subtitles → CC button; next episode → next button.
    api(`/api/video/${v.id}`).then((d) => {
      if (current !== state) return;
      const attachSubs = () => {
        video.querySelectorAll("track").forEach((t) => t.remove());
        const track = document.createElement("track");
        track.kind = "subtitles";
        track.label = "Subtitles";
        track.srclang = "en";
        track.src = `/subs/${v.id}.vtt?ts=${Date.now()}`;
        track.default = true;
        video.appendChild(track);
        playerEl.querySelectorAll('[data-c="cc"]').forEach((b) => {
          b.hidden = false; b.classList.add("active"); b.classList.remove("ccsearch", "busy");
        });
        state.ccSearch = false;
      };
      state.subOffset = d.sub_offset || 0;
      state.attachSubs = attachSubs;
      if (d.subtitles) {
        attachSubs();
      } else {
        subsearchStatus().then((st) => {
          if (current !== state || !st.configured) return;
          state.ccSearch = true;   // CC now means "go find subtitles"
          playerEl.querySelectorAll('[data-c="cc"]').forEach((b) => {
            b.hidden = false; b.classList.add("ccsearch");
            b.setAttribute("aria-label", "Find subtitles");
          });
        });
      }
      state.next = d.next || null;
      state.series = !!d.series;
      if (state.next) playerEl.querySelectorAll('[data-c="next"]').forEach((b) => { b.hidden = false; });
      if (d.series) playerEl.querySelectorAll('[data-c="eps"]').forEach((b) => { b.hidden = false; });
    }).catch(() => {});

    const overlay = el(`
      <div class="overlay">
        <button class="back" type="button" aria-label="Back">${BACK_SVG}</button>
        <div class="ptitle">${esc(v.title)}<small>${esc(v.folder)}</small></div>
      </div>`);
    playerEl.appendChild(overlay);
    $(".back", overlay).addEventListener("click", () => goHome(fromApp));

    const PAUSE_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
    const cbar = el(`
      <div class="cbar">
        <input type="range" class="seek" min="0" max="1000" step="1" value="0" aria-label="Seek">
        <div class="crow">
          <button class="pbtn playbtn" data-c="play" aria-label="Play or pause">${PLAY_SVG}</button>
          <button class="pbtn" data-c="back30" aria-label="Skip back"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg><span class="skiplbl">${APPCFG.skip_seconds}</span></button>
          <button class="pbtn" data-c="fwd30" aria-label="Skip forward"><svg viewBox="0 0 24 24" width="22" height="22" style="transform:scaleX(-1)"><path fill="currentColor" d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg><span class="skiplbl">${APPCFG.skip_seconds}</span></button>
          <div class="volwrap no-ios">
            <button class="pbtn" data-c="mute" aria-label="Mute"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1c2.9.9 5 3.5 5 6.7s-2.1 5.8-5 6.7v2.1c4-.9 7-4.5 7-8.8s-3-7.9-7-8.8z"/></svg></button>
            <input type="range" class="vol" min="0" max="100" step="5" aria-label="Volume">
          </div>
          <span class="ctime">0:00 / 0:00</span>
          <span class="cspace"></span>
          <button class="pbtn ccbtn" data-c="cc" hidden aria-label="Subtitles">CC</button>
          <button class="pbtn speedbtn" data-c="speed" aria-label="Playback speed">1\u00d7</button>
          <button class="pbtn" data-c="eps" hidden aria-label="Episodes"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M4 6h13v2H4V6zm0 5h13v2H4v-2zm0 5h13v2H4v-2zm15-9h2v2h-2V7zm0 5h2v2h-2v-2zm0 5h2v2h-2v-2z"/></svg></button>
          <button class="pbtn" data-c="next" hidden aria-label="Next episode"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M6 18l8.5-6L6 6v12zm10-12v12h2V6h-2z"/></svg></button>
          <button class="pbtn" data-c="lock" aria-label="Lock the screen"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M17 8h-1V6a4 4 0 0 0-8 0v2H7a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2zm-7-2a2 2 0 0 1 4 0v2h-4V6z"/></svg></button>
          <button class="pbtn" data-c="fs" aria-label="Fullscreen"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg></button>
        </div>
        <div class="bigpanel" aria-hidden="true">
          <div class="bp-row">
            <button class="pbtn" data-c="back30" aria-label="Skip back"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg><span class="skiplbl">${APPCFG.skip_seconds}</span></button>
            <button class="pbtn bigplaybtn" data-c="play" aria-label="Play or pause">${PLAY_SVG}</button>
            <button class="pbtn" data-c="fwd30" aria-label="Skip forward"><svg viewBox="0 0 24 24" style="transform:scaleX(-1)"><path fill="currentColor" d="M12 5V1L7 6l5 5V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"/></svg><span class="skiplbl">${APPCFG.skip_seconds}</span></button>
          </div>
          <div class="bp-row">
            <button class="pbtn speedbtn" data-c="speed" aria-label="Playback speed">1\u00d7</button>
            <button class="pbtn ccbtn" data-c="cc" hidden aria-label="Subtitles">CC</button>
            <button class="pbtn" data-c="eps" hidden aria-label="Episodes"><svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M4 6h13v2H4V6zm0 5h13v2H4v-2zm0 5h13v2H4v-2zm15-9h2v2h-2V7zm0 5h2v2h-2v-2zm0 5h2v2h-2v-2z"/></svg></button>
            <button class="pbtn" data-c="next" hidden aria-label="Next episode"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M6 18l8.5-6L6 6v12zm10-12v12h2V6h-2z"/></svg></button>
            <button class="pbtn" data-c="lock" aria-label="Lock the screen"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M17 8h-1V6a4 4 0 0 0-8 0v2H7a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2zm-7-2a2 2 0 0 1 4 0v2h-4V6z"/></svg></button>
            <button class="pbtn" data-c="fs" aria-label="Fullscreen"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg></button>
          </div>
        </div>
      </div>`);
    playerEl.appendChild(cbar);
    playerEl.hidden = false;
    document.body.style.overflow = "hidden";

    // ---- wire the bar ---------------------------------------------------
    const seek = $(".seek", cbar), vol = $(".vol", cbar), ctime = $(".ctime", cbar), playBtn = $(".playbtn", cbar);
    const paintSeek = () => {
      const d = video.duration || state.duration || 0;
      const pct = d ? (video.currentTime / d) * 100 : 0;
      if (!state.scrubbing) seek.value = Math.round(pct * 10);
      seek.style.setProperty("--pct", pct + "%");
      ctime.textContent = `${fmtTime(video.currentTime)} / ${fmtTime(d)}`;
    };
    video.addEventListener("timeupdate", paintSeek);
    video.addEventListener("loadedmetadata", paintSeek);
    // preview thumbnails while hovering / dragging the seek bar
    let trick = null;
    api(`/api/trickplay/${v.id}`).then((m) => { if (current === state) trick = m; }).catch(() => {});
    const preview = el(`<div class="seekpreview" hidden><div class="sp-img" hidden></div><span class="sp-time"></span></div>`);
    cbar.appendChild(preview);
    const showPreviewFrac = (frac) => {
      const rect = seek.getBoundingClientRect();
      frac = Math.max(0, Math.min(1, frac));
      const dur = video.duration || state.duration || 0;
      if (!dur) return;
      const t = frac * dur;
      $(".sp-time", preview).textContent = fmtTime(t);
      applyTrickTile($(".sp-img", preview), trick, v.id, t);
      const w = preview.offsetWidth || 176;
      const x = Math.max(8, Math.min(rect.left - cbar.getBoundingClientRect().left + frac * rect.width - w / 2,
                                     cbar.clientWidth - w - 8));
      preview.style.left = x + "px";
      preview.hidden = false;
    };
    const showPreview = (clientX) => {
      const rect = seek.getBoundingClientRect();
      showPreviewFrac((clientX - rect.left) / rect.width);
    };
    // swipe up on the picture for the big-button layout; swipe down for the slim bar
    let swipe = null;
    playerEl.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "touch" || state.locked || e.target.closest(".seek")) { swipe = null; return; }
      swipe = { x: e.clientX, y: e.clientY };
    });
    playerEl.addEventListener("pointerup", (e) => {
      if (!swipe || e.pointerType !== "touch") return;
      const dy = e.clientY - swipe.y, dx = e.clientX - swipe.x;
      swipe = null;
      if (Math.abs(dy) < 45 || Math.abs(dx) > Math.abs(dy)) return;
      cbar.classList.toggle("expanded", dy < 0);
      poke();
    });

    // fullscreen button shows enter/exit state (iPhone uses its native fullscreen player)
    const FS_ENTER = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>';
    const FS_EXIT = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>';
    const paintFs = () => {
      const on = !!fsElement();
      cbar.querySelectorAll('[data-c="fs"]').forEach((b) => { b.innerHTML = on ? FS_EXIT : FS_ENTER; b.setAttribute("aria-label", on ? "Exit fullscreen" : "Fullscreen"); });
    };
    document.addEventListener("fullscreenchange", paintFs);
    document.addEventListener("webkitfullscreenchange", paintFs);
    const fsCleanup = () => {
      document.removeEventListener("fullscreenchange", paintFs);
      document.removeEventListener("webkitfullscreenchange", paintFs);
    };

    seek.addEventListener("pointermove", (e) => { if (e.pointerType !== "touch") showPreview(e.clientX); });
    seek.addEventListener("input", () => showPreviewFrac(seek.value / 1000));
    seek.addEventListener("pointerleave", () => { if (!state.scrubbing) preview.hidden = true; });
    seek.addEventListener("pointerup", () => { preview.hidden = true; });

    seek.addEventListener("pointerdown", (e) => { state.scrubbing = true; showPreview(e.clientX); });
    seek.addEventListener("input", () => {
      const d = video.duration || state.duration || 0;
      if (d) video.currentTime = (seek.value / 1000) * d;
    });
    seek.addEventListener("change", () => { state.scrubbing = false; });
    seek.addEventListener("pointerup", () => { state.scrubbing = false; });

    const paintPlay = () => {
      cbar.querySelectorAll('[data-c="play"]').forEach((b) => { b.innerHTML = video.paused ? PLAY_SVG : PAUSE_SVG; });
    };
    video.addEventListener("play", paintPlay);
    video.addEventListener("pause", paintPlay);

    const paintVol = () => { vol.value = video.muted ? 0 : Math.round(video.volume * 100); };
    video.addEventListener("volumechange", paintVol);
    vol.addEventListener("input", () => setVolume(vol.value / 100, { silent: true }));

    // click on the picture: reveal controls on touch, toggle play with a pointer
    video.addEventListener("click", () => {
      if (state.locked) return;
      if ($(".ccmenu", playerEl)) { closeCcMenu(); return; }
      if ($(".eppanel", playerEl)) { closeEpisodePanel(); return; }
      if (!matchMedia("(hover: hover)").matches && playerEl.classList.contains("idle")) { poke(); return; }
      video.paused ? video.play().catch(() => {}) : video.pause();
    });
    video.addEventListener("dblclick", () => { if (!state.locked) toggleFullscreen(video); });

    // ---- speed / skip / lock -------------------------------------------
    const SPEEDS = [1, 1.25, 1.5, 2];
    const speedBtn = $(".speedbtn", cbar);
    const setVolume = (vol, { silent = false } = {}) => {
      vol = Math.round(Math.max(0, Math.min(1, vol)) * 20) / 20;
      video.volume = vol;
      if (vol > 0) video.muted = false;
      localStorage.setItem("matinee-volume", String(vol));
      if (!silent) toast(`Volume ${Math.round(vol * 100)}%`);
    };
    if (!IS_IOS) {
      const savedVol = parseFloat(localStorage.getItem("matinee-volume") || "");
      if (isFinite(savedVol)) video.volume = Math.max(0, Math.min(1, savedVol));
    }
    const applySpeed = () => {
      const sp = parseFloat(localStorage.getItem("matinee-speed") || "1") || 1;
      video.playbackRate = sp;
      cbar.querySelectorAll(".speedbtn").forEach((b) => { b.textContent = (sp % 1 ? sp : sp + "") + "\u00d7"; });
    };
    video.addEventListener("loadedmetadata", applySpeed);
    applySpeed();
    paintVol();

    const lockPlayer = () => {
      state.locked = true;
      playerEl.classList.add("locked");
      const shield = el(`<div class="lockshield" aria-hidden="true"></div>`);
      const pad = el(`
        <button class="padlock" type="button" aria-label="Unlock">
          <svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M17 8h-1V6a4 4 0 0 0-8 0v2H7a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2zm-7-2a2 2 0 0 1 4 0v2h-4V6z"/></svg>
          <span>Locked</span>
        </button>`);
      playerEl.appendChild(shield);
      playerEl.appendChild(pad);
      let hideTimer = null;
      const showPad = () => {
        pad.classList.add("show");
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => { pad.classList.remove("show", "armed"); $("span", pad).textContent = "Locked"; }, 2500);
      };
      shield.addEventListener("click", (e) => { e.stopPropagation(); showPad(); });
      pad.addEventListener("click", (e) => {
        e.stopPropagation();
        if (pad.classList.contains("armed")) {
          state.locked = false;
          playerEl.classList.remove("locked");
          clearTimeout(hideTimer);
          shield.remove();
          pad.remove();
          poke();
          toast("Unlocked");
        } else {
          pad.classList.add("armed");
          $("span", pad).textContent = "Tap again to unlock";
          showPad();
        }
      });
      showPad();
      toast("Screen locked");
    };

      const shiftCues = (delta) => {
        const t = video.textTracks[0];
        if (!t || !t.cues) return;
        for (const c of Array.from(t.cues)) {
          c.startTime = Math.max(0, c.startTime + delta);
          c.endTime = Math.max(0, c.endTime + delta);
        }
      };
      const nudgeSubs = (delta) => {
        state.subOffset = Math.round(((state.subOffset || 0) + delta) * 10) / 10;
        shiftCues(delta);
        fetch(`/api/video/${v.id}/suboffset`, { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ offset: state.subOffset }) }).catch(() => {});
      };
      const toggleCc = () => {
        const tr = video.textTracks[0];
        if (!tr) return false;
        tr.mode = tr.mode === "showing" ? "hidden" : "showing";
        playerEl.querySelectorAll('[data-c="cc"]').forEach((b) => b.classList.toggle("active", tr.mode === "showing"));
        return tr.mode === "showing";
      };
      const closeCcMenu = () => { const m = $(".ccmenu", playerEl); if (m) m.remove(); };
      const toggleCcMenu = () => {
        if ($(".ccmenu", playerEl)) { closeCcMenu(); return; }
        const t = video.textTracks[0];
        const on = t && t.mode === "showing";
        const menu = el(`
          <div class="ccmenu">
            <button class="ccm-row" data-m="toggle">${on ? "Turn subtitles off" : "Turn subtitles on"}</button>
            <div class="ccm-row ccm-sync">
              <button class="ccm-half" data-m="earlier">\u25c0 Earlier</button>
              <span class="ccm-off"></span>
              <button class="ccm-half" data-m="later">Later \u25b6</button>
            </div>
            <div class="ccm-hint">Lines late? tap Earlier \u00b7 too soon? tap Later</div>
            <button class="ccm-row" data-m="swap">\u21bb Try a different subtitle</button>
          </div>`);
        const paintOff = () => {
          const o = state.subOffset || 0;
          $(".ccm-off", menu).textContent = o === 0 ? "in sync" : `${o > 0 ? "+" : ""}${o}s`;
        };
        paintOff();
        const anchorBar = $(".cbar", playerEl);
        if (anchorBar) menu.style.bottom = `${Math.round(window.innerHeight - anchorBar.getBoundingClientRect().top + 10)}px`;
        menu.addEventListener("click", (e) => {
          e.stopPropagation();
          const mb = e.target.closest("[data-m]");
          if (!mb) return;
          const m = mb.dataset.m;
          if (m === "toggle") {
            mb.textContent = toggleCc() ? "Turn subtitles off" : "Turn subtitles on";
          } else if (m === "earlier" || m === "later") {
            nudgeSubs(m === "later" ? 0.5 : -0.5);   // instant — no reload needed
            paintOff();
          } else if (m === "swap") {
            if (state.ccBusy) return;
            state.ccBusy = true;
            closeCcMenu();
            toast("Fetching a different subtitle\u2026");
            fetch(`/api/subsearch/${v.id}?replace=1`, { method: "POST" })
              .then(async (r) => {
                const d2 = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(d2.detail || "No other subtitle found");
                if (current !== state) return;
                state.subOffset = 0;
                state.attachSubs && state.attachSubs();
                toast(`New subtitle \u2713 (${d2.release || "different release"}, ${d2.remaining} left today)`);
              })
              .catch((e2) => toast(e2.message || "No other subtitle found"))
              .finally(() => { state.ccBusy = false; });
          }
        });
        playerEl.appendChild(menu);
      };

    cbar.addEventListener("click", (e) => {
      const btn = e.target.closest(".pbtn");
      if (!btn) return;
      e.stopPropagation();
      const act = btn.dataset.c;
      if (act === "play") video.paused ? video.play().catch(() => {}) : video.pause();
      else if (act === "back30") video.currentTime = Math.max(0, video.currentTime - APPCFG.skip_seconds);
      else if (act === "fwd30") video.currentTime = Math.min(video.duration || Infinity, video.currentTime + APPCFG.skip_seconds);
      else if (act === "mute") { video.muted = !video.muted; }
      else if (act === "cc") {
        if (state.ccSearch) {
          if (state.ccBusy) return;
          state.ccBusy = true;
          playerEl.querySelectorAll('[data-c="cc"]').forEach((b) => b.classList.add("busy"));
          toast("Looking for subtitles\u2026");
          fetch(`/api/subsearch/${v.id}`, { method: "POST" })
            .then(async (r) => {
              const d2 = await r.json().catch(() => ({}));
              if (!r.ok) throw new Error(d2.detail || "Subtitle search failed");
              if (current !== state) return;
              state.attachSubs && state.attachSubs();
              const via = d2.matched_by === "hash" ? "exact match" : "best name match";
              toast(d2.already ? "Subtitles found \u2713" : `Subtitles on \u2713 (${via}, ${d2.remaining} left today)`);
            })
            .catch((e) => { toast(e.message || "Subtitle search failed"); })
            .finally(() => {
              state.ccBusy = false;
              playerEl.querySelectorAll('[data-c="cc"]').forEach((b) => b.classList.remove("busy"));
            });
          return;
        }
        toggleCcMenu();
      }
      else if (act === "speed") {
        const cur = parseFloat(localStorage.getItem("matinee-speed") || "1") || 1;
        const next = SPEEDS[(SPEEDS.indexOf(cur) + 1) % SPEEDS.length];
        localStorage.setItem("matinee-speed", String(next));
        applySpeed();
      }
      else if (act === "next") {
        if (state.next) {
          // Skipping ahead during the credits counts as finishing this episode.
          const dur = (isFinite(video.duration) && video.duration) || state.duration || 0;
          if (dur && video.currentTime / dur >= 0.85) { saveProgress(v.id, dur, dur); state.suppressCloseSave = true; }
          openPlayer(state.next, { fromApp: false, confirmed: true });
        }
      }
      else if (act === "eps") openEpisodePanel({
        folder: v.folder, currentId: v.id, profile: PROFILE, mount: playerEl,
        onPick: (ep) => openPlayer(ep, { fromApp: false, confirmed: true }),
      });
      else if (act === "lock") lockPlayer();
      else if (act === "fs") toggleFullscreen(video);
    });

    // System media integration: Lock Screen / Control Center / Dynamic Island
    // now-playing chip / AirPods controls all drive THIS player.
    if ("mediaSession" in navigator) {
      const ms = navigator.mediaSession;
      try {
        ms.metadata = new MediaMetadata({
          title: v.title,
          artist: v.folder !== v.title ? v.folder : "Matinee",
          album: "Matinee",
          artwork: [
            { src: `/poster/${encodeURIComponent(v.folder)}`, sizes: "680x1000", type: "image/jpeg" },
            { src: "/static/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          ],
        });
      } catch (e) { /* very old browser */ }
      const safe = (name, fn) => { try { ms.setActionHandler(name, fn); } catch (e) { /* unsupported action */ } };
      safe("play", () => video.play().catch(() => {}));
      safe("pause", () => video.pause());
      safe("seekbackward", () => { video.currentTime = Math.max(0, video.currentTime - 30); });
      safe("seekforward", () => { video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 30); });
      safe("seekto", (d) => { if (d && isFinite(d.seekTime)) video.currentTime = d.seekTime; });
      safe("nexttrack", () => { if (state.next) openPlayer(state.next, { fromApp: false, confirmed: true }); });
      let lastPos = 0;
      video.addEventListener("timeupdate", () => {
        const dur = video.duration;
        if (!isFinite(dur) || !dur || Math.abs(video.currentTime - lastPos) < 3) return;
        lastPos = video.currentTime;
        try { ms.setPositionState({ duration: dur, position: video.currentTime, playbackRate: video.playbackRate }); } catch (e) {}
      });
    }

    const resumeAt = canResume(v) ? v.position : 0;
    // resumeDone flips once a seek actually lands near resumeAt. Safari/Chrome can
    // discard a seek issued before the media is ready (or while the tab is hidden),
    // so we retry a few times and never save progress until the resume has landed.
    const state = { video: v, el: video, lastSave: 0, idleTimer: null, fromApp, duration: v.duration || 0,
                    resumeDone: !resumeAt, resumeTries: 0 };
    state.setVolume = setVolume;
    state.cleanupFs = fsCleanup;
    state.nudgeSubs = nudgeSubs;
    state.toggleCc = toggleCc;
    current = state;

    const applyResume = () => {
      if (state.resumeDone || video.readyState < 1 || state.resumeTries >= 4) return;
      state.resumeTries++;
      try { video.currentTime = resumeAt; } catch (e) { return; }
      if (state.resumeTries === 1) {
        toast(`Resumed from ${fmtTime(resumeAt)}`, { label: "Start over", onClick: () => {
          state.resumeDone = true; video.currentTime = 0; video.play().catch(() => {});
        } });
      }
    };
    video.addEventListener("loadedmetadata", () => { state.duration = video.duration || state.duration; applyResume(); });
    video.addEventListener("canplay", applyResume);
    video.addEventListener("durationchange", () => { if (isFinite(video.duration)) state.duration = video.duration; });
    video.addEventListener("seeked", () => {
      if (state.resumeDone) { save(); return; }
      if (Math.abs(video.currentTime - resumeAt) < 1.5) state.resumeDone = true;
      else applyResume();
    });
    video.addEventListener("play", () => { if (!state.resumeDone && video.currentTime < 1) applyResume(); });
    video.addEventListener("timeupdate", () => {
      if (!state.resumeDone && Math.abs(video.currentTime - resumeAt) < 1.5) state.resumeDone = true;
    });

    const save = (opts) => {
      if (!state.resumeDone || !isFinite(video.currentTime)) return;
      state.lastSave = Date.now();
      return saveProgress(v.id, video.currentTime, isFinite(state.duration) ? state.duration : null, opts);
    };
    state.save = save;
    video.addEventListener("timeupdate", () => {
      if (!video.paused && Date.now() - state.lastSave > SAVE_EVERY_MS) save();
    });
    video.addEventListener("pause", () => save());
    const UPNEXT_SECONDS = 15;   // Netflix-style: enough time to read the card or grab the remote
    const maybeUpNext = async () => {
      let nxt = null, ep = null;
      try { nxt = (await api(`/api/video/${v.id}`)).next; } catch (e) { /* offline: just stop */ }
      if (current !== state) return;
      if (!nxt) { toast("Finished \u2713"); return; }
      try {
        const sd = await api(`/api/show/${encodeURIComponent(nxt.folder)}`);
        for (const se of sd.seasons_list || []) for (const e2 of se.episodes || []) if (e2.id === nxt.id) ep = e2;
      } catch (e) { /* card just shows less */ }
      if (current !== state) return;
      const img = (ep && ep.ep_image) || `/poster/${encodeURIComponent(nxt.folder)}`;
      const un = el(`
        <div class="upnext">
          <div class="un-box">
            <img class="un-img" src="${img}" alt="" onerror="this.hidden = true">
            <div class="un-body">
              <div class="un-kicker">Up next</div>
              <div class="un-title">${esc((ep && ep.label) || nxt.title)}</div>
              ${ep && ep.ep_name ? `<div class="un-ep">${esc(ep.ep_name)}</div>` : ""}
              ${ep && ep.ep_summary ? `<div class="un-sum">${esc(ep.ep_summary)}</div>` : ""}
              <div class="un-bar"><i></i></div>
              <div class="un-actions">
                <button class="btn" type="button" data-un="play">${PLAY_SVG}<span>Play now</span></button>
                <button class="btn secondary" type="button" data-un="close">\u2715 Close</button>
              </div>
            </div>
          </div>
        </div>`);
      state.upnextShowing = true;
      playerEl.appendChild(un);
      requestAnimationFrame(() => {
        const bar = $(".un-bar i", un);
        bar.style.transitionDuration = UPNEXT_SECONDS + "s";
        bar.style.width = "100%";
      });
      const go = () => {
        clearTimeout(state.upnextTimer);
        state.upnextShowing = false;
        if (current === state) openPlayer(nxt, { fromApp: false, confirmed: true });
      };
      state.upnextGo = go;   // the phone remote's "Play now" lands here
      state.upnextTimer = setTimeout(go, UPNEXT_SECONDS * 1000);
      un.addEventListener("click", (e) => {
        const b = e.target.closest("[data-un]");
        if (!b) return;
        e.stopPropagation();
        if (b.dataset.un === "play") go();
        else { clearTimeout(state.upnextTimer); state.upnextShowing = false; goHome(fromApp); }
      });
    };
    video.addEventListener("ended", () => {
      saveProgress(v.id, state.duration || video.currentTime, state.duration || null);
      maybeUpNext();
    });
    video.addEventListener("error", () => {
      const c = v.convert || {};
      let msg = "This file can't be played by the browser.";
      if (!v.ios_ok && IS_IOS) {
        if (c.status === "converting") msg = `This episode is being converted for iPhone (${Math.round(c.percent)}% done). Try again in a few minutes.`;
        else if (c.status === "queued") msg = "This file is queued to be converted for iPhone — it will play once the Mac has converted it. Meanwhile it plays in Chrome on the Mac.";
        else if (c.status === "failed") msg = `Conversion for iPhone failed: ${c.error || "unknown error"}. See the Organize page on the Mac.`;
        else msg = `iPhone can't play .${v.ext} files. Turn on convert_for_iphone in config.yaml (needs ffmpeg), or play it in Chrome on the Mac.`;
      }
      playerEl.appendChild(el(`<div class="notice">${esc(msg)}</div>`));
    });

    // Autoplay: allowed because we got here from a tap/click; if the browser still
    // refuses (opened via a direct link), show a big play button instead.
    const showBigPlay = () => {
      if ($(".bigplay", playerEl)) return;
      const b = el(`<button class="bigplay" type="button" aria-label="Play">${PLAY_SVG}</button>`);
      b.addEventListener("click", () => { b.remove(); video.play().catch(() => {}); });
      playerEl.appendChild(b);
    };
    const p = video.play();
    if (p && p.catch) p.catch(() => {
      if (video.paused) {
        showBigPlay();
        state.blocked = true;
        if (viaRemote) toast("Autoplay is blocked \u2014 click \u25b6 once here, or allow Auto-Play for this site in Safari \u25b8 Settings \u25b8 Websites");
      }
    });
    video.addEventListener("play", () => { state.blocked = false; const b = $(".bigplay", playerEl); if (b) b.remove(); });

    // Hide the title overlay while playing and the mouse is idle.
    const poke = () => {
      playerEl.classList.remove("idle");
      clearTimeout(state.idleTimer);
      state.idleTimer = setTimeout(() => { if (!video.paused) playerEl.classList.add("idle"); }, 3000);
    };
    ["mousemove", "touchstart", "click", "keydown"].forEach((ev) => playerEl.addEventListener(ev, poke, { passive: true }));
    video.addEventListener("play", poke);
    video.addEventListener("pause", poke);
    poke();

    const onKey = (e) => {
      if (e.target.tagName === "INPUT") return;
      if (state.locked) { e.preventDefault(); return; }
      switch (e.key) {
        case " ": case "k": e.preventDefault(); video.paused ? video.play().catch(() => {}) : video.pause(); break;
        case "ArrowLeft": case "j": e.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 10); break;
        case "ArrowRight": case "l": e.preventDefault(); video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 10); break;
        case "f": e.preventDefault(); toggleFullscreen(video); break;
        case "m": e.preventDefault(); video.muted = !video.muted; break;
        case "ArrowUp": if (!IS_IOS && state.setVolume) { e.preventDefault(); state.setVolume(video.volume + 0.05); } break;
        case "ArrowDown": if (!IS_IOS && state.setVolume) { e.preventDefault(); state.setVolume(video.volume - 0.05); } break;
        case "Escape":
          if ($(".eppanel", playerEl)) { e.preventDefault(); closeEpisodePanel(); break; }
          if (!document.fullscreenElement) { e.preventDefault(); goHome(fromApp); }
          break;
      }
    };
    document.addEventListener("keydown", onKey);
    state.cleanup = () => document.removeEventListener("keydown", onKey);
  };

  const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement || null;
  const toggleFullscreen = (video) => {
    if (fsElement()) {
      (document.exitFullscreen || document.webkitExitFullscreen).call(document);
      return;
    }
    // Prefer fullscreening OUR player (custom controls stay, any orientation).
    // Only truly ancient iOS falls back to the native video presenter.
    const req = playerEl.requestFullscreen || playerEl.webkitRequestFullscreen;
    if (req) {
      try {
        const r = req.call(playerEl);
        if (r && r.catch) r.catch(() => { if (video.webkitEnterFullscreen) video.webkitEnterFullscreen(); });
      } catch (e) {
        if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
      }
    } else if (video.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
    }
  };

  // Leave browser/native fullscreen. Safari otherwise keeps the (now hidden) player as
  // the fullscreen element and the page underneath stops taking clicks and scroll.
  const exitAnyFullscreen = () => {
    try {
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        const ex = document.exitFullscreen || document.webkitExitFullscreen;
        const r = ex && ex.call(document);
        if (r && r.catch) r.catch(() => {});
      }
    } catch (e) { /* ignore */ }
    try {
      const v = current && current.el;
      if (v && v.webkitDisplayingFullscreen && v.webkitExitFullscreen) v.webkitExitFullscreen();
    } catch (e) { /* ignore */ }
  };
  let armedFsOnce = null;   // remote "fullscreen" waiting for a local click (see handleRemoteCommand)
  const disarmFs = () => {
    if (armedFsOnce) { document.removeEventListener("pointerdown", armedFsOnce, true); armedFsOnce = null; }
  };

  const closePlayer = ({ switching = false } = {}) => {
    if (!current) return;
    if (!switching) { exitAnyFullscreen(); disarmFs(); }
    const { el: video, save, cleanup } = current;
    try { if (!current.suppressCloseSave && video.readyState >= 1 && video.currentTime > 0) save(); } catch (e) { /* ignore */ }
    cleanup && cleanup();
    current.cleanupFs && current.cleanupFs();
    clearTimeout(current.idleTimer);
    clearTimeout(current.upnextTimer);
    video.pause();
    video.removeAttribute("src");
    video.load();
    if ("mediaSession" in navigator) {
      try { navigator.mediaSession.metadata = null; } catch (e) {}
    }
    playerEl.innerHTML = "";
    playerEl.hidden = true;
    playerEl.classList.remove("idle", "locked", "has-panel");
    document.body.style.overflow = "";
    hideToast();
    current = null;
  };

  const goHome = (fromApp) => {
    if (fromApp) history.back();
    else { history.replaceState(null, "", "#/"); route(); }
  };

  // Save when the tab is hidden / page is closed / iPhone is locked.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && current) current.save({ beacon: true });
    if (document.visibilityState === "visible" && onHome()) refreshHomeIfChanged();
  });
  const onHome = () => !current && !searchInput.value && !/^#\/(organize|watch|show|artwork)/.test(location.hash);
  window.addEventListener("pagehide", () => { if (current) current.save({ beacon: true }); });

