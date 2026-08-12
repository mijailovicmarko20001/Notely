# Notely — frontend/UX to-do list

Grounded in a full read of `webui/static/{index.html,style.css,app.js}` as
they existed on 2026-08-12, not generic web-design advice. Every item below
was implemented the same day and verified with real, running-browser checks
(Playwright + a headless Chromium, screenshots actually looked at — not just
"the server returned 200") — see the verification note at the bottom for
what that caught that pure code review wouldn't have.

## The constraint, stated up front

"Keep it simple" here means: **stay vanilla JS + CSS, no build step, no
framework.** That's not just preference — it's this project's own
documented architecture (`DOCUMENTATION.md` §2.2: "FastAPI + vanilla JS
single page (no build step)"). Everything below is still plain CSS custom
properties + template-literal JS. No framework, no bundler, no new runtime
dependency (the one new *dev-time* dependency, Playwright, was used only to
verify this work and isn't part of the shipped app).

## P0 — design system (done)

- [x] **Spacing scale.** `--sp-1` (4px) through `--sp-7` (48px), every
      hardcoded `.Xrem` value in `style.css` migrated onto it.
- [x] **Type scale.** `--fs-xs` through `--fs-lg` (13/14/16/18/24px).
      `h1`/`h2` now clearly differ (24px vs 18px, plus weight/letter-spacing)
      instead of nearly matching at 1.15rem/1.05rem.
- [x] **Expanded color system.** Added `--fg-muted`/`--fg-subtle` (text
      hierarchy beyond the one `--fg`), `--accent-hover`/`--accent-soft`
      (so accent isn't doing active-tab/button/chip/link duty with zero
      state variation), `--line-strong` (nested-context borders), and
      `-soft` background variants of ok/warn/err for banners and toned
      pills.
- [x] **Dark mode, plus a manual toggle (added 2026-08-12, later the same
      day).** Originally shipped system-preference-only; the user's own
      system prefers dark, and asked for a way to get light mode
      regardless. Added a 3-state toggle in the header (Auto → Light →
      Dark → Auto), persisted in `localStorage`, applied via a tiny
      inline pre-paint script in `index.html`'s `<head>` so an explicit
      choice never flashes the system default first (verified: `data-
      theme` is already set in the DOM immediately after a reload, before
      `app.js` even runs). CSS pattern: light tokens stay on bare `:root`;
      the dark `@media` block is guarded with `:not([data-theme="light"])`
      so an explicit light choice can override a dark system preference;
      a separate `:root[data-theme="dark"]` block (same values) lets an
      explicit dark choice override a light system preference. Verified
      all three states (auto/forced-light/forced-dark) render the correct
      computed background color in a real browser, including across a
      page reload.
- [x] **Stopped styling from markup.** Removed the inline
      `style="margin-top:.4rem"` and the raw `size="18"`/`size="6"` input
      attributes; replaced with `.gap-top`/`.input-sm`/`.input-xs`/
      `.crop-value` classes.
- [x] **Shared component helpers in `app.js`.** `chip()`, `pill()`,
      `statusLine()`, `errorBanner()`/`okBanner()`, `nextStep()`, and
      `onClickBusy()` (see P2) — one implementation each, used from every
      tab instead of every view hand-rolling its own markup.

## P1 — workflow guidance (done)

- [x] **Workflow progress in the nav.** Each tab button now has a
      `.tab-step` badge that fills in green once that step is actually
      done, computed from real state in `updateNavProgress()` (preflight
      status, every lecture having a deck, every lecture having notes,
      the study guide existing) — not just a static 1-2-3-4-5.
- [x] **"Next step" callouts.** After preflight passes, after saving
      lectures, after a deck upload, after a job finishes — a
      `.next-step` banner with a button that jumps to the right tab,
      instead of a status string and silence.
- [x] **Review badge count, visible outside the Review tab.** New
      backend field (`webui/review.py::count_low_confidence`, wired into
      `/api/state`) reads each lecture's `needs_review.json` cheaply (no
      per-lecture fetch loop) and sums the actionable low-confidence
      count onto a badge on the Review tab itself. On this project's own
      real 22-lecture course data it correctly shows **817** — verified
      against the real files, not a mock.
- [x] **Multi-select patterns — partially consolidated, honestly.** Chips
      are now the one shared pattern for lecture selection and deck→lecture
      mapping (same `chip()` helper, same visual language). The playlist
      table stayed a table — it genuinely needs a different interaction
      (row reordering via ↑/↓, more columns) that chips don't fit — so
      this is "down to two patterns for two genuinely different needs,"
      not fully down to one. Said so rather than claiming more than was
      actually done.

## P2 — feedback and loading states (done)

- [x] **Loading spinners.** `statusLine()` (small CSS-only spinner +
      text), used everywhere a bare "Checking…"/"Looking up…" string used
      to sit alone.
- [x] **Double-submission guards.** `onClickBusy()` wraps essentially
      every async button handler (`btn-save-settings`, `btn-test-key`,
      `btn-expand`, `btn-confirm-lectures`, `btn-upload`,
      `btn-upload-pool`, `btn-load-review`, `btn-apply-review`,
      `btn-load-preview`, `btn-start`) — disables the button and shows a
      spinner-in-place-of-label for the duration of the call, can't fire
      twice from a fast double-click.
- [x] **Real error/success banners**, not colored inline text —
      `errorBanner()`/`okBanner()`, used in every `catch` block that used
      to just dump `e.message` into a paragraph.
- [x] **Indeterminate-progress explanation.** Stages 2/5/7 (no percent
      signal, per `progress.py`'s own comment) now show "usually seconds"
      next to their spinner instead of a bare unlabeled progress bar.
- [x] **`#job-log` re-themed** onto the same design tokens (`--card-nested`/
      `--fg`/`--line`) instead of a hardcoded `#111`/`#ddd` black box —
      still monospace, no longer a tonal clash with the rest of the page.
      Marked `role="log"` instead of a generic live region (the right
      ARIA role for an appending log, doesn't spam screen readers with
      every appended line the way `aria-live="polite"` would).

## P3 — the two hard UX problems (done)

- [x] **Visual crop-region picker — the single biggest addition.**
      New backend endpoint, `GET /api/lectures/{id}/preview-frame`
      (`webui/api.py`): grabs one real frame from the lecture's own video
      via ffmpeg, independent of stage 3 ever having run (crop is exactly
      the parameter stage 3 needs, so tuning it can't depend on stage 3's
      output). Frontend: pick a lecture, load its frame, **drag a
      rectangle directly on the real image** — the four fractions in
      `--crop "x,y,w,h"` are derived from where you drop it, never typed
      by hand.
      **Verified against this course's actual lecture01 frame** — genuinely
      showed the real Zoom recording (slide content plus the actual
      webcam participant tiles down the right side), and the default crop
      region visibly excludes exactly the participant panel, confirming
      the whole feature does what it's for. Both drag (move) and the
      resize handle were verified to produce correct, expected coordinate
      changes via a real running browser (see verification note below —
      this is also where a real implementation bug got caught and fixed).
- [x] **Deck-mapping filter for scale.** A text filter box (shown once
      files are picked and there are 8+ lectures) narrows each deck's
      lecture-chip list by id/title substring — verified against this
      project's real 22-lecture list. **Not done:** auto-suggesting pool
      mode past some lecture-count threshold — the filter alone was
      judged sufficient for now; revisit if 22-lecture-scale courses still
      find the per-deck chip list unwieldy even filtered.

## P4 — accessibility and responsiveness (done)

- [x] **Responsive `.review-item` grid** — collapses to one column under
      800px instead of crushing a fixed 3-column grid.
- [x] **All tables wrapped in `.table-wrap`** (`overflow-x: auto`) —
      preflight checks, lecture table, playlist entries — so a narrow
      viewport scrolls the table, not the whole page.
- [x] **Status-by-color extended, not just left alone.** The preflight
      table's ✓/✗-plus-color pattern (already accessible) is now the
      template banners/pills follow too — every banner has an icon
      (✓/✕/!) alongside its color, not color alone.
- [x] **`aria-live="polite"` added** to every region that gets replaced
      dynamically with a short status update (preflight, settings/upload/
      run/review status lines, playlist result, deck mapping, job view).
      The log got `role="log"` instead (see P2) — the correct choice for
      an appending stream, not `aria-live`.
- [x] **`:focus-visible` styling** — a real 2px accent-colored outline,
      instead of relying on the browser default against a mostly-flat,
      borderless button style.

## P5 — polish (done)

- [x] **Favicon + brand mark.** Real `<link rel="icon">` (inline SVG data
      URI, no separate asset file needed — "N" monogram on the accent
      color), plus a small `.brand-mark` square next to the wordmark in
      the header. Emoji retired.
- [x] **Transitions.** Tab switches fade+lift in (150ms), buttons/chips
      get a hover transition instead of an instant color snap, corrected
      review items' outline transitions in rather than popping.
- [x] **Toned pills.** `.pill` now takes `tone-ok`/`tone-warn`/`tone-err`
      — review-count pills render amber/warm instead of the same flat
      gray a "0 flagged" pill would use.

## Post-ship fix: dark-mode active-tab contrast (2026-08-12)

The user sent a screenshot of the dark-mode nav and said it "isn't really
nice to look at." Rendered it myself before guessing at a fix: the active
tab's highlight (`--accent-soft`, the pill background behind the current
tab) was `#262b47` against a `--card` background of `#202126` — close
enough in luminance that the "active" state read as muddy instead of
crisp, unlike the light-mode version of the same pill which pops cleanly
against white. Brightened `--accent-soft` (and audited/brightened the
other three `-soft` tokens the same way, since they had the same
card-contrast problem waiting to surface elsewhere) to `#2f376c`/etc. —
verified side by side, in a real browser, before and after.

---

## What real verification caught (worth keeping as a record)

Structural checks alone — HTML validity, CSS brace balance, JS syntax,
every DOM id `app.js` references cross-checked against `index.html`, a
full backend test-suite run, curl smoke tests of every new/changed
endpoint — all passed clean and gave false confidence. They could not
have caught the actual bug that mattered:

**`button, .button { display: inline-flex }` (needed for icon+label
layout) silently defeated every `hidden` attribute in the app.** Per the
CSS cascade, any author-stylesheet rule that sets `display` on an element
overrides the browser's built-in UA-stylesheet `[hidden] { display: none }`
rule, *regardless of selector specificity* — author rules simply outrank
UA rules. `element.hidden` still read `true` in the DOM (so a DOM-property
check would have reported success), but `getComputedStyle(el).display`
was `flex`, and the element rendered fully visible. This affected `#btn-
cancel`, `#btn-upload`, `#btn-upload-pool`, `#deck-filter-row`, `#review-
apply-row`, `#guide-download`, `#guide-pdf` — essentially every
conditionally-shown element introduced or touched in this pass.

Found by actually launching the app with Playwright + headless Chromium,
taking a real screenshot, and looking at it — a "hidden" Cancel button
sitting right next to Start was visually obvious in a way no amount of
code review or DOM-property assertion would have surfaced. Fixed with one
rule: `[hidden] { display: none !important; }`, placed once near the top
of `style.css` with a comment explaining why it's there so it doesn't get
"cleaned up" as redundant-looking later.

The crop tool's drag interaction was verified the same way, and caught a
second, smaller lesson: `page.mouse` (Playwright's simulated mouse)
operates in real viewport coordinates, so an element below the fold
silently can't be dragged by it — the fix was in the *test*, not the app
(the app's own mouse-event handling was confirmed correct via direct
in-page event dispatch, which doesn't care about scroll position, before
the viewport issue was even found).

---
Sources: full read of `webui/static/index.html`, `webui/static/style.css`,
`webui/static/app.js` as of 2026-08-12; `DOCUMENTATION.md` §2.2 for the
architecture constraint; live verification via Playwright + headless
Chromium against the real running app and this project's real 22-lecture
course data.
