# IDX Smart-Money Screener & Paper-Trading System — Consolidated Thesis

**Sources consolidated:**
1. `IDX_Smart_Money_Trading_Blueprint.pdf` — high-level modular pipeline + Smart Money Score + ML roadmap
2. `Indonesia_Big_Money_Auto_Trade_System.pdf` — full 5-layer architecture, weighted detection engine, OJK/microstructure detail
3. `idx-bandarmology-paper-trade-system.pdf` — the "traps that sink these systems" critique (look-ahead, reflexivity, fills, fees)
4. `paper_trading_architecture.pdf` — clean "What (fundamental) / When (technical)" separation
5. `trading_system_guide.pdf` — data-source landscape (Invezgo/Sectors.app), realism caveats

**Cross-checked against skills research:** Bandarmology (Ryan Filbert), Wyckoff 2.0 (Villahermosa), Volume Price Analysis (Coulling), Grimes TA, Magic Formula (Greenblatt).

> **Framing:** The 5 papers describe *how to build the machine*. The skills research defines *what the machine should look for and why it has edge*. This document fuses them: the screener thesis (brain) embedded in the system architecture (skeleton), with contradictions surfaced.

---

## PART A — Consolidated System Architecture (from the 5 papers)

A single pipeline, deduplicated across all five papers. Every paper agrees on this spine; differences are in emphasis.

```
[1] DATA INGESTION        → OHLCV, broker summary, foreign/domestic net flow,
                            order book (L2), corporate actions, halt/suspension flags,
                            financial statements, KSEI ownership
        │  (scheduler keyed to BROKER-SUMMARY availability, NOT market close)
        ▼
[2] UNIVERSE SCREEN       → liquidity gate (non-negotiable), exclude IPOs/suspended/ARA-ARB-pinned
        ▼
[3] DETECTION / FEATURE   → volume anomaly, price-volume divergence, broker concentration,
    ENGINE                  foreign flow, block trades, order-book imbalance, volatility
        ▼
[4] COMPOSITE SCORE       → Smart Money Score / Big Money Score (0–100), weighted, tunable
        ▼
[5] STRATEGY / RULES      → buy filter (score + confirmation), exit (stop/target/trailing/decay)
        ▼
[6] PAPER FILL ENGINE     → lot=100, tick bands, ARA/ARB rejection, next-open fills,
                            FULL fee stack (broker + levy + VAT + 0.1% sell tax), slippage
        ▼
[7] RISK & COMPLIANCE     → 1–2% risk/trade, exposure caps, drawdown breaker, OJK rules, long-only
        ▼
[8] BACKTEST  ⇄  FORWARD PAPER  (separate code paths, shared fill engine)
        ▼
[9] DASHBOARD / MONITOR   → P&L, signals, flow charts
```

### Layer detail (merged)

**[1] Data** — Broker summary (net buy/sell per broker/stock/day) is the *binding constraint* (Paper 3) and the heart of bandarmology. **The single most important engineering decision is the look-ahead control: only use data that was actually published before the session you'd have traded.** Recommended sources: Invezgo API (broker/Bandarmology, foreign flow, KSEI), Sectors.app, RTI/Stockbit, official IDX (Paper 5). Real-time tick/L2 via WebSocket (Paper 2) is optional and only relevant to intraday signals.

**[2] Universe** — Tier by market cap / value traded. **Liquidity filter is non-negotiable: in thin names the flow signal *is* the manipulation, not a read on it** (Paper 3). Exclude fresh IPOs, suspended names, ARA/ARB-pinned stocks (can't fill).

**[3] Detection signals** (consolidated, with Paper 2 weights):

| Signal | Definition | Weight (Paper 2) |
|---|---|---|
| Price-Volume Divergence | High volume, minimal price change (quiet accumulation) | **Very High** |
| Volume Anomaly | Volume > 3× 20-day avg (RVOL) | High |
| Block Trade | Single trade > IDR 1B or > 1% ADV | High |
| Broker Clustering | Top-N broker net-buy concentration | Medium |
| Foreign Flow Spike | Net foreign buy > 2× recent avg | Medium |
| Order-Book Imbalance | Bid/ask > 3:1, increasing depth | Medium |
| Opening Auction Surge | Pre-open directional volume | Low |

**False-signal filters** (Paper 2): retail-FOMO filter (exclude if retail buy > 60% volume), news-correlation check, sector-rotation context, time-of-day weighting.

**[4] Composite Score** — 0–100. Paper 2 thresholds: > 70 = watch, > 85 = entry consideration. Weights tuned on backtest (Sharpe maximization, Paper 1/2).

**[6] Fill realism** (Paper 3, the part "where most paper systems lie to you"): lot size 100, fraksi-harga tick bands, ARA/ARB auto-rejection (±10% non-EMAS / ±20%), next-open fills with slippage (LQ45 0.05–0.15%, mid-cap 0.2–0.5%, small-cap >1%), full fee stack. **Skipping fees alone can flip a winning strategy negative.**

**[7] Risk** (Paper 2): max 1–2% risk/trade; ≤10% per ticker; ≤30% per sector; halt at 3% daily loss; pause at 10% drawdown. OJK: long-only default (short-selling restricted), margin 50%, UPT halts. T+2 settlement (Paper 5).

**[8] Validation** — Historical backtest (survivorship + look-ahead controls) then live forward paper as true out-of-sample. Benchmark deliberately: **LQ45 or sector index, not IHSG** if the universe is large-caps (Paper 3). Track return, Sharpe, max DD, hit rate, turnover (flow strategies churn; fees punish churn).

**Tech stack** (merged): Python (pandas/polars, TA-Lib, Backtrader/vectorbt), PostgreSQL + TimescaleDB, Redis, FastAPI, Airflow/cron orchestration, Streamlit/Grafana or SwiftUI dashboard, Docker on cloud.

**Roadmap** (Paper 2, 24 wks): Foundation (data + basic screener + manual alerts) → Signal refinement (BMS + backtest + filters) → Automation (broker paper API + execution + risk) → Validation & scaling.

---

## PART B — Strategy Thesis (from skills research)

The papers detect "accumulation" via thresholds but are mostly **cycle-phase-blind**. The skills add the missing brain: *where in the bandar cycle are we, and does this have statistical edge?*

**Primary signal — Smart-money flow (lead):**
- **Bandarmology**: Top-5 buy/sell broker net position over a 20-day window; same 1–2 brokers consistently net-buying on flat/down days; dominant broker avg price ≈ current price (not yet marked up). **Veto single-bandar monopoly (gameable).**
- **Wyckoff 2.0**: Only **Accumulation Phase C/D** qualifies — Spring/shakeout or SOS→LPS. Reject Phase A/B (noise) and anything showing BC/UTAD/distribution.
- **VPA (Coulling)**: Confirm with effort-vs-result — stopping volume at lows, no-supply on pullbacks, absorption (high volume / narrow spread / price won't fall). Reject no-demand rallies and high-volume up-bars closing low.

**Confirmation 1 — Technical (Grimes):** Higher-timeframe trend aligned; setup with documented edge (pullback-in-trend, failed-breakdown = Spring, volatility-contraction breakout); logical stop at thesis-invalidation; **R:R ≥ 2:1**. Enter on the pullback/test, **not** the breakout impulse. Failed-breakout has *enhanced* edge on IDX (retail stop-hunts).

**Confirmation 2 — Fundamental (Magic Formula):** Rank survivors on Earnings Yield (EBIT/EV) and Return on Capital. Separates "bandar accumulating a quality compounder" (hold the markup) from "bandar pumping junk" (scalp only). Exclude banks/insurance/utilities from this metric.

**Scoring funnel:** Universe gate → Smart-money score (lead, must pass) → Technical confirm → Fundamental confirm → composite tier (A buy / B trade-on-trigger / C watch / drop). Entry on Spring-test or LPS; stop below spring/swing low; target opposite end of range / next HVN; size to stop at 1% risk; scale out on distribution / UTAD / dominant-broker flip.

---

## PART C — Convergence (where papers + skills agree → thesis solidified)

These are the load-bearing, multiply-confirmed pillars. Build on these first.

1. **Price-volume divergence is the core accumulation tell.** Paper 2 weights it **Very High**; Paper 3 calls "price flat while a few brokers quietly accumulate" the classic setup; VPA calls it absorption / effort-vs-result; Wyckoff calls it Phase B/C accumulation. *Strongest convergence in the entire corpus — make it the centerpiece.*

2. **Liquidity filter is non-negotiable.** Papers 3 & 5 + Wyckoff + Grimes all agree: thin names = manipulation, not signal. Stick to LQ45/IDX80 (or rigorously liquidity-gated lapis-2).

3. **Signals lag and can reverse.** Paper 3 & 5 explicitly; Bandarmology skill states a 2–5 day delay between signal and price. Don't expect instant follow-through; size and stop accordingly.

4. **Reflexivity / manipulation risk is real.** Paper 3's "follow-the-top-buyer gets you front-run or trapped in distribution dressed as accumulation" = Bandarmology's single-bandar-monopoly veto = Wyckoff's UTAD/Spring traps. Multi-broker, multi-day confirmation required.

5. **Position sizing & risk.** Paper 2's 1–2% fixed-fractional risk and stop-loss discipline = Grimes' 1% / size-to-stop. Identical.

6. **Exit on distribution.** Paper 2 & 5 "sell on distribution signals" = Bandarmology distribution phase = VPA upthrust/no-demand = Wyckoff UTAD/LPSY. Aligned.

7. **Fee/tax/slippage realism or the edge is fiction.** Paper 3 & 5 insistent; consistent with Grimes' "edge survives costs or it isn't edge."

---

## PART D — Contradictions (must be resolved before building)

See chat summary. Eight items: (1) signal hierarchy, (2) cycle-phase blindness, (3) entry discipline, (4) universe direction, (5) data cadence, (6) fundamental layer presence + metric, (7) financials exclusion paradox, (8) ML vs overfitting.
