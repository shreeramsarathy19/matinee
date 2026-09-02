  // ------------------------------------------------------------- profiles
  const profileBtn = $("#profileBtn");
  const AV_COLORS = ["hsl(354 75% 46%)", "hsl(210 65% 46%)", "hsl(140 45% 38%)", "hsl(40 75% 45%)", "hsl(280 50% 50%)"];
  const avatarColor = (name) => {
    const i = PROFILES.indexOf(name);
    return AV_COLORS[(i >= 0 ? i : hue(name)) % AV_COLORS.length];
  };
  const avatarHtml = (name, big) =>
    `<span class="avatar ${big ? "big" : ""}" style="--av:${avatarColor(name)}">${esc(name[0].toUpperCase())}</span>`;

  const updateAvatarBtn = () => {
    profileBtn.hidden = PROFILES.length < 2;
    if (PROFILE) profileBtn.innerHTML = avatarHtml(PROFILE);
  };

  const renderProfileGate = (allowClose) => {
    const oldGate = $("#pgate");
    if (oldGate) oldGate.remove();
    const gate = el(`
      <div class="pgate" id="pgate">
        ${allowClose ? '<button class="pgate-close" type="button" aria-label="Close">\u2715</button>' : ""}
        <h1>Who's watching?</h1>
        <div class="plist"></div>
      </div>`);
    const listEl = $(".plist", gate);
    PROFILES.forEach((name) => {
      const item = el(`
        <button class="pitem ${name === PROFILE ? "current" : ""}" type="button">
          ${avatarHtml(name, true)}
          <span class="pname">${esc(name)}</span>
        </button>`);
      item.addEventListener("click", () => {
        PROFILE = name;
        localStorage.setItem("matinee-profile", name);
        homeCache = null;
        gate.remove();
        updateAvatarBtn();
        route();
      });
      listEl.appendChild(item);
    });
    if (allowClose) $(".pgate-close", gate).addEventListener("click", () => gate.remove());
    document.body.appendChild(gate);
  };
  profileBtn.addEventListener("click", () => renderProfileGate(true));

  // ------------------------------------------------------------ first run
  const renderSetup = () => {
    main.innerHTML = "";
    const box = el(`
      <div class="setup"><div class="setup-box">
        <h1>Welcome \ud83c\udf7f</h1>
        <p class="setup-sub">A few choices and your home cinema is ready. Everything can be changed later in config.yaml.</p>
        <label>Name your cinema
          <input id="suName" type="text" value="Matinee" maxlength="24" autocomplete="off">
        </label>
        <label>Who will be watching? <small>(comma-separated \u2014 each gets their own watch history)</small>
          <input id="suProfiles" type="text" value="You, Family" autocomplete="off">
        </label>
        <label>Skip buttons jump
          <select id="suSkip">
            <option value="10">10 seconds</option>
            <option value="15">15 seconds</option>
            <option value="30" selected>30 seconds</option>
            <option value="60">60 seconds</option>
          </select>
        </label>
        <label class="setup-check">
          <input id="suConvert" type="checkbox" checked>
          Convert MKV/AVI files for phone playback in the background (needs ffmpeg)
        </label>
        <div class="setup-lib">
          <span>Library folder: <code id="suLibPath"></code></span>
          <button class="btn secondary" type="button" id="suLibPick">Choose\u2026</button>
        </div>
        <button class="btn setup-go" type="button" id="suGo">Start watching</button>
      </div></div>`);
    main.appendChild(box);
    let libPath = APPCFG.library || "";
    $("#suLibPath", box).textContent = libPath;
    $("#suLibPick", box).addEventListener("click", () => openFolderPicker((path) => {
      libPath = path;
      $("#suLibPath", box).textContent = path;
    }));
    $("#suGo", box).addEventListener("click", async () => {
      const payload = {
        app_name: $("#suName", box).value,
        profiles: $("#suProfiles", box).value.split(",").map((s) => s.trim()).filter(Boolean),
        skip_seconds: parseInt($("#suSkip", box).value, 10),
        convert_for_iphone: $("#suConvert", box).checked,
        library: libPath || null,
      };
      try {
        const r = await fetch("/api/setup", { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify(payload) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || "Could not save");
        toast(`Welcome to ${payload.app_name}! ${d.count} video(s) found \u2713`);
        setTimeout(() => location.reload(), 900);
      } catch (e2) { toast(e2.message); }
    });
  };

  // ---------------------------------------------------------------- boot
  (async () => {
    try {
      const su = await api("/api/setup");
      Object.assign(APPCFG, su);
    } catch (e) { /* defaults stand */ }
    applyBranding();
    if (APPCFG.first_run) { renderSetup(); return; }
    try {
      const d = await api("/api/profiles");
      PROFILES = d.profiles || [];
    } catch (e) { PROFILES = []; }
    if (!PROFILES.includes(PROFILE)) {
      PROFILE = "";
      localStorage.removeItem("matinee-profile");
    }
    if (PROFILES.length > 1 && !PROFILE) {
      updateAvatarBtn();
      renderProfileGate(false);
      route();          // render home behind the gate (default profile) so closing is never blank
    } else {
      if (!PROFILE) PROFILE = PROFILES[0] || "";
      updateAvatarBtn();
      route();
    }
  })();
