# BACKTEST_PHASE0.md — data layer spec for the walk-forward factor study

**Status:** proposal, not locked. Companion to `DATA_SOURCES.md` (feed→endpoint map),
`REGIME.md` (window boundaries), `LOCKED_SPEC.md` (§1 look-ahead, §12 fill engine).
Same posture: pinned from evidence, open items named, never guessed in code.

**Scope:** what must exist in the store before any backtest result is trustworthy, what it
costs to build, and which windows are legitimate. Does not specify strategies — that is
Phase 1 (pre-registration).

---

## 0. Current state (measured 2026-08-08)

| Layer | State |
|---|---|
| DuckDB schema | ✅ Built — `daily_bar`, `broker_net`, `index_roster_pit`, `ksei_ownership`, `symbol_index`, `pattern_instance`, `paper_trade`, screener tables. Every table carries `as_of`. |
| Rows ingested | ❌ **0 across every table.** Nothing has been backfilled. |
| DAL feed methods | ⚠️ 7 of 11 — `broker_summary`, `ohlcv_foreign`, `symbol_info`, `corp_actions`, `special_board`, `ksei_ownership`, `run_screener` exist. **Missing:** `fundamentals_hist`, `fundamentals_live`, `float_shares`, `orderbook`, `regime`. |
| Auth | ✅ Pure-Python login verified (`dal/login.py`, `token_store.py`); device-trust `player_id` persisted. |
| Backfill orchestration | ⚠️ `ingest/backfill.py` exists, unexercised at scale. |
| PIT fundamentals | ❌ Not built. This is `DATA_SOURCES.md` §3.1 — the real gap. |
| Survivorship / delisting source | ❌ **No source identified.** Not solvable from Stockbit. |

**Net:** Phase 0 is ~40% done — the hard architectural decisions (as_of stamping, PIT roster
table, regime clamping) are made and correct. What remains is ingestion plus three genuine
data gaps.

---

## 1. Window decision (supersedes the "back to 2016" proposal)

Three independent ceilings bind, and 2016 clears none of them:

| Ceiling | Value | Source |
|---|---|---|
| Regime validity (Track B) | **2024-07-01** | `REGIME.md` §1 — FCA + criteria churn |
| Regime validity (Track A) | **2024-01-01** | `REGIME.md` §1 — ARB re-normalization |
| Broker summary history | **~2019** | `DATA_SOURCES.md` §1 |
| Financial statements | 2008 (73 quarters) | `DATA_SOURCES.md` §1 |
| Order book / absorption | **live only** | `DATA_SOURCES.md` §3.4 — never backtestable |

### The resolution: two studies, different windows, never pooled

The regime constraint binds **microstructure** — ARA/ARB bands, FCA call auction, tick
sizes, fill realism. It does not equally bind a slow, quarterly-rebalanced fundamental
factor on liquid large caps. That distinction licenses two separate studies with
different honest claims.

**Study 1 — Flow system (the LOCKED_SPEC pipeline).**
- Window: **2024-07-01 → present** (~2.1y, ~512 trading days)
- Universe: Track A + Track B
- Uses broker summary, bandar detection, full fill engine with ARA/ARB
- Seam: estimation → `CATALOG_HOLDOUT_START` (2026-01-01), OOS 2026-01-01 → present
- Claim ceiling: `stability: UNKNOWN (current regime only)` per `REGIME.md` §4
- **This is what `LOCKED_SPEC.md` governs. No spec bump needed.**

**Study 2 — Slow factor study (the "books as principles" test).**
- Window: **2019-01-01 → present** (~7.6y, ~1,854 trading days, 4 walk-forward folds)
- Universe: **Track A only** (LQ45/IDX80) — liquid, continuous price discovery, FCA does not bind
- Quarterly rebalance, fundamentals + float + dividend consistency only
- **No broker/bandar inputs. No ARA/ARB fill logic** — conservative fills instead
  (VWAP + fixed slippage floor, reject any day the name closed pinned)
- Claim ceiling: explicitly **cross-regime**, labeled `stability: CROSS-REGIME, microstructure-agnostic`
- Requires a `LOCKED_SPEC` note: this study is exempt from `REGIME_START` clamping
  *because it consumes no regime-dependent constant*. If it ever touches the shared
  fill engine's band logic, the exemption is void.

Study 2 is what answers "which selection logic works across regimes." Study 1 is what
you actually trade. **Their results must never be averaged or presented together.**

---

## 2. Source spec by feed

### 2.1 Solved — direct pull, no engineering risk

| Feed | Endpoint | Calls (S1) | Calls (S2) | Notes |
|---|---|---|---|---|
| Daily OHLCV + foreign flow | `company-price-feed/historical/summary/{sym}` | ~2,200 | ~3,040 | Paginated `limit=50`; 11 pages/sym for S1, 38 for S2 |
| Corporate actions | `corpaction/{sym}` | ~200 | ~80 | Full history per call |
| Status / suspend / UMA / notation | `emitten/{sym}/info` | ~200 | ~80 | Current state only — see §3.4 |
| Special board membership | `emitten/indexes/special-board` | 1 | 1 | Current only — see §3.4 |
| KSEI ownership | `emitten-metadata/shareholders/{sym}/chart?value_year=` | ~600 | ~640 | 1 call/sym/year |

**Cheap.** At 2 req/s the entire non-broker backfill is under an hour of wall clock.

### 2.2 The expensive one — broker summary

`marketdetectors/{sym}` is **one call per symbol per trading day** (documented: a
multi-day range returns a single range-aggregate, so per-day rows require `from = to`).

| Window | Symbols | Days | Calls | Wall clock @1 req/s |
|---|---|---|---|---|
| S1: 2024-07-01 → now | 200 | 512 | **102,400** | ~28h continuous |
| If extended to 2019 | 200 | 1,854 | **370,800** | ~103h continuous |

Behind `paywall/eligibility/check` + `paywall/counter/increment`. This asymmetry is the
single strongest argument for **not** extending broker data past `REGIME_START` — it buys
data your own spec says is invalid, at 3.6× the cost and with account-flagging risk.

**Mitigation (already in CLAUDE.md):** ingest once, cache keyed `(symbol, date, as_of)`,
never re-pull. Backfill must be **resumable** — checkpoint per `(symbol, date)` so a 401
or counter trip loses minutes, not days. Run as throttled nightly increments over ~1–2
weeks, not one continuous blast.

---

## 3. The four real gaps

### 3.1 Survivorship — NOT solvable from Stockbit *(highest priority)*

Every endpoint is symbol-addressed. You cannot ask Stockbit "what was listed in 2019."
Backfilling today's roster and walking back silently deletes every delisted, suspended,
and forcibly-delisted name — and in Indonesia those are exactly the names a value or
high-float screen surfaces. Uncorrected, this inflates returns systematically and
invisibly.

**Recommended source:** **IDX Statistics annual books** (idx.co.id, published yearly,
free PDF). Each lists all companies listed that year. Diff consecutive years → delisting
events with dates. Cross-check against IDX *Pengumuman* → Delisting/Relisting archive.

**Populate:** `index_roster_pit(index_name, symbol, effective_from, effective_to, source, as_of)`
— the table already exists and is exactly the right shape. Add `LISTED` as a pseudo-index
covering the full board.

**Honest fallback:** if a delisted name's price history is unretrievable from Stockbit,
you still know it existed. Report the **bias magnitude** (how many names, what fraction of
starting market cap) rather than silently omitting — per the "no silent caps" convention.
A backtest that says "8% of the 2019 universe is unrecoverable" is usable. One that
doesn't mention it is not.

Effort: **3–5 days.** Cost: free.

### 3.2 Point-in-time fundamentals — build the §3.1 parser

`ratios`/`keystats` return **current TTM snapshot only**. Using them historically means
your 2019 screen reads 2026's restated figures — textbook look-ahead.

**Source:** `findata-view/company/financial` — HTML, ~73 quarterly periods since 2008.
One call per symbol (cheap to fetch, expensive to parse).

**Reporting lag** — you need `availability_ts`, not period-end. Stockbit gives period, not
filing date. Conservative defaults pending verification against IDX filing archives:

| Report | Lag applied |
|---|---|
| Q1 / Q2 / Q3 interim | period end **+ 90 days** |
| Q4 / annual audited | period end **+ 120 days** |

> ⚠️ **VERIFY (operator):** OJK/IDX submission deadlines (interim 30/60/90d depending on
> review status; annual audited ~90d). These defaults are deliberately conservative —
> if actual filing dates are later, move the lag out, never in.

Store as `fundamentals_pit(symbol, period, line_item, value, availability_ts, as_of)`.
DAL method `fundamentals_hist()`. Snapshot raw HTML before parsing so re-parses need no
re-fetch (per `DATA_SOURCES.md` §4).

Effort: **4–6 days.** Cost: free.

### 3.3 Free-float history — reconstruct, and mind the 2026 break

Free Float (`fitem_id 21535`) is served but is a **current snapshot**. There is no float
time series. This matters more than any other gap: float/ownership concentration is the
central characteristic of this market and the whole premise of the factor study.

**Reconstruct from:** `insider/shareholding/composition/companies/{sym}` +
`emitten-metadata/shareholders/{sym}/chart?value_year=` (monthly, KSEI-sourced).
Float = `total_shares − (controlling + strategic + treasury)`.

**Schema change required:** `ksei_ownership` currently stores only `foreign_pct` /
`local_pct`. Needs full composition by holder class to compute float. Add
`ksei_composition(symbol, date, holder_class, shares, pct, as_of)`.

> ⚠️ **STRUCTURAL BREAK — do not average across it.** The MSCI-driven reform moved the
> substantial-shareholder disclosure threshold from **5% → 1%** (announced 2026). Before
> that, holders between 1% and 5% are invisible and fall into computed "float."
> **Every pre-2026 free-float figure is therefore systematically overstated**, and the
> series has a discontinuity at the changeover. Treat float as two regimes: model the
> level shift explicitly, or use float *rank within cross-section* rather than level.

Effort: **2–3 days** (+ the break handling is a Phase 1 modelling decision, not a data task).

### 3.4 Point-in-time status flags — currently snapshot only

`emitten/{sym}/info` and `special-board` return **current** state. A backtest needs to
know whether a name was on the Special Monitoring Board / suspended / UMA-flagged *on the
decision date*. Using today's flags is look-ahead in both directions (a name clean in 2024
but flagged in 2026 gets wrongly excluded from 2024).

**Options:**
1. **Forward accrual only** — snapshot `special_board` + status flags daily from go-live.
   Correct but gives zero history. Fine for Study 1's OOS window, useless for Study 2.
2. **Reconstruct from IDX announcements** — UMA / suspension / FCA-entry notices are
   published and dated. Scrapeable, laborious.
3. **Proxy** — derive suspicion from the price/volume series itself (zero-volume runs,
   pinned closes, call-auction price patterns). Imperfect but free and PIT-safe.

**Recommendation:** (1) starting immediately (it costs one call/day and can never be
backfilled later), plus (3) for Study 2. Reserve (2) for if the proxy proves noisy.

Effort: **1 day** for (1) + (3); **4+ days** for (2).

---

## 4. Cost summary

### Cash
| Item | Cost |
|---|---|
| Stockbit Pro subscription | ⚠️ **Verify current tier pricing** — the only recurring cash item. Required for `marketdetectors` + broker history. |
| IDX Statistics annual books | Free |
| DuckDB / storage / compute | Free — local, single machine. Full store well under 10 GB. |
| Commercial PIT vendor (Refinitiv / S&P Capital IQ / CEIC) | Enterprise, thousands USD/yr. **Out of scope** — only needed if the result must be defended externally rather than traded personally. |

**Effective marginal cash cost of Phase 0: the Stockbit subscription you already hold.**

### API calls
| Feed | Study 1 | Study 2 |
|---|---|---|
| Broker summary | 102,400 | — (not used) |
| OHLCV | 2,200 | 3,040 |
| Financial statements | 200 | 80 |
| Corp actions | 200 | 80 |
| KSEI ownership/composition | 600 | 640 |
| **Total** | **~105,600** | **~3,840** |

Study 2 — the multi-period walk-forward you actually asked for — is **~3.6% of the call
budget of Study 1**. It is by far the cheaper study to run and covers 3.6× the history.

### Wall clock
- Non-broker backfill: **< 1 hour**
- Broker backfill (Study 1): **1–2 weeks** of throttled nightly increments

### Engineering
| Task | Days |
|---|---|
| E1 · Delisted roster + `index_roster_pit` population | 3–5 |
| E2 · `fundamentals_hist` HTML parser + reporting lag | 4–6 |
| E3 · Free-float composition history + schema | 2–3 |
| E4 · Backfill hardening (resumable, counter-aware, checkpointed) | 2–3 |
| E5 · Integrity: gap checks, corp-action adjustment verification, empty≠zero | 3–4 |
| **Total** | **14–21 days** solo |

---

## 5. Build order

Strictly sequential — each step's output is the next step's input, and steps 1–2 are
where a wrong answer silently poisons everything downstream.

1. **E1 — roster & survivorship.** Build the PIT universe *first*. Everything else is
   fetched per-symbol; fetching the wrong symbol set makes all later work wasted.
2. **E4 — backfill hardening.** Before pulling 100k calls, make the puller resumable.
3. **Cheap backfill** — OHLCV, corp actions, KSEI. Under an hour. Gives an immediately
   testable store.
4. **E5 — integrity gate.** Verify corp-action adjustment against hand-checked splits
   (pick 3 known reverse-splits). Verify empty≠zero. **Do not proceed until green.**
5. **E2 — PIT fundamentals parser.** Now the slow parse work, against a verified price spine.
6. **E3 — float history.**
7. **Start daily status-flag accrual** (§3.4 option 1) — do this on day one in parallel;
   it is the only thing that gets permanently worse the longer you wait.
8. **Broker backfill** — throttled nightly, Study 1 window only.

Steps 1–6 gate **Study 2** (the factor walk-forward). Step 8 gates **Study 1**.
Study 2 is runnable roughly two weeks before Study 1.

---

## 6. Acceptance criteria (before any backtest number is believed)

Per `LOCKED_SPEC.md` §13 conventions, as failing tests first:

- **Look-ahead:** no row consumed where `availability_ts >= decision_ts`. Applies to
  fundamentals (reporting lag), roster (effective_from), and float (KSEI publication lag).
- **Survivorship:** the universe on any historical date includes names later delisted.
  Test: a known delisted name appears in the 2019 roster and vanishes at its actual date.
- **Corp actions:** adjusted series reproduces hand-checked split/reverse-split cases.
- **Empty ≠ zero:** a no-trade day, an unpublished day, and a gap are three distinct states.
- **Regime clamp:** Study 1 fails loud below `REGIME_START`. Study 2 carries its exemption
  label and is blocked from touching band-dependent fill logic.
- **Bias disclosure:** the backtest report states the unrecoverable-name count and its
  share of starting market cap. No silent caps.

---

*Drafted 2026-08-08 against measured repo state (schema present, 0 rows, 7/11 DAL methods).
Windows follow `REGIME.md`; the Study 2 exemption is a proposal requiring operator sign-off.*
