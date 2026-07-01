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

## Slice 2 — Universe gate (§3) + Broker Flow Analyzer  ⬜
**Goal:** first end-to-end vertical; proves the pipeline.
- [ ] Hard floor: ADV ≥ IDR 10bn (20d), price ≥ 100, not suspended, no IPO<60d, no ARA/ARB-pinned close, complete broker summary, no corp action ±5d.
- [ ] Track A/B assignment from `emitten/{sym}/info.indexes` (A = LQ45/IDX80 & ADV≥25bn; B = rest).
- [ ] Index-rebalancing filter: down-weight pure-beta moves 30% (don't reject).
- [ ] ARA/ARB band derivation (`DATA_SOURCES.md` §3.2): board type + prev close → pinned check.
- [ ] **Broker Flow Analyzer** (observation, no gate): per-stock broker net buy/sell, broker DNA
      (Foreign/Local Inst / Smart Money / Retail / Prop), top-N share + Herfindahl, persistence,
      custom syndicate grouping, buyer-vs-seller matrix.
- [ ] SCR-0 eligibility screener wired (server-side pre-filter → ~100–150 names).
- **Manual armed-list alerts; validate signal quality 2–4 weeks before automating.**
- **Tests:** universe-gate unit tests; ARA/ARB math; broker concentration/Herfindahl.

## Slice 3 — Foreign Flow Dashboard + Money Flow Replay  ⬜
**Goal:** build the audit tool early.
- [ ] **Foreign Flow Dashboard** (observation): NBSA magnitude & persistence vs float,
      foreign-vs-domestic split (market/stock/sector), flow-reversal detection, KSEI overlay.
- [ ] **Money Flow Replay** (timeline): scrub historical flow/price for any name; price/volume/
      foreign/broker on one axis. **The audit tool for every downstream signal.**
- [ ] SCR-1A foreign-accumulation screener (Track A, LQ45 scope).
- **Tests:** replay/audit test — reconstruct any past signal from stored `as_of` data.

## Slice 4 — Phase classifier + SMS (internal) + veto filters  ⬜
**Goal:** the core decision engine — internal only, gated by RULE B.
- [ ] **Wyckoff phase classifier (RULE A HARD GATE):** PASS only if Phase C/D accumulation.
- [ ] **SMS (§4)** track-specific weights → 0–100, **INTERNAL, no number displayed.**
- [ ] Veto filters (§5): single-bandar monopoly (>60%), distribution-dressed-as-accum, retail-FOMO
      (>60%), event-driven news, phase mismatch.
- [ ] `ARMED` state: SMS≥70 AND phase∈{C,D} AND no veto → watchlist (no score shown).
- [ ] **Institutional Accumulation Detector** + **Smart Money Heatmap** as observation over these.
- [ ] Backtest 2+ yrs with full fees & look-ahead controls.
- [ ] SCR-1B (bandar accum, IDXSMC-LIQ), SCR-1C (stealth divergence proxy), SCR-2 (RVOL) wired.
- **Tests:** phase-gate rejects non-C/D on labeled charts; RULE B — SMS number hidden pre-validation.

## Slice 5 — Stage-2 distribution / trap layer  ⬜
**Goal:** the credibility layer.
- [ ] Distribution / UTAD / no-demand / trap detectors.
- [ ] Wire trap/veto flags into **every** view.
- [ ] SCR-EXIT distribution/mirror screener runs continuously over open + ARMED names.

## Slice 6 — Sector Rotation Map + Portfolio Risk Monitor  ⬜
**Goal:** Stage-4 gates surfaced as risk observations (not return predictions).
- [ ] **Sector Rotation Map:** flow by sector, RS-vs-flow quadrant, foreign/domestic tide.
- [ ] **Portfolio Risk Monitor:** crowding ("same bandar" corr), beta vs IHSG, sector Herfindahl,
      VaR, liquidity/days-to-exit, gap/event risk, scenario stress.
- [ ] Feed §6 exposure caps (≤10%/name, ≤30%/sector) + correlated-pair check.

## Slice 7 — Execution  ⬜
**Goal:** trigger → order → fill → risk; run forward-paper.
- [ ] Technical trigger (LD-3): Spring-test OR LPS; compute stop + R:R; require **R:R ≥ 2:1** or skip.
- [ ] Fundamental tilt (§7): Magic Formula rank (EY=1/2897, ROC=13411) → COMPOUNDER/NEUTRAL/SPECULATIVE;
      FLOW_ONLY dual-track for financials/utilities (sector proxy).
- [ ] Order gen: **limit only**, size to 1% risk, conviction multiplier, exposure caps, circuit breakers.
- [ ] **IDX-aware paper fill engine (§12):** lot=100, tick bands, ARA/ARB reject, next-open + slippage,
      FULL fee stack (broker + levy + VAT + 0.1% sell tax), T+2.
- [ ] Risk/exit mgr (§8): stop, target, trailing, signal-decay exit (divergence = best exit signal).
- [ ] SCR-3 trend-confirm, SCR-4 fundamental-tilt screeners wired.
- **Tests:** fill-engine reproduces lot/tick/ARA-ARB/fee hand-checked cases; every order is a limit
  with defined stop and R:R≥2:1.

## Slice 8 — Paper-trade validation wiring (RULE B switch)  ⬜
**Goal:** connect forward results to per-module validation state.
- [ ] Backtest ⇄ forward-paper share one fill engine; results reconcile.
- [ ] Per-module validation state; `PAPER_VALIDATION_MONTHS` (default 3) promotes observation → claim.
- [ ] Implement the observation↔claim presentation switch across all gated modules (SMS, AI ranking,
      Daily Top Opportunities).
- [ ] Benchmark net-of-fees to LQ45 / sector index — never IHSG.
- **Tests:** RULE B end-to-end — a module shows its number only after clearing validation.

## Slice 9 — Scale / ML (gated)  ⬜
**Goal:** only after ≥3 months positive forward-paper walk-forward Sharpe (LD-8).
- [ ] ML strictly as signal-weight optimizer / ranker on engineered features.
- [ ] Mandatory purged/embargoed CV + out-of-sample; no live weight hand-editing.

---

## Acceptance criteria (definition of done — `LOCKED_SPEC.md` §13)

- [ ] Look-ahead test passes (no `availability_ts >= decision_ts`).
- [ ] Phase gate rejects all non-C/D candidates (unit-tested on labeled charts).
- [ ] No unvalidated module displays a number (RULE B test).
- [ ] Per-module validation state drives the observation↔claim UI switch.
- [ ] Every order is a limit order with a defined stop and R:R ≥ 2:1.
- [ ] Fill engine reproduces lot/tick/ARA-ARB/fee math against hand-checked cases.
- [ ] Backtest and forward-paper share one fill engine; results reconcile.
- [ ] Reported return is net of full fee stack, benchmarked to LQ45/sector (not IHSG).
- [ ] Money Flow Replay reconstructs any past signal from stored `as_of` data.
- [ ] All data stays local; nothing republished.
- [ ] No live hand-editing of SMS weights; tuning only via walk-forward optimizer.
