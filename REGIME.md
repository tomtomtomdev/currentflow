# REGIME.md — market-regime boundaries (companion to LOCKED_SPEC / DATA_SOURCES)

**Status:** companion reference, same posture as `DATA_SOURCES.md` — pinned from evidence,
open items named, never guessed in code. Governs every backtest window, base-rate
estimation window, and historical derivation in the repo.

**The rule this file exists to enforce:** all engine constants (`config.TICK_BANDS`,
ARA/ARB band derivation, fee stack, session times) are pinned to the **current IDX
regime**. Any computation over data generated under *earlier* rules is invalid unless
the data is clamped to the regime boundaries below. There is no era-versioned constant
system — by decision (see §4), the system is **current-regime-scoped**, not
multi-regime.

---

## 1. Boundaries (config constants)

| Constant | Value | Applies to |
|---|---|---|
| `REGIME_START_TRACK_A` | **2024-01-01** | LQ45/IDX80 large-caps (foreign-flow-led) |
| `REGIME_START_TRACK_B` | **2024-07-01** | lapis-2 / IDXSMC-LIQ (broker-concentration-led) |
| `CATALOG_HOLDOUT_START` | **2026-01-01** | pattern-catalog estimation/OOS seam (§3) |

Per-name clamp: every historical read for a name is bounded below by
`REGIME_START[track(name)]`. Portfolio-level runs spanning both tracks start at the
**Track B** boundary (the simple option — per-name windows complicate fold alignment
for ~6 months of extra Track A data; revisit only if Track A n proves insufficient).

## 2. Rationale — what defines each boundary

**Track A = 2024-01-01.** The binding events for large caps were the COVID-emergency
unwinds, staged through 2023: trading hours restored (early 2023) and the auto-rejection
(ARB) re-normalization completed in stages during 2023. 2024-01-01 clears the final
stage with buffer and is calendar-clean for fold construction. FCA (below) does not
bind LQ45/IDX80 names.

> ⚠️ **VERIFY (operator):** the exact effective date of the final ARB normalization
> stage against the IDX announcement (KEP/PENG series, H2-2023). The staged-through-2023
> shape is high-confidence; the precise month is not. If the final stage lands later
> than expected, move `REGIME_START_TRACK_A` after it — never before.

**Track B = 2024-07-01.** The *papan pemantauan khusus* / **full call auction (FCA)**
mechanism launched Dec 2023 (hybrid), went full FCA ~end of Q1 2024, then had its
criteria **revised after retail backlash**. FCA removes continuous price discovery for
flagged names — exactly the segment Track B trades — so pre-FCA and transition-period
small-cap data is generated under a different game. Mid-2024 clears both the mechanism
change and the criteria churn.

> ⚠️ **VERIFY (operator):** the date the revised FCA criteria took effect (H1-2024
> announcements). If the last revision post-dates 2024-07-01, move
> `REGIME_START_TRACK_B` after it.

## 3. Estimation / out-of-sample seam

For the pattern catalog (see `PATTERN-CATALOG-SPEC.md`):

- **Estimation window:** `REGIME_START[track]` → `CATALOG_HOLDOUT_START`
- **OOS window:** `CATALOG_HOLDOUT_START` → present (grows daily via the scheduler)

A base rate is *estimated* only on the estimation window; the OOS window is a first
decay check (a pattern whose OOS rate collapses vs estimate is flagged, never averaged
in). Forward accrual past the seam continually widens OOS — the seam never moves
forward silently; moving it is a re-estimation event recorded in PROGRESS.md.

## 4. What current-regime scoping trades away (accepted)

- **No cross-regime stability check.** Every catalog claim carries the label
  `stability: UNKNOWN (current regime only)`. We cannot distinguish a structural edge
  from a this-regime edge. Accepted: the system trades in this regime.
- **~2.5y ceiling on n.** Rare patterns will carry wide intervals for a long time.
  Accepted: wide-but-honest over borrowed-and-contaminated (pooling pre-2024 data
  would mix FCA/COVID/pre-COVID microstructure into current-regime estimates).

## 5. Tripwire — when IDX changes the rules again

On any IDX microstructure announcement (tick bands / auto-rejection / board mechanics /
session times / FCA criteria):

1. Add a row to the table in §6 with the effective date and what changed.
2. Decide per track whether the change re-defines the regime. If yes:
   `REGIME_START_[TRACK]` moves to the new effective date (+ settle buffer if the
   change has transition churn), all derived artifacts and catalog estimates are
   **re-derived**, and the event is logged in PROGRESS.md decisions.
3. Engine constants (`TICK_BANDS`, band derivation, fees, sessions) are updated to the
   new rules in the same change — the invariant "constants == current regime" must
   never be false for more than one release.

Recent form suggests roughly one such event per year. This procedure is the entire
cost of not building an era-versioned constant system.

## 6. Rule-change log (append-only)

| Effective | Change | Bound tracks | Action taken |
|---|---|---|---|
| 2023 (staged) | ARB re-normalization from COVID 7% regime | A, B | pre-boundary — excluded by `REGIME_START_TRACK_A` |
| 2023-12 | FCA / papan pemantauan khusus launch (hybrid) | B | pre-boundary — excluded by `REGIME_START_TRACK_B` |
| ~2024-Q1/Q2 | Full FCA + criteria revisions | B | pre-boundary — excluded by `REGIME_START_TRACK_B` |
| — | *(next IDX change lands here)* | — | — |

---

*Boundaries chosen 2026-07-15 (operator decision: Track B mid-2024, Track A "slightly
looser"). Both ⚠️ VERIFY items are operator actions against primary IDX announcements
before the slice-17 backfill is declared complete — the backfill itself may proceed
(re-clamping stored data is cheap; re-estimating the catalog is the only redo cost).*
