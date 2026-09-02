  // ---------------------------------------------------------------- show
  const renderShow = async (folder) => {
    closePlayer();
    let s;
    try { s = await api(`/api/show/${encodeURIComponent(folder)}`); }
    catch (e) { main.innerHTML = `<div class="empty"><div><h2>Not found</h2><p>${esc(folder)}</p></div></div>`; return; }
    const nxt = s.next;
    const resume = nxt && canResume(nxt);
    const nextLabel = nxt ? (resume ? `Resume ${episodeCode(nxt)} from ${fmtTime(nxt.position)}` : `Play ${episodeCode(nxt)}`) : "";
    const page = el(`
      <div class="show" style="--tile-bg:${tileBg({ folder: s.folder, title: "" })}">
        <section class="hero show-hero ${s.poster ? "has-poster" : ""}" ${s.poster ? `style="background-image:url('${s.poster}')"` : ""}>
          <div class="hero-inner">
            <a class="backlink" href="#/">← Back</a>
            ${s.flag ? `<div class="flag inline">${esc(s.flag)}</div>` : ""}
            <h1>${esc(s.title)}</h1>
            <div class="meta">${s.rating ? `★ ${s.rating} &nbsp;·&nbsp; ` : ""}${s.count} episode${s.count === 1 ? "" : "s"}${s.seasons > 1 ? ` &nbsp;·&nbsp; ${s.seasons} seasons` : ""}${s.watched ? ` &nbsp;·&nbsp; <b>${s.watched} watched</b>` : ""}</div>
            ${(s.genres || []).length ? `<div class="genres">${s.genres.map((g) => `<span class="chip">${esc(g)}</span>`).join("")}</div>` : ""}
            ${s.summary ? `<p class="show-summary">${esc(s.summary)}</p>` : ""}
            <div class="actions">${nxt ? `<button class="btn" type="button" id="showPlay">${PLAY_SVG}<span>${esc(nextLabel)}</span></button>` : ""}<button class="btn secondary" type="button" id="artBtn">${s.poster ? "Change artwork" : "\ud83d\uddbc Artwork"}</button></div>
            ${resume ? `<div class="bar"><i style="width:${nxt.percent}%"></i></div>` : ""}
          </div>
        </section>
        <div class="seasons"></div>
      </div>`);
    if (nxt) $("#showPlay", page).addEventListener("click", () => openPlayer(nxt, { fromApp: true }));
    $("#artBtn", page).addEventListener("click", () => openArtworkPicker(s.folder, !!s.poster));
    const seasons = $(".seasons", page);
    const epRow = (v) => {
      const epTitle = v.ep_name ? `${v.episode != null ? "E" + String(v.episode).padStart(2, "0") + " · " : ""}${v.ep_name}` : v.label;
      const rowEl = el(`
        <button class="ep ${v.finished ? "finished" : ""}" type="button">
          ${v.ep_image
            ? `<img class="epimg" src="${esc(v.ep_image)}" alt="" loading="lazy">`
            : `<span class="epnum">${v.episode != null ? String(v.episode).padStart(2, "0") : "•"}</span>`}
          <span class="epbody">
            <span class="eptitle">${esc(epTitle)}${v.finished ? ' <span class="check">✓</span>' : ""}</span>
            ${v.ep_summary ? `<span class="epsummary">${esc(v.ep_summary)}</span>` : ""}
            <span class="epmeta">${[fmtDuration(v.duration), v.airdate || "", canResume(v) ? `${Math.round(v.percent)}% watched` : ""].filter(Boolean).join(" · ")}</span>
            ${v.percent && !v.finished ? `<span class="bar"><i style="width:${v.percent}%"></i></span>` : ""}
          </span>
          ${convertTag(v)}
          <span class="play">${PLAY_SVG}</span>
        </button>`);
      rowEl.addEventListener("click", () => openPlayer(v, { fromApp: true }));
      return rowEl;
    };
    // Season tabs: one list, switched by the tab bar (opens on the season you're watching).
    let activeSeason = null;
    const tabs = el(`<div class="season-tabs" role="tablist" aria-label="Seasons"></div>`);
    const list = el(`<div class="eplist"></div>`);
    const renderSeason = (season) => {
      activeSeason = season;
      list.innerHTML = "";
      season.episodes.forEach((v) => list.appendChild(epRow(v)));
      [...tabs.children].forEach((t) => {
        t.classList.toggle("active", t._season === season);
        t.setAttribute("aria-selected", t._season === season ? "true" : "false");
      });
    };
    s.seasons_list.forEach((season) => {
      const t = el(`<button class="stab" type="button" role="tab">${esc(season.name)}<small>${season.episodes.length}</small></button>`);
      t._season = season;
      t.addEventListener("click", () => renderSeason(season));
      tabs.appendChild(t);
    });
    const markBtn = el(`<button class="btn secondary small markseason" type="button">Mark season watched</button>`);
    markBtn.addEventListener("click", async () => {
      if (!activeSeason) return;
      const eps = activeSeason.episodes.filter((e) => e.duration && !e.finished);
      if (!eps.length) { toast("Season already watched"); return; }
      if (!confirm(`Mark all of ${activeSeason.name} as watched${PROFILE ? " for " + PROFILE : ""}?`)) return;
      markBtn.disabled = true;
      for (const e of eps) await saveProgress(e.id, e.duration, e.duration);
      toast(`${activeSeason.name} marked watched`);
      renderShow(folder);
    });
    const head = el(`<div class="season-head"></div>`);
    if (s.seasons_list.length > 1) head.appendChild(tabs);
    head.appendChild(markBtn);
    seasons.appendChild(head);
    seasons.appendChild(list);
    let initial = s.seasons_list[0];
    if (nxt) initial = s.seasons_list.find((se) => se.episodes.some((e) => e.id === nxt.id)) || initial;
    if (initial) renderSeason(initial);
    main.innerHTML = "";
    main.appendChild(page);
    window.scrollTo(0, 0);
  };
  const renderArtworkPage = async () => {
    closePlayer();
    main.innerHTML = `<div class="org"><p class="muted">Loading\u2026</p></div>`;
    let data;
    try { data = await api("/api/artwork/overview"); } catch (e) { main.innerHTML = `<div class="org"><p>${esc(e.message)}</p></div>`; return; }

    const page = el(`
      <div class="org">
        <a class="backlink" href="#/">\u2190 Back to library</a>
        <h1>Artwork</h1>
        <p class="muted">Posters are suggested from the internet for every show. Approve them one by one,
          or hit <b>Apply all suggestions</b> and fix any odd ones afterwards. Nothing is used without your approval.</p>
        <div class="org-head">
          <h2>Shows <small>${data.shows.length}</small></h2>
          <button class="btn small" id="applyAllArt" type="button" disabled>Apply all suggestions</button>
        </div>
        <div class="org-list" id="artList"></div>
        <div class="org-head" style="margin-top:26px"><h2>Movies <small>${(data.movies || []).length}</small></h2></div>
        <div class="org-list" id="artMovies"></div>
      </div>`);
    const list = $("#artList", page);
    const movieList = $("#artMovies", page);
    const applyAll = $("#applyAllArt", page);
    const pending = [];   // rows whose suggestion can be applied

    const posterThumb = (url, folder) =>
      url ? `<img class="art-cur" src="${esc(url)}" alt="">`
          : `<span class="art-cur art-none" style="--tile-bg:${tileBg({ folder, title: "" })}">${esc(folder[0] || "?")}</span>`;

    const items = [...data.shows, ...(data.movies || []).map((m) => ({ ...m, count: null, _kind: "movie" }))];
    items.forEach((sh) => {
      const row = el(`
        <div class="org-row artrow">
          ${posterThumb(sh.poster, sh.folder)}
          <div class="org-body">
            <div class="art-name">${esc(sh.folder)}${sh.count ? ` <small class="muted">${sh.count} ep</small>` : ""}</div>
            <div class="art-sug muted small">${sh.poster ? "Poster set \u2713" : "Searching\u2026"}</div>
          </div>
          <div class="art-btns">
            <button class="btn secondary small" data-a="more" type="button">${sh.poster ? "Change" : "Pick\u2026"}</button>
            ${sh.poster ? '<button class="btn secondary small" data-a="remove" type="button">Remove</button>' : ""}
          </div>
        </div>`);
      row._folder = sh.folder;
      row._kind = sh._kind || "series";
      $('[data-a="more"]', row).addEventListener("click", () => openArtworkPicker(sh.folder, !!sh.poster, row._kind));
      const rm = $('[data-a="remove"]', row);
      if (rm) rm.addEventListener("click", async () => {
        await api("/api/artwork/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder: sh.folder }) });
        homeCache = null; renderArtworkPage();
      });
      (sh._kind === "movie" ? movieList : list).appendChild(row);
      if (!sh.poster) pending.push(row);
    });
    main.innerHTML = "";
    main.appendChild(page);
    window.scrollTo(0, 0);

    // fetch a suggestion for each show without a poster, one at a time (kind to the APIs)
    const applyTo = async (row, cand) => {
      await api("/api/artwork/set", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder: row._folder, url: cand.url, tvmaze_id: cand.tvmaze_id || null }) });
      homeCache = null;
      $(".art-sug", row).innerHTML = `Saved \u2713 \u2014 ${esc(cand.title)}`;
      $(".art-cur", row).outerHTML = `<img class="art-cur" src="${esc(cand.thumb)}" alt="">`;
      const btns = $(".art-btns", row);
      btns.innerHTML = '<button class="btn secondary small" data-a="more" type="button">Change</button>';
      $('[data-a="more"]', btns).addEventListener("click", () => openArtworkPicker(row._folder, true, row._kind));
      row._cand = null;
    };
    for (const row of pending) {
      if (!document.body.contains(row)) return;   // user left the page
      let cands = [];
      try { cands = (await api(`/api/artwork/search?folder=${encodeURIComponent(row._folder)}&kind=${row._kind}`)).candidates || []; } catch (e) { /* offline */ }
      const sug = $(".art-sug", row);
      if (!cands.length) { sug.textContent = "No suggestion found \u2014 use Pick\u2026 or rename the folder"; continue; }
      const c = cands[0];
      row._cand = c;
      sug.innerHTML = `Suggestion: <img class="art-mini" src="${esc(c.thumb)}" alt=""> <b>${esc(c.title)}</b>${c.year ? ` (${esc(c.year)})` : ""} \u00b7 ${esc(c.source)}`;
      const use = el('<button class="btn small" data-a="use" type="button">Use this</button>');
      use.addEventListener("click", async () => { use.disabled = true; try { await applyTo(row, c); } catch (e) { toast("Could not save"); use.disabled = false; } });
      $(".art-btns", row).prepend(use);
      applyAll.disabled = false;
    }

    applyAll.addEventListener("click", async () => {
      applyAll.disabled = true;
      applyAll.textContent = "Applying\u2026";
      let n = 0;
      for (const row of pending) {
        if (row._cand && document.body.contains(row)) {
          try { await applyTo(row, row._cand); n++; } catch (e) { /* keep going */ }
        }
      }
      applyAll.textContent = `Applied ${n} \u2713`;
      toast(`${n} poster${n === 1 ? "" : "s"} saved`);
    });
  };

  let subsearchStatusP = null;
  const subsearchStatus = () => (subsearchStatusP ||= api("/api/subsearch/status").catch(() => ({ configured: false })));

  const closeEpisodePanel = () => {
    const pnl = $(".eppanel");
    if (pnl) pnl.remove();
    playerEl.classList.remove("has-panel");
  };

  // Netflix-style in-player episode list. onPick decides what "play" means
  // (play here, or send to the laptop).
  const openEpisodePanel = async ({ folder, currentId, profile, mount, onPick }) => {
    const existing = $(".eppanel");
    if (existing) { closeEpisodePanel(); return; }
    const panel = el(`
      <div class="eppanel">
        <div class="ep-head">
          <span class="ep-title">${esc(folder)}</span>
          <select class="ep-season" aria-label="Season" hidden></select>
          <button class="pbtn ep-close" type="button" aria-label="Close">\u2715</button>
        </div>
        <div class="ep-body"><p class="muted small" style="padding:14px">Loading\u2026</p></div>
      </div>`);
    (mount || document.body).appendChild(panel);
    if (mount === playerEl) { playerEl.classList.add("has-panel"); }
    // sit just above the bar that holds the button (player control bar, or the phone remote bar)
    const anchor = mount === playerEl ? $(".cbar", playerEl) : $("#remoteBar");
    if (anchor && !anchor.hidden) {
      const gap = window.innerHeight - anchor.getBoundingClientRect().top + 10;
      panel.style.setProperty("--ep-bottom", `${Math.round(gap)}px`);
    }
    $(".ep-close", panel).addEventListener("click", closeEpisodePanel);
    panel.addEventListener("click", (e) => e.stopPropagation());

    let show;
    try {
      const url = `/api/show/${encodeURIComponent(folder)}` + (profile ? `?p=${encodeURIComponent(profile)}` : "");
      show = await (await fetch(url)).json();
    } catch (e) { $(".ep-body", panel).innerHTML = '<p class="muted small" style="padding:14px">Could not load episodes.</p>'; return; }
    if (!document.body.contains(panel)) return;

    const seasons = show.seasons_list || [];
    const body = $(".ep-body", panel);
    const sel = $(".ep-season", panel);
    const renderSeason = (season) => {
      body.innerHTML = "";
      season.episodes.forEach((ep) => {
        const now = ep.id === currentId;
        const name = ep.ep_name ? `${ep.episode != null ? "E" + String(ep.episode).padStart(2, "0") + " \u00b7 " : ""}${ep.ep_name}` : ep.label;
        const row = el(`
          <button class="eprow ${now ? "now" : ""} ${ep.finished ? "done" : ""}" type="button">
            <span class="epr-num">${ep.episode != null ? String(ep.episode).padStart(2, "0") : "\u2022"}</span>
            <span class="epr-body">
              <span class="epr-name">${esc(name)}${ep.finished ? ' <span class="check">\u2713</span>' : ""}</span>
              ${ep.ep_summary ? `<span class="epr-sum">${esc(ep.ep_summary)}</span>` : ""}
              <span class="epr-meta">${now ? '<b class="nowtag">\u25b6 Now playing</b> \u00b7 ' : ""}${fmtDuration(ep.duration) || ""}${!now && canResume(ep) ? ` \u00b7 ${Math.round(ep.percent)}%` : ""}</span>
              ${ep.percent && !ep.finished ? `<span class="bar"><i style="width:${ep.percent}%"></i></span>` : ""}
            </span>
          </button>`);
        if (!now) row.addEventListener("click", () => { closeEpisodePanel(); onPick(ep); });
        body.appendChild(row);
      });
      const nowRow = $(".eprow.now", body);
      if (nowRow) nowRow.scrollIntoView({ block: "center" });
    };
    if (seasons.length > 1) {
      sel.hidden = false;
      seasons.forEach((se, i) => sel.appendChild(el(`<option value="${i}">${esc(se.name)}</option>`)));
      sel.addEventListener("change", () => renderSeason(seasons[+sel.value]));
    }
    let initial = seasons[0];
    const holding = seasons.findIndex((se) => se.episodes.some((e) => e.id === currentId));
    if (holding >= 0) { initial = seasons[holding]; sel.value = String(holding); }
    if (initial) renderSeason(initial);
  };

  const openArtworkPicker = async (folder, hasPoster, kind = "series") => {
    modalEl.innerHTML = "";
    const box = el(`
      <div class="box artbox" role="dialog" aria-modal="true">
        <h3>Artwork \u00b7 ${esc(folder)}</h3>
        <p class="muted small">Pick a poster from the internet \u2014 it appears on the tiles and this page.</p>
        <div class="artgrid"><p class="muted small">Searching\u2026</p></div>
        <div class="art-actions">
          ${hasPoster ? '<button class="btn secondary small" id="artRemove" type="button">Remove artwork</button>' : ""}
          <button class="btn secondary small close" type="button">Cancel</button>
        </div>
      </div>`);
    $(".close", box).addEventListener("click", () => { modalEl.hidden = true; });
    const rm = $("#artRemove", box);
    if (rm) rm.addEventListener("click", async () => {
      await api("/api/artwork/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder }) });
      modalEl.hidden = true; homeCache = null; toast("Artwork removed"); route();
    });
    modalEl.appendChild(box);
    modalEl.hidden = false;
    let cands = [];
    try { cands = (await api(`/api/artwork/search?folder=${encodeURIComponent(folder)}&kind=${kind}`)).candidates || []; } catch (e) { /* offline */ }
    const grid = $(".artgrid", box);
    grid.innerHTML = "";
    if (!cands.length) {
      grid.appendChild(el(`<p class="muted small">Nothing found \u2014 needs internet (hotspot is fine). Try a simpler folder name.</p>`));
      return;
    }
    cands.forEach((c) => {
      const item = el(`
        <button class="artitem" type="button">
          <img src="${esc(c.thumb)}" alt="" loading="lazy">
          <span>${esc(c.title)}${c.year ? ` (${esc(c.year)})` : ""}<small>${esc(c.source)}</small></span>
        </button>`);
      item.addEventListener("click", async () => {
        item.disabled = true;
        item.classList.add("busy");
        try {
          await api("/api/artwork/set", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, url: c.url, tvmaze_id: c.tvmaze_id || null }) });
          modalEl.hidden = true; homeCache = null; toast("Artwork saved \u2713"); route();
        } catch (e) { toast("Could not save that image"); item.disabled = false; item.classList.remove("busy"); }
      });
      grid.appendChild(item);
    });
  };

  const episodeCode = (v) => {
    const m = v.title.match(/S(\d{1,2})E(\d{1,3})/i);
    return m ? `S${m[1].padStart(2, "0")}E${m[2].padStart(2, "0")}` : v.title;
  };


  // ---------------------------------------------------------------- movie
  const renderMovie = async (id, prefetched) => {
    closePlayer();
    let d = prefetched;
    if (!d) {
      // instant page from caches; the full lookup (Wikipedia, first time only) follows
      try { d = await api(`/api/movie/${id}&fast=1`.replace("&", "?")); }
      catch (e) { history.replaceState(null, "", "#/"); return renderHome(); }
      if (!d.info || !d.info.extract) {
        api(`/api/movie/${id}`).then((full) => {
          if (location.hash === `#/movie/${id}` && (full.info && full.info.extract)) renderMovie(id, full);
        }).catch(() => {});
      }
    }
    main.innerHTML = "";
    const info = d.info || {};
    const resume = canResume(d);
    const label = resume ? `Resume from ${fmtTime(d.position)}` : d.finished ? "Play again" : "Play";
    const meta = [info.year, fmtDuration(d.duration),
                  resume ? `<b>${Math.round(d.percent)}% watched</b>` : "",
                  d.finished ? "\u2713 Watched" : ""].filter(Boolean).join(" &nbsp;\u00b7&nbsp; ");
    let subLine;
    if (d.subtitles) {
      subLine = `Subtitles available \u2713 &nbsp;<button class="linkbtn" id="mSubSwap">\u21bb fetch a different one</button>`;
    } else if (d.subsearch && d.subsearch.configured) {
      subLine = d.subsearch.remaining > 0
        ? `<button class="linkbtn" id="mSubFind">\ud83d\udcac Find subtitles</button> &nbsp;(${d.subsearch.remaining} downloads left today)`
        : `No subtitles \u2014 daily download limit used (${d.subsearch.used}/${d.subsearch.limit}), try tomorrow`;
    } else {
      subLine = "";
    }
    const sec = el(`
      <section class="hero show-hero movie-hero ${d.poster ? "has-poster" : ""}" style="--tile-bg:${tileBg(d)};${d.poster ? `background-image:url('${d.poster}')` : ""}">
        <div class="hero-inner">
          <a class="backlink" href="#/">\u2190 Back</a>
          <div class="kicker">${esc(info.description || "Movie")}</div>
          <h1>${esc(d.title)}</h1>
          <div class="meta">${meta}</div>
          ${info.extract ? `<p class="movie-extract">${esc(info.extract)}</p>` : ""}
          <div class="actions">
            <button class="btn" type="button" id="mPlay">${PLAY_SVG}<span>${esc(label)}</span></button>
            ${resume ? '<button class="btn secondary" type="button" id="mRestart">Start over</button>' : ""}
          </div>
          ${resume ? `<div class="bar"><i style="width:${d.percent}%"></i></div>` : ""}
          ${subLine ? `<div class="meta subline">${subLine}</div>` : ""}
        </div>
      </section>`);
    $("#mPlay", sec).addEventListener("click", () => openPlayer(d, { fromApp: true }));
    const rs = $("#mRestart", sec);
    if (rs) rs.addEventListener("click", () => openPlayer({ ...d, position: 0 }, { fromApp: true }));
    const fetchSubs = async (replace) => {
      toast(replace ? "Fetching a different subtitle\u2026" : "Looking for subtitles\u2026");
      try {
        const r = await fetch(`/api/subsearch/${d.id}${replace ? "?replace=1" : ""}`, { method: "POST" });
        const d2 = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d2.detail || "Subtitle search failed");
        toast(`Subtitles saved \u2713 (${d2.release || "found"}, ${d2.remaining} left today)`);
        renderMovie(id);
      } catch (e2) { toast(e2.message || "Subtitle search failed"); }
    };
    const sw = $("#mSubSwap", sec), sf = $("#mSubFind", sec);
    if (sw) sw.addEventListener("click", () => fetchSubs(true));
    if (sf) sf.addEventListener("click", () => fetchSubs(false));
    main.appendChild(sec);
    window.scrollTo(0, 0);
  };
