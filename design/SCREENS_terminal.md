# Handoff: IDX Smart-Money Flow Terminal

> **⚠️ Superseded in part by v2 (2026-07-13).** This document describes the pre-v2 shell with a
> **left module nav rail and eight top-level modules**. In v2 the nav rail is **removed**, **Signal
> Pipeline** is the sole top-level view, and Broker Flow / Foreign Flow / Accumulation Detector /
> Money Replay are **evidence tabs** opened from a pipeline row. Use [`HANDOFF_v2.md`](HANDOFF_v2.md)
> as the authoritative v2 layout/spec; the **per-module component/chart/token specs below remain
> accurate** for those four evidence views and the shell chrome (top bar, right rail, ticker) — only
> the navigation model changed. RULE A/B are unchanged.

## Overview
A private, single-operator **flow terminal** for the Indonesia Stock Exchange (IDX), built to the attached `LOCKED_SPEC.md` (v1.1). It surfaces broker/foreign "smart-money" flow as **observation** — never as advice — and enforces the spec's two governing rules directly in the UI:

- **RULE A (Tradeability gate, LD-2):** only Wyckoff Accumulation Phase C/D is tradeable; the phase classifier gates before scoring.
- **RULE B (Presentation gate, LD-9):** no confidence number, probability, Smart Money Score, or ranked buy/sell claim may be *displayed* until that module has survived `PAPER_VALIDATION_MONTHS` (default 3) of fill-realistic forward paper trading. Until then the module shows raw **observation** (components, flows) with **no number attached**.

The design is a dark, paned, keyboard-adjacent "workbench": top status bar, left module nav rail, main module pane, right-hand ARMED watchlist, bottom disclaimer ticker.

## About the Design Files
The files in this bundle are **design references created in HTML** — a working prototype showing intended look and behavior, **not production code to copy directly**. The task is to **recreate these designs in the target codebase's environment**, using its established patterns and libraries.

The spec (§10) calls for a **local-first Python stack: Streamlit UI, DuckDB/SQLite store, Pandas/Polars + TA-Lib analytics.** If you are implementing in that stack, treat this HTML as the visual/interaction target and rebuild it as Streamlit components (or a small React front-end over a local API) — do not ship the HTML. If no front-end environment exists yet, pick the most appropriate one for a single-user local tool and implement there. All numbers in the prototype are **simulated/deterministic mock data** — wire real feeds per §2/§10.

> ⚠️ The prototype is authored as a "Design Component" (`.dc.html`) — a single streaming HTML file with an inline `<x-dc>` template + a `class Component extends DCLogic` logic block. That authoring format is specific to the design tool and is **not** a production framework. Read it for layout, styling, data shape, and interaction logic only.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, chart layouts, and interactions are all specified. Recreate the UI pixel-accurately using the target stack's libraries. Charts in the prototype are hand-built SVG (via `React.createElement`) — in production use the stack's charting lib (e.g. Plotly/Altair in Streamlit, or Recharts/visx in React), matching the described geometry.

Design canvas width: **1280px** (`$preview` width; height 800). The terminal fills the viewport (`100vh`, min-height 760px) and is intended for desktop. It remains usable down to ~900px but is not a mobile design.

---

## Global Layout (the shell)

Top-level: `display:flex; flex-direction:column; height:100vh; min-height:760px; background:#070a10; color:#e6edf3; font-family:'Geist'`.

Four horizontal bands:

1. **Top bar** — height **52px**, `linear-gradient(180deg,#0d121b,#0a0e14)`, bottom border `1px solid rgba(255,255,255,0.07)`. Contents left→right:
   - Logo mark: 26×26 rounded 6px, `linear-gradient(135deg,#58c4dd,#3a8fb0)`, "V" in `Geist 700`, color `#04121a`.
   - Wordmark "VECTOR·LAB" (Geist 600, 15px) with sub-label "IDX SMART-MONEY FLOW TERMINAL" (10px, `#5a6675`, letter-spacing 0.14em).
   - Live "Broker summary published · T+1" with a pulsing green dot (`#3fb950`, `livedot` animation).
   - "as-of {date} WIB" (Geist Mono).
   - **RULE B pill** (right side): `1px solid rgba(210,153,34,0.32)`, bg `rgba(210,153,34,0.08)`, text `#e8c168` — "RULE B · OBSERVATION ONLY — scores gated until paper-validated".
   - IHSG quote + "Track B".

2. **Body** — `flex:1; display:flex`, three columns:
   - **Nav rail** — width **82px**, bg `#0a0e14`, right border. 8 stacked items (see Nav below).
   - **Main pane** — `flex:1`, contains a module header ribbon (title + subtitle + status badge) then a scrollable module body (`padding:18px 20px`).
   - **ARMED watchlist rail** — width **296px**, bg `#0a0e14`, left border (see Watchlist below).

3. **Status/disclaimer bar** — height **26px**, bg `#0a0e14`, top border. Left chip "LOCAL · SINGLE-USER · PAPER" (Geist Mono, 9.5px). Right: a horizontally-scrolling ticker (`tickscroll` 42s linear infinite) cycling the four §15 disclaimers.

### Nav rail items (in order)
`⇄ Broker Flow` · `⌖ Foreign Flow` · `◱ Accum. Detect` · `⟲ Money Replay` · `▦ Smart Heatmap` · `✦ Sector Rotate` · `◈ Risk Monitor` · `∑ SMS / Rank`

- Each: column flex, glyph (18px) + 2-line label (9px, `white-space:pre-line`), 11px vertical padding, 8px horizontal margin, radius 9px.
- **Active:** bg `rgba(88,196,221,0.10)`, border `1px solid rgba(88,196,221,0.25)`, glyph/text `#58c4dd`/`#e6edf3`.
- **Inactive:** text `#8b98a9`.
- **SMS / Rank** shows a 🔒 lock glyph (top-right, 9px) **only while unvalidated**; it disappears when `paperValidated` is true. All items are clickable and open a real screen.

### ARMED Watchlist (right rail)
- Header: "ARMED WATCHLIST" + caption "Highest flow-signal names today — *observation, not a recommendation.*" + dynamic note (withheld vs validated).
- One card per ticker (7 Track-B names). Card: padding 11px, radius 9px, bg `#0d121b` (selected: `rgba(88,196,221,0.08)` + accent border). Contents:
  - Status dot (armed=`#d29922` pulsing `armedpulse`; watch=`#58c4dd`; none=`#5a6675`), ticker (Geist Mono 600 13px), track chip "B", and a right-aligned state label: `ARMED` / `WATCH` / `—` (or `SMS nn` when validated).
  - **5 component spark-bars** (height 26px, flex row, gap 3px), labeled underneath: `DIV BRK FF RVOL BLK`. Bar height = component strength %. FF (foreign flow) bar is blue `#58a6ff` when positive else faded red. Others amber when armed, cyan when watching, grey otherwise.
- Footer note reiterates: components are raw observation; no probability/verb until validated.

Clicking a watchlist card (or a matrix column header) calls `selectStock(ticker)` — updates the selected stock across Broker Flow, Foreign Flow, Accumulation, and Replay.

---

## Screens / Views

### 1. Broker Flow Analyzer (`broker`) — the differentiator
Header badge: green "OBSERVATION · ships now".
- **Trap / decay flag banner** (top of pane, above the stock header): a full-width tinted banner colored by the top flag's severity (WATCH → amber `rgba(210,153,34,0.08)`/text `#e8c168`; ALERT → orange `#f0a862`; CRITICAL → red `#f6a9a4`). Reads `Trap / decay — {icon} {KIND} — {detail}` at 16px. `{icon}` is a Unicode glyph (♦ ◇ ● ▲ ▼), `{KIND}` is Geist Mono 600.
- **"All trap & decay flags" collapsible** (below banner, default OPEN): a `#0d121b` panel with a clickable header row (rotating `›` caret + "All trap & decay flags (observation, not a recommendation)" + flag-count on the right). Expanded body is a grid table, columns `# | CATEGORY | KIND | SEVERITY | ICON | DETAIL`. Each row: index (mono, faint), category (DECAY/TRAP, mono), kind (mono 600, severity-colored), a **severity chip** (dot + WATCH/ALERT/CRITICAL, tinted per severity), the icon glyph, and the detail sentence. These are **VSA / Wyckoff behavioural observations** (NO_DEMAND, NO_SUPPLY, CHURN, UPTHRUST, BULL_TRAP, DISTRIBUTION) — pure observation, framed "not a recommendation", companion to the §5 veto filters. Flags are generated deterministically per selected ticker and sorted by severity descending; the banner mirrors the top flag. Toggle state = `trapOpen` (bool).
- **Stock header:** ticker (Geist 700, 26px), name, "TRACK B · lapis-2" chip (`#58c4dd`), sector chip, price (Geist Mono 20px) + % change (green/red), 20-day ADV.
- **Broker Net Flow table** (left, ~1.35fr): columns `# | BROKER·DNA | NET | PERSIST`. Each row: rank, broker code (Geist Mono 600), broker name, **DNA chip**, net value in IDR bn (green/red) with a proportional under-bar, and a 7-dot persistence strip (filled dots = consecutive net-buy days).
- **Concentration panel** (right, 1fr): "Top-2 net-buy share" (big cyan %, progress bar) + "Herfindahl (HHI)" (2-dp, label dispersed/concentrated/highly-concentrated) + a note naming the top-2 buyers.
- **Veto Checks panel** (§5): 4 rows, each a ✓/✕ chip + label + value. Checks: single-bandar monopoly (>60%), distribution-dressed-as-accumulation, retail-FOMO (>60%), event-driven.
- **Broker × Stock Matrix:** grid `150px + 7 columns`. Column headers = watchlist tickers (clickable → select). Rows = top-3 buyers + bottom-2 sellers. Cells: net value, bg tinted green/red with intensity = |net| share; selected ticker's column highlighted.

### 2. Foreign Flow Dashboard (`foreign`)
Header badge: green "OBSERVATION". Stock header + "FOREIGN-INST LENS" chip (`#58a6ff`) + market-tide caption.
- **Chart** (SVG, viewBox 0 0 1000 300): top lane = cumulative foreign-net area+line (`#58a6ff`); bottom lane = daily foreign-net bars around a zero baseline (blue positive / red negative). 30-day window.
- **Flow-reversal callout:** colored dot + "Foreign flow reversed to net BUY/SELL on {date} — N-day persistence."
- **Foreign vs Domestic split bar** (today): single 26px bar split blue (`#58a6ff`, foreign) / violet (`#a371f7`, domestic), each labeled with signed IDR bn.
- **Right column:** Foreign Flow Stats (net today, 5-day cumulative, net-buy persistence N/6, vs-20d-avg ×), Foreign-own-vs-free-float gauge (big blue %, of X% free-float), KSEI 6-month ownership sparkline (polyline, rising/easing label).

### 3. Institutional Accumulation Detector (`accum`)
Header badge: green "OBSERVATION". Stock header + verdict pill (amber if stealth detected).
- **Chart** (SVG viewBox 0 0 1000 230): price line (`#58c4dd`) + cumulative smart-money accumulation line (`#e3b341`) on the same lane; **stealth zone** (days 14–30) shaded amber `rgba(227,179,65,0.07)`; **accumulator VWAP** dashed amber horizontal line labeled "VWAP nnn"; legend for price/accumulation.
- **Verdict note** panel: prose explaining the divergence.
- **Stealth Metrics** (labeled "measured, not scored" — RULE-B-safe): price↔accumulation correlation (2-dp, "divergent (stealth)" if <0.1), volume dry-up RVOL (×, "dried up" if <0.8), consolidation tightness (%, "tight" if <12). Each with a progress bar + sub-label.
- **Accumulator VWAP card:** VWAP value, last price, % vs VWAP (green/red). **Absorption:** "depth feed off — module degrades gracefully (§10)".

### 4. Money Flow Replay (`replay`)
Header badge: green "OBSERVATION". Stock header + "reconstructing from stored as_of {date}".
- **Chart** (SVG viewBox 0 0 1000 442): three stacked lanes — PRICE (cyan area+line with a moving playhead dot), VOLUME (bars green/red vs prior close), FLOW (foreign `#58a6ff` + broker `#e3b341` lines around a zero baseline). **Future region past the playhead is shaded/dimmed** with a dashed cyan playhead line. Wyckoff **Spring / LPS / SOS** markers as amber tags on the price lane.
- **"At Playhead" panel:** Close, Δ vs prev, Volume (RVOL ×), Foreign net, Broker net (SM). Below: **Wyckoff Phase** box (A/B/C/D) with color + note (amber border for tradeable C/D). Plus a rolling insight sentence.
- **Transport bar:** circular play/pause button (`#58c4dd`), a range slider (0–43, day index), start/end dates. Play advances the scrub at `replaySpeedMs` (default 180ms) via `setInterval`. 44-day series.

### 5. Smart Money Heatmap (`heatmap`)
Header badge: cyan "DERIVED VIEW · rendering, no new claim".
- Legend row: red→green scale (net sell → net buy), "intensity = flow as % of cap", divergence key.
- **Grid:** 7 sector rows × 6 stock cells each. Cell = ticker + signed flow %, bg green/red with alpha = intensity. Divergence cells (local buy + foreign sell) get a `1.5px solid #bc8cff` border + ◆ marker.
- **Divergence Alerts panel** below: purple ◆ header + rows (ticker + description), framed "observation, not a recommendation".

### 6. Sector Rotation Map (`sector`)
Header badge: cyan "DERIVED VIEW".
- **Quadrant scatter** (SVG viewBox 0 0 460 460): x-axis = relative strength (RS), y-axis = flow. Four tinted quadrants labeled **LEADERS** (green, top-right), **EARLY RECOVERY** (cyan, top-left), **DISTRIBUTION WARN** (red, bottom-right), **AVOID** (grey, bottom-left). Sector bubbles: radius = flow magnitude, fill/stroke by quadrant color, abbreviation label.
- **Right column:** one card per sector, left border = quadrant color, quadrant chip, note, and `flow / RS / tide` line (Geist Mono).

### 7. Portfolio Risk Monitor (`risk`)
Header badge: green "OBSERVATION" (risk observations, not return predictions).
- **Top metric cards (4):** Portfolio β vs IHSG, VaR (95%·1d), Sector HHI, Invested/cash.
- **Open Paper Positions table:** columns `NAME | SECTOR | %EQUITY | β | DTE | P&L`. % equity has a bar vs the 10% cap (amber if >8.5%). P&L green/red.
- **Exposure Caps** (§6): per-sector bars vs 30% cap (amber if >25%).
- **Crowding Matrix:** N×N same-bandar correlation heatmap (red intensity = ρ), diagonal neutral. Note flags names sharing a lead broker (e.g. BRMS & CUAN via DX ≈ 0.72).
- **Circuit Breakers** (§6): Daily P&L gauge (halt new entries @ −3%), peak-to-trough drawdown gauge (pause @ −10%).
- **Scenario Stress:** 4 rows (IHSG −5% gap, foreign exodus, single-name ARB-lock, rupiah shock) with impact values.

### 8. Smart Money Score / AI Ranking (`sms`) — RULE B centerpiece
Header badge: amber "GATED · number withheld (RULE B)" when unvalidated; green "CLAIM · paper-validated" when validated.
- **Validation-state bar:** "PER-MODULE VALIDATION STATE · PAPER_VALIDATION_MONTHS = 3" + progress bar. Unvalidated: amber, "1.4 / 3 months forward-paper — number withheld". Validated: green, "3.0 / 3 — CLAIM ENABLED". Plus a threshold note.
- **Ranked table:** columns `# | NAME | COMPONENTS·obs | {SMS|withheld}`. Components column = 4 mini bars (divergence cyan, broker-conc gold, RVOL/block grey). Score column:
  - **Unvalidated (observation):** shows `•••` (withheld), state label ARMED/WATCH/—, ordering is "flow-derived, not a recommendation".
  - **Validated (claim):** shows numeric **SMS 0–100** (amber if ARMED), ARMED@70 highlighting, stronger language.
- **Digest panel** (right): "HIGHEST FLOW-SIGNAL NAMES TODAY" (unvalidated) / "DAILY TOP OPPORTUNITIES" (validated) — top-3 names with observation vs claim copy.

---

## Interactions & Behavior
- **Module switching:** clicking a nav item sets `activeModule`; only the active module builds/renders (others return null and are guarded by conditionals). Any running replay interval is cleared on switch.
- **Stock selection:** watchlist cards and matrix column headers call `selectStock(ticker)`; resets replay scrub to end, stops playback. Selected stock drives Broker/Foreign/Accum/Replay.
- **Replay transport:** play toggles a `setInterval` at `replaySpeedMs`; advances scrub 0→43 then auto-stops at end. Slider `onInput` sets scrub and pauses. All chart geometry recomputes from the scrub index (`as_of` reveal — future is shaded).
- **RULE B switch:** the `paperValidated` flag flips SMS/Rank and the watchlist between **observation** (withheld `•••`, "flow-derived ranking") and **claim** (numeric SMS, ARMED@70, "paper-validated"); it also removes the SMS nav lock and changes the module header badge.
- **Animations:** `armedpulse` (1.8s, box-shadow pulse on armed dots), `livedot` (1.8s opacity), `tickscroll` (42s linear, disclaimer marquee). Chart transitions are driven by re-render on scrub/selection, not CSS.
- **Hover:** matrix/heatmap/crowding cells and spark-bars expose `title` tooltips with exact values.

## State Management
- `activeModule` (string) — default `broker` (overridable by `defaultModule` prop).
- `selectedStock` (ticker string) — default `BRMS`.
- `scrub` (0–43) — replay playhead index.
- `playing` (bool) + an interval handle.
- **Props (tweakable):** `defaultModule` (enum), `paperValidated` (bool, drives RULE B observation↔claim), `replaySpeedMs` (60–400).
- All data is deterministic: a seeded PRNG keyed off the ticker (`seedFor` + `rng` mulberry-style) generates broker flows, replay series, foreign flows, and component scores, so a given ticker is consistent across modules and reloads. **Replace with real feature-store reads (`as_of`-stamped) in production.**

## Design Tokens

**Colors**
- Background: `#070a10` (app), `#0a0e14` (rails), `#0d121b` (panels/cards)
- Borders: `rgba(255,255,255,0.06)` (panels), `rgba(255,255,255,0.07)` (bars), `rgba(255,255,255,0.035–0.05)` (row dividers)
- Text: `#e6edf3` (primary), `#c2ccd8` (secondary), `#8b98a9` (muted), `#5a6675` (faint), `#3d4654`/`#4a5568` (disabled)
- Semantic: buy/positive `#3fb950`, sell/negative `#f85149`, armed/caution `#d29922` (accent text `#e8c168`), brand accent `#58c4dd`, divergence `#bc8cff`
- **Broker-DNA:** Foreign Inst `#58a6ff` · Local Inst `#a371f7` · Smart Money `#e3b341` · Retail `#6e7681` · Prop `#56d4bd`

**Typography**
- Display / UI: **Geist** (weights 400/500/600/700). Logo & big titles 600–700.
- Numerics / codes / dates: **Geist Mono** (400/500/600). Use for every number, ticker, broker code, and value.
- Scale (approx): page title 19–26px/600–700; panel title 11–12px/600; body 10.5–12px; labels 9–10px with letter-spacing 0.03–0.08em; big stat numbers 20–26px.

**Spacing / shape**
- Panel radius 10px (cards), 6–9px (chips/inner), 14px (large hero cards). Panel padding 14px; table cell padding 8–11px×14px. Gaps 12–14px between panels, 4–8px inside grids.
- Progress/under bars: 3–7px tall, radius 2–5px.

**Shadows:** none used; depth comes from layered backgrounds + hairline borders. Only shadow is the `armedpulse` glow on armed dots.

## Assets
No external images or icon fonts — all glyphs are Unicode symbols (`⇄ ⌖ ◱ ⟲ ▦ ✦ ◈ ∑ ▹ ◆ ❚❚ ▶`). Charts are hand-drawn SVG. Fonts load from Google Fonts (Geist, Geist Mono). In production, swap glyphs for the codebase's icon set and charts for its charting lib.

## Ticker & broker reference (mock domain data)
- **Track-B tickers used:** BRMS (Bumi Resources Minerals, Basic Materials), PTRO (Petrosea, Energy), RAJA (Rukun Raharja, Energy), CUAN (Petrindo Jaya Kreasi, Energy), DEWA (Darma Henwa, Energy), NCKL (Trimegah Bangun Persada, Basic Materials), MBMA (Merdeka Battery, Basic Materials).
- **Broker codes → DNA:** KZ/AK/RX/ZP/YU = Foreign Inst; CC/NI/OD/DR = Local Inst; DX/AI/KI = Smart Money; BQ = Prop; YP/PD/CP/GR = Retail. (Illustrative — verify against real IDX broker registry.)

## Files
- `IDX Flow Terminal.dc.html` — the full prototype (template + logic, all 8 modules, RULE A/B enforcement, seeded mock data). Read the `<x-dc>` block for markup/styling and the `class Component extends DCLogic` block for data generation, chart builders (`buildReplayChart`, `buildForeignModule`, `buildAccumModule`, `buildRiskModule`, `buildSmsModule`, `buildHeatmap`, `buildSectorScatter`), and interaction handlers.
- `LOCKED_SPEC.md` — the authoritative v1.1 specification. **This governs; the UI serves it.** Pay special attention to §0 (RULE A/B), §4 (SMS weights, internal-until-validated), §5 (veto filters), §6 (entry/sizing/risk), §9 (terminal modules & gating tiers), §10 (local-first stack), §13 (acceptance criteria), §15 (disclaimers).

## Implementation notes / gotchas
- **RULE B is a hard constraint, not decoration.** No module may render a score/probability/buy-sell verb until its per-module validation state clears `PAPER_VALIDATION_MONTHS`. Keep the observation↔claim switch server-authoritative (the paper-trade engine promotes state), not a client toggle, in production.
- **RULE A gates before scoring:** the phase classifier must reject non-C/D candidates before SMS is computed (§13 test).
- **Look-ahead control:** every datum is `as_of`-stamped; a signal may only use data with `availability_ts < decision_ts`. The Replay module is the audit tool that must reconstruct any past signal from stored `as_of` data.
- **Never IHSG as headline benchmark** — Track A → LQ45, Track B → sector/SMC index (§8).
- **Fee-aware, IDX-aware paper broker** (§12): lots of 100, ARA/ARB reject bands, full fee stack incl. 0.1% sell tax, next-open fills with slippage.
- Embed the §15 disclaimers in-app (the prototype runs them in the bottom ticker).
