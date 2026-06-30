# IDX Money-Flow Screener — Detection-Layer Spec

> **Subordinate to `LOCKED_SPEC.md` v1.0.** This document is the detection-layer
> feature library for the system defined in `LOCKED_SPEC`. It details how the
> signals in pipeline steps **[2] Universe Gate → [5] Veto Filters** are computed,
> consolidated from the four screener PDFs in this folder (CompleteSet, Spec,
> Funnel, Framework).
>
> **Conflict rule:** where this document and `LOCKED_SPEC` differ, **`LOCKED_SPEC`
> wins.** All thresholds, weights, and gates below are inherited from `LOCKED_SPEC`
> and are not independently tunable here. Aligned 2026-06-30.
>
> Informational/educational only — not financial advice. Paper results do not
> guarantee live performance.

---

## Relationship to `LOCKED_SPEC`

`LOCKED_SPEC` is the authoritative end-to-end system (screen → arm → trigger →
size → fill → validate). This spec is the **brain that feeds its front half** —
the detection features and disqualifiers. Mapping:

| This document | `LOCKED_SPEC` pipeline step |
|---|---|
| §1 Universe gate | [2] UNIVERSE GATE (LD-4) |
| §2 Phase gate | [3] PHASE CLASSIFIER (LD-2) |
| §3 Detection features → SMS | [4] SMART MONEY SCORE (LD-1, §4) |
| §4 Veto filters | [5] VETO FILTERS (§5) |
| §5 ARMED condition | gate to `state = ARMED` |
| §6 Confirmation / entry | [6] TECHNICAL TRIGGER (LD-3) — by reference |
| §7 Fundamental tilt | [7] FUNDAMENTAL TILT (LD-6/7) — by reference |
| §8 Risk / exit | [10] RISK / EXIT MGR — by reference |

Everything downstream of ARMED (order generation, fill engine, fees, backtest vs
forward-paper, dashboard, acceptance criteria) lives **only** in `LOCKED_SPEC`.

### What changed to align (from the prior standalone v0)

The earlier standalone consolidation made four choices that `LOCKED_SPEC`
overrides. They have been removed here:

1. **Added a hard phase gate.** Accumulation is no longer purely scored — a
   Wyckoff phase classifier (Phase C/D only) is now a hard gate *before* scoring.
   Hard gates are now: universe, phase, veto (was: eligibility + risk only).
2. **Fundamentals are a multiplier, not score points.** Dropped the additive
   +15 fundamental weight; fundamentals now only set a size/hold multiplier and
   never affect the arming score.
3. **Fundamental metric is Magic Formula (EY + ROC).** Dropped ROE/PE/PB.
4. **Dual-track scoring** (large-cap vs lapis-2) replaces the single weight vector.
   Numeric floors raised to `LOCKED_SPEC` levels; IHSG dropped as a benchmark.

---

## §1 Universe gate `[HARD]` — LD-4

Run first; cheap, brutal. Fail any → dropped, never scored.

**Hard floor (all must pass):**

| Filter | Threshold |
|---|---|
| 20-day avg daily **value** traded | ≥ **IDR 10 bn** |
| Last price | ≥ **IDR 100** |
| State | not suspended |
| Listing history | **not** IPO with < 60 trading days |
| Signal-day close | did **not** close ARA/ARB-pinned (no fillable band → reject) |

**Track assignment** (decides which signals are reliable, §3):

- **Track A** — member of LQ45/IDX80 **AND** ADV ≥ IDR 25 bn → *foreign-flow-reliable*.
- **Track B** — passes the hard floor but not Track A (liquidity-gated lapis-2) → *broker-concentration-reliable*.

**Index-rebalancing filter:** if a candidate's move is explained by index/sector
beta (rolling β-adjusted return ≈ sector return, flow concentrated on
index-tracker brokers near rebalance dates), **down-weight SMS by 30%** — do not
reject. Don't pay alpha prices for beta.

*Data: broker summary, daily value traded, float, index membership, state/listing flags.*

---

## §2 Phase gate `[HARD]` — LD-2

A Wyckoff phase classifier runs **before** scoring. **PASS only if Accumulation
Phase C or D** (Spring/shakeout, or SOS → LPS). Reject Phase A/B (noise) and
anything printing BC/UTAD/distribution. The detection features in §3 *feed* this
classifier — they never bypass it. This is the #1 edge-vs-artifact decision: a
volume/flow threshold with no phase context buys distribution tops.

*Data: range structure, VPA effort-vs-result, accumulator VWAP vs price.*

---

## §3 Detection features → Smart Money Score (SMS) — LD-1 / §4

Each feature is computed on the liquid, Phase-C/D universe and combined into
**SMS (0–100)** with **track-specific weights**. Weights are the *only* tunable
surface and only via backtest Sharpe maximization with walk-forward — never
hand-edited live.

| Feature | Definition (anchor) | Track A | Track B |
|---|---|---:|---:|
| **Price-Volume Divergence** *(the spine)* | high volume, price ≤ ±0.5%, corr < 0.3 on high-vol bars | **30** | **30** |
| **Broker concentration** | top-2 net-buy share, ≥ N consecutive days, on flat/down bars | 20 | **35** |
| **NBSA foreign-flow accumulation** | net buy > 2× 20-day avg, rising | **25** | 0 |
| **Volume anomaly / RVOL** | > 3× 20-day avg | 10 | 15 |
| **Block-trade footprint** | > IDR 1 B or > 1% ADV | 5 | 10 |
| **Wyckoff phase-alignment bonus** | Spring/LPS proximity | 10 | 10 |

**Supporting detection signals** (feed the features above + the §2 phase gate;
not separately weighted):

- **Persistence** — the same small broker set net-buying across consecutive
  sessions (qualifies broker concentration; one-day blips don't count).
- **Accumulator VWAP** — dominant-accumulator est. avg price vs current price.
  Near/below current = not yet marked up (supports Phase C/D); far above =
  distribution risk (feeds §8 signal-decay).
- **Absorption** *(L2, optional / Phase 2+)* — large sell pressure repeatedly
  absorbed without price dropping; VPA stopping-volume / no-supply confirmation.

`SMS ≥ 70` is the ARMED threshold (locked, §5).

*Data: EOD/15-min broker summary, broker classification map, NBSA foreign flow, OHLCV/RVOL, VWAP engine, L2 depth (optional).*

---

## §4 Veto filters `[HARD reject, regardless of SMS]` — §5

Any one triggered → killed even at SMS 100.

- **Single-bandar monopoly** — one broker > 60% of net-buy concentration (gameable). *Note: concentration is bullish up to this point; monopoly past it is not.*
- **Distribution-dressed-as-accumulation** — high volume + up-bars closing in lower half / UTAD / no-demand rallies / dominant buyer flipping to net sell.
- **Retail-FOMO** — retail buy ratio > 60% of volume.
- **Event-driven** — material news in window (flow is reacting, not leading).
- **Phase mismatch** — anything not Phase C/D (enforced at §2, restated here).

**Trap taxonomy.** The distribution veto above is the coarse gate; these finer
screens — carried over from the Funnel source and **adopted into `LOCKED_SPEC`
§5 as of v1.1** — sharpen it and feed §8 signal-decay:

- **Markup-on-thin-volume** — price spiking on low value traded (pump, not demand).
- **Wash / churn** — same broker showing high buy AND high sell (manufactured volume).
- **Broker rotation** — buying baton passing between related/correlated broker codes (one player disguised as many).

---

## §5 ARMED condition

```
SMS ≥ 70  AND  phase ∈ {C, D}  AND  no veto   →   state = ARMED   (watchlist)
```

ARMED is **not** an entry. It sets the watchlist; entry requires the §6 trigger.
(Replaces the prior 5-band action scale; `LOCKED_SPEC` uses this state machine.)

---

## §6 Confirmation / entry `[by reference — LD-3]`

Detailed in `LOCKED_SPEC` §6; summarized for completeness:

- **Trigger:** close of Spring-test bar (narrow spread, low vol, holds above spring low) **OR** LPS pullback after SOS. Enter on the test, not the breakout impulse.
- **Order:** limit at/below trigger. No market orders.
- **Stop:** below spring low / swing low (thesis-invalidation). Never widened.
- **R:R ≥ 2:1** to first structural target (AR high / next HVN) or **skip**.
- **Relative strength** vs LQ45 / sector confirms the candidate isn't just riding market beta. **Benchmark: Track A → LQ45, Track B → sector index. Never IHSG.**

---

## §7 Fundamental tilt `[multiplier only — by reference, LD-6/7]`

Fundamentals **never block an entry** — they set the conviction multiplier and
hold horizon only.

- **Non-financials / non-utilities:** Magic Formula combined rank (EY = EBIT/EV, ROC = EBIT/(NWC+NFA)). Top tercile → **COMPOUNDER ×1.0** (hold through markup, wide trail); mid → **NEUTRAL ×0.75**; bottom / negative EBIT → **SPECULATIVE ×0.5** (tight trail, exit at first target). **Reject ROE/PE/PB** (leverage-distorted on a bank-heavy index).
- **Financials + utilities (`FLOW_ONLY`):** skip Magic Formula; sector-proxy sanity only (banks: ROE > 12%, CAR healthy). Default **×0.75**, shorter hold, tighter trail; may promote to ×1.0 but never to COMPOUNDER hold rules.

---

## §8 Risk / exit `[by reference — LD-3 / §8]`

- **Position size:** `qty = (equity × 1%) / (entry − stop)`, rounded down to whole lots. Risk locked at **1%**. Multiplied by the §7 conviction multiplier.
- **Exposure caps:** ≤ 10% equity per name; ≤ 30% per sector; correlated-pair check.
- **Circuit breakers:** halt new entries at −3% daily P&L; pause system at −10% peak-to-trough drawdown.
- **Exit (any one):** stop · target · trailing stop · **signal-decay** — NBSA flips negative / dominant broker flips to net sell / VPA prints UTAD or no-demand / phase rolls to distribution.

---

## Cadence & build order (inherited)

- **Cadence:** EOD / T+1, scheduler fires on **broker-summary publication** (not market close); look-ahead control `availability_ts < decision_ts`. Real-time tick / L2 (absorption, iceberg) deferred to Phase 2+.
- **Build order** (`LOCKED_SPEC` §9): Foundation (ingest + universe gate) → Signal (phase classifier + SMS + veto) → Execution (trigger + tilt + fill + risk) → Scale/ML (gated: only after ≥ 3 months positive forward-paper walk-forward Sharpe; ML as ranker/weight-optimizer on engineered features with purged CV — never a black-box entry generator).
