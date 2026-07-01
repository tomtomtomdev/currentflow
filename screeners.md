# Screeners — Stockbit server-side pre-filters (companion to LOCKED_SPEC v1.1 + DATA_SOURCES.md)

**Purpose.** Turn IHSG's ~900 names into a small candidate set **cheaply, server-side**, before the engine spends paywall-counted calls pulling full broker summary + OHLCV per name. Stockbit's screener (`POST /screener/templates`) evaluates `fitem_id` filters across the whole market in one call; our engine then does the precise, un-screenable compute (divergence correlation, broker Herfindahl, CMF, VWAP, Wyckoff phase) only on the survivors.

```
IHSG universe (~900)
   │  SCR-0  eligibility (hard)            ── one screener call
   ▼ ~100–150 liquid names
   │  SCR-1A/1B/1C  accumulation leads     ── observation candidate lists
   ▼ shortlist (union, deduped)
   │  ENGINE pulls broker_summary + ohlcv_foreign for shortlist ONLY  (DATA_SOURCES §6)
   ▼  computes SMS components → phase gate → veto → ARMED   (spec §2–§5)
```

**RULE B (spec §1):** every screener output is an **observation candidate list**, never a ranked buy claim. The composite Smart Money Score is computed in-engine and **stays hidden until paper-validated**. Screeners fill *inputs*, they do not emit *scores*.

---

## 1. Stockbit screener mechanics

`POST https://exodus.stockbit.com/screener/templates` (Bearer auth). Body fields:

| Field | Meaning |
|---|---|
| `universe` | JSON string. All: `{"scope":"IHSG","scopeID":"0","name":"IHSG"}`. Index scope: `{"scope":"idx","scopeID":"550","name":"LQ45"}`. **scopeIDs (from `screener/universe`):** LQ45 `550`, IDX80 `1000003288`, IDX30 `559`, KOMPAS100 `555`, **IDXSMC-LIQ `1000003583`** (small-mid liquid → Track B), IDXSMC-COM `1000003584`. |
| `filters` | JSON string — array of filter objects (see below). AND-combined. |
| `sequence` | comma-separated `fitem_id`s → result columns. |
| `ordercol` / `ordertype` | sort column index (into `sequence`) / `ASC`\|`DESC`. |
| `name`, `description`, `screenerid`, `type` | template identity; `type:"TEMPLATE_TYPE_CUSTOM"`. |

**Filter object — two forms** (confirmed from captured payloads):

```jsonc
// basic: metric OP constant
{"item1":16454,"item1_name":"Value MA 20","item2":"10000000000","item2_name":"","multiplier":"0","operator":">","type":"basic"}

// compare: metric OP (metric2 × multiplier)
{"item1":12469,"item1_name":"Volume","item2":"12464","item2_name":"Volume MA 20","multiplier":"3","operator":">","type":"compare"}
```

Operators: `>`, `<` (also `>=`,`<=`,`=`). Results return in `calcs[].results[]` as `{id, item, raw, display}` per company.

---

## 2. Metric ID reference (screenable — the ones the spec uses)

**Bandarmology (category 93):**
| id | name | id | name |
|---|---|---|---|
| 14399 | Bandar Value | 14426 | Bandar Value MA 20 |
| 14400 | Bandar Accum/Dist | 14424 | Bandar Value MA 10 |
| 14425 | Previous Bandar Value | 3194 | Net Foreign Buy/Sell |
| 3218 | Foreign Flow | 13521 | Foreign Flow MA 20 |
| 13540 | Net Foreign Buy/Sell MA20 | 13539 | …MA10 |
| 13561 | Net Foreign Buy Streak | 13562 | Net Foreign Sell Streak |
| 13580/81/82/83/84 | Net Foreign Flow 1M/3M/6M/1Y/YTD | 13591 | …1W |
| 21365/66/67/68 | Net Insider Buy/Sell 3M/6M/1Y/YTD (%) | | |

**Technical:**
| id | name | id | name |
|---|---|---|---|
| 2661 | Price (last/close) | 13620 | Value |
| 12469 | Volume | 16454 | Value MA 20 |
| 12464 | Volume MA 20 | 12466 | Volume MA 50 |
| 12458 | Price MA 20 | 12460 | Price MA 50 |
| 12462 | Price MA 200 | 3229 | Frequency |
| 15396 | Frequency Spike | 15395 | Frequency Analyzer MA 50 |
| 13650 | 1 Day Volume Change | 20892/20893 | Low / High Price |
| 21536 | RSI (14) | 21539/21540 | Bollinger Upper/Lower (20) |
| **21552** | **VWAP** (screenable — closes §3/§6 residual) | **21559** | **ATR 14** (stop sizing, §6) |
| **21562** | **ADX 14** (trend strength) | 21563/21564 | ADX DI+ / DI- |
| 21537 | MACD (12,26) | 21538 | Stochastic (14,1,3) |
| 21575 | Parabolic SAR | 21576 | SuperTrend |
| 21553–21558 | EMA 5/10/20/50/100/200 | 21561 | CCI 20 |

**Price performance / RS:** 1564 1M · 1565 3M · 1566 6M return · 1570 52wk High · 13371/13373/13374 RS Line 1Y/3M/1M · 13412 Near 52-Wk High.

**Fundamental tilt (§7) / size:** 2892 Market Cap · 2895 EV · 21535 Free Float · 2897 EV/EBIT (EY=1/2897) · 13411 ROC Greenblatt · **13474 Rank(Magic Formula)(%)** *(combined Greenblatt rank — prefer over summing)* · 13424 Rank(Earnings Yield) · 13425 Rank(ROC Greenblatt) · 15276 Rank ROIC · 1461 ROE (bank proxy).

---

## 3. The screeners

Each maps to a spec stage / SMS component and lists the **engine residual** (what the screener *can't* express and the engine must compute from raw data).

### SCR-0 · Universe Eligibility — HARD GATE (spec Stage 0 / §3)
Cuts ~900 → ~100–150. Run first; all other screeners inherit these two liquidity/price lines.

| Filter | fitem | rule |
|---|---|---|
| Liquidity | 16454 Value MA 20 | `> 10,000,000,000` (Rp 10 bn ADV) |
| Price floor | 2661 Price | `> 100` |
| Float | 21535 Free Float | `> 15` (%) |

```json
{"name":"scr0-eligibility","type":"TEMPLATE_TYPE_CUSTOM","ordercol":0,"ordertype":"DESC","sequence":"16454,2661,21535,2892",
 "universe":"{\"scope\":\"IHSG\",\"scopeID\":\"0\",\"name\":\"IHSG\"}",
 "filters":"[{\"item1\":16454,\"item1_name\":\"Value MA 20\",\"item2\":\"10000000000\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"},{\"item1\":2661,\"item1_name\":\"Price\",\"item2\":\"100\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"},{\"item1\":21535,\"item1_name\":\"Free Float\",\"item2\":\"15\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"}]"}
```
**Engine residual (not screenable):** exclude suspended / halted / UMA (`emitten/{sym}/info` flags), IPO < 60 trading days, ARA/ARB-pinned close (DATA_SOURCES §3.2 derivation), incomplete broker summary (gap check). Assign Track A/B from `indexes[]`.

---

### SCR-1B · Bandar Accumulation — Track B lead (spec §4 broker-concentration; SMS wt 35)
Big-broker net value rising above its own 20-day trend, net positive, in an accumulation regime. **Scoped to IDXSMC-LIQ (`1000003583`)** — the small-mid-cap liquid index, the natural lapis-2 universe.

| Filter | fitem | rule |
|---|---|---|
| Accumulation rising | 14399 Bandar Value `>` 14426 Bandar Value MA20 ×1 | compare |
| Net accumulation | 14399 Bandar Value | `> 0` |
| Acc regime | 14400 Bandar Accum/Dist | `> 0` |
| + inherit SCR-0 liquidity | 16454 | `> 10bn` |

```json
{"name":"scr1b-bandar-accum","type":"TEMPLATE_TYPE_CUSTOM","ordercol":0,"ordertype":"DESC","sequence":"14399,14426,14400,16454",
 "universe":"{\"scope\":\"idx\",\"scopeID\":\"1000003583\",\"name\":\"IDXSMC-LIQ\"}",
 "filters":"[{\"item1\":14399,\"item1_name\":\"Bandar Value\",\"item2\":\"14426\",\"item2_name\":\"Bandar Value MA 20\",\"multiplier\":\"1\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":14399,\"item1_name\":\"Bandar Value\",\"item2\":\"0\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"},{\"item1\":14400,\"item1_name\":\"Bandar Accum/Dist\",\"item2\":\"0\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"},{\"item1\":16454,\"item1_name\":\"Value MA 20\",\"item2\":\"10000000000\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"}]"}
```
**Engine residual:** top-2 broker net-buy share + Herfindahl concentration, persistence over N consecutive days on flat/down bars, single-bandar-monopoly veto (>60% one broker), accumulator VWAP vs price (spec §5 veto, §4).

---

### SCR-1A · Foreign Accumulation — Track A lead (spec §4 NBSA; SMS wt 25). **Scoped to LQ45 (`550`).**
Foreign net buy spiking above 2× its 20-day average, with a positive persistence streak. Swap scopeID to IDX80 (`1000003288`) to widen Track A.

| Filter | fitem | rule |
|---|---|---|
| Foreign spike | 3194 Net Foreign Buy/Sell `>` 13540 …MA20 ×2 | compare |
| Persistent buy | 13561 Net Foreign Buy Streak | `> 2` (≥3 days) |
| Flow trend up | 13521 Foreign Flow MA20 | `> 0` |
| + inherit SCR-0 liquidity | 16454 | `> 10bn` |

```json
{"name":"scr1a-foreign-accum","type":"TEMPLATE_TYPE_CUSTOM","ordercol":0,"ordertype":"DESC","sequence":"3194,13540,13561,13521",
 "universe":"{\"scope\":\"idx\",\"scopeID\":\"550\",\"name\":\"LQ45\"}",
 "filters":"[{\"item1\":3194,\"item1_name\":\"Net Foreign Buy / Sell\",\"item2\":\"13540\",\"item2_name\":\"Net Foreign Buy / Sell MA20\",\"multiplier\":\"2\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":13561,\"item1_name\":\"Net Foreign Buy Streak\",\"item2\":\"2\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"},{\"item1\":13521,\"item1_name\":\"Foreign Flow MA 20\",\"item2\":\"0\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"},{\"item1\":16454,\"item1_name\":\"Value MA 20\",\"item2\":\"10000000000\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"}]"}
```
**Engine residual:** foreign net-buy Z-score, KSEI ownership Δ overlay (`shareholders/{sym}/chart`), Track-A-only weighting.

---

### SCR-1C · Stealth Divergence proxy — highest-value Stage 1 signal (spec §4 P-V Divergence; SMS wt 30)
Approximates "accumulation up while price flat/down." True divergence (vol/price corr < 0.3 on high-vol bars) is engine-computed — this is a coarse pre-filter.

| Filter | fitem | rule |
|---|---|---|
| Accumulation rising | 14399 Bandar Value `>` 14426 MA20 ×1 | compare |
| Price ~flat | 1564 1-Month Price Returns | `< 3` (%) |
| Effort present | 12469 Volume `>` 12464 Volume MA20 ×1.5 | compare |
| + inherit SCR-0 liquidity | 16454 | `> 10bn` |

```json
{"name":"scr1c-stealth-divergence","type":"TEMPLATE_TYPE_CUSTOM","ordercol":0,"ordertype":"ASC","sequence":"14399,1564,12469,12464",
 "universe":"{\"scope\":\"IHSG\",\"scopeID\":\"0\",\"name\":\"IHSG\"}",
 "filters":"[{\"item1\":14399,\"item1_name\":\"Bandar Value\",\"item2\":\"14426\",\"item2_name\":\"Bandar Value MA 20\",\"multiplier\":\"1\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":1564,\"item1_name\":\"1 Month Price Returns\",\"item2\":\"3\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\"<\",\"type\":\"basic\"},{\"item1\":12469,\"item1_name\":\"Volume\",\"item2\":\"12464\",\"item2_name\":\"Volume MA 20\",\"multiplier\":\"1.5\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":16454,\"item1_name\":\"Value MA 20\",\"item2\":\"10000000000\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"}]"}
```
**Engine residual:** true price-volume correlation on high-vol bars, absorption (needs live depth), VSA effort-vs-result, Wyckoff phase classification (RULE A gate).

---

### SCR-2 · Volume / Frequency Anomaly — RVOL (spec §4 volume anomaly; SMS wt 10–15)
Feeds the volume-anomaly component; run over the SCR-0 survivors.

| Filter | fitem | rule |
|---|---|---|
| RVOL ≥ 3× | 12469 Volume `>` 12464 Volume MA20 ×3 | compare |
| Frequency spike | 15396 Frequency Spike | `> 0` |

```json
{"name":"scr2-volume-anomaly","type":"TEMPLATE_TYPE_CUSTOM","ordercol":0,"ordertype":"DESC","sequence":"12469,12464,3229,15396",
 "universe":"{\"scope\":\"IHSG\",\"scopeID\":\"0\",\"name\":\"IHSG\"}",
 "filters":"[{\"item1\":12469,\"item1_name\":\"Volume\",\"item2\":\"12464\",\"item2_name\":\"Volume MA 20\",\"multiplier\":\"3\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":15396,\"item1_name\":\"Frequency Spike\",\"item2\":\"0\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"}]"}
```
**Engine residual:** block-trade footprint (> Rp 1 bn / > 1% ADV), avg-trade-size expansion, retail-FOMO veto (retail buy > 60% — needs broker/investor split).

---

### SCR-3 · Trend Confirmation (spec Stage 3 / §6 trigger context)
Close > 20MA > 50MA with positive relative strength. Run on ARMED-candidate names to confirm structure before the technical trigger.

| Filter | fitem | rule |
|---|---|---|
| Above 20MA | 2661 Price `>` 12458 Price MA20 ×1 | compare |
| 20MA > 50MA | 12458 Price MA20 `>` 12460 Price MA50 ×1 | compare |
| **Above VWAP** | 2661 Price `>` 21552 VWAP ×1 | compare |
| **Trend strength** | 21562 ADX 14 | `> 20` |
| RS positive | 13373 3-Month RS Line | `> 0` |

```json
{"name":"scr3-trend-confirm","type":"TEMPLATE_TYPE_CUSTOM","ordercol":4,"ordertype":"DESC","sequence":"2661,12458,12460,21552,21562,21559,13373",
 "universe":"{\"scope\":\"IHSG\",\"scopeID\":\"0\",\"name\":\"IHSG\"}",
 "filters":"[{\"item1\":2661,\"item1_name\":\"Price\",\"item2\":\"12458\",\"item2_name\":\"Price MA 20\",\"multiplier\":\"1\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":12458,\"item1_name\":\"Price MA 20\",\"item2\":\"12460\",\"item2_name\":\"Price MA 50\",\"multiplier\":\"1\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":2661,\"item1_name\":\"Price\",\"item2\":\"21552\",\"item2_name\":\"VWAP\",\"multiplier\":\"1\",\"operator\":\">\",\"type\":\"compare\"},{\"item1\":21562,\"item1_name\":\"Average Directional Index 14\",\"item2\":\"20\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"},{\"item1\":13373,\"item1_name\":\"3 Month RS Line\",\"item2\":\"0\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"}]"}
```
**Note:** VWAP (21552) and ADX (21562) are now screened server-side; ATR 14 (21559) is pulled in `sequence` to seed stop sizing. **Engine residual:** Spring-test / LPS detection, exact stop placement, R:R ≥ 2:1 computation, 9-day exhaustion cap (spec §6).

---

### SCR-4 · Fundamental Tilt reference — NOT a gate (spec §7)
Ranking pull, not a filter. Sort survivors by Magic Formula inputs to set conviction multiplier / hold horizon. Fundamentals never block entry.

Sequence leads with **`13474` Rank(Magic Formula)(%)** — Stockbit's combined Greenblatt rank, used directly (no manual summing). Tercile → COMPOUNDER / NEUTRAL / SPECULATIVE. Supporting columns: `13411` ROC Greenblatt, `2897` EV/EBIT, `15276` Rank ROIC, `1461` ROE (bank proxy), `2892` Market Cap. Guru preset alternative: Stockbit ships `Greenblatt's Magic Formula` (`screener/preset` id 6, `TEMPLATE_TYPE_GURU`).
```json
{"name":"scr4-fundamental-tilt","type":"TEMPLATE_TYPE_CUSTOM","ordercol":0,"ordertype":"DESC","sequence":"13474,13411,2897,15276,1461,2892",
 "universe":"{\"scope\":\"IHSG\",\"scopeID\":\"0\",\"name\":\"IHSG\"}",
 "filters":"[]"}
```
**Engine residual:** FLOW_ONLY dual-track (financials/utilities skip MF, use ROE/NIM/CAR proxy), point-in-time fundamentals for backtest (DATA_SOURCES §3.1).

---

### SCR-EXIT · Distribution / Mirror warning (spec §8 signal-decay exit)
Runs continuously over open positions + ARMED list. Any hit → decay flag.

| Filter | fitem | rule |
|---|---|---|
| Dist regime | 14400 Bandar Accum/Dist | `< 0` |
| Foreign outflow | 13540 Net Foreign Buy/Sell MA20 | `< 0` |
| Sell streak | 13562 Net Foreign Sell Streak | `> 2` |

```json
{"name":"scr-exit-distribution","type":"TEMPLATE_TYPE_CUSTOM","ordercol":0,"ordertype":"ASC","sequence":"14400,13540,13562",
 "universe":"{\"scope\":\"IHSG\",\"scopeID\":\"0\",\"name\":\"IHSG\"}",
 "filters":"[{\"item1\":14400,\"item1_name\":\"Bandar Accum/Dist\",\"item2\":\"0\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\"<\",\"type\":\"basic\"},{\"item1\":13540,\"item1_name\":\"Net Foreign Buy / Sell MA20\",\"item2\":\"0\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\"<\",\"type\":\"basic\"},{\"item1\":13562,\"item1_name\":\"Net Foreign Sell Streak\",\"item2\":\"2\",\"item2_name\":\"\",\"multiplier\":\"0\",\"operator\":\">\",\"type\":\"basic\"}]"}
```
**Engine residual:** UTAD / no-demand VPA prints, dominant-broker flip to net sell, price-vs-flow divergence exit (spec §8: "divergence is the single best exit signal").

---

## 4. Orchestration & cadence

Fires on broker-summary publication (spec LD-5), nightly:

1. **SCR-0** → eligible universe (~100–150). Cache to DuckDB with `as_of`.
2. **SCR-1A ∪ SCR-1B ∪ SCR-1C** → accumulation candidate shortlist (dedupe union). Each column value stored as an SMS *component input*, not a score.
3. Engine pulls `broker_summary` + `ohlcv_foreign` for the shortlist **only** (paywall-frugal), computes precise components → **Wyckoff phase gate (RULE A)** → **veto filters (§5)** → internal SMS → `ARMED` state.
4. **SCR-2 / SCR-3** refine RVOL + trend on ARMED names ahead of the technical trigger.
5. **SCR-4** attaches the fundamental tilt (conviction ×, hold horizon).
6. **SCR-EXIT** runs continuously against open + ARMED names → decay flags.

**Coverage note (spec "no silent caps"):** screeners are AND-filters on Stockbit-computed metrics; anything they can't express is logged as an *engine residual* above and computed downstream — never silently dropped. Screener output is a **candidate/observation list**; the score that ranks it stays hidden until it clears `PAPER_VALIDATION_MONTHS` (RULE B).

---

## 5. What the screeners can vs. cannot fill

| Spec need | Screener-served | Engine-computed residual |
|---|---|---|
| Stage 0 liquidity / price / float | ✅ SCR-0 | suspend/IPO/ARA-ARB/gap flags |
| Broker concentration | partial (Bandar Value) | top-2 share, Herfindahl, persistence, monopoly veto |
| Foreign flow | ✅ SCR-1A | Z-score, KSEI Δ |
| Price-volume divergence | proxy (SCR-1C) | true corr, absorption, VSA, Wyckoff phase |
| Volume anomaly / RVOL | ✅ SCR-2 | block trades, avg-ticket, retail-FOMO veto |
| Trend / RS / VWAP / ADX | ✅ SCR-3 (VWAP+ADX now screened) | Spring/LPS trigger, exact stop, R:R |
| Fundamental tilt | ✅ SCR-4 (combined MF rank 13474) | FLOW_ONLY proxy, point-in-time backtest |
| Distribution / exit | ✅ SCR-EXIT | UTAD/no-demand, broker flip, divergence exit |

**Bottom line:** the screeners fill the entire *universe + candidate + component-input* layer server-side, leaving the engine to do only the un-screenable statistical/structural compute on a small shortlist. That is exactly the data the system needs to function through the ARMED state; the entry decision and any displayed number remain gated by RULE A and RULE B.

---

*Metric IDs verified against `screener/metric` (515 leaf metrics, 15 categories) + captured `screener/templates` POST payloads. Universe scopeIDs from `screener/universe`; Guru presets from `screener/preset`. Filter schema confirmed from the operator's own `bandar-accumulating` template.*
