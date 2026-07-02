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
| 3 · Foreign Flow Dashboard + Money Flow Replay | ⬜ | — | Not started. |
| 4 · Phase classifier + SMS (internal) + veto | ⬜ | — | Not started. |
| 5 · Stage-2 distribution / trap layer | ⬜ | — | Not started. |
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
| Foreign Flow Dashboard | ⬜ not built | Pure observation. |
| Institutional Accumulation Detector | ⬜ not built | Pure observation. |
| Money Flow Replay | ⬜ not built | Audit tool — build early. |
| Smart Money Heatmap | ⬜ not built | Derived visualization. |
| Sector Rotation Map | ⬜ not built | Derived visualization. |
| Portfolio Risk Monitor | ⬜ not built | Risk observation, not return prediction. |

### Gated modules — NO number until earned (RULE B)
| Module | Validation state | Paper-months accrued | Notes |
|---|---|---|---|
| Smart Money Score (SMS) | `OBSERVATION_ONLY` | 0 / 3 | Components shown as raw observation; number hidden. |
| AI Buy/Sell Ranking | `OBSERVATION_ONLY` | 0 / 3 | "flow-derived ranking, not a recommendation." Also gated by LD-8. |
| Daily Top Opportunities | `OBSERVATION_ONLY` | 0 / 3 | "highest flow-signal names today," observation framing. |

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
