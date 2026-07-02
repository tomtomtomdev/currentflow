# PROGRESS.md — CurrentFlow durable log

Durable record of shipped slices and each module's **validation state** (RULE B). Update this
whenever a slice lands or a module changes validation state. Pairs with `PLAN.md` (the plan) and
`LOCKED_SPEC.md` (the spec).

`PAPER_VALIDATION_MONTHS = 3` (default). A module cannot display a number until `VALIDATED`.

---

## Shipped slices

| Slice | Status | Date | Notes |
|---|---|---|---|
| Spec & docs (LOCKED_SPEC v1.1, DATA_SOURCES, screeners) | ✅ | 2026-07-01 | Spec locked; data layer mapped to Stockbit `exodus`; server-side screeners defined. |
| Companion scaffolding (PLAN, CLAUDE, PROGRESS) | ✅ | 2026-07-01 | This file + PLAN.md + CLAUDE.md created per spec §14. |
| UI design handoff (hifi) | ✅ | 2026-07-01 | `design/` — README + `.dc.html` prototype; all 8 modules, RULE A/B enforced, seeded mock. Spec in bundle verified identical to root. Reference only, not shipped. |
| 1 · Data layer + integrity checks | ✅ | 2026-07-01 | `ExodusClient` (broker_summary + ohlcv_foreign; 401 fail-loud, refresh, exponential backoff); DuckDB store keyed `(symbol,date,as_of)`, ingest-once; integrity TRADED/NO_TRADES/NOT_PUBLISHED/GAP; look-ahead-safe reads. 23 tests pass. Pending live: `login/v6`+MFA transport & empirical broker publish-latency pinning (LD-5, conservative next-day fallback in force). |
| 2 · Universe gate + Broker Flow Analyzer | ✅ | 2026-07-02 | §3 hard floor + Track A/B + ARA/ARB derivation + rebalance down-weight (`universe/`); Broker Flow Analyzer observation module (`signals/broker_flow.py`) + Streamlit view (`ui/`); SCR-0 wired via screener POST, cached to `scr0_eligible` with `as_of`; DAL adds `symbol_info`/`corp_actions`/`special_board`. 60 new tests (83 total). Armed-list alerts stay manual pending 2–4 wks signal-quality validation. |
| 3 · Foreign Flow Dashboard + Money Flow Replay | ✅ | 2026-07-02 | Foreign Flow observation module (`signals/foreign_flow.py`): NBSA magnitude/persistence/reversal, vs-20d multiple + z-score (measurements), %-of-float, market/sector tide, KSEI overlay. Money Flow Replay (`signals/replay.py`): per-frame historical `decision_ts` re-reads (D+1 09:15 WIB), audit-tested against live signals. SCR-1A wired + cached with `as_of`; new DAL feed `ksei_ownership` (fetch-time `as_of`). Both wired into Streamlit. 27 new tests (110 total). |
| 4 · Phase classifier + SMS (internal) + veto | ✅ | 2026-07-02 | RULE A Wyckoff gate (`signals/phase.py`, detector-fed: climax→range→spring/SOS+LPS/UTAD; only C/D tradeable); internal SMS with locked §4 track weights (`signals/sms.py`, number GATED); full §5 veto taxonomy (`signals/veto.py`); engine gate→score→veto→ARMED (`signals/engine.py`); RULE B switch (`validation/state.py`) — SMS/Rank ships components only (`ui/sms_view.py`). Institutional Accumulation Detector + Smart Money Heatmap observation modules; replay phase lane lit. SCR-1B/1C/2 wired + cached. Backtest deferred to slice 7 (shared fill engine). 45 new tests (155 total). |
| 5 · Stage-2 distribution / trap layer | ✅ | 2026-07-02 | §8 signal-decay observation layer (`signals/distribution.py`): PHASE_ROLLOVER (UTAD/distribution), NO_DEMAND (VSA), BEARISH_DIVERGENCE (price up while flow falls — best exit), FOREIGN_OUTFLOW (NBSA sell streak) — categorical severities, no number (RULE B). `TrapMonitor` unifies §5 veto + §8 decay; `ui/trap_view.py` ribbon wired into every built view. SCR-EXIT screener wired + cached to `scr_exit_distribution` with `as_of`; `exit_flags_for` scopes to open+ARMED. 17 new tests (172 total). |
| 6 · Sector Rotation Map + Portfolio Risk Monitor | ⬜ | — | Not started. |
| 7 · Execution (trigger/tilt/fill/risk) | ⬜ | — | Not started. |
| 8 · Paper-trade validation wiring | ⬜ | — | Not started. |
| 9 · Scale / ML (gated) | ⬜ | — | Not started. |

---

## Module validation state (RULE B)

State machine: `OBSERVATION_ONLY` → (accrues forward-paper) → `VALIDATING` → (≥ `PAPER_VALIDATION_MONTHS`
positive walk-forward) → `VALIDATED`. Only `VALIDATED` modules may show a number.

### Ships-now — observation modules (no gate; never show a predictive number)
| Module | State | Notes |
|---|---|---|
| Broker Flow Analyzer | ✅ built (2026-07-02) | Pure observation — net flow, DNA, top-N/HHI, persistence, syndicates, matrix. No score, no verb. |
| Foreign Flow Dashboard | ✅ built (2026-07-02) | Pure observation — NBSA series, persistence, reversal, splits, KSEI overlay. Multiples/z-scores are measurements, no score, no verb. |
| Institutional Accumulation Detector | ✅ built (2026-07-02) | Pure observation — stealth divergence, accumulator VWAP, volume dry-up + price tightness. Absorption needs L2 depth → `None` (graceful). No score. |
| Money Flow Replay | ✅ built (2026-07-02) | Audit tool — every frame re-read from the store at its historical `decision_ts`; reconciles with live signals (acceptance-tested). Wyckoff phase lane now lit by the slice-4 classifier (label only, `UNKNOWN` until enough history). |
| Smart Money Heatmap | ✅ built (2026-07-02) | Derived visualization — direction + intensity (flow-as-%-of-cap), sector drill-down, local-buy/foreign-sell divergence alerts. No score. |
| Distribution / Trap layer | ✅ built (2026-07-02) | Pure observation (slice 5) — §8 signal-decay flags: PHASE_ROLLOVER, NO_DEMAND, BEARISH_DIVERGENCE, FOREIGN_OUTFLOW. `TrapMonitor` unifies §5 veto traps + §8 decay; ribbon wired into every view. Categorical severities (INFO/WATCH/WARN), no number. |
| Sector Rotation Map | ⬜ not built | Derived visualization. |
| Portfolio Risk Monitor | ⬜ not built | Risk observation, not return prediction. |

### Gated modules — NO number until earned (RULE B)
| Module | Validation state | Paper-months accrued | Notes |
|---|---|---|---|
| Smart Money Score (SMS) | `OBSERVATION_ONLY` | 0 / 3 | Built (slice 4): components shown as raw observation, internal 0–100 drives ARMED, **number withheld (`•••`)** by `validation.state` until VALIDATED. RULE-B-tested. |
| AI Buy/Sell Ranking | `OBSERVATION_ONLY` | 0 / 3 | "flow-derived ranking, not a recommendation." Also gated by LD-8. Not built. |
| Daily Top Opportunities | `OBSERVATION_ONLY` | 0 / 3 | "highest flow-signal names today," observation framing. Not built. |

---

## Validation metrics log

Record forward-paper results here as they accrue (net of full fee stack, benchmarked to LQ45/sector).

| Date | Module | Ann. return (net) | Sharpe | Max DD | Hit rate | Turnover | vs benchmark |
|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — |

---

## Decisions & deviations log

Record any spec deviation here with a reason and the spec version bump it triggered.

| Date | Change | Reason | Spec version |
|---|---|---|---|
| 2026-07-01 | Initial companion files created | Spec §14 requires PLAN/CLAUDE/PROGRESS | v1.1 (no bump) |
| 2026-07-01 | UI design handoff added under `design/` | Operator-provided hifi design target for §9 modules | v1.1 (no bump) |
| 2026-07-01 | Front-end stack = Streamlit (chose over React/hybrid) | Stay faithful to spec §10; single-user local tool; accept approximate fidelity | v1.1 (no bump) |
| 2026-07-01 | Store pinned = DuckDB (chose over SQLite) | Analytical (OLAP) workload — backtest, window-function aggregations (persistence/Herfindahl/NBSA), Replay range-scans over `(symbol, date, as_of)`; native Pandas/Polars/Arrow integration; nightly-batch single-writer ingest makes SQLite's OLTP/concurrency edge irrelevant. Within spec §10's SQLite/DuckDB allowance — pin, not a deviation. | v1.1 (no bump) |
| 2026-07-01 | Slice 1 impl choices: async DAL (injectable transport); timestamps WIB-local tz-naive; broker `as_of` = feed `data_last_updated` else conservative next-day 09:00 (LD-5) | Async matches DATA_SOURCES §6 surface + enables throttled concurrent paywalled pulls; single-exchange tz needs no zone mixing; conservative fallback keeps look-ahead honest until latency measured. Implementation detail within spec. | v1.1 (no bump) |
| 2026-07-02 | Dev-board ARA/ARB band (spec's "±10–25%" range) resolved by price tier: prev close ≥ 5000 → 10%, else 25%; ε = 0.5% absorbs tick rounding at the band; unknown board falls back to the tightest (main 7%) band | Spec §12 pins a range, not a rule; tiering mirrors IDX's tighter-band-for-higher-price convention; conservative fallback never hides a pinned close. Constants in `config.py`, tunable when measured against real ARA/ARB days. | v1.1 (no bump) |
| 2026-07-02 | Broker-DNA registry seeded from design handoff (KZ/AK/RX/ZP/YU foreign-inst, CC/NI/OD/DR local-inst, DX/AI/KI smart-money, BQ prop, YP/PD/CP/GR retail); fallback to feed's Asing/Lokal tag; unmapped local codes stay UNKNOWN | No served DNA feed; registry is operator knowledge, explicitly illustrative and overridable per call — never silently guessed. | v1.1 (no bump) |
| 2026-07-02 | Rebalance down-weight fires only when ALL of: β-residual ≤ 1%, tracker-broker flow share ≥ 50%, within ±7d of an MSCI review date (operator-maintained calendar in `universe/rebalance.py`) | §3 says down-weight pure-beta moves, not every move near a rebalance; conjunction keeps genuine alpha at full weight. Thresholds in `config.py`. | v1.1 (no bump) |
| 2026-07-02 | Replay frame for trading day D reconstructs `decision_ts` = D+1 09:15 WIB (`config.REPLAY_DECISION_TIME`, injectable) | First actionable pre-open moment at which both D's EOD bar (~16:15) and D's broker summary (LD-5 conservative D+1 09:00) are knowable; earlier moments would show broker lanes always empty. Revisit once publish latency measured. | v1.1 (no bump) |
| 2026-07-02 | KSEI ownership `as_of` = fetch time (not month-end) | KSEI publishes monthly with an undisclosed lag; stamping month-end would fabricate availability. Fetch time is the only honest availability claim — conservative by construction. | v1.1 (no bump) |
| 2026-07-02 | Foreign-vs-domestic "split" rendered as participation (turnover share) + signed nets; NBSA vs-20d multiple uses mean |net| (magnitude-robust) alongside a signed z-score | Per-stock net domestic ≡ −net foreign (two sides per trade), so a net-only split is a mirror; a signed 20d mean near zero makes the raw ratio explode. Both stats are measurements (RULE-B-safe), not scores. | v1.1 (no bump) |
| 2026-07-02 | Wyckoff classifier is **detector-fed & heuristic**: selling-climax/AR anchors the trading range (else a consolidation-base fallback); spring→C, SOS+LPS→D, UTAD/weak-up-bars→DISTRIBUTION, extension→E; thresholds in `config` (`PHASE_*`). SOS without a confirming LPS stays non-tradeable (B). | §2/LD-2 pins the *gate* (only C/D tradeable) but not an algorithm. Conservative-by-default (ambiguous → not tradeable) honors RULE A; thresholds are tunable against labeled real charts as the DNA registry is. | v1.1 (no bump) |
| 2026-07-02 | SMS components each normalize to a sub-score in [0,1]; SMS = Σ weightᵢ·subscoreᵢ × rebalance multiplier. Weights live in `config.SMS_WEIGHTS` (the only tunable surface). | §4 pins weights + the ARMED@70 threshold but not each detector's shape; normalization keeps the composite in 0–100 and each component independently observable (RULE B components). Weights isolated so only the walk-forward optimizer writes them. | v1.1 (no bump) |
| 2026-07-02 | Markup-on-thin-volume veto fires only on **up** spikes (pump), not the spring's downward shakeout | §5 defines it as "price spiking on low value traded (pump signature)" — markup is upward. Using `abs(Δprice)` mis-vetoed the Phase C spring. Fix keeps the C event intact. | v1.1 (no bump) |
| 2026-07-02 | RULE B switch: `validation.state` (per-module state, default OBSERVATION_ONLY) is the server-authoritative gate; `ui/sms_view.score_display` returns `•••` until VALIDATED. Slice 8 promotes via the paper-trade engine. | LD-9 requires the observation↔claim switch to be server-authoritative, never a client toggle. Shipping the switch now (defaulted closed) satisfies §13's RULE B acceptance test; slice 8 only flips it. | v1.1 (no bump) |
| 2026-07-02 | Replay Wyckoff phase lane now carries the classifier **label** (RULE A verdict, not a number), read over a longer look-ahead-safe base (`REPLAY_PHASE_LOOKBACK_DAYS`); `UNKNOWN` when history is short | The lane was a placeholder pending the slice-4 classifier, which now exists. A label is a gate verdict, not a score — RULE-B-safe; longer base is needed for a range to form and stays `as_of`-filtered. | v1.1 (no bump) |
| 2026-07-02 | Slice 5 distribution layer = the **exit/decay** complement to slice-4 vetoes, not a re-implementation. Veto (§5) rejects an *entry candidate*; a decay flag (§8) warns on an *open/ARMED* name. New detectors: PHASE_ROLLOVER, NO_DEMAND (VSA), BEARISH_DIVERGENCE, FOREIGN_OUTFLOW. UTAD/distribution & dominant-broker-flip stay in the phase classifier / veto and are *surfaced* (not duplicated) via `TrapMonitor`. Flags are categorical severities (INFO/WATCH/WARN), never numbers (RULE B). Thresholds in `config.DECAY_*`, tunable against labeled charts. | v1.1 (no bump) |
| 2026-07-02 | SCR-EXIT scans IHSG server-side (per screeners.md); `exit_flags_for` intersects survivors with the operator's open+ARMED watchlist so "runs continuously over open + ARMED names" (spec §11) holds without a bespoke per-name screener call — off-watch survivors are logged, never silently dropped. | v1.1 (no bump) |
| 2026-07-02 | **2-yr backtest deferred from slice 4 to slice 7** | §11/§13 require backtest and forward-paper to *share one fill engine* and report net-of-full-fee-stack returns; the IDX fill engine lands in slice 7. Running a P&L backtest now (no fills/fees/exits) would violate that discipline and produce a number the spec forbids. Engine + phase are already look-ahead-safe and replay-auditable in the interim. | v1.1 (no bump) |
