/* Notely web UI — vanilla JS, no build step. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const api = async (path, opts = {}) => {
  const r = await fetch("/api" + path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

/* ---------- tabs ---------- */
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab))
);
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "setup") loadSetup();
  if (name === "sources") refreshState();
  if (name === "run") { refreshState(); pollJob(); }
  if (name === "review") populateReviewLectures();
  if (name === "guide") loadGuide();
}

/* ---------- setup ---------- */
async function loadSetup() {
  try {
    const pf = await api("/preflight");
    $("#preflight").innerHTML =
      "<table>" +
      Object.entries(pf.checks)
        .map(([k, v]) => `<tr><td>${k}</td><td class="${v.ok ? "ok" : "warn"}">${v.ok ? "✓" : "✗"}</td><td>${v.detail || ""}</td></tr>`)
        .join("") +
      "</table>" +
      (pf.ok ? '<p class="ok">Ready to run.</p>' : '<p class="warn">Some required tools are missing — the pipeline may fail.</p>');
  } catch (e) {
    $("#preflight").textContent = "Preflight failed: " + e.message;
  }
  const s = await api("/settings");
  $("#set-whisper").value = s.settings.WHISPER_MODEL || "medium";
  $("#set-ocr").value = s.settings.OCR_LANG || "";
  $("#set-key").placeholder = s.settings.has_api_key ? "saved (" + s.settings.ANTHROPIC_API_KEY + ")" : "sk-ant-…";
}
$("#btn-save-settings").addEventListener("click", async () => {
  const body = { WHISPER_MODEL: $("#set-whisper").value, OCR_LANG: $("#set-ocr").value };
  if ($("#set-key").value.trim()) body.ANTHROPIC_API_KEY = $("#set-key").value.trim();
  await api("/settings", { method: "PUT", body });
  $("#set-key").value = "";
  $("#settings-status").textContent = "Saved ✓";
  loadSetup();
});
$("#btn-test-key").addEventListener("click", async () => {
  $("#settings-status").textContent = "Testing…";
  const r = await api("/settings/test-key", { method: "POST" }).catch((e) => ({ ok: false, error: e.message }));
  $("#settings-status").textContent = r.ok ? "Key works ✓" : "Key failed: " + (r.error || "");
});

/* ---------- sources ---------- */
let pendingEntries = [];
$("#btn-expand").addEventListener("click", async () => {
  const url = $("#playlist-url").value.trim();
  if (!url) return;
  $("#playlist-result").textContent = "Looking up…";
  try {
    const r = await api("/playlist/expand", { method: "POST", body: { url } });
    pendingEntries = r.entries;
    renderPlaylist();
  } catch (e) {
    $("#playlist-result").innerHTML = `<p class="err">${e.message}</p>`;
  }
});
function renderPlaylist() {
  $("#playlist-result").innerHTML =
    "<table><tr><th></th><th>#</th><th>Title</th><th>Length</th><th>Move</th></tr>" +
    pendingEntries
      .map(
        (e, i) =>
          `<tr><td><input type="checkbox" ${e.excluded ? "" : "checked"} data-i="${i}"></td>` +
          `<td>${i + 1}</td><td>${e.title}</td><td>${fmtDur(e.duration)}</td>` +
          `<td><button data-i="${i}" class="mv-up" ${i === 0 ? "disabled" : ""}>↑</button>` +
          `<button data-i="${i}" class="mv-dn" ${i === pendingEntries.length - 1 ? "disabled" : ""}>↓</button></td></tr>`
      )
      .join("") +
    '</table><div class="row"><button class="primary" id="btn-confirm-lectures">Save as lecture01…NN (in this order)</button></div>' +
    '<p class="hint">Untick anything that isn\'t a lecture (intros, announcements); use ↑↓ if the playlist isn\'t in course order. Top-to-bottom = study-guide order.</p>';
  $("#btn-confirm-lectures").addEventListener("click", confirmLectures);
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
  $("#playlist-result").innerHTML = `<p class="ok">Saved ${checked.length} lectures ✓</p>`;
  refreshState();
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
  const render = (guesses, notes) =>
    ($("#deck-mapping").innerHTML = files
      .map(
        (f, i) => `<div class="deck-row" data-i="${i}">
          <div><strong>${f.name}</strong> <span class="hint">${notes[i] || ""}</span></div>
          <div class="hint">Covers which lecture(s)? Tick all that apply:</div>
          <div class="chip-list">
            ${lectures
              .map(
                (l) => `<label class="chip"><input type="checkbox" class="deck-map-chk"
                  data-i="${i}" value="${l.id}" ${guesses[i].includes(l.id) ? "checked" : ""}>
                  ${l.id.replace("lecture", "L")} · ${l.title}</label>`
              )
              .join("")}
          </div></div>`
      )
      .join(""));

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

$("#btn-upload-pool").addEventListener("click", async () => {
  const files = [...$("#deck-file").files];
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  $("#upload-status").textContent = "Merging decks…";
  try {
    const r = await api("/slides/upload-pool", { method: "POST", body: fd });
    const dedupeNote = r.duplicate_pages_skipped > 0
      ? ` (${r.duplicate_pages_skipped} duplicate page(s) of ${r.scanned_pages} skipped)`
      : "";
    $("#upload-status").textContent =
      `Combined ${r.pool_decks.length} deck(s) into one ${r.merged_pages}-page deck${dedupeNote}, shared by ${r.lectures.length} lecture(s) ✓`;
    $("#deck-file").value = "";
    $("#deck-mapping").innerHTML = "";
    $("#btn-upload").hidden = $("#btn-upload-pool").hidden = true;
    refreshState();
  } catch (e) {
    $("#upload-status").textContent = "Failed: " + e.message;
  }
});

$("#btn-upload").addEventListener("click", async () => {
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
    $("#upload-status").textContent = `Uploading ${i + 1}/${files.length}…`;
    try {
      await api("/slides/upload", { method: "POST", body: fd });
      done++;
    } catch (e) {
      errors.push(`${files[i].name}: ${e.message}`);
    }
  }
  $("#upload-status").textContent =
    `Saved ${done}/${files.length} ✓` + (errors.length ? " — " + errors.join("; ") : "");
  if (done) {
    $("#deck-file").value = "";
    $("#deck-mapping").innerHTML = "";
    $("#btn-upload").hidden = true;
    refreshState();
  }
});

let stateCache = null;
async function refreshState() {
  stateCache = await api("/state");
  const { lectures } = stateCache;
  // lecture table
  $("#lecture-table").innerHTML = lectures.length
    ? "<table><tr><th>ID</th><th>Title</th><th>Deck</th><th>Stages 0–6</th></tr>" +
      lectures
        .map(
          (l) =>
            `<tr><td>${l.id}</td><td>${l.title}</td><td>${l.deck || '<span class="warn">missing</span>'}</td>` +
            `<td class="stage-dots">${Object.values(l.stages).map((ok) => `<span class="${ok ? "done" : ""}"></span>`).join("")}</td></tr>`
        )
        .join("") +
      "</table>"
    : "No lectures yet.";
  // lecture chips (preserve un-ticks across refreshes)
  const prevUnchecked = new Set(
    [...document.querySelectorAll("#run-lectures input:not(:checked)")].map((c) => c.value)
  );
  $("#run-lectures").innerHTML = lectures
    .map(
      (l) => `<label class="chip"><input type="checkbox" value="${l.id}" ${prevUnchecked.has(l.id) ? "" : "checked"}> ${l.id.replace("lecture", "L")} · ${l.title}</label>`
    )
    .join("");
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
$("#btn-start").addEventListener("click", async () => {
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
    $("#run-status").textContent = e.message;
  }
});
$("#btn-cancel").addEventListener("click", () => api("/jobs/current/cancel", { method: "POST" }).catch(() => {}));

let es = null;
function connectSSE() {
  if (es) es.close();
  es = new EventSource("/api/jobs/current/events");
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
      .map(
        (t) =>
          `<div class="task"><span class="name">${t.lecture_id || "all"} · ${t.stage_name}</span>` +
          (t.status === "running" && t.percent != null
            ? `<progress max="100" value="${t.percent}"></progress><span>${t.percent}%</span>`
            : t.status === "running"
            ? `<progress></progress>`
            : "") +
          `<span class="status-${t.status}">${t.status}${t.detail ? " — " + t.detail : ""}</span></div>`
      )
      .join("") +
    "</div>";
  if (busy && !es) connectSSE();
}

/* ---------- review ---------- */
let corrections = {};
async function populateReviewLectures() {
  if (!stateCache) await refreshState();
  $("#review-lecture").innerHTML = stateCache.lectures
    .filter((l) => l.stages[4])
    .map((l) => `<option value="${l.id}">${l.id} — ${l.title}</option>`)
    .join("");
}
$("#btn-load-review").addEventListener("click", loadReview);
async function loadReview() {
  const id = $("#review-lecture").value;
  if (!id) return;
  corrections = {};
  $("#review-status").textContent = "";
  const d = await api("/review/" + id);
  const slideByNum = Object.fromEntries(d.slides.map((s) => [s.slide_number, s]));
  const low = d.review.low_confidence_matches || [];
  const un = d.review.unmatched_slides || [];
  const back = d.review.backward_jumps || [];
  let html = `<h3>Uncertain matches <span class="pill">${low.length}</span></h3>`;
  if (!low.length) html += "<p class='ok'>Nothing flagged ✓</p>";
  html += low
    .map((item, i) => {
      const slide = slideByNum[item.slide_number];
      return `<div class="review-item" data-ts="${item.timestamp}" id="ri-${i}">
        <div><strong>Video frame @ ${fmtDur(item.timestamp)}</strong><br><img src="${item.frame_url}" loading="lazy"></div>
        <div><strong>Matched: slide ${item.slide_number}</strong> <span class="meta">score ${item.score.toFixed(2)}</span><br>
          ${slide ? `<img src="${slide.image}" loading="lazy">` : ""}</div>
        <div><div class="meta">OCR read: “${(item.ocr_excerpt || "").slice(0, 120)}”</div>
          <div class="slide-pick">Correct slide #:
            <input type="number" min="1" style="width:5rem" data-i="${i}" class="pick-num" placeholder="${item.slide_number}">
            <button data-i="${i}" class="pick-ok">Set</button>
            <button data-i="${i}" class="pick-drop">Not a slide</button>
          </div></div></div>`;
    })
    .join("");
  html += `<h3>Slides never shown <span class="pill">${un.length}</span></h3>
    <p class="hint">Usually fine — the deck covers more lectures than this one video.</p>
    <p class="meta">${un.map((s) => `#${s.slide_number} ${s.title || ""}`).join(" · ")}</p>`;
  html += `<h3>Backward jumps <span class="pill">${back.length}</span></h3>
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
$("#btn-apply-review").addEventListener("click", async () => {
  const id = $("#review-lecture").value;
  const list = Object.values(corrections);
  if (!list.length) { $("#review-status").textContent = "No corrections chosen."; return; }
  try {
    const r = await api("/review/" + id, { method: "POST", body: { corrections: list } });
    $("#review-status").textContent = `Applied ${r.applied} ✓ — rebuilding notes…`;
    showTab("run");
    connectSSE();
    pollJob();
  } catch (e) {
    $("#review-status").textContent = e.message;
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
    return ` MATH${stash.length - 1} `;
  });
  let html = marked.parse(guarded);
  html = html.replace(/ MATH(\d+) /g, (_, i) => stash[Number(i)]);
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
