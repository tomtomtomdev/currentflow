# IDX Smart-Money Screener & Paper-Trading System

Research and specification repo for a screener that detects **institutional
("smart-money" / *bandar*) accumulation on the Indonesia Stock Exchange (IDX)**
before markup, and paper-trades it under realistic IDX fill/fee constraints.

This repo holds **specifications, not code** — the source research, the
consolidated thesis, and the locked build spec.

> ⚠️ **Educational / informational only. Not financial advice.** All thresholds
> must be backtested and calibrated per regime. Paper results do not guarantee
> live performance.

---

## Core thesis

> **Smart-money flow *leads* → technical structure *confirms timing* →
> fundamental quality *sizes conviction & hold horizon*.**
> EOD / T+1 cadence · long-only · liquidity-gated.

The IDX-specific edge is **broker-code flow**: the exchange exposes per-broker
net buy/sell, so accumulation by a concentrated broker set is visible in a way it
isn't on most global markets. That signal is gated by a Wyckoff phase classifier
(only Accumulation Phase C/D is tradeable) and disproved against distribution
traps before any entry.

---

## Start here

| If you want… | Read |
|---|---|
| **The authoritative build spec** | [`LOCKED_SPEC.md`](LOCKED_SPEC.md) **(v1.1 — source of truth)** |
| How the detection signals are computed | [`screener/CONSOLIDATED_SCREENER_SPEC.md`](screener/CONSOLIDATED_SCREENER_SPEC.md) |
| Why each decision was made | [`CONSOLIDATED_THESIS.md`](CONSOLIDATED_THESIS.md) |
| The raw source research | [`papertrade system/`](papertrade%20system/) and [`screener/`](screener/) PDFs |

**`LOCKED_SPEC` wins all conflicts.** Every other document is upstream rationale
or a subordinate detail of it.

---

## Repository map

```
.
├── LOCKED_SPEC.md / .pdf              ← AUTHORITATIVE build spec (v1.1)
├── CONSOLIDATED_THESIS.md / .pdf      ← upstream: fuses 5 papers + skills research,
│                                        surfaces the 8 contradictions LOCKED resolves
├── screener/
│   ├── CONSOLIDATED_SCREENER_SPEC.md / .pdf   ← detection-layer spec (subordinate to LOCKED)
│   └── *.pdf                          ← 4 source screener papers
└── papertrade system/
    └── *.pdf                          ← 5 source trading-system papers
```

### Two consolidation lineages, one system

```
papertrade system/ (5 PDFs) + skills research
        └─► CONSOLIDATED_THESIS.md  ──►  LOCKED_SPEC.md  (v1.1, authoritative)
                                              ▲
screener/ (4 PDFs)                            │ feeds detection layer (steps [2]–[5])
        └─► screener/CONSOLIDATED_SCREENER_SPEC.md ──────┘
```

`LOCKED_SPEC` is the end-to-end system (screen → arm → trigger → size → fill →
validate). The screener spec is the **detection-layer feature library** that
feeds its Universe Gate, Phase Classifier, Smart-Money Score, and Veto Filters.

---

## The system at a glance

Pipeline (see `LOCKED_SPEC` §2 for the locked version):

```
INGEST → UNIVERSE GATE → PHASE CLASSIFIER → SMART-MONEY SCORE → VETO FILTERS
   → (SMS ≥ 70 AND phase ∈ {C,D} AND no veto → ARMED)
   → TECHNICAL TRIGGER → FUNDAMENTAL TILT → ORDER → FILL ENGINE → RISK/EXIT
   → BACKTEST ⇄ FORWARD-PAPER → DASHBOARD
```

Key locked decisions:

- **Hard gates:** liquidity floor, Wyckoff phase (C/D only), veto filters.
- **Scored, not boolean:** a track-specific Smart-Money Score (0–100); `≥ 70` arms.
- **Dual-track:** Track A (large-cap, foreign-flow co-leads) vs Track B (lapis-2, broker concentration leads, foreign flow excluded).
- **Fundamentals tilt, never gate:** Magic Formula (EY + ROC) sets a size/hold multiplier; ROE/PE/PB rejected.
- **Entry discipline:** Spring-test / LPS trigger, limit orders only, R:R ≥ 2:1, 1% risk.
- **Realism or it's fiction:** full fee stack, lot/tick/ARA-ARB modeling, look-ahead control (`availability_ts < decision_ts`).
- **ML deferred & gated:** only after ≥ 3 months positive forward-paper; ranker/weight-optimizer, never a black-box entry generator.

---

## Build phases (`LOCKED_SPEC` §9)

1. **Foundation** — ingest + universe gate + manual armed-list alerts.
2. **Signal** — phase classifier + SMS + veto filters; backtest 2+ yrs with fees & look-ahead controls.
3. **Execution** — trigger logic + fundamental tilt + fill engine + risk manager; run forward-paper.
4. **Scale / ML** — gated on validated forward-paper performance.

---

## Change control

`LOCKED_SPEC` is **locked**: changes require a **version bump + documented reason**
(see its changelog). Current version: **v1.1** (§5 veto filters extended with the
trap taxonomy).
