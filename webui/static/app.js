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
  const icon = t === "light" ? "☀" : t === "dark" ? "☾" : "◐";
  const label = t === "light" ? "Light" : t === "dark" ? "Dark" : "Auto";
  $("#theme-toggle").innerHTML = `<span class="theme-icon">${icon}</span>${label}`;
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
  return `<div class="banner tone-err"><span class="banner-icon">✕</span><span>${message}</span></div>`;
}
function okBanner(message) {
  return `<div class="banner tone-ok"><span class="banner-icon">✓</span><span>${message}</span></div>`;
}
function nextStep(message, tab, cta) {
  return `<div class="next-step"><span>${message}</span><button data-goto="${tab}">${cta} →</button></div>`;
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
    if (el) el.classList.toggle("step-done", done);
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
    $("#preflight").innerHTML =
      '<div class="table-wrap"><table>' +
      Object.entries(pf.checks)
        .map(([k, v]) => `<tr><td>${k}</td><td class="${v.ok ? "ok" : "warn"}">${v.ok ? "✓" : "✗"}</td><td>${v.detail || ""}</td></tr>`)
        .join("") +
      "</table></div>" +
      (pf.ok ? okBanner("Ready to run.") : `<div class="banner tone-warn"><span class="banner-icon">!</span><span>Some required tools are missing — the pipeline may fail.</span></div>`);
    lastPreflightOk = pf.ok;
    updateNavProgress(pf.ok);
    $("#setup-next").innerHTML = pf.ok ? nextStep("Environment looks good.", "sources", "Add lectures") : "";
  } catch (e) {
    $("#preflight").innerHTML = errorBanner("Preflight failed: " + e.message);
  }
  const s = await api("/settings");
  $("#set-whisper").value = s.settings.WHISPER_MODEL || "medium";
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
  if (!s) return "";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

$("#deck-file").addEventListener("change", async () => {
  const files = [...$("#deck-file").files];
  const lectures = stateCache ? stateCache.lectures : [];
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
    $("#deck-mapping").innerHTML = "";
    $("#btn-upload").hidden = true;
    $("#sources-next").innerHTML = nextStep("Decks are in.", "run", "Go to Run");
    await refreshState();
  }
});

let stateCache = null;
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
    : "No lectures yet.";
  // lecture chips (preserve un-ticks across refreshes)
  const prevUnchecked = new Set(
    [...document.querySelectorAll("#run-lectures input:not(:checked)")].map((c) => c.value)
  );
  $("#run-lectures").innerHTML = lectures
    .map((l) => chip("run-lecture", l.id, `${l.id.replace("lecture", "L")} · ${l.title}`, !prevUnchecked.has(l.id)))
    .join("");
  // crop tool's lecture picker (only lectures with a video, stage 0 done)
  const withVideo = lectures.filter((l) => l.stages["0"]);
  $("#crop-lecture-pick").innerHTML = withVideo.length
    ? withVideo.map((l) => `<option value="${l.id}">${l.id} — ${l.title}</option>`).join("")
    : '<option value="">no videos fetched yet</option>';
  updateNavProgress(lastPreflightOk);
}

/* ---------- run ---------- */
async function loadDefaults() {
  const s = await api("/settings");
  const d = s.stage_defaults;
  $("#opt-crop").value = d.crop;
  $("#opt-threshold").value = d.threshold;
  $("#opt-interval").value = d.interval;
  $("#opt-ocr_lang").value = d.ocr_lang;
  $("#opt-min_dwell").value = d.min_dwell;
}
onClickBusy($("#btn-start"), async () => {
  const lecture_ids = [...document.querySelectorAll("#run-lectures input:checked")].map((c) => c.value);
  const stages = [...document.querySelectorAll("#run-stages input:checked")].map((c) => +c.value);
  const options = {
    crop: $("#opt-crop").value, threshold: $("#opt-threshold").value,
    interval: $("#opt-interval").value, ocr_lang: $("#opt-ocr_lang").value,
    min_dwell: $("#opt-min_dwell").value,
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
    if (evt.type === "progress" || evt.type === "stage_start" || evt.type === "stage_done") pollJob();
    if (evt.type === "job_done") { pollJob(); es.close(); es = null; refreshState(); }
  };
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
async function populateReviewLectures() {
  if (!stateCache) await refreshState();
  $("#review-lecture").innerHTML = stateCache.lectures
    .filter((l) => l.stages["4"])
    .map((l) => `<option value="${l.id}">${l.id} — ${l.title}${l.needs_review_count ? ` (${l.needs_review_count} flagged)` : ""}</option>`)
    .join("");
}
onClickBusy($("#btn-load-review"), loadReview);
async function loadReview() {
  const id = $("#review-lecture").value;
  if (!id) return;
  corrections = {};
  $("#review-status").textContent = "";
  $("#review-body").innerHTML = statusLine("Loading…");
  const d = await api("/review/" + id);
  const slideByNum = Object.fromEntries(d.slides.map((s) => [s.slide_number, s]));
  const low = d.review.low_confidence_matches || [];
  const un = d.review.unmatched_slides || [];
  const back = d.review.backward_jumps || [];
  let html = `<h3>Uncertain matches ${pill(low.length, low.length ? "warn" : "ok")}</h3>`;
  if (!low.length) html += okBanner("Nothing flagged.");
  html += low
    .map((item, i) => {
      const slide = slideByNum[item.slide_number];
      return `<div class="review-item" data-ts="${item.timestamp}" id="ri-${i}">
        <div><strong>Video frame @ ${fmtDur(item.timestamp)}</strong><br><img src="${item.frame_url}" loading="lazy"></div>
        <div><strong>Matched: slide ${item.slide_number}</strong> <span class="meta">score ${item.score.toFixed(2)}</span><br>
          ${slide ? `<img src="${slide.image}" loading="lazy">` : ""}</div>
        <div><div class="meta">OCR read: “${(item.ocr_excerpt || "").slice(0, 120)}”</div>
          <div class="slide-pick">Correct slide #:
            <input type="number" min="1" class="input-xs pick-num" data-i="${i}" placeholder="${item.slide_number}">
            <button data-i="${i}" class="pick-ok">Set</button>
            <button data-i="${i}" class="pick-drop">Not a slide</button>
          </div></div></div>`;
    })
    .join("");
  html += `<h3>Slides never shown ${pill(un.length)}</h3>
    <p class="hint">Usually fine — the deck covers more lectures than this one video.</p>
    <p class="meta">${un.map((s) => `#${s.slide_number} ${s.title || ""}`).join(" · ")}</p>`;
  html += `<h3>Backward jumps ${pill(back.length)}</h3>
    <p class="hint">The professor going back to an earlier slide — informational.</p>`;
  $("#review-body").innerHTML = html;
  $("#review-apply-row").hidden = !low.length;
  document.querySelectorAll(".pick-ok").forEach((b) =>
    b.addEventListener("click", () => {
      const i = +b.dataset.i;
      const v = document.querySelector(`.pick-num[data-i="${i}"]`).value;
      if (!v) return;
      corrections[i] = { timestamp: low[i].timestamp, slide_number: +v };
      $("#ri-" + i).classList.add("corrected");
    })
  );
  document.querySelectorAll(".pick-drop").forEach((b) =>
    b.addEventListener("click", () => {
      const i = +b.dataset.i;
      corrections[i] = { timestamp: low[i].timestamp, slide_number: null };
      $("#ri-" + i).classList.add("corrected");
    })
  );
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

// Mirrors scripts/08_export_pdf.py::markdown_to_html: stash $...$/$$...$$
// before handing text to the markdown parser (otherwise LaTeX underscores
// like x_a get read as emphasis markers), restore after, then MathJax
// typesets the restored math in place.
function renderGuideMarkdown(mdText) {
  const stash = [];
  const guarded = mdText.replace(/\$\$[\s\S]*?\$\$|\$[^$\n]+\$/g, (m) => {
    stash.push(m);
    return ` MATH${stash.length - 1} `;
  });
  let html = marked.parse(guarded);
  html = html.replace(/ MATH(\d+) /g, (_, i) => stash[Number(i)]);
  // study_guide.md's image paths are relative to output/, which is what
  // /files/ is mounted at (webui/main.py) — same root stage 08 resolves
  // relative paths against for the PDF.
  html = html.replace(/(src|href)="(?!https?:|\/|data:)([^"]*)"/g, (_, attr, p) => `${attr}="/files/${p}"`);
  return html;
}

async function loadGuide() {
  const g = await api("/guide");
  $("#guide-download").hidden = !g.exists;
  $("#guide-pdf").hidden = !g.exists;
  const body = $("#guide-body");
  if (!g.exists) {
    body.textContent = "Nothing assembled yet — run the pipeline first.";
    return;
  }
  body.innerHTML = renderGuideMarkdown(g.markdown);
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
