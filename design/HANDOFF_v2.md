# Handoff: VectorLab — IDX Smart-Money Flow Terminal

## Overview
VectorLab is a **single-operator, paper-trading analytics terminal** for the Indonesia Stock Exchange (IDX). It ingests broker-summary and foreign-flow data (T+1) and surfaces "smart money" accumulation signals for a curated watchlist of Track-A (large-cap) and Track-B (lapis-2 / mid-cap) names.

The product's spine is a **Signal Pipeline**: every candidate flows left→right through four locked stages (Universe Gate → Phase Classifier → Signal Components → Veto Filters) and lands on a verdict — `ARMED`, `WATCH`, `REJECTED`, or `EXITED`. Clicking any candidate opens per-stock **evidence views** (Broker Flow, Foreign Flow, Accumulation Detector, Money Replay). A persistent right rail shows the "Armed Watchlist."

Everything in the UI is framed as **observation, not a recommendation** — this legal/behavioral framing (RULE B) is load-bearing and must be preserved in copy.

---

## About the Design Files
The files in this bundle are a **design reference created in HTML** — a working prototype that shows the intended look, layout, data model, and interactions. **It is not production code to copy directly.**

- `IDX Flow Terminal v2.dc.html` — the design. It is authored as a "Design Component" (a proprietary template + logic format); the `<x-dc>` markup, `{{ }}` bindings, `<sc-for>`/`<sc-if>` control-flow tags, and `support.js` runtime are **authoring conveniences, not part of the target implementation**. Read them for structure, styling, copy, and the data model.
- `support.js` — the prototype runtime. **Ignore for implementation**; it is only included so the HTML opens in a browser for visual reference.

**Your task:** recreate this design in the target codebase's existing environment (React, Vue, Svelte, etc.) using its established component library, state management, and styling conventions. If no environment exists yet, pick the most appropriate stack (a React + TypeScript SPA is a natural fit) and implement there. Translate the inline styles into the codebase's styling system (CSS modules, Tailwind, styled-components, etc.) — do not ship the inline-style soup verbatim.

## Fidelity
**High-fidelity (hifi).** Colors, typography, spacing, and interactions are final and intentional. Recreate the UI pixel-accurately. All exact values are listed under **Design Tokens** below. The data shown (tickers, broker names, numbers) is realistic **mock data** — wire it to real feeds in production, but keep the same shape.

---

## Layout — Application Shell

The app is a full-viewport, dark, fixed-chrome terminal. `100vh`, `min-height: 760px`, `display:flex; flex-direction:column`. Three horizontal bands stacked top→bottom:

1. **Top Bar** — `height: 52px`, fixed.
2. **Body** — `flex:1`, a horizontal flex row containing **Main column** (`flex:1`) + **Right Rail** (`296px` fixed).
3. **Status/Disclaimer Bar** — `height: 26px`, fixed, with a scrolling ticker of legal disclaimers.

> **Note:** an earlier version had a left icon nav rail for switching modules. It has been **removed** — Signal Pipeline is now the only top-level module, so there is no left rail. The Main column starts flush at the left edge of the body.

### Top Bar (`52px`, `linear-gradient(180deg,#0d121b,#0a0e14)`, bottom border `1px rgba(255,255,255,0.07)`)
Left→right, `gap: 18px`, horizontal padding `18px`:
- **Logo lockup:** `26×26` rounded-6 square, `linear-gradient(135deg,#58c4dd,#3a8fb0)`, contains a bold `V` in `#04121a`. Next to it: `VECTOR·LAB` (Geist 600, 15px; the `·` is `#58c4dd`) over the tagline `IDX SMART-MONEY FLOW TERMINAL` (10px, `#5a6675`, letter-spacing `0.14em`).
- Vertical divider (`1px × 26px`, `rgba(255,255,255,0.08)`).
- **Data-freshness pill:** pulsing green dot (`#3fb950`, `livedot` animation) + `Broker summary published · T+1` (11px, `#8b98a9`).
- **As-of stamp:** `as-of` (`#5a6675`) + timestamp (Geist Mono, `#e6edf3`, e.g. `13 Jun · 16:32`) + `WIB`.
- Spacer (`flex:1`).
- **RULE B banner:** amber-bordered pill — border `1px rgba(210,153,34,0.32)`, bg `rgba(210,153,34,0.08)`, radius 6, text `#e8c168`, 10.5px: **`RULE B · OBSERVATION ONLY — scores gated until paper-validated`**.
- **Index quote:** `IHSG 7,241.6 -0.42%` (both figures `#f85149` when down) + `Track A·B` (Geist Mono, 11px, `#8b98a9`).
- Vertical divider.
- **Account cluster:** green dot + account label (`operator`) + token preview (Geist Mono, `····a1f9`), and a **Sign out** button (bordered, `1px rgba(255,255,255,0.12)`, radius 6; hover: bg `rgba(255,255,255,0.06)`, border `rgba(255,255,255,0.22)`).

### Main Column
- **Module header ribbon** (`padding: 14px 20px 12px`, bottom border `1px rgba(255,255,255,0.06)`, `display:flex; align-items:flex-end; gap:16px`):
  - When inside an evidence view, a **‹ Pipeline** back button appears first (bordered pill, hover lightens).
  - **Title** (Geist 600, 19px) + **subtitle** (11.5px, `#8b98a9`, `max-width: 640px`).
  - Spacer, then a **status badge** on the right (see Badges).
- **Evidence tab bar** — only rendered when a candidate is open (`inDetail`). `padding: 9px 20px`, bg `#0a0e14`, bottom border. Label `EVIDENCE` (9.5px, `#5a6675`) then 4 tabs: `Broker Flow`, `Foreign Flow`, `Accum. Detect`, `Money Replay`. Active tab: bg `rgba(88,196,221,0.12)`, text `#8fdcec`, border `1px rgba(88,196,221,0.35)`; inactive: text `#8b98a9`, border `1px rgba(255,255,255,0.08)`. Tab pills: `padding 6px 13px`, radius 7, 11px.
- **Content area** — `flex:1; overflow:auto; padding: 18px 20px`. Renders exactly one of: Signal Pipeline (default) OR one of the four evidence views.

### Right Rail — Armed Watchlist (`296px` fixed, bg `#0a0e14`, left border `1px rgba(255,255,255,0.06)`)
- Header: `ARMED WATCHLIST` (12px, 600) + note "Highest flow-signal names today — **observation, not a recommendation.**" (`observation…` in `#e8c168`).
- Scrolling list of cards, one per non-rejected / non-exited name (sorted ARMED → WATCH). Each card (`padding 11px`, radius 9, bg `#0d121b`, selected: bg `rgba(88,196,221,0.08)` + border `rgba(88,196,221,0.3)`):
  - Row: status dot (armed = `#d29922` with `armedpulse`; watch = `#58c4dd`; else `#5a6675`) + ticker (Geist Mono 600, 13px) + track chip + right-aligned state label (`ARMED`/`WATCH`/`—`, or `SMS NN` when paper-validated).
  - A 5-bar micro sparkline (`height 26px`, bars flex-1) representing the signal components, labeled `DIV · BRK · FF · RVOL · BLK` beneath (8.5px, `#5a6675`).
- Footer note (9px, `#5a6675`) explaining the RULE B gating.

### Status / Disclaimer Bar (`26px`, bg `#0a0e14`, top border)
- Fixed left label `LOCAL · SINGLE-USER · PAPER` (Geist Mono, 9.5px, `#5a6675`).
- Infinite horizontal marquee (`tickscroll` 42s linear) of four disclaimers separated by `•`, e.g. "Private personal-use analytics tool — not a product…", "Not investment advice…", "…used at own risk; not republished.", "No live execution. Paper trading only…". Duplicate the set once so the loop is seamless.

---

## Screens / Views

### 1. Signal Pipeline (default view)
**Purpose:** triage the whole watchlist at a glance — see every candidate's verdict and *why*, per stage.

**Layout:** a stage-header grid then two "lane" groups (Track A, Track B). Every candidate row is a CSS grid:
`grid-template-columns: 170px repeat(4, 1fr) 150px; gap: 8px;` — i.e. **[Candidate] [Gate] [Phase] [Signal] [Veto] [Result]**.

- **Stage-flow header:** same grid. Col 1 = `CANDIDATE` label. Cols 2–5 = the four stages, each a card (bg `rgba(255,255,255,0.03)`, border `1px rgba(255,255,255,0.07)`, radius 8) with a numbered chip (Geist Mono, bg `#58c4dd`, text `#04121a`, `15×15`) + title + sub-caption, and a `›` chevron bleeding off the right edge. Col 6 = `RESULT · WHY?` label.
  - Stage titles/subs: `1 UNIVERSE GATE` (§3 hard liquidity floor · track assignment) · `2 PHASE CLASSIFIER` (RULE A · only Wyckoff Phase C/D passes) · `3 SIGNAL COMPONENTS` (§4 track-weighted · SMS internal (RULE B)) · `4 VETO FILTERS` (§5 hard rejects — any one kills the signal).
- **Lane group** (one per track): a header row — track chip (`TRACK A`/`TRACK B`, bg `rgba(88,196,221,0.12)`, `#58c4dd`), a description, a hairline rule, and a right-aligned count string like `1 armed · 1 watch · 1 exited · 2 rejected` (only shows `exited` when > 0).
  - Track A desc: "large-cap · LQ45/IDX80 · foreign-flow reliable — NBSA co-lead (wt 25)"
  - Track B desc: "lapis-2 · passes hard floor only · broker-concentration reliable (wt 35)"
- **Candidate row** (clickable → opens Broker Flow evidence; hover bg `rgba(88,196,221,0.035)`):
  - **Candidate cell:** bg `#0d121b`, border, radius 8. Ticker (Geist Mono 600, 14px) + day change (`+1.81%` green / red) + `{name} · {price}` (9.5px, `#5a6675`, ellipsized).
  - **Four stage cells:** each `min-height: 64px`, radius 8. A `16×16` mark chip + a status tag (Geist Mono, 8.5px), then a wrapped reason line (10px). Cell styling per stage state (below).
  - **Result cell:** status dot + verdict label (Geist Mono 600, 11.5px). For `EXITED`, a right-aligned **realized-P&L badge** (`REALIZED +2.4%` etc., green/red). Below: the verdict note (9px, `#5a6675`) and a `why? — tap for detail ›` link (`#58c4dd`).
- **Footer note** (10px, `#5a6675`) explaining the locked left→right order, the EXITED semantics (a position that cleared the pipeline, was entered, then sold when its thesis broke — reversed stage flagged with `⤶` + realized P&L), and "Observation, not a recommendation."

**Stage-cell state styling** (the `s` field on each stage):

| state | mark | tag (default) | text/fg | dot | bg | border |
|---|---|---|---|---|---|---|
| `pass` | `✓` | PASS (or a tag like `PHASE C`) | `#7ee08a` | `#3fb950` | `rgba(63,185,80,0.05)` | `rgba(63,185,80,0.22)` |
| `fail` | `✕` | REJECT (or tag) | `#f6a9a4` | `#f85149` | `rgba(248,81,73,0.07)` | `rgba(248,81,73,0.38)` |
| `low` | `▽` | BELOW THRESHOLD | `#e8c168` | `#d29922` | `rgba(210,153,34,0.07)` | `rgba(210,153,34,0.38)` |
| `rev` | `⤶` | THESIS BROKEN (or tag) | `#f0a0a8` | `#e06b7a` | `rgba(224,107,122,0.08)` | `rgba(224,107,122,0.42)` |
| `skip` | `·` | NOT EVALUATED | `#3d4654` | `#3d4654` | `rgba(255,255,255,0.015)` | `rgba(255,255,255,0.04)` |

The mark chip is a `16×16` radius-4 box, bg `rgba(255,255,255,0.05)`, glyph colored by the state's dot color.

**Result-cell verdict styling** (the `result` field):

| verdict | label color | dot | animation | bg | border |
|---|---|---|---|---|---|
| `ARMED` | `#e8c168` | `#d29922` | `armedpulse 1.8s infinite` | `rgba(210,153,34,0.08)` | `rgba(210,153,34,0.42)` |
| `WATCH` | `#8fdcec` | `#58c4dd` | — | `rgba(88,196,221,0.06)` | `rgba(88,196,221,0.3)` |
| `REJECTED` | `#8b98a9` | `#f85149` | — | `rgba(255,255,255,0.02)` | `rgba(255,255,255,0.07)` |
| `EXITED` | `#f0a0a8` | `#e06b7a` | — | `rgba(224,107,122,0.07)` | `rgba(224,107,122,0.4)` |

For `EXITED`, the P&L badge: `margin-left:auto`, Geist Mono 9.5px 600, `padding 1.5px 7px`, radius 5; color = `#3fb950` if the `exitPnl` string does **not** contain the minus sign `−` (U+2212), else `#f85149`; bg = `{color}1e` (12% alpha hex suffix), border `1px {color}55`.

---

### 2. Broker Flow Analyzer (evidence tab: `broker`)
**Purpose:** the differentiator — who is actually buying/selling this name, and how concentrated is it.

**Layout:**
- **Trap/decay banner** at top — a full-width alert card (bg/border/fg vary by severity) with a headline like "Trap / decay — [icon] [KIND] — [detail]".
- **Collapsible "All trap & decay flags" table** — click header (caret rotates) to expand; columns `# · CATEGORY · KIND · SEVERITY · ICON · DETAIL`. Each flag has a kind label chip and a severity pill with a colored dot.
- **Stock header** — big ticker (Geist 700, 26px) + name + track chip + sector chip + right-aligned price (Geist Mono, 20px) & change, and a `20d ADV` stat separated by a left border.
- **Two-column grid** `1.35fr 1fr`:
  - **Left — BROKER NET FLOW table** (bg `#0d121b`, radius 10): header "BROKER NET FLOW · today" + "net value, IDR bn". Rows: rank, broker code+name, **DNA chip**, right-aligned net value (Geist Mono 600, green/red) with a proportional under-bar, and a 5-dot persistence strip.
  - **Right — two cards:** **CONCENTRATION** (two big stats side by side: "Top-2 net-buy share" as a big `#58c4dd` % with a gradient progress bar `linear-gradient(90deg,#3a8fb0,#58c4dd)`, and "Herfindahl (HHI)" with a label like "concentrated"; plus a note) and **VETO CHECKS · §5 hard rejects** (list of pass/fail rows with a mark chip, label, and value).
- **BROKER × STOCK MATRIX** (full width): a `150px repeat(7,1fr)` grid heat-map. Columns = the 7 Track-B tickers (clickable to select), rows = brokers (code + DNA chip). Each cell is a `30px` tile colored green (net buy) / red (net sell), intensity = share of flow; shows the signed value and a tooltip. Legend in header (green=net buy, red=net sell, "intensity = share of flow").

**DNA chip** (broker classification): `font-size 8.5px`, `padding 1.5px 6px`, radius 4. Colored by DNA type using `{DNA}22` bg / `{DNA}` fg / `{DNA}44` border. DNA palette under Design Tokens.

---

### 3. Money Flow Replay (evidence tab: `replay`)
**Purpose:** scrub the historical price + flow evolution — the audit tool behind every signal.

**Layout:**
- Header: ticker + name, right-aligned "reconstructing from stored **as_of** · {date}".
- **Grid `1fr 260px`:**
  - **Left:** a multi-series SVG chart (price line + foreign/broker flow) in a card.
  - **Right:** an **AT PLAYHEAD** readout card (Close, Δ vs prev, Volume (RVOL), Foreign net, Broker net (SM) — each a label + Geist-Mono value colored by sign), a **WYCKOFF PHASE** sub-card (phase letter big, colored, with note), and an **insight** card (prose that changes with the playhead).
- **Transport bar** (full width): a circular play/pause button (`38px`, bg `#58c4dd`, glyph `#04121a`), a range `<input type=range>` scrubber styled per the slider tokens, and a start-date / `day N / max` / end-date caption.

**Playback:** Play advances the scrubber on an interval (default 220ms/step, tweakable 60–400ms — see State). The series is ~44 days.

---

### 4. Foreign Flow Dashboard (evidence tab: `foreign`)
**Purpose:** foreign-institutional lens — magnitude, persistence, ownership, reversals.

**Layout:** header (ticker + name + `FOREIGN-INST LENS` chip in `#58a6ff` + right-aligned market-tide caption). **Grid `minmax(0,1fr) minmax(220px,296px)`:**
- **Left column:** cumulative + per-day foreign-net SVG chart card; a **FLOW-REVERSAL DETECTION** card (status dot + sentence); a **FOREIGN vs DOMESTIC — today** split bar (blue `#58a6ff` FGN segment + purple `#a371f7` DOM segment, widths proportional).
- **Right column:** **FOREIGN FLOW STATS** (label/value rows: Foreign net today, 5-day cumulative, Net-buy persistence, vs 20-day avg); **FOREIGN OWN vs FREE-FLOAT** (big blue % of free-float with a progress bar); **KSEI OWNERSHIP · 6mo** sparkline (`polyline`, stroke `#58a6ff`) with a rising/easing tag.

---

### 5. Institutional Accumulation Detector (evidence tab: `accum`)
**Purpose:** detect stealth accumulation — price flat/down while net accumulation rises.

**Layout:** header (ticker + name + a **verdict pill** on the right: colored dot + verdict text on amber-tinted bg). **Grid `minmax(0,1fr) minmax(220px,296px)`:**
- **Left:** price/accumulation SVG chart card + a verdict-note prose card.
- **Right:** **STEALTH METRICS · measured, not scored** (each metric = label + Geist-Mono value + progress bar + sub-caption); **ACCUMULATOR VWAP** card (big VWAP + last price + vs-VWAP delta); **ABSORPTION** card (degrades gracefully: "unavailable (needs L2 depth) — degrades gracefully, never faked (§10)").
- Full-width **STEALTH-DIVERGENCE DETECTION** card at the bottom (dot + sentence).

---

## Interactions & Behavior
- **Open evidence:** clicking a pipeline candidate row → `openDetail(ticker, 'broker')`: sets `detailStock` + `selectedStock` to that ticker, `detailTab='broker'`, resets scrubber to 43, stops playback. The header ribbon shows a contextual title ("Why {TICKER} is ARMED / is on WATCH / was REJECTED / was EXITED") and subtitle derived from the decisive stage (fail/low → "Decisive stage: …"; rev → "Entry thesis broken — … {note}."; else "Passed every stage — …").
- **Evidence tabs:** switch the active evidence view (`broker`/`foreign`/`accum`/`replay`); switching stops playback.
- **Back to Pipeline:** `‹ Pipeline` clears `detailStock` and returns to the pipeline.
- **Broker matrix column click:** selects that ticker (updates the matrix highlight + broker table context) without leaving the broker view.
- **Replay transport:** play/pause toggles an interval that increments the scrubber; dragging the range input sets the playhead and pauses. All readouts, phase, and insight recompute from the playhead index.
- **Trap-flags disclosure:** clicking the "All trap & decay flags" header toggles `trapOpen` (caret rotates 90°).
- **Sign out:** clears the mock session and resets `activeModule` (see Auth note).
- **Hover states:** rows and buttons lighten (`rgba(255,255,255,0.06)` fills, border brightening). Selected watchlist/pipeline items get the cyan-tinted selected treatment.
- **Animations/keyframes:**
  - `armedpulse` (1.8s ∞): expanding amber box-shadow ring on ARMED dots — `0 0 0 0 rgba(210,153,34,0.55)` → `0 0 0 5px rgba(210,153,34,0)`.
  - `tickscroll` (42s linear ∞): disclaimer marquee, `translateX(0)` → `translateX(-50%)`.
  - `livedot` (1.8s ∞): opacity `1` ↔ `0.25` on the freshness dot.
  - Caret rotate transition `0.15s`; button bg transitions `0.15s`.

## State Management
Component-level state (no server state in the prototype — everything is derived from static mock arrays via seeded RNG):
- `selectedStock` (string, default `'BRMS'`) — the ticker in context for evidence views.
- `detailStock` (string | null) — when set, an evidence view is shown instead of the pipeline. `inDetail = !!detailStock`.
- `detailTab` (`'broker' | 'foreign' | 'accum' | 'replay'`, default `'broker'`).
- `scrub` (int, default 43) — replay playhead index.
- `playing` (bool) — replay transport running; drives a `setInterval`.
- `trapOpen` (bool, default true) — broker trap-flags disclosure.
- **Auth/session substate** (see note): `authed` (default **true**), `verifying`, `authMode`, `authStep`, `authRound`, `selChannel`, `resendIn`, `tokenPreview`, `username`, and error fields.

**Derived, per render:** pipeline lanes/rows (from `PIPE` + `TICKERS`), evidence-module data (built lazily only for the active tab), watchlist rows (excludes `REJECTED` and `EXITED`, sorted ARMED→WATCH). Numbers in evidence views are generated by a **seeded RNG** keyed off the ticker so they're stable per name — in production, replace these builders with real data fetches that return the same field shapes.

**Tweakable props** (surface as settings if useful; otherwise bake the defaults):
- `startAuthed` (bool, default false) — start already signed in.
- `paperValidated` (bool, default false) — RULE B gate. When true, the watchlist state labels show a numeric `SMS NN` "claim"; when false they show `ARMED`/`WATCH` and the score is withheld.
- `replaySpeedMs` (int 60–400, default 220) — replay auto-advance interval.

### Auth / session note
The class contains a **complete credential + MFA (OTP) login flow** (`OTP_ROUNDS`, `submitCredentials`, `verifyOtp`, resend countdown, a Bearer-token fallback) plus a top-bar session cluster and Sign-out. **However, there is currently no login-screen markup rendered** — `authed` defaults to `true`, so the terminal shows immediately and the login UI is dormant. If the product needs an auth gate, the logic/field-shapes are documented in the source (`showLogin`, `showCredentials`, `showOtp`, `showBearer`, channel chips for Email/WhatsApp/SMS) and can be built out; otherwise treat auth as out of scope and wire real session handling from the host app.

---

## Data Model (mock — mirror these shapes in production)

**Ticker** `{ t, name, sector, price, chg, adv, track }` — `track` is `'A'` or `'B'`. The 12 seeded tickers: ANTM, ADRO, BBNI, UNTR, MDKA (Track A); BRMS, PTRO, RAJA, CUAN, DEWA, NCKL, MBMA (Track B).

**Broker** `{ code, name, dna }` — 17 brokers. `dna ∈ { 'Foreign Inst', 'Local Inst', 'Smart Money', 'Prop', 'Retail' }`. (e.g. `KZ` = CLSA = Foreign Inst; `DX` = Bahana = Smart Money; `YP` = Mirae = Retail.)

**Pipeline record** (`PIPE[ticker]`): four stage objects `gate / phase / sig / veto`, each `{ s, tag?, r }` where `s ∈ { pass, fail, low, rev, skip }`, `tag` is an optional label (e.g. `PHASE C`, `FLOW REVERSED`), `r` is the reason string. Plus `result ∈ { ARMED, WATCH, REJECTED, EXITED }`, `note` (verdict caption), and — for EXITED only — `exitPnl` (e.g. `'+2.4%'` / `'−1.1%'`). A candidate stops at the first non-pass stage; downstream stages are `skip`. For EXITED, all stages passed on entry and the stage whose signal later reversed carries `s:'rev'`.

**Stage/verdict enums drive all the cell/badge styling** — see the two tables under Signal Pipeline.

---

## Design Tokens

### Core palette (`C`)
| token | hex | use |
|---|---|---|
| buy | `#3fb950` | positive / net-buy / pass |
| sell | `#f85149` | negative / net-sell / fail |
| armed | `#d29922` | ARMED / below-threshold / caution |
| accent | `#58c4dd` | primary cyan — links, selection, brand |
| div | `#bc8cff` | divergence highlight (violet) |
| text | `#e6edf3` | primary text |
| muted | `#8b98a9` | secondary text |
| faint | `#5a6675` | tertiary / captions |

### Broker-DNA palette (`DNA`)
| type | hex |
|---|---|
| Foreign Inst | `#58a6ff` |
| Local Inst | `#a371f7` |
| Smart Money | `#e3b341` |
| Retail | `#6e7681` |
| Prop | `#56d4bd` |

### Surfaces & lines
- App background: `#070a10`
- Chrome / rail background: `#0a0e14`
- Card background: `#0d121b`
- Header gradient: `linear-gradient(180deg,#0d121b,#0a0e14)`
- Hairline borders: `rgba(255,255,255,0.06)` (standard), `rgba(255,255,255,0.07)` (slightly stronger), `rgba(255,255,255,0.05)` (inner)
- Subtle fills: `rgba(255,255,255,0.03)` / `0.02`
- Hover fill: `rgba(255,255,255,0.06)`
- Selection tint (cyan): bg `rgba(88,196,221,0.08)–0.12`, border `rgba(88,196,221,0.25)–0.35`
- `::selection`: `rgba(88,196,221,0.3)`

### Semantic tints (bg / border pattern, `rgba(base, α)`)
- Green (pass): bg `0.05`, border `0.22` of `63,185,80`
- Red (fail): bg `0.07`, border `0.38` of `248,81,73`
- Amber (low/armed): bg `0.07–0.10`, border `0.32–0.42` of `210,153,34`
- Rose (rev/exited): bg `0.07–0.08`, border `0.4–0.42` of `224,107,122`

### Typography
- **Sans:** `Geist` (weights 400/500/600/700) — UI text, titles.
- **Mono:** `Geist Mono` (400/500/600) — all numerics, tickers, codes, timestamps, tags.
- Loaded from Google Fonts. Representative sizes: page/stock ticker 22–26px/700; module title 19px/600; card titles 11–12px/600; body 10.5–11.5px; captions 9–10px; micro-labels 8.5–9px with letter-spacing `0.03–0.08em` (uppercase eyebrows). Big stat numerals 20–26px/600 Geist Mono.

### Radii, borders, misc
- Card radius: **10px**. Cell/pill radius: **7–8px**. Chip/mark radius: **4–5px**. Dots/tiles: **50%** or **2px**.
- Borders: **1px** everywhere (occasionally 1.5px on emphasized state cells). Left-accent border on some cards: **3px**.
- No drop shadows except the `armedpulse` ring. Flat, border-defined dark UI.
- Fixed widths: right rail **296px**; status bar left label auto; scrollbar **9px** (thumb `#1e2632`).
- Range slider: track `4px` `#1e2632`; thumb `16px` circle `#58c4dd` with `2px #0a0e14` ring.

### Badges (module header status)
- `obs` → **OBSERVATION · ships now** — dot `#3fb950`, bg `rgba(63,185,80,0.10)`, border `rgba(63,185,80,0.3)`, fg `#7ee08a`.
- (Legacy `derived`/`gated`/`claim` badge styles exist in source but are unused now that only the pipeline + its obs-badged evidence views remain.)

---

## Assets
- **Fonts:** Geist + Geist Mono via Google Fonts (`https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600`). Use the codebase's font-loading convention.
- **Icons/glyphs:** all "icons" are Unicode glyphs and CSS shapes — no image or SVG-icon assets. Notable glyphs: `✓ ✕ ▽ ⤶ ·` (stage marks), `‹ ›` (nav/chevrons), `◈ ⇶ ∑ ▦ ✦` (legacy module glyphs — most removed), `✉ ✆ ▤` (auth channels). Charts are hand-built inline `<svg>`. Replace glyph marks with the codebase's icon set if preferred, but keep the exact colors/semantics.
- **Logo:** the `V` lockup is pure CSS (gradient square + letter) — no asset.
- No external images. No Anthropic brand assets.

---

## Files
- `IDX Flow Terminal v2.dc.html` — the complete design reference (markup + logic + mock data). Structure map:
  - Lines ~1–30: `<head>` + fonts + global CSS resets & `@keyframes`.
  - ~33–105: application shell — top bar, body flex, module header ribbon, evidence tab bar.
  - ~120–235: **Signal Pipeline** template.
  - ~235–290: **Broker Flow** template (+ trap flags, concentration, veto, broker×stock matrix).
  - ~330–390: **Money Replay** template.
  - ~392–460: **Foreign Flow** template.
  - ~462–495: **Accumulation Detector** template.
  - ~505–545: **Right rail** (Armed Watchlist) + status/disclaimer bar.
  - ~550–770: logic class — state, auth/session flow, palette (`C`/`DNA`), `BROKERS`, `TICKERS`, `PIPE_STAGES`, `PIPE`.
  - ~765–1260: builders — `buildPipeline`, `replaySeries`, `buildReplayChart`, `buildForeignModule`, `buildAccumModule`, broker/matrix data.
  - ~1260–end: `renderVals()` — assembles every value the template binds, including the header map, badges, and derived flags.
- `support.js` — prototype runtime only; **not for implementation**.

Read the HTML top-to-bottom once for structure, then use this README's tables as the source of truth for exact values.
