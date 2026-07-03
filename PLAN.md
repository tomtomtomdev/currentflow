# PLAN.md — CurrentFlow execution plan

Slice-by-slice plan derived from `LOCKED_SPEC.md` §11 (Build Order), with data endpoints from
`DATA_SOURCES.md` §6 and server-side pre-filters from `screeners.md`.

**UI target:** each view slice below rebuilds its module from the hifi design handoff in
[`design/`](design/) (README + prototype). The 8 design modules map to slices as: Broker Flow →
S2, Foreign Flow + Money Replay → S3, Accumulation Detector + Smart Heatmap → S4, Sector Rotate +
Risk Monitor → S6, SMS/Rank (RULE B centerpiece) → gated, shipped observation-only from S4 and
promoted to claim at S8. Match the design's tokens/geometry; wire real `as_of` data, not the
prototype's seeded mock.

**Governing filter throughout:** if a signal can't survive fill-realistic paper trading, it does
not earn the right to show a number (RULE B). Each slice is a full vertical: **data → signal →
view → test**. Nothing renders until its data is trustworthy.

Legend: ⬜ not started · 🟡 in progress · ✅ done. Keep the box in sync with `PROGRESS.md`.

---

## Slice 1 — Data layer + integrity checks  ✅
**Goal:** trustworthy ingestion before anything renders.
- [x] `ExodusClient`: Bearer auth + token refresh/re-capture (one refresh on 401); 401 → fail loud, no retry; paywall/rate-limit/5xx exponential backoff (2,4,8,16s). Transport injected. `currentflow/dal/client.py`.
- [x] `broker_summary(sym, from, to)` — `marketdetectors/{sym}` → `list[BrokerNet]` (buy/sell, investor tag, accumulator VWAP).
- [x] `ohlcv_foreign(sym, from, to)` — `company-price-feed/historical/summary/{sym}` → `list[DailyBar]` (OHLCV + foreign + VWAP).
- [x] `as_of` (availability_ts) stamped on every record (`dal/timing.py`); DuckDB store keyed `(symbol, date, as_of)` (`store/db.py`, `store/schema.py`).
- [x] Ingest-once cache: `ingest_symbol` fetches only missing trading days; writes `ON CONFLICT DO NOTHING`; re-pull is a no-op (`ingest/pipeline.py`).
- [x] Integrity/gap checks: TRADED / NO_TRADES / NOT_PUBLISHED / GAP; missing never read as zero (`store/integrity.py`).
- [~] **Empirically measure broker-summary publish latency** (LD-5): measurement tool built (`ingest/publish_latency.py`); actual pinning of `config.BROKER_PUBLISH_LATENCY` awaits accrued live data — conservative next-day fallback in force until then.
- [x] **Tests (23 passing):** look-ahead (`as_of < decision_ts`, strict, latest-visible); gap-vs-zero; cache-idempotency; + parser/client/pipeline.
- [~] **Live transport not wired:** `login/v6` + MFA Bearer capture and the real httpx transport need operator credentials (out of scope for CI). The client is transport-agnostic and ready to accept it.

## Slice 2 — Universe gate (§3) + Broker Flow Analyzer  ✅
**Goal:** first end-to-end vertical; proves the pipeline.
- [x] Hard floor: ADV ≥ IDR 10bn (20d), price ≥ 100, not suspended, no IPO<60d, no ARA/ARB-pinned close, complete broker summary, no corp action ±5d (`universe/gate.py`; every reject carries reasons — no silent caps).
- [x] Track A/B assignment from `emitten/{sym}/info.indexes` (A = LQ45/IDX80 & ADV≥25bn; B = rest).
- [x] Index-rebalancing filter: down-weight pure-beta moves 30% (don't reject) — `universe/rebalance.py`; multiplier consumed by SMS at slice 4.
- [x] ARA/ARB band derivation (`DATA_SOURCES.md` §3.2): board type + prev close → pinned check (`universe/bands.py`; dev-board 10–25% resolved by price tier, see PROGRESS decisions).
- [x] **Broker Flow Analyzer** (observation, no gate): per-stock broker net buy/sell, broker DNA
      (Foreign/Local Inst / Smart Money / Retail / Prop), top-N share + Herfindahl, persistence,
      custom syndicate grouping, buyer-vs-seller matrix (`signals/broker_flow.py`; Streamlit view
      `ui/app.py` + `ui/broker_flow_view.py`). DNA registry seeded from design handoff — illustrative,
      operator-verified over time.
- [x] SCR-0 eligibility screener wired (server-side pre-filter → ~100–150 names): `screeners/scr0.py`,
      POST via `ExodusClient.run_screener`, cached to DuckDB `scr0_eligible` with `as_of`.
- [x] New DAL feeds: `symbol_info`, `corp_actions`, `special_board` + tolerant parsers.
- **Manual armed-list alerts; validate signal quality 2–4 weeks before automating.**
- [x] **Tests (60 new, 83 total passing):** universe-gate unit tests (each rule + multi-failure);
      ARA/ARB hand-checked math; broker concentration/Herfindahl hand-checked; persistence;
      look-ahead through the analyzer; SCR-0 template fidelity + ingest-once cache.

## Slice 3 — Foreign Flow Dashboard + Money Flow Replay  ✅
**Goal:** build the audit tool early.
- [x] **Foreign Flow Dashboard** (observation): NBSA magnitude (vs-20d multiple + z-score,
      both measurements), persistence & flow-reversal detection, NBSA-as-%-of-float (from
      latest visible SCR-0 row), foreign-vs-domestic split (net + turnover share),
      market/sector tide aggregate (operator sector map; skips logged, never zeroed),
      KSEI monthly ownership overlay (`signals/foreign_flow.py`; view `ui/foreign_flow_view.py`).
- [x] **Money Flow Replay** (timeline): one frame per trading day, each reconstructed by
      re-reading the store at that day's historical `decision_ts` (D+1 09:15 WIB — after
      D's EOD bar and the LD-5 conservative broker publish); price/volume/foreign/broker
      lanes; gaps render as empty frames; Wyckoff phase lane stays a placeholder until the
      slice-4 classifier exists (`signals/replay.py`; view `ui/replay_view.py`).
- [x] SCR-1A foreign-accumulation screener (Track A, LQ45 scope; IDX80 scope constant
      provided): `screeners/scr1a.py`, cached to DuckDB `scr1a_foreign_accum` with `as_of`.
- [x] New DAL feed: `ksei_ownership` (`emitten-metadata/shareholders/{sym}/chart`),
      `as_of` = fetch time (KSEI publish lag undisclosed — conservative by construction).
- [x] **Tests (27 new, 110 total passing):** replay/audit acceptance test — future invisible,
      revisions respect `as_of`, LD-5 broker availability honored, frames reconcile exactly
      with live `foreign_flow`/`broker_flow` signals at the same historical `decision_ts`;
      hand-checked NBSA stats; reversal/persistence; missing-≠-zero; SCR-1A template fidelity
      + ingest-once; KSEI parser shapes + look-ahead.

## Slice 4 — Phase classifier + SMS (internal) + veto filters  ✅
**Goal:** the core decision engine — internal only, gated by RULE B.
- [x] **Wyckoff phase classifier (RULE A HARD GATE):** detector-fed (selling climax →
      trading range → spring / SOS+LPS / UTAD); PASS only Phase C/D (`signals/phase.py`).
- [x] **SMS (§4)** track-specific weights (`config.SMS_WEIGHTS`, the only tunable surface)
      → components + internal 0–100; **number GATED, never displayed** (`signals/sms.py`).
- [x] Veto filters (§5) full v1.1 trap taxonomy: single-bandar monopoly (>60%),
      distribution-dressed / dominant-buyer flip / UTAD, markup-on-thin-volume (up-spike),
      wash/churn, broker rotation, retail-FOMO (>60%), event-driven, phase mismatch (`signals/veto.py`).
- [x] `ARMED` state: SMS≥70 AND phase∈{C,D} AND no veto → watchlist, **no score shown**
      (`signals/engine.py`; states GATE_REJECTED/VETOED/WATCH/ARMED).
- [x] Per-module validation state drives the observation↔claim switch (`validation/state.py`);
      SMS/Rank ships observation-only (components) — `ui/sms_view.py` withholds the number.
- [x] **Institutional Accumulation Detector** (`signals/accumulation.py`) + **Smart Money
      Heatmap** (`signals/heatmap.py`) as observation; wired into Streamlit; replay phase lane lit.
- [~] **Backtest 2+ yrs** — DEFERRED to slice 7: a fee-realistic backtest must share the
      IDX fill engine (§11/§13 "backtest and forward-paper share one fill engine"), which lands
      in slice 7. Engine + phase are already look-ahead-safe and replay-auditable; running a
      P&L backtest now (no fills/fees) would violate that discipline. Logged in PROGRESS decisions.
- [x] SCR-1B (bandar accum, IDXSMC-LIQ), SCR-1C (stealth divergence proxy), SCR-2 (RVOL) wired
      (`screeners/scr1b.py`, `scr1c.py`, `scr2.py`; cached to DuckDB with `as_of`).
- [x] **Tests (45 new, 155 total):** phase-gate rejects non-C/D on labeled charts; RULE B — SMS
      number hidden pre-validation and revealed only when VALIDATED; veto taxonomy per labeled case;
      engine state machine + look-ahead; SMS component math; accumulation/heatmap; screener fidelity.

## Slice 5 — Stage-2 distribution / trap layer  ✅
**Goal:** the credibility layer.
- [x] **§8 signal-decay detectors** (`signals/distribution.py`, pure observation): PHASE_ROLLOVER
      (phase → DISTRIBUTION / UTAD), NO_DEMAND (up bar, narrow spread, shrinking volume — VSA),
      BEARISH_DIVERGENCE (price up while net flow falls — "the single best exit signal", §8),
      FOREIGN_OUTFLOW (NBSA sell streak). Exit-side complement to the slice-4 §5 entry vetoes;
      categorical severities (INFO/WATCH/WARN), never a number (RULE B); `missing ≠ zero`.
- [x] **Wire trap/veto flags into every view:** `TrapMonitor` unifies §5 veto traps + §8 decay
      from one look-ahead-safe read; `ui/trap_view.py` ribbon (most-severe-first) is rendered at
      the top of every built module (Broker Flow, Foreign Flow, Accum, Replay, Heatmap, SMS).
- [x] **SCR-EXIT** distribution/mirror screener (`screeners/scr_exit.py`) exactly per screeners.md
      (14400<0 ∧ 13540<0 ∧ 13562>2), cached to DuckDB `scr_exit_distribution` with `as_of`,
      ingest-once, look-ahead-safe read; `exit_flags_for` intersects survivors with the open+ARMED
      watchlist (off-watch names logged, never silently dropped).
- [x] **Tests (17 new, 172 total):** each decay detector fires on its labeled chart; clean
      accumulation stays clean (no false alarms); look-ahead-safe monitor; missing≠zero; SCR-EXIT
      template fidelity + ingest-once + watchlist intersection; ribbon severity ordering + RULE B.

## Slice 6 — Sector Rotation Map + Portfolio Risk Monitor  ✅
**Goal:** Stage-4 gates surfaced as risk observations (not return predictions).
- [x] **Sector Rotation Map** (derived view): net-foreign flow by sector on the RS-vs-flow
      quadrant (LEADERS / EARLY_RECOVERY / DISTRIBUTION_WARN / AVOID — spec §9 labels),
      relative strength vs the universe (equal-weight proxy), foreign/domestic tide.
      Look-ahead-safe; `missing ≠ zero` (no-data symbols skipped+logged, a sector missing an
      axis carries `quadrant=None`). `signals/sector_rotation.py`, view `ui/sector_view.py`.
- [x] **Portfolio Risk Monitor** (observation, risk ≠ prediction): §6 exposure caps, sector
      Herfindahl, "same-bandar" crowding matrix (broker-overlap cosine) + correlated-pair check,
      β vs an injected benchmark, historical VaR (95%·1d), liquidity/days-to-exit, scenario
      stress (defined what-ifs), §6 circuit breakers. `signals/risk_monitor.py`, view
      `ui/risk_view.py`. Positions are an input (fill engine → slice 7); P&L withheld until an
      entry price exists.
- [x] Feed §6 exposure caps (≤10%/name, ≤30%/sector) + correlated-pair check (crowding ρ ≥ 0.7).
- [x] Both wired into the Streamlit nav (Sector Rotate, Risk Monitor).
- [x] **Tests (20 new, 192 total):** quadrant classification + RS/flow aggregation + look-ahead +
      missing≠zero; exposure caps, HHI, crowding/shared-broker, β (incl. zero-variance guard),
      historical VaR nearest-rank, days-to-exit, scenario impacts, circuit-breaker states,
      end-to-end report + look-ahead firewall on broker flow.

## Slice 7 — Execution  ✅
**Goal:** trigger → order → fill → risk; run forward-paper.
- [x] Technical trigger (LD-3): Spring-test (Phase C) OR LPS (Phase D); stop below spring/LPS swing low;
      first target = AR high (C) / measured move resistance+span (D); **R:R ≥ 2:1 or skip** (`execution/trigger.py`).
- [x] Fundamental tilt (§7): Magic Formula combined-rank tercile (fitem 13474; EY=1/2897, ROC=13411) →
      COMPOUNDER ×1.0 / NEUTRAL ×0.75 / SPECULATIVE ×0.5; negative EBIT → SPECULATIVE; FLOW_ONLY dual-track
      for financials/utilities (ROE proxy can lift ×0.75→×1.0, never COMPOUNDER hold) — never a gate
      (`fundamentals/tilt.py`).
- [x] Order gen: **LIMIT only**, size to 1% risk × conviction multiplier, §6 exposure caps (10%/name,
      30%/sector), §6 circuit breakers halt new entries (`execution/order.py`).
- [x] **IDX-aware paper fill engine (§12):** lot=100, tick bands (fraksi harga), ARA/ARB reject on the
      adverse side, next-open + liquidity-tiered slippage, limit discipline, FULL fee stack (commission +
      levy + VAT-on-commission + 0.1% sell tax), T+2 settlement — the ONE fill engine (`paper/fill.py`).
- [x] Risk/exit mgr (§8): stop → target → trailing (hold-profile width) → signal-decay exit (via
      `signals.distribution`; divergence = best exit signal), capital-first priority (`execution/risk.py`).
- [x] SCR-3 trend-confirm, SCR-4 fundamental-tilt screeners wired + cached with `as_of`
      (`screeners/scr3.py`, `scr4.py`; new `Scr3Row`/`Scr4Row` + store tables).
- [x] **Tests (54 new, 246 total):** fill-engine lot/tick/ARA-ARB/fee/slippage/T+2 hand-checked; tilt
      terciles + FLOW_ONLY + negative-EBIT; trigger geometry + R:R gate; order sizing/caps/breakers;
      exit priority + signal-decay; SCR-3/4 template fidelity + ingest-once + look-ahead; end-to-end
      ARMED→trigger→tilt→order→fill invariant (LIMIT, defined stop, R:R≥2:1).
- **Deferred within slice:** forward-paper *run* + the 2-yr backtest reconciliation land in slice 8
  (they wire this shared fill engine to the validation state machine). Live fundamentals DAL feed
  (`fundamentals_live`) not yet wired — the tilt is pure over injected/SCR-4 values.

## Slice 8 — Paper-trade validation wiring (RULE B switch)  ✅
**Goal:** connect forward results to per-module validation state.
- [x] **Paper-trade runner** (`validation/runner.py`) — the forward-paper *run* deferred from slice 7:
      walks a symbol through engine → trigger → tilt → order → **shared fill engine** → risk/exit,
      emitting closed `PaperTrade`s. `run_backtest` (batch) + `run_forward` (day-by-day accrual) are
      two code paths over the same `_attempt_entry`/`_attempt_exit` helpers (hence one `fill_order`),
      so they **reconcile** over identical data (§13). Look-ahead-safe (per-day `decision_ts`).
- [x] **`PaperTrade` atom** (`validation/trade.py`) — P&L net of the full fee stack by construction
      (built from the two engine fills' cash flows; no fee math redone).
- [x] **§8 metrics** (`validation/metrics.py`) — net-of-fee total/annualised return, Sharpe, max
      drawdown, hit rate, turnover, excess-vs-benchmark; `walk_forward_sharpe` (worst of N folds).
      **IHSG is refused as a benchmark** (raises) — never the composite (§8).
- [x] **Promotion engine** (`validation/promotion.py`) — `ValidationLedger`, the server-authoritative
      *sole authority*: `record_forward_paper` is the only writer; promotes OBSERVATION_ONLY →
      VALIDATING → VALIDATED only on ≥ `PAPER_VALIDATION_MONTHS` **and** positive walk-forward Sharpe.
- [x] **Observation↔claim switch across ALL gated modules** — shared `validation.state.gated_display`
      (`•••` until VALIDATED); `ui/sms_view` refactored onto it; new `ui/ranking_view` (AI Buy/Sell
      Ranking) + `ui/daily_top_view` (Daily Top). All three wired into the Streamlit nav, reading the
      ledger's states (never a client toggle).
- [x] Benchmark net-of-fees to LQ45 / sector index — never IHSG (metrics guard + §8 discipline).
- [x] **Tests (21 new, 267 total):** runner round-trip (entry→target) + backtest/forward reconciliation
      + look-ahead firewall; metrics hand-checked + IHSG-refusal + walk-forward folds; promotion state
      machine (months × walk-forward, per-module isolation); RULE B end-to-end across all three gated
      modules (withheld pre-validation, revealed only after the ledger promotes).
- **Deferred:** a *real* multi-month forward-paper run against live-session data (needs the live DAL
  transport + accrued time) — the harness is built and tested; production modules stay OBSERVATION_ONLY
  until an actual run promotes them. Live-fundamentals feed still injected (tilt), unchanged from slice 7.

## Slice 9 — Scale / ML (gated)  ⬜
**Goal:** only after ≥3 months positive forward-paper walk-forward Sharpe (LD-8).
- [ ] ML strictly as signal-weight optimizer / ranker on engineered features.
- [ ] Mandatory purged/embargoed CV + out-of-sample; no live weight hand-editing.

---

## Acceptance criteria (definition of done — `LOCKED_SPEC.md` §13)

- [x] Look-ahead test passes (no `availability_ts >= decision_ts`).
- [x] Phase gate rejects all non-C/D candidates (unit-tested on labeled charts).
- [x] No unvalidated module displays a number (RULE B test — all 3 gated modules, slice 8).
- [x] Per-module validation state drives the observation↔claim UI switch (`ValidationLedger`, slice 8).
- [x] Every order is a limit order with a defined stop and R:R ≥ 2:1.
- [x] Fill engine reproduces lot/tick/ARA-ARB/fee math against hand-checked cases.
- [x] Backtest and forward-paper share one fill engine; results reconcile (slice 8).
- [x] Reported return is net of full fee stack, benchmarked to LQ45/sector (not IHSG — metrics guard, slice 8).
- [x] Money Flow Replay reconstructs any past signal from stored `as_of` data.
- [x] All data stays local; nothing republished.
- [x] No live hand-editing of SMS weights; tuning only via walk-forward optimizer.

> Code/test-complete for v1.1. The one thing code cannot satisfy on its own — a **real
> multi-month forward-paper run** that actually promotes a module (needs the live DAL
> transport + accrued calendar time) — is the standing deferral; the harness is built and
> tested, and modules correctly stay OBSERVATION_ONLY until such a run clears them.
