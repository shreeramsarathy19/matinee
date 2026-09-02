  // --------------------------------------------------------------- cards
  const canResume = (v) => v.position > MIN_RESUME && !v.finished && (!v.duration || v.position / v.duration < FINISHED_RATIO);

  // Badge for files iPhone can't play (yet): shows conversion state.
  const convertTag = (v) => {
    if (v.ios_ok) return "";
    const c = v.convert || {};
    if (c.status === "converting") return `<span class="tag busy" title="Being converted to MP4 for iPhone">⟳ ${Math.round(c.percent)}%</span>`;
    if (c.status === "queued") return `<span class="tag" title="Queued for conversion to MP4 (plays in Chrome meanwhile)">${esc(v.ext.toUpperCase())} · queued</span>`;
    if (c.status === "failed") return `<span class="tag bad" title="${esc(c.error || "Conversion failed")}">${esc(v.ext.toUpperCase())} · failed</span>`;
    return `<span class="tag" title="${IS_IOS ? "This format does not play on iPhone" : "Plays in Chrome, not on iPhone"}">${IS_IOS ? "⚠︎ " : ""}${esc(v.ext.toUpperCase())}</span>`;
  };

  const CARD_DATA = new Map();   // id -> serialized video, for the delegated click handler

  const card = (v, { removable = false, caption = false, seriesFolders = null, openMovie = false } = {}) => {
    const node = el(`
      <button class="card ${v.finished ? "finished" : ""} ${v.poster ? "has-poster" : ""}" type="button" style="--tile-bg:${tileBg(v)};${v.poster ? `background-image:url('${v.poster}')` : ""}" title="${esc(v.title)}">
        <span class="glyph" aria-hidden="true">${esc((v.folder[0] || "").toUpperCase())}</span>
        <span class="sub">${esc(v.subfolder || v.folder)}</span>
        ${convertTag(v)}
        <span class="title">${esc(v.title)}</span>
        <span class="play">${PLAY_SVG}</span>
        ${v.percent ? `<span class="bar"><i style="width:${v.percent}%"></i></span>` : ""}
      </button>`);
    CARD_DATA.set(v.id, v);
    node.dataset.card = openMovie ? "movie" : "play";
    node.dataset.vid = v.id;

    if (!removable && !caption) return node;
    const wrap = el(`<div class="card-wrap"></div>`);
    wrap.appendChild(node);
    if (removable) {
      const x = el(`<button class="remove" type="button" data-card="clear" data-vid="${esc(v.id)}" title="Remove from Continue Watching" aria-label="Remove">✕</button>`);
      wrap.appendChild(x);
    }
    if (caption) {
      const isSeries = seriesFolders && v.folder && seriesFolders.has(v.folder);
      wrap.appendChild(el(`<div class="caption"><span>${esc(timeLeft(v) || fmtDuration(v.duration) || "")}</span>${
        isSeries ? `<a class="serieslink" href="#/show/${encodeURIComponent(v.folder)}">Episodes \u203a</a>` : ""
      }</div>`));
    }
    return wrap;
  };

  // One tile for a whole series (Recently Added, search results).
  const seriesCard = (s) => {
    const sub = `${s.count} episode${s.count === 1 ? "" : "s"}${s.seasons > 1 ? ` · ${s.seasons} seasons` : ""}`;
    const node = el(`
      <button class="card series ${s.poster ? "has-poster" : ""}" type="button" style="--tile-bg:${tileBg({ folder: s.folder, title: "" })};${s.poster ? `background-image:url('${s.poster}')` : ""}" title="${esc(s.title)}">
        <span class="glyph" aria-hidden="true">${esc((s.title[0] || "").toUpperCase())}</span>
        ${s.flag ? `<span class="flag">${esc(s.flag)}</span>` : ""}
        ${!s.ios_ok ? `<span class="tag" title="Some episodes are still being converted for iPhone">⟳</span>` : ""}
        <span class="title">${esc(s.title)}</span>
        <span class="sub bottom">${esc(sub)}</span>
        <span class="play">${PLAY_SVG}</span>
        ${s.percent ? `<span class="bar"><i style="width:${s.percent}%"></i></span>` : ""}
      </button>`);
    node.dataset.card = "show";
    node.dataset.folder = s.folder;
    return node;
  };

  // Recently Added / search entries: {type: "series"} | {type: "movie"|"episode", video, flag}
  const entryCard = (e) => {
    if (e.type === "series") return seriesCard(e);
    // movies open their page (info + resume + subtitles); episodes play directly
    const node = card(e.video, e.type === "movie" ? { openMovie: true } : {});
    if (e.flag) node.insertBefore(el(`<span class="flag">${esc(e.flag)}</span>`), node.firstChild);
    return node;
  };

  // One click handler for every card, attached to the stable #main container:
  // even if a background refresh swaps the row DOM mid-tap, the click still lands.
  main.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-card]");
    if (!t) return;
    const kind = t.dataset.card;
    if (kind === "play") {
      const v = CARD_DATA.get(t.dataset.vid);
      if (v) openPlayer(v, { fromApp: true });
    } else if (kind === "movie") {
      location.hash = `#/movie/${t.dataset.vid}`;
    } else if (kind === "show") {
      location.hash = `#/show/${encodeURIComponent(t.dataset.folder)}`;
    } else if (kind === "clear") {
      e.stopPropagation();
      try { await api(`/api/progress/${t.dataset.vid}/clear`, { method: "POST" }); } catch (e2) { /* ignore */ }
      renderHome();
    }
  });

  const row = (name, items, opts = {}) => {
    if (!items || !items.length) return null;
    const { series = false, entries = false } = opts;
    const r = el(`<section class="row"><h2>${series ? `<a href="#/show/${encodeURIComponent(name)}" class="rowlink">${esc(name)} <span class="chev">›</span></a>` : esc(name)}<small>${items.length}</small></h2><div class="scroller"></div></section>`);
    const sc = $(".scroller", r);
    items.forEach((it) => sc.appendChild(entries ? entryCard(it) : card(it, opts)));
    return r;
  };

  // ---------------------------------------------------------------- hero
  const hero = (v, kickerText, poster, isSeries = false) => {
    const resume = canResume(v);
    const label = resume ? `Resume from ${fmtTime(v.position)}` : v.finished ? "Play again" : "Play";
    const kicker = kickerText || (v.played_at ? (resume ? "Continue watching" : "Last played") : "Recently added");
    const meta = [esc(v.folder), esc(v.subfolder || ""), fmtDuration(v.duration), resume ? `<b>${Math.round(v.percent)}% watched</b>` : ""].filter(Boolean).join(" &nbsp;·&nbsp; ");
    const h = el(`
      <section class="hero ${poster ? "has-poster" : ""}" style="--tile-bg:${tileBg(v)};${poster ? `background-image:url('${poster}')` : ""}">
        <div class="hero-inner">
          <div class="kicker">${esc(kicker)}</div>
          <h1>${esc(v.title)}</h1>
          <div class="meta">${meta}</div>
          <div class="actions">
            <button class="btn" type="button" id="heroPlay">${PLAY_SVG}<span>${esc(label)}</span></button>
            ${resume ? '<button class="btn secondary" type="button" id="heroRestart">Start over</button>' : ""}
            ${isSeries ? `<a class="btn secondary" href="#/show/${encodeURIComponent(v.folder)}">\u2630 Episodes</a>`
                       : `<a class="btn secondary" href="#/movie/${v.id}">\u24d8 Info</a>`}
          </div>
          ${resume ? `<div class="bar"><i style="width:${v.percent}%"></i></div>` : ""}
        </div>
      </section>`);
    $("#heroPlay", h).addEventListener("click", () => openPlayer(v, { fromApp: true }));
    const restart = $("#heroRestart", h);
    if (restart) restart.addEventListener("click", () => openPlayer({ ...v, position: 0 }, { fromApp: true }));
    return h;
  };

