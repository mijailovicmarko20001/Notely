/* Notely web UI — vanilla JS, no build step. */
"use strict";

const $ = (sel) => document.querySelector(sel);

// Only relevant when the server is started with NOTELY_AUTH_TOKEN set
// (e.g. exposed beyond localhost, see docker-compose.yml). No-op otherwise.
const AUTH_TOKEN_KEY = "notely-auth-token";
const api = async (path, opts = {}, _retried = false) => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const r = await fetch("/api" + path, {
    headers: {
      ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...opts,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
  });
  if (r.status === 401 && !_retried) {
    const entered = prompt("This Notely instance requires an access token:");
    if (entered) {
      localStorage.setItem(AUTH_TOKEN_KEY, entered.trim());
      return api(path, opts, true);
    }
  }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

/* ---------- icon set (inline SVG, currentColor -- replaces the unicode
   glyphs ✓ ✗ ☀ ☾ ◐ ! ✕ that rendered slightly differently depending on
   the browser/OS fallback font. FRONTEND_TODO.md Round 3 P2. Feather-
   style outline icons, 24x24 viewBox, stroke via the shared .icon CSS
   class so no per-icon color/sizing rules are needed anywhere they're
   used. Declared before the theme toggle below: icon() is a hoisted
   function declaration so call order wouldn't normally matter, but
   ICON_PATHS is a const, which stays in the temporal dead zone until its
   own declaration line runs -- renderThemeToggle() calls icon() at
   top-level, immediately, on page load, so this block has to come first
   or that call throws "Cannot access 'ICON_PATHS' before initialization"
   (a real bug caught by an uncaught pageerror in live verification: it
   silently killed the entire script, so NOT ONE network request fired,
   not even the boot() sequence -- confirmed via request/response
   tracing, not by guessing from the symptom). ---------- */
const ICON_PATHS = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  cross: '<path d="M18 6 6 18"/><path d="M6 6l12 12"/>',
  warn: '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z"/>',
  auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/>',
  upload: '<path d="M12 16V4M12 4l-5 5M12 4l5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  inbox: '<path d="M4 12h4l2 3h4l2-3h4"/><path d="M4 12 5.5 4.5A2 2 0 0 1 7.5 3h9a2 2 0 0 1 2 1.5L20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z"/>',
  chevronLeft: '<path d="M15 18l-6-6 6-6"/>',
  chevronRight: '<path d="M9 18l6-6-6-6"/>',
};
function icon(name, extraClass) {
  return `<svg class="icon${extraClass ? " " + extraClass : ""}" viewBox="0 0 24 24">${ICON_PATHS[name] || ""}</svg>`;
}

/* ---------- theme toggle: auto (system) -> light -> dark -> auto.
   Explicit choices persist in localStorage; a tiny inline script in
   index.html's <head> applies the stored choice before style.css even
   loads, so there's no flash of the wrong theme on reload. ---------- */
const THEME_KEY = "notely-theme";
function currentTheme() {
  const t = localStorage.getItem(THEME_KEY);
  return t === "light" || t === "dark" ? t : "auto";
}
function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}
function renderThemeToggle() {
  const t = currentTheme();
  const iconName = t === "light" ? "sun" : t === "dark" ? "moon" : "auto";
  const label = t === "light" ? "Light" : t === "dark" ? "Dark" : "Auto";
  $("#theme-toggle").innerHTML = `${icon(iconName, "theme-icon")}${label}`;
  $("#theme-toggle").title = `Color theme: ${label} (click to change)`;
}
$("#theme-toggle").addEventListener("click", () => {
  const next = { auto: "light", light: "dark", dark: "auto" }[currentTheme()];
  if (next === "auto") localStorage.removeItem(THEME_KEY);
  else localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
  renderThemeToggle();
});
applyTheme(currentTheme());
renderThemeToggle();

/* ---------- shared UI components (one implementation, used everywhere —
   see FRONTEND_TODO.md P0: divergent hand-rolled markup per view was why
   chips/pills/status looked slightly different in every tab) ---------- */
function chip(name, value, label, checked) {
  return `<label class="chip"><input type="checkbox" name="${name}" value="${value}" ${checked ? "checked" : ""}> ${label}</label>`;
}
function pill(text, tone) {
  return `<span class="pill${tone ? " tone-" + tone : ""}">${text}</span>`;
}
function statusLine(text) {
  return `<span class="status-line"><span class="spinner"></span>${text}</span>`;
}
function errorBanner(message) {
  return `<div class="banner tone-err">${icon("cross", "banner-icon")}<span>${message}</span></div>`;
}
function okBanner(message) {
  return `<div class="banner tone-ok">${icon("check", "banner-icon")}<span>${message}</span></div>`;
}
function warnBanner(message) {
  return `<div class="banner tone-warn">${icon("warn", "banner-icon")}<span>${message}</span></div>`;
}
function nextStep(message, tab, cta) {
  return `<div class="next-step"><span>${message}</span><button data-goto="${tab}">${cta} →</button></div>`;
}
// Icon + copy + optional CTA instead of one bare sentence for "nothing
// here yet" moments (FRONTEND_TODO.md Round 3 P2).
function emptyState(iconName, message, tab, cta) {
  return `<div class="empty-state">${icon(iconName)}<strong>${message}</strong>` +
    (tab ? `<button data-goto="${tab}" class="primary">${cta} →</button>` : "") +
    `</div>`;
}

// Collapsed-by-default "All N selected" summary + Customize toggle for a
// chip-list (FRONTEND_TODO.md Round 3 P1: the Run tab showed 22 lecture +
// 8 stage chips always fully expanded and checked -- the overwhelmingly
// common "just run everything" case rendered as 30 checkboxes instead of
// one line). Attaches its `change` listener to the list container itself,
// not the individual chip inputs, so it survives the container's
// innerHTML being replaced wholesale (refreshState() rebuilds
// #run-lectures on every call) -- only needs setting up once; callers
// that replace a list's innerHTML should call the returned render()
// again afterward to reflect the new checked count.
function setupChipSummary(listId, summaryId, noun) {
  const list = $("#" + listId);
  const summary = $("#" + summaryId);
  function render() {
    const inputs = [...list.querySelectorAll("input")];
    const total = inputs.length;
    const checked = inputs.filter((i) => i.checked).length;
    const expanded = !list.classList.contains("collapsed");
    const countText = total === 0 ? `No ${noun}s yet`
      : checked === total ? `All ${total} ${noun}${total === 1 ? "" : "s"} selected`
      : `${checked} of ${total} ${noun}s selected`;
    summary.innerHTML = `<span class="count">${countText}</span>` +
      (total > 0 ? `<button type="button" class="chip-customize">${expanded ? "Show less" : "Customize"}</button>` : "");
    const btn = summary.querySelector(".chip-customize");
    if (btn) btn.addEventListener("click", () => { list.classList.toggle("collapsed"); render(); });
  }
  list.addEventListener("change", render);
  render();
  return render;
}
// Wrap a button's async click handler so it can't fire twice from a fast
// double-click, and shows a spinner in place of its label while in flight
// (FRONTEND_TODO.md P2: most buttons didn't guard against this before).
function onClickBusy(el, handler) {
  el.addEventListener("click", async () => {
    if (el.classList.contains("busy")) return;
    el.classList.add("busy");
    el.disabled = true;
    try {
      await handler();
    } finally {
      el.classList.remove("busy");
      el.disabled = false;
    }
  });
}

/* ---------- tabs ---------- */
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab))
);
document.addEventListener("click", (e) => {
  const goto = e.target.closest("[data-goto]");
  if (goto) showTab(goto.dataset.goto);
});
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "setup") loadSetup();
  if (name === "sources") refreshState();
  if (name === "run") { refreshState(); pollJob(); }
  if (name === "review") populateReviewLectures();
  if (name === "guide") loadGuide();
}

/* ---------- nav progress indicators (FRONTEND_TODO.md P1: the app is a
   real 5-step sequence, but the tab row gave zero indication of that) --- */
function updateNavProgress(preflightOk) {
  const lectures = (stateCache && stateCache.lectures) || [];
  const hasKey = stateCache ? stateCache.has_api_key : false;
  const sourcesDone = lectures.length > 0 && lectures.every((l) => l.deck);
  const runDone = lectures.length > 0 && lectures.every((l) => l.stages["6"]);
  const guideDone = stateCache ? stateCache.study_guide : false;
  const reviewCount = lectures.reduce((n, l) => n + (l.needs_review_count || 0), 0);

  const steps = {
    setup: preflightOk === true,
    sources: sourcesDone,
    run: runDone,
    review: false, // review has no "done" state, only "needs attention" below
    guide: guideDone,
  };
  for (const [key, done] of Object.entries(steps)) {
    const el = $("#step-" + key);
    if (!el) continue;
    el.classList.toggle("step-done", done);
    // check icon when done, the step number otherwise -- data-step is the
    // source of truth so the number survives toggling back and forth
    // (moved off a CSS ::before checkmark so it's a real icon, not a
    // unicode glyph -- FRONTEND_TODO.md Round 3 P2).
    el.innerHTML = done ? icon("check") : el.dataset.step;
  }
  const badge = $("#review-badge");
  if (reviewCount > 0) { badge.hidden = false; badge.textContent = reviewCount; }
  else badge.hidden = true;
  void hasKey; // reserved for a future setup-specific indicator
}

/* ---------- setup ---------- */
// Cached across calls so refreshState() (triggered from other tabs, which
// don't re-run preflight) can still pass an accurate value to
// updateNavProgress instead of re-deriving it from the DOM.
let lastPreflightOk = null;
async function loadSetup() {
  $("#preflight").innerHTML = statusLine("Checking…");
  try {
    const pf = await api("/preflight");
    // Compact status-grid instead of a full <table> -- 9 short pass/fail
    // rows don't need header/border table chrome (FRONTEND_TODO.md Round
    // 3 P1: read like a database dump for what's really a checklist).
    $("#preflight").innerHTML =
      '<div class="status-grid">' +
      Object.entries(pf.checks)
        .map(([k, v]) =>
          `<div class="status-row">${icon(v.ok ? "check" : "warn", v.ok ? "ok" : "warn")}` +
          `<span class="name">${k}</span><span class="detail">${v.detail || ""}</span></div>`
        )
        .join("") +
      "</div>" +
      (pf.ok ? okBanner("Ready to run.") : warnBanner("Some required tools are missing — the pipeline may fail."));
    lastPreflightOk = pf.ok;
    updateNavProgress(pf.ok);
    $("#setup-next").innerHTML = pf.ok ? nextStep("Environment looks good.", "sources", "Add lectures") : "";
  } catch (e) {
    $("#preflight").innerHTML = errorBanner("Preflight failed: " + e.message);
  }
  const s = await api("/settings");
  // If the saved model doesn't match any hardcoded <option> (this course's
  // real setting, large-v3-turbo, didn't until this fix -- and any future
  // model name has the same problem), synthesize one instead of letting
  // the <select> silently render blank (FRONTEND_TODO.md Round 3 P0,
  // confirmed live: sel.value === '' and sel.options[sel.selectedIndex]
  // === undefined).
  const wantedModel = s.settings.WHISPER_MODEL || "medium";
  const whisperSel = $("#set-whisper");
  if (![...whisperSel.options].some((o) => o.value === wantedModel)) {
    whisperSel.add(new Option(`${wantedModel} (current)`, wantedModel));
  }
  whisperSel.value = wantedModel;
  $("#set-ocr").value = s.settings.OCR_LANG || "";
  $("#set-key").placeholder = s.settings.has_api_key ? "saved (" + s.settings.ANTHROPIC_API_KEY + ")" : "sk-ant-…";
}
onClickBusy($("#btn-save-settings"), async () => {
  const body = { WHISPER_MODEL: $("#set-whisper").value, OCR_LANG: $("#set-ocr").value };
  if ($("#set-key").value.trim()) body.ANTHROPIC_API_KEY = $("#set-key").value.trim();
  await api("/settings", { method: "PUT", body });
  $("#set-key").value = "";
  $("#settings-status").textContent = "Saved ✓";
  await loadSetup();
});
onClickBusy($("#btn-test-key"), async () => {
  $("#settings-status").innerHTML = statusLine("Testing…");
  const r = await api("/settings/test-key", { method: "POST" }).catch((e) => ({ ok: false, error: e.message }));
  $("#settings-status").textContent = r.ok ? "Key works ✓" : "Key failed: " + (r.error || "");
});

/* ---------- sources ---------- */
let pendingEntries = [];
onClickBusy($("#btn-expand"), async () => {
  const url = $("#playlist-url").value.trim();
  if (!url) return;
  $("#playlist-result").innerHTML = statusLine("Looking up…");
  try {
    const r = await api("/playlist/expand", { method: "POST", body: { url } });
    pendingEntries = r.entries;
    renderPlaylist();
  } catch (e) {
    $("#playlist-result").innerHTML = errorBanner(e.message);
  }
});
function renderPlaylist() {
  $("#playlist-result").innerHTML =
    '<div class="table-wrap"><table><tr><th></th><th>#</th><th>Title</th><th>Length</th><th>Move</th></tr>' +
    pendingEntries
      .map(
        (e, i) =>
          `<tr><td><input type="checkbox" ${e.excluded ? "" : "checked"} data-i="${i}"></td>` +
          `<td>${i + 1}</td><td>${e.title}</td><td>${fmtDur(e.duration)}</td>` +
          `<td><button data-i="${i}" class="mv-up" ${i === 0 ? "disabled" : ""}>↑</button>` +
          `<button data-i="${i}" class="mv-dn" ${i === pendingEntries.length - 1 ? "disabled" : ""}>↓</button></td></tr>`
      )
      .join("") +
    '</table></div><div class="row"><button class="primary" id="btn-confirm-lectures">Save as lecture01…NN (in this order)</button></div>' +
    '<p class="hint">Untick anything that isn\'t a lecture (intros, announcements); use ↑↓ if the playlist isn\'t in course order. Top-to-bottom = study-guide order.</p>';
  onClickBusy($("#btn-confirm-lectures"), confirmLectures);
  const move = (i, d) => {
    // remember excluded state through re-render
    document.querySelectorAll("#playlist-result input[type=checkbox]").forEach(
      (c) => (pendingEntries[+c.dataset.i].excluded = !c.checked)
    );
    [pendingEntries[i], pendingEntries[i + d]] = [pendingEntries[i + d], pendingEntries[i]];
    renderPlaylist();
  };
  document.querySelectorAll(".mv-up").forEach((b) => b.addEventListener("click", () => move(+b.dataset.i, -1)));
  document.querySelectorAll(".mv-dn").forEach((b) => b.addEventListener("click", () => move(+b.dataset.i, 1)));
}

async function confirmLectures() {
  const checked = [...document.querySelectorAll("#playlist-result input[type=checkbox]:checked")].map(
    (c) => pendingEntries[+c.dataset.i]
  );
  if (!checked.length) return;
  await api("/lectures", { method: "POST", body: { entries: checked } });
  $("#playlist-result").innerHTML = okBanner(`Saved ${checked.length} lectures ✓`) +
    nextStep("Next, upload the slide decks below.", "sources", "Scroll to decks");
  await refreshState();
}
function fmtDur(s) {
  // Was `if (!s) return ""` -- 0 is falsy in JS, so a slide matched at the
  // very start of a video (timestamp 0, the totally normal slide-1 case)
  // rendered nothing after "@" instead of "0:00" (FRONTEND_TODO.md Round
  // 3 P0, confirmed in a real Review-tab screenshot).
  if (s == null || isNaN(s)) return "";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

// Drag-and-drop for the styled dropzone (FRONTEND_TODO.md Round 3 P2):
// the real <input type=file> is nested inside the <label>, so click-to-
// browse already works natively via implicit label association -- this
// only adds the drag/drop path, which needs its own handlers regardless
// of how the zone is styled.
const dropzone = $("#dropzone");
["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag-over"); })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag-over"); })
);
dropzone.addEventListener("drop", (e) => {
  const dropped = e.dataTransfer && e.dataTransfer.files;
  if (dropped && dropped.length) {
    $("#deck-file").files = dropped;
    $("#deck-file").dispatchEvent(new Event("change"));
  }
});

$("#deck-file").addEventListener("change", async () => {
  const files = [...$("#deck-file").files];
  const lectures = stateCache ? stateCache.lectures : [];
  $("#dropzone-label").textContent = files.length
    ? `${files.length} file${files.length === 1 ? "" : "s"} selected`
    : "Drag decks here, or click to browse";
  $("#btn-upload").hidden = !files.length;
  $("#btn-upload-pool").hidden = !files.length;
  $("#deck-filter-row").hidden = !files.length || lectures.length < 8;
  $("#deck-filter").value = "";

  // Not built via the shared chip() helper: this one needs data-i (which
  // file it belongs to) and data-lecture (a searchable haystack for the
  // filter box) on the wrapping <label> itself, which chip() doesn't take.
  const deckChip = (fileIndex, lecture, checked) =>
    `<label class="chip deck-map-chk-wrap" data-i="${fileIndex}" data-lecture="${(lecture.id + " " + lecture.title).toLowerCase()}">` +
    `<input type="checkbox" class="deck-map-chk" data-i="${fileIndex}" value="${lecture.id}" ${checked ? "checked" : ""}> ` +
    `${lecture.id.replace("lecture", "L")} · ${lecture.title}</label>`;

  const render = (guesses, notes) => {
    $("#deck-mapping").innerHTML = files
      .map(
        (f, i) => `<div class="deck-row" data-i="${i}">
          <div><strong>${f.name}</strong> <span class="hint">${notes[i] || ""}</span></div>
          <div class="hint">Covers which lecture(s)? Tick all that apply:</div>
          <div class="chip-list deck-chips" data-i="${i}">
            ${lectures.map((l) => deckChip(i, l, guesses[i].includes(l.id))).join("")}
          </div></div>`
      )
      .join("");
    applyDeckFilter();
  };

  // 1st pass: filename-number guesses, shown immediately
  const numbered = files.map((f) => {
    const m = f.name.match(/(\d{1,2})/);
    if (!m) return null;
    const id = "lecture" + String(+m[1]).padStart(2, "0");
    return lectures.some((l) => l.id === id) ? id : null;
  });
  const guesses = files.map((f, i) => (numbered[i] ? [numbered[i]] : []));
  const notes = files.map((f, i) => (numbered[i] ? "from filename" : "…matching content"));
  render(guesses, notes);

  // 2nd pass: content suggestions for files without a filename number
  for (let i = 0; i < files.length; i++) {
    if (numbered[i]) continue;
    try {
      const fd = new FormData();
      fd.append("file", files[i]);
      const r = await api("/slides/suggest", { method: "POST", body: fd });
      if (r.suggestions.length) {
        guesses[i] = [r.suggestions[0].lecture_id];
        notes[i] = `suggested by content (${Math.round(r.suggestions[0].score * 100)}% title match) — please verify`;
      } else {
        notes[i] = "no confident match — pick manually";
      }
    } catch {
      notes[i] = "pick manually";
    }
    render(guesses, notes);
  }
});

// Filter the (potentially long, at 20+ lectures) per-deck lecture chip
// lists by id/title substring (FRONTEND_TODO.md P3: this was the other
// genuinely-hard UX problem, not just polish).
$("#deck-filter").addEventListener("input", applyDeckFilter);
function applyDeckFilter() {
  const q = $("#deck-filter").value.trim().toLowerCase();
  document.querySelectorAll(".deck-chips .chip").forEach((c) => {
    const hay = c.dataset.lecture || c.textContent.toLowerCase();
    c.style.display = !q || hay.includes(q) ? "" : "none";
  });
}

onClickBusy($("#btn-upload-pool"), async () => {
  const files = [...$("#deck-file").files];
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  $("#upload-status").innerHTML = statusLine("Merging decks…");
  try {
    const r = await api("/slides/upload-pool", { method: "POST", body: fd });
    const dedupeNote = r.duplicate_pages_skipped > 0
      ? ` (${r.duplicate_pages_skipped} duplicate page(s) of ${r.scanned_pages} skipped)`
      : "";
    $("#upload-status").innerHTML = okBanner(
      `Combined ${r.pool_decks.length} deck(s) into one ${r.merged_pages}-page deck${dedupeNote}, shared by ${r.lectures.length} lecture(s)`
    );
    $("#deck-file").value = "";
    $("#dropzone-label").textContent = "Drag decks here, or click to browse";
    $("#deck-mapping").innerHTML = "";
    $("#btn-upload").hidden = $("#btn-upload-pool").hidden = $("#deck-filter-row").hidden = true;
    $("#sources-next").innerHTML = nextStep("Decks are in.", "run", "Go to Run");
    await refreshState();
  } catch (e) {
    $("#upload-status").innerHTML = errorBanner(e.message);
  }
});

onClickBusy($("#btn-upload"), async () => {
  const files = [...$("#deck-file").files];
  if (!files.length) {
    $("#upload-status").textContent = "Pick at least one file.";
    return;
  }
  let done = 0, errors = [];
  for (let i = 0; i < files.length; i++) {
    const ids = [...document.querySelectorAll(`.deck-map-chk[data-i="${i}"]:checked`)].map((c) => c.value);
    if (!ids.length) {
      errors.push(`${files[i].name}: no lecture selected`);
      continue;
    }
    const fd = new FormData();
    fd.append("file", files[i]);
    fd.append("lecture_ids", ids.join(","));
    $("#upload-status").innerHTML = statusLine(`Uploading ${i + 1}/${files.length}…`);
    try {
      await api("/slides/upload", { method: "POST", body: fd });
      done++;
    } catch (e) {
      errors.push(`${files[i].name}: ${e.message}`);
    }
  }
  $("#upload-status").innerHTML =
    (done ? okBanner(`Saved ${done}/${files.length}`) : "") +
    (errors.length ? errorBanner(errors.join("; ")) : "");
  if (done) {
    $("#deck-file").value = "";
    $("#dropzone-label").textContent = "Drag decks here, or click to browse";
    $("#deck-mapping").innerHTML = "";
    $("#btn-upload").hidden = true;
    $("#sources-next").innerHTML = nextStep("Decks are in.", "run", "Go to Run");
    await refreshState();
  }
});

let stateCache = null;
// Set up once, both rebuilt later (run-stages by loadDefaults() at boot
// from GET /settings's stage registry data, run-lectures by every
// refreshState() call) -- each render() is re-invoked after its rebuild
// instead of re-attaching a new listener.
const renderStagesSummary = setupChipSummary("run-stages", "run-stages-summary", "stage");
const renderLecturesSummary = setupChipSummary("run-lectures", "run-lectures-summary", "lecture");

async function refreshState() {
  stateCache = await api("/state");
  const { lectures } = stateCache;
  // lecture table
  $("#lecture-table").innerHTML = lectures.length
    ? '<div class="table-wrap"><table><tr><th>ID</th><th>Title</th><th>Deck</th><th>Stages 0–6</th><th>Review</th></tr>' +
      lectures
        .map(
          (l) =>
            `<tr><td>${l.id}</td><td>${l.title}</td><td>${l.deck || '<span class="warn">missing</span>'}</td>` +
            `<td class="stage-dots">${Object.values(l.stages).map((ok) => `<span class="${ok ? "done" : ""}"></span>`).join("")}</td>` +
            `<td>${l.needs_review_count ? pill(l.needs_review_count, "warn") : ""}</td></tr>`
        )
        .join("") +
      "</table></div>"
    : emptyState("inbox", "No lectures yet.", "sources", "Add lectures");
  // lecture chips (preserve un-ticks across refreshes)
  const prevUnchecked = new Set(
    [...document.querySelectorAll("#run-lectures input:not(:checked)")].map((c) => c.value)
  );
  $("#run-lectures").innerHTML = lectures
    .map((l) => chip("run-lecture", l.id, `${l.id.replace("lecture", "L")} · ${l.title}`, !prevUnchecked.has(l.id)))
    .join("");
  renderLecturesSummary();
  // crop tool's lecture picker (only lectures with a video, stage 0 done)
  const withVideo = lectures.filter((l) => l.stages["0"]);
  $("#crop-lecture-pick").innerHTML = withVideo.length
    ? withVideo.map((l) => `<option value="${l.id}">${l.id} — ${l.title}</option>`).join("")
    : '<option value="">no videos fetched yet</option>';
  updateNavProgress(lastPreflightOk);
}

/* ---------- run ---------- */
// Kept so the mode toggle can retune the sampling interval without refetching.
let stageDefaults = null;

async function loadDefaults() {
  const s = await api("/settings");
  const d = s.stage_defaults;
  stageDefaults = d;
  $("#opt-crop").value = d.crop;
  $("#opt-threshold").value = d.threshold;
  $("#opt-interval").value = d.interval;
  $("#opt-ocr_lang").value = d.ocr_lang;
  $("#opt-min_dwell").value = d.min_dwell;
  $("#opt-mode").value = d.mode;
  $("#opt-visual_threshold").value = d.visual_threshold;
  $("#opt-visual_min_seconds").value = d.visual_min_seconds;
  syncModeOptions();
  // Stage chips, from the backend's stage registry (Phase 7 of the
  // cleanup plan) instead of a hardcoded list in index.html that had
  // drifted from it (e.g. "Match frames" vs "Match frames to slides").
  $("#run-stages").innerHTML = s.stages
    .map((st) => chip("run-stage", st.number, `${st.number} · ${st.name}`, true))
    .join("");
  renderStagesSummary();
}
// The visual-mode knobs only mean anything in visual mode; hide them in deck
// mode rather than showing inputs that are silently ignored.
function syncModeOptions() {
  $("#visual-opts").hidden = $("#opt-mode").value !== "visual";
}
// Switching mode also retunes stage 3's sampling interval, because the two
// modes want genuinely different resolutions: deck mode samples finely so a
// quick slide flip isn't missed, while visual mode won't emit a segment
// shorter than 45s and so pays for ~3x more OCR than it can use. Written into
// the visible field rather than applied invisibly at submit time, so it stays
// an editable default rather than magic.
function syncIntervalForMode(defaults) {
  const visual = $("#opt-mode").value === "visual";
  $("#opt-interval").value = visual ? defaults.visual_interval : defaults.interval;
}
$("#opt-mode").addEventListener("change", () => {
  syncModeOptions();
  if (stageDefaults) syncIntervalForMode(stageDefaults);
});

onClickBusy($("#btn-start"), async () => {
  const lecture_ids = [...document.querySelectorAll("#run-lectures input:checked")].map((c) => c.value);
  const stages = [...document.querySelectorAll("#run-stages input:checked")].map((c) => +c.value);
  const options = {
    crop: $("#opt-crop").value, threshold: $("#opt-threshold").value,
    interval: $("#opt-interval").value, ocr_lang: $("#opt-ocr_lang").value,
    min_dwell: $("#opt-min_dwell").value, mode: $("#opt-mode").value,
    visual_threshold: $("#opt-visual_threshold").value,
    visual_min_seconds: $("#opt-visual_min_seconds").value,
  };
  $("#run-status").textContent = "";
  try {
    await api("/jobs", { method: "POST", body: { lecture_ids, stages, options, force: $("#run-force").checked } });
    $("#job-log").textContent = "";
    pollJob();
    connectSSE();
  } catch (e) {
    $("#run-status").innerHTML = errorBanner(e.message);
  }
});
$("#btn-cancel").addEventListener("click", () => api("/jobs/current/cancel", { method: "POST" }).catch(() => {}));

// Stages with no percent signal (webui/progress.py's own comment: "2/5/7
// are quick: indeterminate spinner") — labeled so a bare progress bar
// doesn't read as stuck (FRONTEND_TODO.md P2).
const INDETERMINATE_STAGES = new Set([2, 5, 7]);

let es = null;
function connectSSE() {
  if (es) es.close();
  // EventSource can't set an Authorization header, so pass the token (if
  // any) as a query param -- the server accepts either for this endpoint.
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  es = new EventSource("/api/jobs/current/events" + qs);
  es.onmessage = (m) => {
    const evt = JSON.parse(m.data);
    if (evt.type === "log") appendLog(evt.line);
    if (evt.type === "progress" || evt.type === "stage_start" || evt.type === "stage_done") schedulePoll();
    if (evt.type === "job_done") { schedulePoll(); es.close(); es = null; refreshState(); }
  };
}

// The SSE stream is just a "something changed" ping, one per progress tick
// -- and real ticks are frequent: one per whisper segment (hundreds for a
// single lecture), one per detected/OCR'd frame, one per generated slide
// note. Naively calling pollJob() per ping meant a full GET
// /api/jobs/current per ping -- ~900 GETs for one lecture's worth of
// transcription alone, confirmed against this project's own real
// lecture01 data (764 whisper segments + 62 frame events + 62 OCR'd
// frames + 23 notes). schedulePoll() coalesces a burst of pings into one
// in-flight request plus, if more pings arrived while it was in flight or
// during a short cooldown after, exactly one follow-up -- so the UI still
// reflects the latest state promptly without a GET per ping.
const POLL_COOLDOWN_MS = 400;
let pollInFlight = false;
let pollAgainAfter = false;
let pollCooldownTimer = null;
function schedulePoll() {
  if (pollInFlight || pollCooldownTimer) {
    pollAgainAfter = true;
    return;
  }
  runScheduledPoll();
}
function runScheduledPoll() {
  pollInFlight = true;
  pollJob().finally(() => {
    pollInFlight = false;
    pollCooldownTimer = setTimeout(() => {
      pollCooldownTimer = null;
      if (pollAgainAfter) {
        pollAgainAfter = false;
        runScheduledPoll();
      }
    }, POLL_COOLDOWN_MS);
  });
}
function appendLog(line) {
  const el = $("#job-log");
  el.textContent += line + "\n";
  if (el.textContent.length > 200000) el.textContent = el.textContent.slice(-150000);
  el.scrollTop = el.scrollHeight;
}
async function pollJob() {
  const { job, busy } = await api("/jobs/current");
  $("#btn-cancel").hidden = !busy;
  $("#btn-start").disabled = busy;
  if (!job) { $("#job-view").innerHTML = ""; return; }
  $("#job-view").innerHTML =
    `<div class="card"><strong>Job ${job.id}</strong> — ${job.status}` +
    job.tasks
      .map((t) => {
        const indeterminate = t.status === "running" && t.percent == null;
        return `<div class="task"><span class="name">${t.lecture_id || "all"} · ${t.stage_name}</span>` +
          (t.status === "running" && t.percent != null
            ? `<progress max="100" value="${t.percent}"></progress><span>${t.percent}%</span>`
            : indeterminate
            ? `<progress></progress><span class="indeterminate-note">${INDETERMINATE_STAGES.has(t.stage) ? "usually seconds" : "working…"}</span>`
            : "") +
          `<span class="status-${t.status}">${t.status}${t.detail ? " — " + t.detail : ""}</span></div>`;
      })
      .join("") +
    "</div>";
  if (busy && !es) connectSSE();
  if (!busy && job.status === "done") {
    $("#job-view").innerHTML += okBanner("Job finished.") + nextStep(
      job.tasks.some((t) => t.stage === 4) ? "Worth a look before trusting the notes." : "Notes are ready.",
      job.tasks.some((t) => t.stage === 4) ? "review" : "guide",
      job.tasks.some((t) => t.stage === 4) ? "Review matches" : "Read the guide"
    );
  }
}

/* ---------- crop tool: drag a rectangle over a real video frame instead
   of hand-typing four fractions blind (FRONTEND_TODO.md P3 — the hardest
   single UX problem in the app). ---------- */
let cropPreviewObjectUrl = null;
onClickBusy($("#btn-load-preview"), async () => {
  const lectureId = $("#crop-lecture-pick").value;
  if (!lectureId) { $("#crop-preview-status").textContent = "No lecture with a fetched video yet."; return; }
  $("#crop-preview-status").innerHTML = statusLine("Extracting a frame…");
  try {
    const r = await fetch(`/api/lectures/${lectureId}/preview-frame`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const blob = await r.blob();
    if (cropPreviewObjectUrl) URL.revokeObjectURL(cropPreviewObjectUrl);
    cropPreviewObjectUrl = URL.createObjectURL(blob);
    const img = $("#crop-img");
    img.onload = () => initCropRect();
    img.src = cropPreviewObjectUrl;
    $("#crop-tool").hidden = false;
    $("#crop-preview-status").textContent = "Drag the box to the slide region.";
  } catch (e) {
    $("#crop-preview-status").innerHTML = errorBanner(e.message);
  }
});

function parseCrop(str) {
  const parts = (str || "").split(",").map((s) => parseFloat(s));
  if (parts.length === 4 && parts.every((n) => !isNaN(n))) return parts;
  return [0.12, 0.06, 0.63, 0.88]; // this course's validated default, a reasonable starting box regardless
}

function initCropRect() {
  const frame = $("#crop-frame"), rect = $("#crop-rect"), img = $("#crop-img");
  const [x, y, w, h] = parseCrop($("#opt-crop").value);
  const setBox = (fx, fy, fw, fh) => {
    const W = img.clientWidth, H = img.clientHeight;
    rect.style.left = fx * W + "px"; rect.style.top = fy * H + "px";
    rect.style.width = fw * W + "px"; rect.style.height = fh * H + "px";
  };
  setBox(x, y, w, h);
  updateCropReadout();

  const clamp01 = (n) => Math.max(0, Math.min(1, n));

  function dragMove(startEvent, mode) {
    startEvent.preventDefault();
    const W = img.clientWidth, H = img.clientHeight;
    const startRect = rect.getBoundingClientRect(), frameRect = frame.getBoundingClientRect();
    const startX = startEvent.clientX, startY = startEvent.clientY;
    const startLeft = startRect.left - frameRect.left, startTop = startRect.top - frameRect.top;
    const startW = startRect.width, startH = startRect.height;

    function onMove(e) {
      const dx = e.clientX - startX, dy = e.clientY - startY;
      if (mode === "move") {
        const nl = clamp01((startLeft + dx) / W) * W;
        const nt = clamp01((startTop + dy) / H) * H;
        rect.style.left = Math.min(nl, W - startW) + "px";
        rect.style.top = Math.min(nt, H - startH) + "px";
      } else {
        const nw = Math.max(20, Math.min(startW + dx, W - startLeft));
        const nh = Math.max(20, Math.min(startH + dy, H - startTop));
        rect.style.width = nw + "px";
        rect.style.height = nh + "px";
      }
      updateCropReadout();
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // Mouse events, not Pointer Events: this tool is desktop-oriented (tuning
  // a crop region is a mouse/trackpad task), and plain mouse events are
  // simpler and more universally reliable to dispatch/test than Pointer
  // Events turned out to be.
  rect.onmousedown = (e) => { if (e.target === rect) dragMove(e, "move"); };
  $("#crop-handle").onmousedown = (e) => { e.stopPropagation(); dragMove(e, "resize"); };
}

function updateCropReadout() {
  const frame = $("#crop-frame"), rect = $("#crop-rect"), img = $("#crop-img");
  if (!img.clientWidth) return;
  const W = img.clientWidth, H = img.clientHeight;
  const fx = parseFloat(rect.style.left) / W, fy = parseFloat(rect.style.top) / H;
  const fw = parseFloat(rect.style.width) / W, fh = parseFloat(rect.style.height) / H;
  const str = [fx, fy, fw, fh].map((n) => n.toFixed(3)).join(",");
  $("#opt-crop").value = str;
  $("#crop-readout").textContent = `--crop "${str}"`;
}

/* ---------- review ---------- */
let corrections = {};
let reviewLow = [], reviewSlideByNum = {}, reviewIndex = 0;

// A bare "score 0.21" gives a new user nothing to go on without already
// knowing stage 4's scoring scale -- a small toned bar communicates "how
// sure" at a glance (FRONTEND_TODO.md Round 3 P1). Thresholds are
// relative within this list's own range: everything shown here already
// cleared stage 4's own confidence_threshold as "low," so this is about
// distinguishing worse from less-bad within that band, not an absolute
// good/bad line.
function confidenceMeter(score) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const tone = score < 0.1 ? "err" : score < 0.2 ? "warn" : "ok";
  return `<span class="confidence"><span class="confidence-bar"><span class="tone-${tone}" style="width:${pct}%"></span></span>${score.toFixed(2)}</span>`;
}

// Compact tag list instead of a comma-joined paragraph (FRONTEND_TODO.md
// Round 3 P1: "slides never shown" runs 60-80+ items on this course's
// real pooled-deck data). Full list kept in a Map instead of a data-
// attribute so it never needs HTML-escaping through an attribute value.
const tagListFullHtml = new Map();
function renderTagList(items, limit = 24) {
  if (!items.length) return "";
  const tag = (t) => `<span class="tag">${t}</span>`;
  if (items.length <= limit) return `<div class="tag-list">${items.map(tag).join("")}</div>`;
  const id = "taglist-" + Math.random().toString(36).slice(2, 8);
  tagListFullHtml.set(id, items.map(tag).join(""));
  return `<div class="tag-list" id="${id}">${items.slice(0, limit).map(tag).join("")}` +
    `<button type="button" class="tag show-more-tags" data-target="${id}">+${items.length - limit} more</button></div>`;
}
document.addEventListener("click", (e) => {
  const moreTags = e.target.closest(".show-more-tags");
  if (moreTags) {
    const html = tagListFullHtml.get(moreTags.dataset.target);
    if (html) $("#" + moreTags.dataset.target).innerHTML = html;
  }
});

async function populateReviewLectures() {
  if (!stateCache) await refreshState();
  const withMatches = stateCache.lectures.filter((l) => l.stages["4"]);
  $("#review-lecture").innerHTML = withMatches
    .map((l) => `<option value="${l.id}">${l.id} — ${l.title}${l.needs_review_count ? ` (${l.needs_review_count} flagged)` : ""}</option>`)
    .join("");
  // Placeholder until a lecture is actually loaded (FRONTEND_TODO.md
  // Round 3 P2: this was a blank <div>, not a designed empty state).
  if (!$("#review-body").innerHTML.trim()) {
    $("#review-body").innerHTML = withMatches.length
      ? emptyState("inbox", "Pick a lecture above and hit Load to review its matches.")
      : emptyState("inbox", "No lecture has reached stage 4 (slide matching) yet.", "run", "Go run the pipeline");
  }
}
onClickBusy($("#btn-load-review"), loadReview);
async function loadReview() {
  const id = $("#review-lecture").value;
  if (!id) return;
  corrections = {};
  reviewIndex = 0;
  $("#review-status").textContent = "";
  $("#review-body").innerHTML = statusLine("Loading…");
  const d = await api("/review/" + id);
  reviewSlideByNum = Object.fromEntries(d.slides.map((s) => [s.slide_number, s]));
  reviewLow = d.review.low_confidence_matches || [];
  const un = d.review.unmatched_slides || [];
  const back = d.review.backward_jumps || [];

  let html = `<h3>Uncertain matches ${pill(reviewLow.length, reviewLow.length ? "warn" : "ok")}</h3>`;
  if (!reviewLow.length) {
    html += okBanner("Nothing flagged.");
  } else {
    // One item at a time instead of a long scroll -- real lectures on
    // this course have 100+ flagged items (FRONTEND_TODO.md Round 3 P3).
    html += `<div class="review-pager">
      <button type="button" id="review-prev">${icon("chevronLeft")} Prev</button>
      <span class="position" id="review-position"></span>
      <button type="button" id="review-next">Next ${icon("chevronRight")}</button>
    </div>
    <div class="review-dots" id="review-dots"></div>
    <div id="review-item-slot"></div>`;
  }
  html += `<h3>Slides never shown ${pill(un.length)}</h3>
    <p class="hint">Usually fine — the deck covers more lectures than this one video.</p>
    ${renderTagList(un.map((s) => `#${s.slide_number} ${s.title || ""}`))}`;
  html += `<h3>Backward jumps ${pill(back.length)}</h3>
    <p class="hint">The professor going back to an earlier slide — informational.</p>`;
  $("#review-body").innerHTML = html;
  $("#review-apply-row").hidden = !reviewLow.length;

  if (reviewLow.length) {
    $("#review-prev").addEventListener("click", () => { reviewIndex = Math.max(0, reviewIndex - 1); renderReviewItem(); });
    $("#review-next").addEventListener("click", () => { reviewIndex = Math.min(reviewLow.length - 1, reviewIndex + 1); renderReviewItem(); });
    renderReviewDots();
    renderReviewItem();
  }
}

function renderReviewDots() {
  $("#review-dots").innerHTML = reviewLow.map((_, i) =>
    `<button type="button" class="review-dot${i === reviewIndex ? " current" : ""}${corrections[i] ? " done" : ""}" data-i="${i}" title="Item ${i + 1}"></button>`
  ).join("");
  $("#review-dots").querySelectorAll(".review-dot").forEach((b) =>
    b.addEventListener("click", () => { reviewIndex = +b.dataset.i; renderReviewItem(); })
  );
}

function renderReviewItem() {
  const i = reviewIndex;
  const item = reviewLow[i];
  const slide = reviewSlideByNum[item.slide_number];
  $("#review-position").textContent = `${i + 1} of ${reviewLow.length}`;
  $("#review-prev").disabled = i === 0;
  $("#review-next").disabled = i === reviewLow.length - 1;
  $("#review-item-slot").innerHTML = `<div class="review-item${corrections[i] ? " corrected" : ""}" data-ts="${item.timestamp}">
    <div><strong>Video frame @ ${fmtDur(item.timestamp)}</strong><br><img src="${item.frame_url}" loading="lazy"></div>
    <div><strong>Matched: slide ${item.slide_number}</strong> ${confidenceMeter(item.score)}<br>
      ${slide ? `<img src="${slide.image}" loading="lazy">` : ""}</div>
    <div><div class="meta">OCR read: “${(item.ocr_excerpt || "").slice(0, 120)}”</div>
      <div class="slide-pick">Correct slide #:
        <input type="number" min="1" class="input-xs pick-num" placeholder="${item.slide_number}">
        <button type="button" class="pick-ok">Set</button>
        <button type="button" class="pick-drop">Not a slide</button>
      </div></div></div>`;
  $("#review-item-slot .pick-ok").addEventListener("click", () => {
    const v = $("#review-item-slot .pick-num").value;
    if (!v) return;
    corrections[i] = { timestamp: item.timestamp, slide_number: +v };
    afterReviewCorrection();
  });
  $("#review-item-slot .pick-drop").addEventListener("click", () => {
    corrections[i] = { timestamp: item.timestamp, slide_number: null };
    afterReviewCorrection();
  });
}
// Mark done, then auto-advance -- "decide, advance" is the whole point of
// the one-at-a-time pager (FRONTEND_TODO.md Round 3 P3).
function afterReviewCorrection() {
  $("#review-item-slot .review-item").classList.add("corrected");
  renderReviewDots();
  if (reviewIndex < reviewLow.length - 1) {
    reviewIndex++;
    renderReviewItem();
  }
}
onClickBusy($("#btn-apply-review"), async () => {
  const id = $("#review-lecture").value;
  const list = Object.values(corrections);
  if (!list.length) { $("#review-status").textContent = "No corrections chosen."; return; }
  try {
    const r = await api("/review/" + id, { method: "POST", body: { corrections: list } });
    $("#review-status").innerHTML = okBanner(`Applied ${r.applied} — rebuilding notes…`);
    showTab("run");
    connectSSE();
    pollJob();
  } catch (e) {
    $("#review-status").innerHTML = errorBanner(e.message);
  }
});

/* ---------- guide ---------- */
$("#btn-refresh-guide").addEventListener("click", loadGuide);

// Rendering (markdown -> HTML, math-stashing, image-path rewriting) is
// server-side now (notely.pipeline.export.render_guide_html, Phase 7 of
// the cleanup plan) -- this used to duplicate that logic client-side with
// marked.js and its own regex, so the on-screen preview and the exported
// PDF (markdown_to_html) could silently drift apart. MathJax still runs
// here, typesetting the $...$/$$...$$ the server left untouched in the
// HTML it sent.
async function loadGuide() {
  const g = await api("/guide");
  $("#guide-download").hidden = !g.exists;
  $("#guide-pdf").hidden = !g.exists;
  const body = $("#guide-body");
  if (!g.exists) {
    body.innerHTML = emptyState("inbox", "Nothing assembled yet.", "run", "Go run the pipeline");
    return;
  }
  body.innerHTML = g.html;
  try {
    await window.MathJax?.typesetPromise?.([body]);
  } catch (e) {
    console.warn("MathJax typesetting failed (offline? CDN blocked?):", e);
  }
}

/* ---------- boot ---------- */
(async function boot() {
  await refreshState().catch(() => {});
  await loadDefaults().catch(() => {});
  const s = await api("/settings").catch(() => null);
  // first-run: no lectures and no key -> setup; otherwise land on run
  if (s && !s.settings.has_api_key && !(stateCache && stateCache.lectures.length)) showTab("setup");
  else if (!(stateCache && stateCache.lectures.length)) showTab("sources");
  else showTab("run");
  if (stateCache && stateCache.busy) { showTab("run"); connectSSE(); }
})();
