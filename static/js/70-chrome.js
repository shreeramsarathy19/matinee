  // -------------------------------------------------------------- routing
  const route = async () => {
    if (location.hash.startsWith("#/organize")) return renderOrganize();
    if (location.hash.startsWith("#/artwork")) return renderArtworkPage();
    const sm = location.hash.match(/^#\/show\/(.+)$/);
    if (sm) return renderShow(decodeURIComponent(sm[1]));
    const mv = location.hash.match(/^#\/movie\/([0-9a-f]+)/);
    if (mv) return renderMovie(mv[1]);
    const m = location.hash.match(/^#\/watch\/([A-Za-z0-9]+)/);
    if (m) {
      if (current && current.video.id === m[1]) return;
      let v = homeCache && homeCache.rows.flatMap((r) => (r.kind === "videos" ? r.items : [])).find((x) => x.id === m[1]);
      if (!v) {
        try { v = await api(`/api/video/${m[1]}`); } catch (e) { history.replaceState(null, "", "#/"); return renderHome(); }
      }
      openPlayer(v, { fromApp: false });
      return;
    }
    renderHome();
  };
  // Back navigation fires both popstate and hashchange; coalesce them into one route().
  let routePending = false;
  const scheduleRoute = () => {
    if (routePending) return;
    routePending = true;
    setTimeout(() => { routePending = false; route(); }, 0);
  };
  window.addEventListener("popstate", scheduleRoute);
  window.addEventListener("hashchange", scheduleRoute);

  window.addEventListener("scroll", () => topbar.classList.toggle("solid", window.scrollY > 40), { passive: true });

  // --------------------------------------------------------- activity chip
  // Shows what organize/convert is doing right now; tap to open the Organize page.
  const activityChip = $("#activityChip");
  const baseName = (rel) => rel.split("/").pop();
  const renderActivity = (a) => {
    const onOrganize = location.hash.startsWith("#/organize");
    // yellow badge on the Organize button: things waiting for a decision there
    const badge = $("#organizeBadge");
    if (badge && a) {
      const n = (a.pending_moves || 0) + (a.failed_count || 0);
      const attention = n > 0 || a.low_disk || (a.originals_gb || 0) >= 5;
      badge.textContent = n > 0 ? String(n) : "!";
      badge.hidden = !attention;
      const why = [];
      if (a.pending_moves) why.push(`${a.pending_moves} rename suggestion(s) to approve`);
      if (a.failed_count) why.push(`${a.failed_count} failed conversion(s)`);
      if ((a.originals_gb || 0) >= 5) why.push(`${a.originals_gb} GB of originals could go to the Bin`);
      if (a.low_disk) why.push("disk almost full");
      $("#organizeBtn").title = why.length ? "Organize: " + why.join(" \u00b7 ") : "Organize files";
    }
    if (!a || current || onOrganize) { activityChip.hidden = true; return; }
    if (a.library_offline) {
      activityChip.innerHTML = `\u26a0\ufe0e Library offline \u2014 is the disk connected?`;
      activityChip.hidden = false;
      return;
    }
    if (a.low_disk) {
      activityChip.innerHTML = `\u26a0\ufe0e Disk almost full (${a.free_gb} GB free) \u2014 conversions paused. Empty the Bin to free space.`;
      activityChip.hidden = false;
    } else if (a.converting) {
      const pct = Math.round(a.converting.percent);
      activityChip.innerHTML = `<span class="spin">\u27f3</span> ${a.converting.stage === "transcode" ? "Converting" : "Preparing"} <b>${esc(baseName(a.converting.rel_path))}</b> \u00b7 ${pct}%${a.queued ? ` \u00b7 ${a.queued} queued` : ""}`;
      activityChip.hidden = false;
    } else if (a.queued) {
      activityChip.innerHTML = `<span class="spin">\u27f3</span> ${a.queued} file${a.queued === 1 ? "" : "s"} waiting to be converted`;
      activityChip.hidden = false;
    } else if (a.recent_count) {
      activityChip.innerHTML = `\ud83d\uddc2 Organized <b>${a.recent_count}</b> file${a.recent_count === 1 ? "" : "s"} \u2014 tap to see what changed`;
      activityChip.hidden = false;
    } else if (a.failed) {
      activityChip.innerHTML = `\u26a0\ufe0e ${a.failed} conversion${a.failed === 1 ? "" : "s"} failed \u2014 tap to review`;
      activityChip.hidden = false;
    } else {
      activityChip.hidden = true;
    }
  };
  const pollActivity = async () => {
    try { renderActivity(await fetch("/api/activity").then((r) => r.json())); } catch (e) { /* ignore */ }
  };
  setInterval(pollActivity, 5000);
  pollActivity();

  // Watch the library: when new shows/episodes appear (auto-organize runs on the
  // server every 30 s), refresh whatever page is open and say so.
  let librarySig = null;
  let lastPointer = 0;   // a re-render mid-tap would eat the click — hold off
  document.addEventListener("pointerdown", () => { lastPointer = Date.now(); }, { capture: true, passive: true });
  const sigOf = (d) => JSON.stringify([d.count, d.rows.map((r) => r.name + ":" + (r.items || []).length)]);
  const refreshHomeIfChanged = async ({ announce = false } = {}) => {
    if (document.visibilityState === "hidden" || current) return;
    let d;
    try { d = await api("/api/home"); } catch (e) { return; }
    const sig = sigOf(d);
    if (librarySig && sig !== librarySig && Date.now() - lastPointer > 1500) {
      homeCache = d;
      const m = location.hash.match(/^#\/show\/(.+)$/);
      if (m) renderShow(decodeURIComponent(m[1]));
      else if (onHome()) renderHome();
      if (announce) toast("Library updated \u2713");
    }
    librarySig = sig;
  };
  setInterval(() => refreshHomeIfChanged({ announce: true }), 20000);

