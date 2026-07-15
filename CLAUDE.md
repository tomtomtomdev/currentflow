# CLAUDE.md — CurrentFlow repo conventions

CurrentFlow is a **private, single-operator IDX smart-money screener & flow terminal**.
Source of truth: [`LOCKED_SPEC.md`](LOCKED_SPEC.md) (v1.6, LOCKED), with data mapping in
[`DATA_SOURCES.md`](DATA_SOURCES.md) and server-side pre-filters in [`screeners.md`](screeners.md).

**Read the spec before writing code.** Any change to locked behavior requires a spec version
bump with a documented reason — do not silently diverge from `LOCKED_SPEC.md`.

---

## The two hard constraints (never violate)

These override everything, including any global/iOS instructions.

**RULE A — Tradeability gate (LD-2).** The Wyckoff phase classifier is a HARD GATE *before*
scoring. Only Accumulation **Phase C or D** is tradeable. Threshold detectors feed the
classifier; they never bypass it. Code path: phase gate runs at pipeline step [3], before SMS
([4]) and veto ([5]).

**RULE B — Presentation gate (LD-9).** A module may display a confidence number, probability,
Smart Money Score, or ranked buy/sell claim **ONLY after it has survived `PAPER_VALIDATION_MONTHS`
of fill-realistic forward paper trading** (default 3). Until earned, render the **observation**
(raw flow, components, divergence) with **no number attached**. Framing is always *observation*
("here is the flow, you decide"), never *advice* ("buy these"). The paper-trade engine is the
sole authority that promotes observation → claim.

> Practical rule for any UI/report code: if a module's validation state is not `VALIDATED`,
> it MUST NOT emit SMS, probabilities, or buy/sell verbs. Show components only. This is
> acceptance-tested — see `LOCKED_SPEC.md` §13.

---

## Stack (LD-10, §10) — local-first, single-user

- **Language:** Python 3.11+.
- **Analytics/signals:** Pandas / Polars, TA-Lib.
- **Store:** DuckDB — local time-series, no cloud. Keyed `(symbol, date, as_of)`. (Pinned from
  the spec's SQLite/DuckDB allowance; see PROGRESS.md decisions log.)
- **UI:** Streamlit (prototype; richer later). Keyboard-driven, dark, paned.
- **Data:** Stockbit `exodus` API is the **primary DAL source**, consumed from the operator's
  **own authenticated session** (Bearer token). Local persistence only — nothing leaves the
  machine, nothing is republished. See `DATA_SOURCES.md`.
- **Feature store:** every datum stamped with `as_of` (availability_ts) so gated modules have
  clean, look-ahead-safe inputs when they earn validation.

No SaaS, no billing, no multi-user, no redistribution. Parser breakage on endpoint changes is
expected maintenance, not a crisis.

---

## Architecture — vertical slices

Each build slice is a **full vertical**: data → signal → view → test (`LOCKED_SPEC.md` §11).
Nothing renders until its data is trustworthy (integrity/gap checks first).

Pipeline stages (see §2): INGEST → UNIVERSE GATE → PHASE CLASSIFIER (RULE A) → SMS (internal) →
VETO → ARMED → TECHNICAL TRIGGER → FUNDAMENTAL TILT → ORDER GEN → PAPER FILL → RISK/EXIT →
BACKTEST⇄FORWARD-PAPER → TERMINAL UI.

Suggested module layout (create as slices land):

```
currentflow/
  dal/          # ExodusClient + feed adapters; as_of stamping; paywall backoff; 401 fail-loud
  store/        # DuckDB schema, feature store, integrity/gap checks
  universe/     # §3 gate; Track A/B assignment; ARA-ARB derivation
  signals/      # phase classifier (RULE A), SMS components, veto filters
  fundamentals/ # Magic Formula (live JSON + point-in-time backtest parse)
  execution/    # technical trigger, sizing, order gen
  paper/        # IDX-aware fill engine (lots/ticks/ARA-ARB/full fee stack)
  validation/   # backtest ⇄ forward-paper; per-module validation state (drives RULE B)
  ui/           # Streamlit terminal; observation vs claim modules
  screeners/    # server-side Stockbit pre-filters (screeners.md)
tests/
```

**DAL rules:** one method per feed; every returned record carries `availability_ts`; the DAL
enforces `availability_ts < decision_ts`; on 401 fail loud (never emit stale/empty); ingest once
and cache — never re-pull a stored `(symbol, date, as_of)`.

**Backtest vs forward-paper:** separate code paths, **shared fill engine**. Live scoring uses
clean JSON fundamentals; backtest uses parsed historical statements with a reporting-publication
lag. Never mix.

---

## TDD loop

Write the failing test first, especially for the acceptance criteria (`LOCKED_SPEC.md` §13):

- **Look-ahead test:** no signal consumes data with `availability_ts >= decision_ts`.
- **Phase gate test:** rejects all non-C/D candidates on labeled charts.
- **RULE B test:** no unvalidated module emits a number.
- **Fill engine test:** lot/tick/ARA-ARB/fee math reproduces hand-checked cases.
- **Reconciliation test:** backtest and forward-paper (shared fill engine) reconcile.
- **Replay/audit test:** Money Flow Replay reconstructs any past signal from stored `as_of` data.

Report net-of-full-fee-stack returns benchmarked to **LQ45 / sector**, never IHSG (§8).

---

## Conventions

- **Never hand-edit SMS weights live.** Weights (§4) tune only via walk-forward Sharpe
  optimizer. They are the only tunable surface.
- **Missing data is never zero flow.** Distinguish "no trades" / "not yet published" / "gap".
- **No silent caps.** If coverage is bounded (top-N, sampling, no-retry), `log` what was dropped.
- **Regime-clamped history.** Historical computations are regime-clamped per [`REGIME.md`](REGIME.md);
  a backtest reaching before `config.regime_start(track)` is a bug, not a bigger sample (it fails
  loud). Base rates / backtests run only over the current IDX regime; there is no era-versioned
  constant system.
- **Pattern-catalog base rates are presentation-confined (LD-14).** Historical frequencies live
  **only** in the dedicated catalog view under P1–P4 ([`PATTERN-CATALOG-SPEC.md`](PATTERN-CATALOG-SPEC.md));
  never on a live candidate/pipeline/rail/evidence surface, never a buy/sell verb or composite,
  never multiplied into SMS. Attaching a pattern's stats to a live name is a claim → the standard
  RULE B path, not the catalog.
- **Commits:** `type(scope): lowercase description`. No AI attribution / Co-Authored-By lines.
- **Disclaimers (§15):** private personal-use tool; not investment advice; paper only; own-session
  data used at own risk; nothing republished.

---

## UI / design target

The terminal's look & interaction are specified as a **high-fidelity design handoff** in
[`design/`](design/) (the "VectorLab" handoff bundle):

> **v2 restructure (2026-07-13, shipped in PLAN.md Slice 14):** the left module nav rail is
> **removed**; **Signal Pipeline** is the sole top-level view (Track A/B triage grid → four locked
> stages → verdict `ARMED`/`WATCH`/`REJECTED`; `EXITED` pending, PLAN.md Phase 2). Broker Flow,
> Foreign Flow, Accumulation Detector, Money Replay are now **evidence tabs** opened from a pipeline
> row. [`design/HANDOFF_v2.md`](design/HANDOFF_v2.md) is the authoritative v2 spec;
> `currentflow/ui/pipeline_view.py` + `shell.py` (`.cf-pipe*`) implement it. RULE A/B unchanged.

- [`design/HANDOFF_v2.md`](design/HANDOFF_v2.md) — **authoritative v2 per-screen spec** (pipeline +
  the four evidence tabs, tokens, data model, interactions). Read this first for the current layout.
- [`design/README.md`](design/README.md) — the handoff index: what the app is, fidelity,
  non-negotiable implementation constraints, shared design tokens, and reading order.
- [`design/SCREENS_terminal.md`](design/SCREENS_terminal.md) — per-screen spec: global shell
  (top bar / nav rail / main pane / ARMED watchlist / disclaimer ticker), all 8 module screens,
  design tokens (colors, Geist/Geist Mono type, spacing), interactions, and state model.
- [`design/STYLE_GUIDE.md`](design/STYLE_GUIDE.md) — pixel-fidelity token reference: exact hex
  palette, the type scale, shell dimensions, component patterns, motion keyframes, and the
  RULE B rendering states — every value lifted verbatim from the prototype. Match these exactly.
- [`design/SCREENS_login.md`](design/SCREENS_login.md) — the slice-11 login / session-gate
  handoff (§9.1): the credential + OTP flow that stands between `./run.sh` and the terminal.
  Auth plumbing only — gates no signal or RULE A/B behavior.
- [`design/IDX Flow Terminal.dc.html`](<design/IDX Flow Terminal.dc.html>) — working prototype
  (`.dc.html` design-tool format) for the terminal; the login/session gate now lives in its own
  [`design/Login Session Gate.dc.html`](<design/Login Session Gate.dc.html>). **Reference only —
  do not ship this HTML.** Read it for layout, styling, data shape, and interaction logic; charts
  are hand-built SVG.

**Stack decision (2026-07-01): Streamlit** per §10 — pure Python, single process, Plotly/Altair
for charts. Fidelity to the hifi prototype is *approximate*: the dark paned layout, keyboard nav,
and replay animation are best-effort within Streamlit's model, not pixel-exact. Match the design's
tokens (colors, Geist type, spacing) and chart geometry as closely as the framework allows; don't
add a React/JS layer to chase pixels. All prototype numbers are **seeded mock data** — wire real
`as_of`-stamped feature-store reads in production. The design already enforces both hard rules:
the SMS/Rank module withholds its number (`•••`) until `paperValidated` (RULE B), and the Replay
module reconstructs from stored `as_of` (look-ahead audit). Keep the observation↔claim switch
**server-authoritative** (promoted by the paper-trade engine), never a client toggle.

The §9 modules were built as slices 2–8 (each maps to `LOCKED_SPEC.md` §9). **v2 reorganizes the
navigation, not the spec:** Signal Pipeline is the home; Broker/Foreign/Accum/Replay are its evidence
tabs; and Smart Heatmap, Sector Rotate, Risk Monitor, SMS/Rank, AI Ranking, Daily Top, ML are no
longer top-level (code + tests retained — see PROGRESS.md decisions). No `LOCKED_SPEC.md` bump — RULE
A/B and §9's module *behavior* are unchanged; only the shell's nav model changed.

## Companion files

- [`PLAN.md`](PLAN.md) — slice-by-slice execution plan (from §11).
- [`PROGRESS.md`](PROGRESS.md) — durable log of shipped slices + each module's validation state.
- [`REGIME.md`](REGIME.md) — market-regime boundaries (governs every backtest / base-rate / historical
  window; slice 20). Same posture as `DATA_SOURCES.md`: pinned from evidence, open items named.
- [`PATTERN-CATALOG-SPEC.md`](PATTERN-CATALOG-SPEC.md) — subordinate spec for the LD-14 pattern
  catalog (P1–P4 presentation rules, feature vocabulary, estimation-honesty protocol).
- [`design/`](design/) — hifi UI design target (VectorLab handoff: index + screen specs + HTML prototype).
