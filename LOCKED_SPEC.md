# IDX Smart-Money Screener — Locked Specification v1.0

**Status:** LOCKED. This resolves the eight contradictions from `CONSOLIDATED_THESIS.md` into single, non-negotiable decisions. Build to this; changes require a version bump and a documented reason.

**Core thesis (unchanged):** Smart-money flow *leads* → technical structure *confirms timing* → fundamental quality *sizes conviction & hold horizon*. EOD/T+1 cadence. Long-only. Liquidity-gated.

---

## 1. Decision Log — the eight, resolved

| # | Contradiction | LOCKED DECISION | Rationale |
|---|---|---|---|
| **LD-1** | Signal hierarchy | **Price-Volume Divergence is the universal spine.** Confirming-leads are tier-dependent: Track A (large-cap) = NBSA foreign flow + broker concentration co-lead; Track B (lapis-2) = broker concentration leads, foreign flow excluded. Weights locked in §4. | Foreign flow only reliable on foreign-held large-caps; broker concentration is the lapis-2 signal. Divergence is confirmed by every source. |
| **LD-2** | Cycle-phase blindness | **Wyckoff phase classifier is a HARD GATE before scoring.** Only Accumulation **Phase C or D** is tradeable. Threshold detectors *feed* the classifier; they never bypass it. | A volume/flow threshold with no phase context buys distribution tops. This is the #1 edge-vs-artifact decision. |
| **LD-3** | Entry discipline | **Grimes wins. No market-on-signal.** A passing score sets state = `ARMED`, not `ENTER`. Entry requires a confirmation trigger (Spring-test or LPS) via **limit order**, and **R:R ≥ 2:1** or no trade. | Paper 3 agrees on mechanics (next-open + ARA/ARB make market fills fiction anyway). |
| **LD-4** | Universe direction | **Hard liquidity floor is absolute** (§3). Within the liquid set, an index-rebalancing filter down-weights pure-beta moves. **Never chase illiquidity for alpha.** | Resolves "non-benchmark alpha" vs "stay liquid": stay liquid, then strip index noise *inside* the liquid set. |
| **LD-5** | Data cadence | **EOD/T+1 first.** Scheduler fires on **broker-summary publication**, not market close. Real-time tick / L2 order book DEFERRED to Phase 2+, added only if an intraday signal proves incremental edge. | The binding signal (broker summary) is EOD. Low-latency architecture is over-engineering for a T+1 signal. |
| **LD-6** | Fundamental layer | **Present, as a conviction/horizon TILT — not an entry gate.** Metric = **Magic Formula (EY = EBIT/EV, ROC = EBIT/(NWC+NFA))**. Reject ROE/PE/PB. | ROE/PE/PB distorted by leverage — fatal on a bank-heavy index. Fundamentals decide *how much / how long*, not *whether to enter*. |
| **LD-7** | Financials-exclusion paradox | **Dual-track scoring.** Financials (bank/insurance/multifinance) + utilities run **flow+technical only**, scored with sector proxies (banks: ROE, NIM, CAR), flagged `FLOW_ONLY → shorter hold, tighter trail`. They stay in the universe. | They are the most liquid, most Wyckoff-able, most foreign-flow-driven names. Excluding them discards the best universe. |
| **LD-8** | ML vs overfitting | **Deferred to Phase 4+ and gated.** Rules system must first show **≥3 months forward-paper with positive walk-forward Sharpe**. ML role = signal-weight optimizer / ranker on engineered features ONLY — never a black-box entry generator. Mandatory purged/embargoed CV + out-of-sample. | Reflexive, non-stationary, small-sample IDX flow data overfits trivially. |

---

## 2. Pipeline (locked)

```
SCHEDULER (fires on broker-summary publication, ~T+0 evening / T+1)
   │   look-ahead control: every datum stamped with availability_ts;
   │   a signal may use a datum ONLY IF availability_ts < decision_ts
   ▼
[1] INGEST   OHLCV · broker summary · NBSA foreign flow · corp actions ·
             halt/suspend flags · KSEI ownership · financials (TTM)
   ▼
[2] UNIVERSE GATE (§3)        — hard liquidity floor; assign Track A / B; tag sector
   ▼
[3] PHASE CLASSIFIER (LD-2)   — Wyckoff phase; PASS only if Phase C or D accumulation
   ▼
[4] SMART MONEY SCORE (§4)    — track-specific weights → SMS 0–100
   ▼
[5] VETO FILTERS (§5)         — kill single-bandar / distribution-dressed-as-accum / news
   ▼
   SMS ≥ 70  AND  phase ∈ {C,D}  AND  no veto  →  state = ARMED  (watchlist)
   ▼
[6] TECHNICAL TRIGGER (LD-3)  — Spring-test OR LPS; compute stop + R:R; require R:R ≥ 2:1
   ▼
[7] FUNDAMENTAL TILT (LD-6/7) — MF rank (or sector proxy) → conviction & hold horizon
   ▼
[8] ORDER GEN                 — limit @ trigger; size to 1% risk (§6)
   ▼
[9] PAPER FILL ENGINE         — next-open + slippage; lot=100; tick bands; ARA/ARB reject;
                                FULL fee stack (broker + levy + VAT + 0.1% sell tax)
   ▼
[10] RISK / EXIT MGR (§6,§7)  — stop · target · trailing · signal-decay exit
   ▼
[11] BACKTEST ⇄ FORWARD-PAPER (separate code paths, shared fill engine)
   ▼
[12] DASHBOARD               — P&L, armed list, flow, attribution vs benchmark (§8)
```

---

## 3. Universe Gate (LD-4) — locked thresholds

**Hard floor (all must pass):**
- 20-day avg daily value traded **≥ IDR 10 bn**
- Last price **≥ IDR 100**
- Not suspended; not IPO with < 60 trading days of history
- Did not close ARA/ARB-pinned on the signal day (no fillable band → reject)

**Track assignment:**
- **Track A** — member of LQ45/IDX80 AND ADV ≥ IDR 25 bn → foreign-flow-reliable
- **Track B** — passes hard floor, not Track A → broker-concentration-reliable

**Index-rebalancing filter:** if a candidate's move is explained by index/sector beta (rolling β-adjusted return ≈ sector return, flow concentrated on index-tracker brokers near rebalance dates), **down-weight SMS by 30%**. Don't reject — just stop paying alpha prices for beta.

---

## 4. Smart Money Score (LD-1) — locked weights (0–100)

| Component | Track A (large-cap) | Track B (lapis-2) |
|---|---|---|
| **Price-Volume Divergence** (high vol, ≤ ±0.5% price; corr < 0.3 on high-vol bars) | **30** | **30** |
| Broker concentration (top-2 net-buy share, ≥ N consecutive days, on flat/down bars) | 20 | **35** |
| NBSA foreign-flow accumulation (net buy > 2× 20d avg, rising) | **25** | 0 |
| Volume anomaly / RVOL (> 3× 20d avg) | 10 | 15 |
| Block-trade footprint (> IDR 1B or > 1% ADV) | 5 | 10 |
| Wyckoff phase-alignment bonus (Spring/LPS proximity) | 10 | 10 |

`SMS ≥ 70` = ARMED threshold (locked). Weights are the **only** tunable surface, and only via backtest Sharpe maximization with walk-forward — never hand-edited live.

---

## 5. Veto Filters (hard reject regardless of SMS)

- **Single-bandar monopoly** — one broker > 60% of net-buy concentration (gameable).
- **Distribution-dressed-as-accumulation** — high volume + up-bars closing in lower half / UTAD / no-demand rallies / dominant buyer flipping to net sell.
- **Retail-FOMO** — retail buy ratio > 60% of volume.
- **Event-driven** — material news in window (flow is reacting, not leading).
- **Phase mismatch** — anything not Phase C/D (enforced at [3], restated here).

---

## 6. Entry, Sizing, Risk (LD-3) — locked

- **Trigger:** close of Spring-*test* bar (narrow spread, low vol, holds above spring low) **OR** LPS pullback after SOS.
- **Order:** limit at/below trigger price. No market orders.
- **Stop:** below spring low / swing low (thesis-invalidation level). Never widened.
- **R:R:** ≥ 2:1 to first structural target (AR high / next HVN) or **skip**.
- **Position size:** `qty = (equity × 1%) / (entry − stop)`, rounded down to whole lots. **Risk locked at 1%** (not 2% — IDX manipulation tax).
- **Conviction multiplier from §7:** compounder ×1.0; speculative ×0.5.
- **Exposure caps:** ≤ 10% equity per name; ≤ 30% per sector; correlated-pair check.
- **Circuit breakers:** halt new entries at −3% daily P&L; pause system at −10% peak-to-trough drawdown.

---

## 7. Fundamental Tilt (LD-6/7) — locked

**Non-financials/non-utilities:** compute Magic Formula combined rank (EY + ROC).
- Top tercile → `COMPOUNDER`: full size (×1.0), hold through markup, wide trailing stop.
- Mid → `NEUTRAL`: ×0.75, standard trail.
- Bottom tercile / negative EBIT → `SPECULATIVE`: ×0.5, tight trail, exit at first target.

**Financials + utilities (`FLOW_ONLY`):** skip MF. Sector proxy sanity check only (banks: ROE > 12%, CAR healthy). Default to ×0.75, shorter hold, tighter trail. Quality proxy can promote to ×1.0 but never to COMPOUNDER hold rules.

Fundamentals **never block an entry** — they only set the multiplier and hold horizon.

---

## 8. Exit, Benchmark, Validation — locked

**Exit (any one triggers):** stop hit · target hit · trailing stop · **signal-decay** (NBSA flips negative / dominant broker flips to net sell / VPA prints UTAD or no-demand / phase rolls to distribution).

**Benchmark (LD per source):** Track A → LQ45. Track B → relevant sector index (or IDX SMC index). **Never IHSG** as the headline benchmark.

**Metrics tracked:** total/annualized return, Sharpe, max drawdown, hit rate, **turnover** (flow strategies churn; fees punish churn), and **return net of full fee stack** (the only number that counts).

**Validation gate to advance a phase:** walk-forward + out-of-sample only; backtest and forward-paper are separate code paths sharing the fill engine; survivorship + look-ahead controls mandatory.

---

## 9. Build Order (locked phasing)

1. **Foundation** — ingest (OHLCV + broker summary + NBSA, with availability timestamps), universe gate, manual armed-list alerts. Validate signal quality 2–4 weeks before automating.
2. **Signal** — phase classifier + SMS + veto filters; backtest 2+ yrs with fees & look-ahead controls.
3. **Execution** — technical-trigger logic, fundamental tilt, fill engine (fees/ARA-ARB/lots/ticks), risk mgr; run forward-paper.
4. **Scale / ML (gated)** — only after ≥3 months positive forward-paper walk-forward Sharpe; ML as ranker/weight-optimizer with purged CV.

---

## 10. Acceptance Criteria (definition of done for v1.0 rules engine)

- [ ] No signal consumes data with `availability_ts ≥ decision_ts` (look-ahead test passes).
- [ ] Phase gate rejects all non-C/D candidates (unit-tested on labeled charts).
- [ ] Every order is a limit order with a defined stop and R:R ≥ 2:1.
- [ ] Fill engine reproduces lot/tick/ARA-ARB/fee math against hand-checked cases.
- [ ] Backtest and forward-paper share one fill engine; results reconcile.
- [ ] Reported return is net of the full fee stack and benchmarked to LQ45/sector, not IHSG.
- [ ] No live hand-editing of SMS weights; tuning only via walk-forward optimizer.

---

*Educational/informational only. Paper results do not guarantee live performance.*
