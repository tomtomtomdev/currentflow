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
| 1 · Data layer + integrity checks | ⬜ | — | Not started. |
| 2 · Universe gate + Broker Flow Analyzer | ⬜ | — | Not started. |
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
| Broker Flow Analyzer | ⬜ not built | Pure observation. |
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
