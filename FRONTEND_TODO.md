# Notely — frontend/UX to-do list

Grounded in a full read of `webui/static/{index.html,style.css,app.js}` as
they exist today (2026-08-12), not generic web-design advice. Each item
says *why*, with a reference into the actual code.

## The constraint, stated up front

"Keep it simple" here means: **stay vanilla JS + CSS, no build step, no
framework.** That's not just your preference — it's this project's own
documented architecture (`DOCUMENTATION.md` §2.2: "FastAPI + vanilla JS
single page (no build step)"), chosen deliberately for a tool students run
locally with zero setup. Nothing below requires React/Vue/a bundler/a CSS
framework. "Terrible UX" and "no build step" aren't in tension — the
current UI's problems are about *design system + interaction consistency*,
not about the lack of a framework.

## P0 — there is no design system, and that's the root cause of most of this

Everything below this line will look inconsistent no matter how carefully
it's built until this exists, because right now every rule invents its own
numbers: spacing alone uses `.05rem .1rem .15rem .2rem .25rem .3rem .35rem
.4rem .45rem .5rem .6rem .7rem .8rem .9rem 1rem 1.2rem 1.4rem 1.6rem` —
18 distinct values with no discernible scale (`style.css`). Fix the
foundation first:

- [ ] **Define a real spacing scale** (e.g. 4/8/12/16/24/32px as CSS custom
      properties: `--sp-1` through `--sp-6`) and migrate every hardcoded
      `.Xrem` value in `style.css` onto it. This alone will make the page
      feel designed instead of assembled.
- [ ] **Define a type scale** (3-4 sizes, not the current ad hoc
      `1.15rem` / `1.05rem` / `.95rem` / `.9rem` / `.88rem` / `.85rem` /
      `.8rem` / `.78rem`). `h1` (1.15rem) and `h2` (1.05rem) are currently
      so close in size they barely read as a hierarchy at all.
- [ ] **Expand the color system beyond 5 flat variables.** Right now
      `--accent` alone carries active-tab, primary-button, chip-selected,
      and link-equivalent duties — one color doing four jobs means nothing
      stands out as *the* important action on a page. Add at minimum:
      a muted/secondary text color distinct from `--fg`, a subtle
      hover/pressed state for the accent, and a proper border/divider
      color distinct from `--line` for nested contexts (cards inside
      cards, e.g. `.review-item` sitting inside the Review tab).
- [ ] **Add dark mode.** Zero support today — `:root` hardcodes light
      values with no `prefers-color-scheme` branch at all. Students keep
      this tab open for the length of a lecture; a forced-light page next
      to whatever else they're running is a real, common complaint this
      cheaply fixes.
- [ ] **Stop styling from markup.** `index.html` has at least one inline
      style (`style="margin-top:.4rem"` on the force-rerun checkbox) and a
      raw HTML `size="18"`/`size="6"` attribute on several `<input>`s
      instead of a CSS width rule. Once the spacing scale exists, these
      become `.chip.gap-top` and a `.input-sm`/`.input-md` class.
- [ ] **Add small reusable component helpers in `app.js`**, not more
      hand-built template-literal HTML per view. Right now `.pill`,
      status coloring, and chip rendering are each reimplemented slightly
      differently in `loadSetup`, `renderPlaylist`, `refreshState`,
      `loadReview`, and the deck-mapping renderer — which is *why* the
      three-different-multi-select-patterns problem below happened in the
      first place. A `chip(label, checked)`, `pill(text, tone)`, and
      `statusBadge(status)` helper, used everywhere, keeps future changes
      from re-diverging.

## P1 — the workflow itself doesn't guide anyone

The app's own README describes it as "follow the tabs left to right," but
nothing in the UI itself reinforces that — it's a flat row of 5 unranked
buttons (`<nav id="tabs">`).

- [ ] **Show workflow progress, not just a tab list.** Setup → Sources →
      Run → Review → Guide is a real sequence with real prerequisites
      (`app.js`'s own `boot()` function already computes "first-run: no
      lectures and no key" logic to decide where to land — that
      completion-awareness exists in the code, it's just not shown to the
      user). Surface it: a checkmark/number badge per tab reflecting
      whether it's done, not-yet-relevant, or needs attention.
- [ ] **Add "next step" calls to action** instead of expecting the user to
      know to click the next tab. Concretely: after `confirmLectures()`
      saves lectures, after a deck upload succeeds, after a job finishes —
      each currently just prints a status string and stops
      (`$("#playlist-result").innerHTML = "<p class='ok'>Saved..."` and
      nothing else). A one-line "→ Upload slide decks next" /
      "→ Go to Run" affordance closes the loop.
- [ ] **Surface Review's item count outside the Review tab.** Right now
      you only learn "3 matches need a look" by manually opening Review
      and calling `loadReview()`. A badge on the Review tab (like an
      inbox-unread-count) driven by the same `needs_review.json` data the
      tab already fetches would make this discoverable instead of hidden.
- [ ] **Consolidate the three different multi-select interaction
      patterns** into one. Today: lecture selection on the Run tab uses
      pill-shaped chips (`.chip` checkboxes styled as toggle buttons),
      deck→lecture mapping also uses chips, but playlist-entry selection
      uses a plain HTML table with checkboxes plus separate ↑/↓ reorder
      buttons (`renderPlaylist()`). Three visual languages for "pick some
      items from a list" in one app. Pick one (chips read better for
      short lists like lectures/decks; the playlist table probably stays
      a table since it needs reordering + more columns, but should at
      least borrow the same checkbox/selected styling).

## P2 — feedback and loading states read as broken, not slow

- [ ] **Loading states are bare text with no motion** ("Checking…",
      "Looking up…", "Merging decks…" — `app.js` throughout). During an
      actual multi-second wait (preflight checks hit 6 subprocesses;
      playlist lookup calls `yt-dlp`) a static string reads as frozen, not
      working. Add a minimal CSS-only spinner or pulsing-dot class, one
      shared component, applied everywhere a status string currently sits
      alone.
- [ ] **Prevent double-submission.** `$("#btn-save-settings")`,
      `$("#btn-expand")`, and `$("#btn-upload")`'s click handlers don't
      disable the button while their `await` is in flight — only
      `$("#btn-start")` does this correctly (via `pollJob()`'s
      `$("#btn-start").disabled = busy`). A fast double-click on "Look up"
      fires two playlist lookups; on "Save settings," two writes.
- [ ] **Errors are a color change on inline text, not a real error
      state.** Every `catch` block does the same thing:
      `$("#...").innerHTML = "<p class='err'>" + e.message + "</p>"` —
      raw exception text, no icon, no suggested next step, no way to
      retry from the same message. At minimum: a consistent error-banner
      component (icon + message + dismiss), and for known failure modes
      (e.g. playlist lookup failing because the URL isn't a playlist),
      catch and rephrase rather than surfacing the raw fetch error.
- [ ] **Explain indeterminate progress bars.** Stages 2/5/7 have no
      percent signal (`webui/progress.py`'s own comment: "stages 2/5/7 are
      quick: indeterminate spinner"), so `pollJob()` renders a bare
      `<progress></progress>` with no label. A student watching that with
      no percent and no explanation reasonably assumes it's stuck. Add
      static text like "not time-based — usually seconds" for those three
      stages specifically.
- [ ] **`#job-log`'s black terminal box is a jarring tonal shift** from
      the rest of the light, card-based UI (`#job-log { background: #111;
      color: #ddd }`). A monospace font is right for a log; a full
      inverted-theme panel dropped into an otherwise light page isn't a
      deliberate choice, it's a leftover. Either theme it to match the
      card system (light monospace panel with a subtle border) or commit
      to it deliberately as a "console" motif and extend that same
      treatment consistently (it currently doesn't appear anywhere else).

## P3 — two specific flows are hard UX problems, not polish

- [ ] **The crop-region field is four raw numbers with no visual
      feedback.** `Advanced (per-stage tuning)` exposes `Crop (x,y,w,h)`
      as a bare text input (e.g. `0.12,0.06,0.63,0.88`) — per
      `DOCUMENTATION.md` §4.1, getting this wrong ("defaults detected
      almost nothing") was a real, non-obvious tuning problem even for the
      person who built this. Expecting a student to hand-tune four
      fractions blind is the single hardest UX moment in the whole app.
      Replace it with a visual cropper: show one sampled frame from the
      video (stage 3 already saves these) with a draggable rectangle
      overlay, and derive the four numbers from where the user drags it.
- [ ] **Deck-to-lecture mapping doesn't scale past a handful of files.**
      `$("#deck-file")`'s change handler renders one `.deck-row` per
      uploaded file, each with a full `.chip-list` of *every* lecture as
      a checkbox (`webui/static/app.js`'s deck-mapping renderer) — with
      22 lectures and several decks, that's 22 checkboxes repeated per
      deck, no search/filter, no "select range" gesture. At minimum add a
      text filter over the lecture chips; consider whether pool mode
      (already built, "combine all decks") should be surfaced as the
      default suggestion once deck count crosses some threshold, since
      it sidesteps this problem entirely.

## P4 — accessibility and responsiveness gaps

- [ ] **`.review-item`'s 3-column grid doesn't respond to viewport width**
      (`grid-template-columns: 1fr 1fr 1.2fr`, no media query) — will
      overflow or crush on anything narrower than a laptop. Same for the
      plain `<table>`s (preflight checks, lecture table, playlist
      entries): no `overflow-x: auto` wrapper, so a narrow viewport forces
      horizontal page scroll rather than scrolling just the table.
- [ ] **Status is communicated by color alone in places** (`.task
      .status-done { color: var(--ok) }` etc., with only plain text next
      to it — no icon). Fine where text already disambiguates ("done" vs
      "failed" are readable words), worth a pass specifically on the
      preflight checklist's ✓/✗ (already has both symbol and color, good
      precedent — extend that pattern everywhere status is shown).
- [ ] **No `aria-live` on dynamically-updated regions** — `#job-log`,
      `#run-status`, `#upload-status`, `#settings-status` all get their
      content replaced via `textContent`/`innerHTML` with no live-region
      annotation, so a screen reader user gets no announcement when a job
      finishes, an upload completes, or an error appears.
- [ ] **No visible custom focus styling** — relying entirely on browser
      default outlines, which on the current flat/borderless button style
      (`button { border: 1px solid var(--line) }`, no focus-visible rule)
      can be hard to spot. Add an explicit `:focus-visible` treatment
      using the accent color once the color system above exists.

## P5 — smaller polish, worth doing once the above lands

- [ ] Replace the single emoji favicon/header icon (📚) with something
      more deliberate — even a simple inline SVG monogram would read as
      more finished than one emoji doing double duty as both browser-tab
      icon (there currently isn't even a real `<link rel="icon">`, just
      the emoji baked into `<h1>`) and app identity.
- [ ] Add subtle transitions on tab switches and card state changes
      (e.g. a corrected review item gaining `.corrected`'s outline) —
      currently instant/jarring; a 150ms ease is enough, no animation
      library needed.
- [ ] Give `.pill` more than one visual tone — right now every pill
      (uncertain-match counts, unmatched-slide counts, backward-jump
      counts on the Review tab) renders identically gray regardless of
      whether the number means "fine" or "needs attention."

---
Sources: full read of `webui/static/index.html`, `webui/static/style.css`,
`webui/static/app.js` as of 2026-08-12, cross-referenced against
`DOCUMENTATION.md` §2.2 for the architecture constraint this list respects.
