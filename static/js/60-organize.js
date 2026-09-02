  // ------------------------------------------------------------ iPhone modal
  $("#phoneBtn").addEventListener("click", async () => {
    let info = { urls: [] };
    try { info = await api("/api/info"); } catch (e) { /* ignore */ }
    modalEl.innerHTML = "";
    const box = el(`
      <div class="box" role="dialog" aria-modal="true">
        <h3>Open on your iPhone</h3>
        <p>With the phone's hotspot on and this Mac connected to it, open Safari on the phone and type one of these:</p>
        ${info.urls.map((u) => `<code class="url">${esc(u)}</code>`).join("")}
        <p>To get an app icon: tap <b>Share</b> → <b>Add to Home Screen</b>. Your watch progress is shared between phone and Mac.</p>
        <button class="btn close" type="button">Done</button>
      </div>`);
    $(".close", box).addEventListener("click", () => { modalEl.hidden = true; });
    modalEl.appendChild(box);
    modalEl.hidden = false;
  });
  modalEl.addEventListener("click", (e) => { if (e.target === modalEl) modalEl.hidden = true; });

  // ------------------------------------------------------------- organize
  const fmtWhen = (ts) => {
    const d = new Date(ts * 1000), diff = (Date.now() - d) / 1000;
    if (diff < 90) return "just now";
    if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };
  const kindLabel = (p) => {
    if (p.kind === "episode") return `Episode · S${String(p.season).padStart(2, "0")}E${String(p.episode).padStart(2, "0")}${p.episode_end ? "-E" + String(p.episode_end).padStart(2, "0") : ""}`;
    if (p.kind === "movie") return p.year ? `Movie · ${p.year}` : "Movie";
    return "Unknown";
  };

  const renderOrganize = async () => {
    closePlayer();
    main.innerHTML = `<div class="org"><p class="muted">Loading…</p></div>`;
    let data;
    try { data = await api("/api/organize"); } catch (e) { main.innerHTML = `<div class="org"><p>${esc(e.message)}</p></div>`; return; }

    const page = el(`
      <div class="org">
        <a class="backlink" href="#/">← Back to library</a>
        <h1>Organize library</h1>
        <p class="muted">
          ${data.auto
            ? "<b>Auto-organize is on.</b> Anything you drop into the library is renamed and filed into <code>Show/Season 01/Show - S01E01.ext</code> or <code>Movies/Title (Year).ext</code> about a minute after it finishes copying. Every move is listed below and can be undone."
            : "Auto-organize is <b>off</b> (<code>auto_organize: false</code> in config.yaml). Review the suggestions below and apply the ones you want."}
        </p>

        <section class="org-try">
          <label>Try a name: <input id="tryName" type="text" placeholder="e.g. Breaking.Bad.S01E03.720p.HDTV.x264-KILLERS.mkv" autocomplete="off" spellcheck="false"></label>
          <div id="tryOut" class="try-out muted">Type a file name to see where it would go.</div>
        </section>

        <section class="org-section" id="pendingSection">
          <div class="org-head">
            <h2>${data.pending.length ? `Suggested changes <small>${data.pending.length}</small>` : "Everything is tidy"}</h2>
            <div class="org-actions">
              <a class="btn secondary small" href="#/artwork">\ud83d\uddbc Artwork</a>
              <button class="btn secondary small" id="rescanBtn" type="button">Rescan now</button>
              ${data.pending.length ? '<button class="btn small" id="applyBtn" type="button">Apply selected</button>' : ""}
            </div>
          </div>
          <p class="muted small">${data.tidy} file(s) already have tidy names.${data.pending.length ? " Unticked rows are things the app wasn't sure about — tick them (or edit the destination) to apply." : ""}</p>
          <div class="org-list" id="pendingList"></div>
        </section>

        <section class="org-section" id="convertSection"></section>

        <section class="org-section">
          <h2>Recent moves <small>${data.moves.length}</small></h2>
          <div class="org-list" id="movesList">${data.moves.length ? "" : '<p class="muted small">Nothing has been moved yet.</p>'}</div>
        </section>
      </div>`);

    // ---- iPhone conversions (re-rendered on its own every few seconds while one is running)
    const fmtBytes = (b) => b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.round(b / 1e3)} KB`;
    const convSec = $("#convertSection", page);
    const renderConvert = (cv) => {
    convSec.innerHTML = "";
    const cur = cv.current;
    const queue = cv.queue || [], failed = cv.failed || [], done = cv.done || [];
    convSec.appendChild(el(`
      <div class="conv-wrap">
      <div class="org-head">
        <h2>iPhone conversions <small>${cur ? "1 running" : queue.length ? `${queue.length} queued` : failed.length ? `${failed.length} failed` : done.length ? `${done.length} done` : ""}</small></h2>
        ${cv.originals_bytes ? `<button class="btn secondary small" id="delOrig" type="button">Move originals to Bin (${fmtBytes(cv.originals_bytes)})</button>` : ""}
      </div>
      <p class="muted small">${!cv.available
        ? "ffmpeg is not installed, so MKV/AVI files can't be converted. Run <code>brew install ffmpeg</code> and restart."
        : !cv.enabled
          ? "Conversion is off (<code>convert_for_iphone: false</code>). MKV/AVI files will only play in Chrome."
          : `Files iPhone can't play are converted to MP4 one at a time in the background (usually a quick re-wrap). ${cv.keep_originals ? "Originals are kept in <code>library/.originals/</code> until you move them to the Bin here." : "Originals are deleted after a successful conversion."}`}</p>
      <div class="org-list" id="convList"></div>
      </div>`));
    const convList = $("#convList", convSec);
    if (cur) convList.appendChild(el(`
      <div class="org-row conv running">
        <div class="org-body">
          <div class="org-to"><span class="arrow">⟳</span><span class="dest">${esc(cur.rel_path)}</span></div>
          <div class="org-meta"><span class="muted small">${esc(cur.stage)} · ${Math.round(cur.percent)}%</span></div>
          <div class="bar"><i style="width:${cur.percent}%"></i></div>
        </div>
      </div>`));
    queue.slice(0, 30).forEach((c) => convList.appendChild(el(`
      <div class="org-row conv"><div class="org-body"><div class="org-from">${esc(c.rel_path)}</div><div class="org-meta"><span class="muted small">queued</span></div></div></div>`)));
    if (queue.length > 30) convList.appendChild(el(`<p class="muted small">…and ${queue.length - 30} more</p>`));
    failed.forEach((c) => {
      const r = el(`
        <div class="org-row conv unsure">
          <div class="org-body"><div class="org-from">${esc(c.rel_path)}</div><div class="org-meta"><span class="note">${esc(c.error || "failed")}</span></div></div>
          <button class="btn secondary small retry" type="button">Retry</button>
        </div>`);
      $(".retry", r).addEventListener("click", async () => {
        await api("/api/convert/retry", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rel_path: c.rel_path }) });
        toast("Queued again");
        renderOrganize();
      });
      convList.appendChild(r);
    });
    done.slice(0, 10).forEach((c) => convList.appendChild(el(`
      <div class="org-row conv done"><div class="org-body"><div class="org-from">${esc(c.rel_path)}</div><div class="org-to"><span class="arrow">→</span><span class="dest">${esc(c.output || "")}</span></div><div class="org-meta"><span class="muted small">${fmtWhen(c.updated_at)} · converted</span></div></div></div>`)));
    if (!cur && !queue.length && !failed.length && !done.length) convList.appendChild(el(`<p class="muted small">Nothing needs converting.</p>`));
    const delOrig = $("#delOrig", convSec);
    if (delOrig) delOrig.addEventListener("click", async () => {
      if (!confirm(`Move the original files (${fmtBytes(cv.originals_bytes)}) to the Bin? All converted MP4s stay and everything keeps playing. You can restore them from the Bin, or empty the Bin to actually free the space.`)) return;
      const r = await api("/api/convert/delete-originals", { method: "POST" });
      toast(`Moved ${fmtBytes(r.freed)} to the Bin`);
      renderOrganize();
    });
    // keep the progress bar moving while something converts (only this section re-renders)
    if (cur || queue.length) setTimeout(async () => {
      if (!document.body.contains(convSec)) return;
      try { renderConvert(await api("/api/convert")); } catch (e) { /* ignore */ }
    }, 4000);
    };
    renderConvert(data.convert || {});

    const pendingList = $("#pendingList", page);
    data.pending.forEach((p) => {
      const rowEl = el(`
        <div class="org-row ${p.confident ? "" : "unsure"}">
          <label class="org-check"><input type="checkbox" ${p.confident ? "checked" : ""}></label>
          <div class="org-body">
            <div class="org-from" title="${esc(p.src)}">${esc(p.src)}</div>
            <div class="org-to"><span class="arrow">→</span><input type="text" value="${esc(p.dst)}" spellcheck="false"></div>
            <div class="org-meta"><span class="pill ${p.kind}">${esc(kindLabel(p))}</span>${p.note ? `<span class="note">${esc(p.note)}</span>` : ""}</div>
          </div>
        </div>`);
      rowEl._plan = p;
      pendingList.appendChild(rowEl);
    });

    const movesList = $("#movesList", page);
    data.moves.forEach((m) => {
      const rowEl = el(`
        <div class="org-row move ${m.undone ? "undone" : ""}">
          <div class="org-body">
            <div class="org-from">${esc(m.src)}</div>
            <div class="org-to"><span class="arrow">→</span><span class="dest">${esc(m.dst)}</span></div>
            <div class="org-meta"><span class="muted small">${fmtWhen(m.at)}${m.auto ? " · automatic" : ""}${m.undone ? " · undone" : ""}</span></div>
          </div>
          ${m.undone ? "" : '<button class="btn secondary small undo" type="button">Undo</button>'}
        </div>`);
      const undoBtn = $(".undo", rowEl);
      if (undoBtn) undoBtn.addEventListener("click", async () => {
        undoBtn.disabled = true;
        const res = await api(`/api/organize/undo/${m.id}`, { method: "POST" });
        if (!res.ok) { toast(res.error || "Couldn't undo"); undoBtn.disabled = false; return; }
        toast("Moved back");
        renderOrganize();
      });
      movesList.appendChild(rowEl);
    });

    $("#rescanBtn", page).addEventListener("click", async () => { await api("/api/rescan", { method: "POST" }); renderOrganize(); });
    const applyBtn = $("#applyBtn", page);
    if (applyBtn) applyBtn.addEventListener("click", async () => {
      const moves = [...pendingList.querySelectorAll(".org-row")]
        .filter((r) => r.querySelector("input[type=checkbox]").checked)
        .map((r) => ({ src: r._plan.src, dst: r.querySelector(".org-to input").value.trim() }));
      if (!moves.length) { toast("Nothing selected"); return; }
      applyBtn.disabled = true;
      applyBtn.textContent = "Applying…";
      const res = await api("/api/organize/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ moves }) });
      const failed = res.results.filter((r) => !r.ok);
      toast(failed.length ? `${res.results.length - failed.length} moved, ${failed.length} failed: ${failed[0].error}` : `${res.results.length} file(s) organized`);
      renderOrganize();
    });

    let tryTimer = null;
    const tryIn = $("#tryName", page), tryOut = $("#tryOut", page);
    tryIn.addEventListener("input", () => {
      clearTimeout(tryTimer);
      const name = tryIn.value.trim();
      if (!name) { tryOut.textContent = "Type a file name to see where it would go."; return; }
      tryTimer = setTimeout(async () => {
        try {
          const p = await api(`/api/organize/preview?name=${encodeURIComponent(name)}`);
          tryOut.innerHTML = p.changed
            ? `<span class="arrow">→</span> <code>${esc(p.dst)}</code> <span class="pill ${p.kind}">${esc(kindLabel(p))}</span>${p.confident ? "" : ' <span class="note">would be suggested, not moved automatically</span>'}`
            : `<code>${esc(p.dst)}</code> — already tidy${p.note ? " (" + esc(p.note) + ")" : ""}`;
        } catch (e) { tryOut.textContent = e.message; }
      }, 250);
    });

    main.innerHTML = "";
    main.appendChild(page);
    window.scrollTo(0, 0);
  };

