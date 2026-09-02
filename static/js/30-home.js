  // ---------------------------------------------------------------- home
  let homeCache = null;
  const renderHome = async () => {
    closePlayer();
    let data;
    try {
      data = await api("/api/home");
    } catch (e) {
      main.innerHTML = `<div class="empty"><h2>Can't reach the server</h2><p>${esc(e.message)}</p></div>`;
      return;
    }
    homeCache = data;
    if (searchInput.value.trim()) return renderSearch(searchInput.value);
    main.innerHTML = "";
    if (!data.count) {
      main.appendChild(el(`
        <div class="empty"><div>
          <h2>Your library is empty</h2>
          <p>Drop video files into <code>${esc(data.library)}</code>.</p>
          <p>Each folder you create there becomes a row on this page, and each file name becomes the title. New files show up within 30 seconds.</p>
        </div></div>`));
      return;
    }
    const seriesFolders = new Set();
    (data.rows || []).forEach((r) => { if (r.kind === "entries") (r.items || []).forEach((e) => seriesFolders.add(e.folder)); });
    (data.recent || []).forEach((e) => { if (e.type === "series") seriesFolders.add(e.folder); });
    if (data.library_offline) {
      const off = el(`
        <div class="empty"><div>
          <h2>\u26a0\ufe0e Library offline</h2>
          <p>Can't find <code>${esc(data.library)}</code> \u2014 if the videos live on an external disk, plug it in and hit Retry.</p>
          <p class="offline-actions">
            <button class="btn" type="button" id="libRetry">Retry</button>
            <button class="btn secondary" type="button" id="libChange">Use a different folder\u2026</button>
          </p>
        </div></div>`);
      $("#libRetry", off).addEventListener("click", () => renderHome());
      $("#libChange", off).addEventListener("click", () => openFolderPicker());
      main.appendChild(off);
      return;
    }
    if (data.hero) main.appendChild(hero(data.hero, data.hero_kicker, data.hero_poster, seriesFolders.has(data.hero.folder)));
    const rows = [
      row("Continue Watching", data.continue, { removable: true, caption: true, seriesFolders }),
      row("Recently Added", data.recent, { entries: true }),
      // non-series rows ("Movies", "Action", ...) hold movies: open their page, don't autoplay
      ...data.rows.map((r) => row(r.name, r.items, r.kind === "entries" ? { entries: true } : { openMovie: true })),
    ];
    rows.forEach((r) => r && main.appendChild(r));
  };

  let searchSeq = 0;
  const renderSearch = async (q) => {
    q = q.trim();
    if (!q) return route();
    const seq = ++searchSeq;
    let results = [];
    try { results = (await api(`/api/search?q=${encodeURIComponent(q)}`)).results; } catch (e) { /* ignore */ }
    if (seq !== searchSeq || searchInput.value.trim() !== q) return;   // a newer search superseded this one
    main.innerHTML = "";
    const grid = el(`<div class="grid"><h2>Results for “${esc(q)}”</h2></div>`);
    if (!results.length) grid.appendChild(el(`<p class="none">Nothing matches.</p>`));
    results.forEach((e) => grid.appendChild(entryCard(e)));
    main.appendChild(grid);
  };

  let searchTimer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => renderSearch(searchInput.value), 150);
  });
  searchInput.addEventListener("keydown", (e) => { if (e.key === "Escape") { searchInput.value = ""; route(); searchInput.blur(); } });

  // The logo is always a way home: clear search, go to top, re-render.
  $(".logo").addEventListener("click", (e) => {
    searchInput.value = "";
    window.scrollTo(0, 0);
    if ((location.hash || "#/") === "#/") { e.preventDefault(); route(); }
  });


  // Server-side folder browser (a web page can't see real disk paths, so the
  // server lists its own folders and we navigate them — works from the phone too).
  const openFolderPicker = (onPick) => {
    const box = el(`
      <div class="fpicker">
        <h3>Choose the library folder</h3>
        <div class="fp-shortcuts"></div>
        <div class="fp-path"></div>
        <div class="fp-list"></div>
        <div class="fp-actions">
          <button class="btn secondary" type="button" data-fp="cancel">Cancel</button>
          <button class="btn" type="button" data-fp="use">Use this folder</button>
        </div>
      </div>`);
    modalEl.innerHTML = "";
    modalEl.appendChild(box);
    modalEl.hidden = false;
    let cur = null;
    const load = async (path) => {
      let d;
      try { d = await api(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`); }
      catch (e) { toast(e.message || "Can't open that folder"); return; }
      cur = d;
      $(".fp-path", box).innerHTML = `${d.parent ? '<button class="fp-up" type="button">\u2b06</button> ' : ""}<code>${esc(d.path)}</code>`;
      const up = $(".fp-up", box);
      if (up) up.addEventListener("click", () => load(d.parent));
      const sc = $(".fp-shortcuts", box);
      sc.innerHTML = "";
      d.shortcuts.forEach((sh) => {
        const b = el(`<button class="fp-chip" type="button">${esc(sh.name)}</button>`);
        b.addEventListener("click", () => load(sh.path));
        sc.appendChild(b);
      });
      const list = $(".fp-list", box);
      list.innerHTML = "";
      if (d.denied) {
        list.appendChild(el(`<p class="fp-note">\u26a0\ufe0e macOS is blocking the server from this folder.
          Quit the server (Matinee app \u2192 Stop) and start it again from the Matinee app \u2014
          when macOS asks for folder access, click Allow.</p>`));
      } else if (!d.dirs.length) {
        list.appendChild(el(`<p class="fp-note">No subfolders here.</p>`));
      }
      d.dirs.forEach((dir) => {
        const rowEl = el(`<button class="fp-row" type="button">\ud83d\udcc1 ${esc(dir.name)}</button>`);
        rowEl.addEventListener("click", () => load(dir.path));
        list.appendChild(rowEl);
      });
      $('[data-fp="use"]', box).textContent =
        d.media_count || d.dirs.length ? `Use this folder${d.media_count ? ` (${d.media_count} videos here)` : ""}` : "Use this folder";
    };
    box.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-fp]");
      if (!b) return;
      if (b.dataset.fp === "cancel") { modalEl.hidden = true; return; }
      if (b.dataset.fp === "use" && cur) {
        if (onPick) { modalEl.hidden = true; onPick(cur.path); return; }
        try {
          const r = await fetch("/api/library", { method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ path: cur.path }) });
          const d2 = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(d2.detail || "Could not switch");
          modalEl.hidden = true;
          toast(`Library: ${d2.count} videos found \u2713`);
          renderHome();
        } catch (e2) { toast(e2.message); }
      }
    });
    load("");
  };
