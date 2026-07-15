# PATTERN-CATALOG-SPEC.md — evidence-graded flow-pattern catalog

**Status:** subordinate spec (same rank as `screener/CONSOLIDATED_SCREENER_SPEC.md`);
`LOCKED_SPEC.md` wins all conflicts. Windows and seams come from `REGIME.md`.

**Purpose.** Convert bandarmology folklore into falsifiable, provenance-tagged claims
with per-window base rates, so (a) the SMS component shapes get an empirical footing,
and (b) statistical evidence accrues at **event** cadence instead of **trade** cadence
— directly attacking the validation-throughput deadlock recorded in PROGRESS.md
(2026-07-08: ~1 armed trade/yr/32 names; LD-11/LD-12 exist because of it). A pattern
instance needs only a flagged date and a forward outcome, not a triggered trade:
2024-07→2026-01 × ~150 names yields hundreds of observations per pattern where the
trade path yields single digits.

---

## 1. RULE B interplay (read first)

A base rate is a **historical frequency measurement about a pattern class** — like a
z-score or a vs-20d multiple, it is a measurement, not a forward claim. But it *reads*
like a probability, so its presentation is pinned:

- **P1 — catalog view only.** Base rates render exclusively in a dedicated catalog /
  research view, labeled `historical frequency · window · n · 90% CI`. They are
  **never** rendered on, next to, or joined against a live candidate row, the
  pipeline, the ARMED rail, or any evidence tab.
- **P2 — no verbs, no composites.** No pattern is ever described with buy/sell verbs,
  no cross-pattern composite score exists, and a base rate is never multiplied into
  SMS or any displayed number.
- **P3 — attaching a pattern's stats to a live name is a claim** and therefore goes
  through the standard RULE B path: a dedicated `ValidationLedger` lane, ≥
  `PAPER_VALIDATION_MONTHS` forward paper, positive walk-forward Sharpe. Until then
  the live surface may show at most the categorical fact "pattern X definition
  matched" with no number.
- **P4 — small n renders wide, never hides.** `n < CATALOG_MIN_N` (default 20) →
  the rate cell renders the interval only (no point estimate). Missing ≠ zero; a
  window with no instances renders "no instances", never 0%.

Because P1–P4 define a new presentation category, adopting this spec is a documented
`LOCKED_SPEC.md` bump (**LD-14, v1.7** — LD-13/v1.6 was already taken by the detection
-enrichment slice, so the plan draft's "LD-13, v1.6" is renumbered): *"Pattern-catalog
base rates are measurements confined to the catalog view under P1–P4; attachment to live
names is RULE-B-gated."*

## 2. Entry lifecycle

```
FOLK (claim recorded, source cited)
  → DEFINED (falsifiable machine definition written; features named)
  → ESTIMATED (backtested on the estimation window; n, rate, CI, confounds recorded)
  → OOS-CHECKED (holdout window rate computed; decay flag if collapsed)
  → live accrual (scheduler appends new instances; re-estimate on schedule)
REJECTED at any stage is a terminal, *kept* state (a disproven folk claim is a result).
```

Every entry is append-only versioned: definition changes create `pattern-name-v2`,
never mutate v1 (else the recorded rates silently describe a different pattern).

## 3. Entry schema

Stored in `pattern_catalog` (DDL in the slice-18 spec) and rendered as the catalog view.

| Field | Meaning |
|---|---|
| `pattern_id`, `version` | e.g. `quiet-accumulation`, 2 |
| `track` | A / B — a pattern is defined per track, never blended (LD-1 discipline) |
| `folk_claim`, `source` | the claim being tested + where it came from (book, forum, operator) |
| `definition` | machine-checkable predicate over named features (§4), incl. all thresholds |
| `outcome_spec` | forward horizon(s) + outcome predicate, e.g. `+20% within 60 trading days`; multiple horizons allowed, each estimated separately |
| `window_est`, `window_oos` | from `REGIME.md` §3 |
| `n_est`, `rate_est`, `ci90_est` | estimation-window instance count, hit rate, Wilson 90% interval |
| `rate_uncond` | unconditional base rate of the same outcome over the same window/universe (the null) |
| `n_oos`, `rate_oos` | holdout results; `decay_flag` if `rate_oos` outside `ci90_est` on the bad side |
| `confounds` | named + tested (minimum set: sector momentum, market regime label from `signals/regime`, liquidity tier) |
| `stability` | always `UNKNOWN (current regime only)` under REGIME.md §4 |
| `status` | FOLK / DEFINED / ESTIMATED / OOS-CHECKED / REJECTED |
| `provenance` | `[RULE]` / `[DERIVED: script, data range]` / `[OBSERVED: date, n]` per claim line |

## 4. Feature vocabulary (canonical definitions)

Patterns compose **only** these named features, computed by one shared module
(`signals/pattern_features.py`) so every pattern and every re-estimation uses identical
math. Initial set (all computable from existing store tables):

- `topN_buy_share(N, day)` — top-N buyer value share (exists: broker_flow)
- `buy_hhi(day)` — Herfindahl of buy side (exists)
- `broker_net_persistence(code, days)` — consecutive net-buy days (exists)
- `cum_net_broker_pct_float(window)` — cumulative top-broker net as % of free float (needs SCR-0 `free_float_pct`; absent float ⇒ feature absent, missing ≠ zero)
- `nbsa_zscore(window)` / `nbsa_streak(days)` — foreign flow stats (exists)
- `price_range_pct(window)`, `flow_price_divergence(window)` — from daily_bar / SMS divergence machinery
- `phase_label(day)` — the RULE A classifier's label (consumed as a *feature value*; the classifier itself is unchanged)
- `regime_label(day)`, `sector`, `liquidity_tier(adv20)` — conditioning features for confound checks
- `ksei_confirm(month_window)` — did local-institution / foreign share move in the pattern's direction in the following KSEI month(s) (exists: ksei_ownership)

Adding a feature = adding a pure function + tests here; patterns never inline ad-hoc math.

## 5. Estimation protocol (the honesty rules)

1. **Point-in-time universe (hard requirement).** Instances are scanned over
   `pit_universe(day)` (slice 17), never over a present-day SCR-0 pull. A pattern
   base rate computed on a survivor universe is invalid and must not be stored.
2. **Look-ahead.** Instance flag date uses only data with
   `availability_ts < decision_ts` (D+1 09:15 WIB, the replay convention). Outcomes
   read forward bars only.
3. **Overlap control.** Instances of the same pattern on the same name within the
   outcome horizon collapse to one (the first); else persistence patterns
   self-replicate and inflate n.
4. **The null travels with the rate.** `rate_uncond` is computed on the identical
   window/universe/outcome — a pattern is interesting only relative to it; the catalog
   view always renders both.
5. **Confounds are subtraction, not narrative.** Minimum check: recompute the rate
   within confound strata; if the edge concentrates in one stratum, the entry says so.
6. **Terminal outcomes count.** A name suspended / FCA'd / delisted during the outcome
   horizon is an outcome (recorded as such), never a dropped row.
7. **Re-estimation cadence.** Monthly scheduler job re-runs OOS accrual; full
   re-estimation only when the seam moves (REGIME.md §3) — estimates don't chase noise.

## 6. Seed patterns (initial FOLK entries)

To be defined and estimated first (all Track B unless noted; sources = operator's
source-drafts + skills research):

1. `quiet-accumulation` — low top-3 concentration + persistent cumulative net inflow vs float + range-bound price → markup
2. `concentrated-markup` — high top-1 share + rising price on rising value → continuation vs exhaustion
3. `dominant-broker-flip` — accumulator turns net seller N days → drawdown (the §5 veto's claim, now with a base rate)
4. `foreign-streak` (Track A) — NBSA buy streak ≥ K with z ≥ z₀ → outperformance vs LQ45
5. `stealth-divergence` — SCR-1C's definition as a pattern → forward outcome (the screener already flags; this measures whether it means anything)
6. `ksei-confirmed-accum` — quiet-accumulation AND next-month KSEI local-inst share up → markup (the confirmation-layer test)

Each seed's thresholds are proposed at DEFINED stage from the existing config constants
where they exist (e.g. `VETO_FLIP_MIN_DAYS`) so the catalog measures the system's own
current beliefs first.

## 7. What this feeds (and what it must not)

- **Feeds:** SMS component *shape* review (a component whose pattern shows no edge vs
  null is a candidate for the walk-forward optimizer to defund — evidence in, decision
  still via the locked §4 optimizer path); veto-filter review (a veto whose pattern
  shows no adverse base rate is a candidate for loosening — via spec bump only);
  research prioritization.
- **Must not:** write SMS weights (optimizer remains sole writer), gate or arm any
  name, surface on live candidate rows (P1), or promote any module (dedicated lanes
  only, P3).
