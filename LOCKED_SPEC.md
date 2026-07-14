# IDX Smart-Money Screener & Flow Terminal — Locked Specification v1.5

**Status:** LOCKED. v1.0 resolved the eight contradictions from `CONSOLIDATED_THESIS.md` into a decision engine. **v1.1 merges `vector-lab.md`** — the same domain re-framed as a private, single-operator flow *terminal* — into this spec: the v1.0 engine is kept intact as the core, wrapped in an observation/UI layer, hardened data posture, and a stricter presentation gate. Build to this; changes require a version bump and a documented reason.

**Changelog — v1.5 (2026-07-14):** added **LD-12 (Haste Mode)** — a further, bounded relaxation of entry discipline, confined to an operator-armed **Haste-Mode** auto paper-trader. Fast Mode (LD-11) already buys every **ARMED** name at once (no Spring/LPS trigger, no R:R gate); Haste Mode additionally **drops the `ARMED@70` arming threshold**, so the auto-trader also enters the **WATCH** cohort — every candidate that already cleared the RULE A phase gate (Wyckoff C/D) **and** the §5 veto layer but scored below the arming cut. *Reason:* even Fast Mode's ARMED-only set arms too rarely (~1 trade/yr across 32 names, PROGRESS 2026-07-08) for forward paper to accrue a statistically meaningful sample, so **RULE B can never promote** and the LD-8 ML gate never opens (the standing deferral); the WATCH cohort is far larger, giving the whole validation path the throughput it depends on — at zero live risk (paper only). **The hard boundary:** Haste relaxes *only* the SMS arming threshold. **RULE A (phase gate) and every §5 veto still bind by construction** — `GATE_REJECTED` and `VETOED` are distinct engine states *upstream* of the `WATCH`/`ARMED` split (pipeline steps [3] and [5]), so neither auto-trader can ever enter a gate-rejected or vetoed name. Haste entries use the same [6′] fast entry geometry, the same §6 sizing/caps/circuit-breakers, and the same §8 exit as Fast Mode; they promote a **dedicated `haste_mode` validation lane** — **never** `fast_mode` and never the trigger-based modules (`sms`/`ai_ranking`/`daily_top`), because a different entry policy earns its own validation (RULE B honesty). **No SMS weight, no `ARMED@70` threshold *definition*, no veto, no phase-gate (RULE A), and no presentation-gate (RULE B) behavior changed** — only the *entry cohort* is widened, and only for Haste Mode. Entry/sizing/exit pinned in §6/§8. See PROGRESS.md decisions log (2026-07-14).

**Changelog — v1.4 (2026-07-14):** added **LD-11 (Fast Mode)** — an operator-armed, hands-off auto paper-trader that **buys every ARMED watchlist name at once**, *without* waiting for the Spring/LPS confirmation trigger, and applies the **same §8 exit ladder** to each buy. This is a deliberate, documented **relaxation of LD-3** (which requires a Spring/LPS trigger + R:R ≥ 2:1 before entry), **confined to Fast Mode** — the standard manual/pipeline path keeps LD-3 in full. *Reason:* the trigger + R:R gate makes arming so rare (§4 changelog / PROGRESS 2026-07-08: ~1 trade/yr across 32 names) that forward paper can never accrue a statistically meaningful sample, so **RULE B can never promote any module** and the LD-8 ML gate never opens — the project's standing deferral. Fast Mode trades entry *precision* for validation *throughput*, and is **paper-only** (§15 unchanged; still no live execution). Fast-Mode entries use a different entry policy than the trigger pipeline, so they promote a **dedicated `fast_mode` validation lane** (RULE B) and **never** the trigger-based modules (`sms`/`ai_ranking`/`daily_top`). Entry geometry, sizing, caps, and the exit are pinned in §6/§8. **No SMS weight, no `ARMED@70` threshold, no veto, no phase-gate (RULE A), and no presentation-gate (RULE B) behavior changed** — only the *entry discipline* is relaxed, and only for Fast Mode. See PROGRESS.md decisions log (2026-07-14).

**Changelog — v1.3 (2026-07-08):** §4 **SMS component calibration** corrected after a full-year backtest over 32 liquid names exposed a *deadlock*: the internal SMS never reached the `ARMED@70` threshold (max 62 across a year), so no module could ever accrue forward paper → RULE B could never promote. Root cause was two component-scoring defects, now fixed: **(a) Price-Volume Divergence** (weight 30, the spine) was computed over the *entire* passed-in history, so its flat-high-volume ratio diluted toward zero for anything that trended — it is now measured over a **recent window** (`SMS_DIVERGENCE_WINDOW_DAYS`, an unpinned implementation choice; §4's ±0.5%/corr conditions + weight are unchanged), and its `corr < 0.3` gate is now a **graduated factor in [0.5, 1.0]** (reducing to the old rule at corr 0 and 0.3) instead of a binary ×0.5 cliff that fired on nearly every name. **(b) Block-trade footprint** graded by a fixed IDR-1B floor OR'd with the %ADV test, which **saturated to 1.0 on any liquid name** (a flat, non-discriminating bonus); it now grades by the max single-broker buy as a **fraction of ADV** (§4's "> 1% ADV"), the IDR floor kept only as the ADV-unknown fallback. **RVOL was investigated and left unchanged** — its near-zero readings on quiet accumulation days are spec-faithful (it is the volume-*anomaly* component). *Reason:* the composite could not reach its own locked threshold on real data, blocking the entire validation path; these are scoring-shape corrections, not weight or threshold edits. **No decision (LD-1…LD-8), no SMS weight, no `ARMED@70` threshold, no veto, no pipeline stage, and no RULE A/B behavior changed.** Post-fix the deadlock is broken (max SMS 62→72; one genuine setup armed and round-tripped in a year-long **in-sample backtest**, net-of-fee) but arming remains rare — no tuning-to-manufacture-trades was done (RULE B). See PROGRESS.md decisions log (2026-07-08) for the full investigation + evidence.

**Changelog — v1.2 (2026-07-03):** §9.1 in-app **session/auth** re-scoped from an out-of-band Bearer *paste* to a full **username + password + MFA login flow** performed by the app against the exodus auth endpoints, yielding access + refresh tokens the DAL already expects (§10). §10 and §15 updated for the credential-handling posture. *Reason:* the operator asked to sign in with credentials rather than hand-capture a Bearer; the tool still consumes only the operator's own session, local-only, nothing republished — so LD-10 holds. No decision (LD-1…LD-8), weight, threshold, gate, pipeline stage, or RULE A/B behavior changed; this is auth plumbing only. The wire contract is now **verified from a real own-session capture** (`login-stockbit.har`, pinned in DATA_SOURCES §4.1) — see the flow in §9.1. **Two items remain unconfirmed and gate implementation:** whether the `recaptcha_token` (reCAPTCHA **v3** — invisible, silently browser-minted; no challenge is ever shown, only the OTP steps are) is **server-enforced**, and the refresh-endpoint shape (not exercised in the capture). The reCAPTCHA question is resolved by a cheap probe (attempt login without/with a junk token); a headless-browser dependency is warranted **only if the probe shows enforcement**.

**Changelog — v1.1 (2026-06-30):** §5 veto filters extended with the finer **trap taxonomy** (markup-on-thin-volume, wash/churn, broker rotation), absorbed from the detection-layer spec (`screener/CONSOLIDATED_SCREENER_SPEC.md`). *Reason:* the single "distribution-dressed-as-accumulation" veto was coarse; these three named sub-screens catch pump, manufactured-volume, and disguised-single-player manipulation it misses. No decision (LD-1…LD-8), weight, or threshold changed.

**Core thesis (unchanged):** Smart-money flow *leads* → technical structure *confirms timing* → fundamental quality *sizes conviction & hold horizon*. EOD/T+1 cadence. Long-only. Liquidity-gated.

**Posture (from vector-lab):** Private, single-user analysis tooling for one operator, consuming the operator's own authenticated data session, for the operator's own decisions. Not a product. No billing, no multi-user, no redistribution. All monetization / go-to-market / "moat" framing is dropped as inapplicable.

---

## 0. The two governing rules

Everything below serves two hard rules. They are orthogonal and compose — one governs *what is tradeable*, the other governs *what may be displayed as a number*.

**RULE A — Tradeability gate (LD-2).** Only Wyckoff **Accumulation Phase C or D** is tradeable. The phase classifier is a hard gate *before* scoring; threshold detectors feed it and never bypass it.

**RULE B — Presentation gate (from vector-lab §1).** A module may display a confidence number, probability, **Smart Money Score, or ranked buy/sell claim ONLY after that signal has survived `PAPER_VALIDATION_MONTHS` of fill-realistic forward paper trading.** Until earned, the module renders the **observation** (raw flow, accumulation, divergence, score *components*) with **no number attached**. The paper-trade engine is the sole authority that promotes a signal from *observation* to *claim*. Config: `PAPER_VALIDATION_MONTHS` (default 3). Framing is always *observation* ("here is the flow, you decide"), never *advice* ("buy these").

> **v1.0→v1.1 change of record:** v1.0 displayed `SMS 0–100` and `ARMED@70` immediately. Under RULE B this is **overridden**: SMS and the ARMED threshold are computed internally but **not shown as a number** until validated. Pre-validation, the screener renders SMS *components* as observation and an internal `ARMED` state drives the watchlist without exposing the score. See §4.

---

## 1. Decision Log — the eight, resolved (+ merge additions)

| # | Contradiction | LOCKED DECISION | Rationale |
|---|---|---|---|
| **LD-1** | Signal hierarchy | **Price-Volume Divergence is the universal spine.** Confirming-leads are tier-dependent: Track A (large-cap) = NBSA foreign flow + broker concentration co-lead; Track B (lapis-2) = broker concentration leads, foreign flow excluded. Weights locked in §4. | Foreign flow only reliable on foreign-held large-caps; broker concentration is the lapis-2 signal. Divergence is confirmed by every source. |
| **LD-2** | Cycle-phase blindness | **Wyckoff phase classifier is a HARD GATE before scoring** (RULE A). Only Accumulation **Phase C or D** is tradeable. Threshold detectors *feed* the classifier; they never bypass it. | A volume/flow threshold with no phase context buys distribution tops. The #1 edge-vs-artifact decision. |
| **LD-3** | Entry discipline | **Grimes wins. No market-on-signal.** A passing score sets state = `ARMED`, not `ENTER`. Entry requires a confirmation trigger (Spring-test or LPS) via **limit order**, and **R:R ≥ 2:1** or no trade. | Next-open + ARA/ARB make market fills fiction anyway. |
| **LD-4** | Universe direction | **Hard liquidity floor is absolute** (§3). Within the liquid set, an index-rebalancing filter down-weights pure-beta moves. **Never chase illiquidity for alpha.** | Stay liquid, then strip index noise *inside* the liquid set. |
| **LD-5** | Data cadence | **EOD/T+1 first.** Scheduler fires on **broker-summary publication**, not market close. Real-time tick / L2 order book DEFERRED to Phase 2+. | The binding signal (broker summary) is EOD. |
| **LD-6** | Fundamental layer | **Present, as a conviction/horizon TILT — not an entry gate.** Metric = **Magic Formula (EY = EBIT/EV, ROC = EBIT/(NWC+NFA))**. Reject ROE/PE/PB. | ROE/PE/PB distorted by leverage — fatal on a bank-heavy index. |
| **LD-7** | Financials-exclusion paradox | **Dual-track scoring.** Financials + utilities run **flow+technical only**, scored with sector proxies, flagged `FLOW_ONLY → shorter hold, tighter trail`. They stay in the universe. | They are the most liquid, most Wyckoff-able, most foreign-flow-driven names. |
| **LD-8** | ML vs overfitting | **Deferred to Phase 4+ and gated.** Rules system must first show **≥3 months forward-paper with positive walk-forward Sharpe**. ML = signal-weight optimizer / ranker on engineered features ONLY. Mandatory purged/embargoed CV + out-of-sample. | Reflexive, non-stationary, small-sample IDX flow data overfits trivially. |
| **LD-9** *(merge)* | Number-display discipline | **Presentation gate (RULE B).** No probability / score / buy-sell verb until `PAPER_VALIDATION_MONTHS` of fill-realistic forward paper. Pre-validation = observation-only. Per-module validation state drives the observation↔claim UI switch. | A self-built confidence number is the easiest thing to over-trust; IDX small-caps can't be honestly calibrated without forward validation. |
| **LD-10** *(merge)* | Data & product posture | **Private single-user, own authenticated session, local-only, never redistributed.** No SaaS, no billing, no multi-user. Stack simplified to local-first (§10). | Personal analysis tool for one operator. Redistribution posture and cloud scale are out of scope. |
| **LD-11** *(v1.4)* | Validation throughput vs entry precision | **Fast Mode — auto paper-trade every ARMED name at once, no trigger.** An operator-armed, hands-off paper auto-trader relaxes LD-3 **for Fast Mode only**: it buys each ARMED name on a marketable LIMIT without a Spring/LPS trigger and **without the R:R ≥ 2:1 gate** (R:R is still computed, as an observation). Stop = Wyckoff range support (invalidation); target = range resistance / measured move (§6). §6 sizing/caps/circuit-breakers and the §8 exit are **unchanged**. **Paper only.** Fast-Mode trades validate a **dedicated `fast_mode` module lane** — never the trigger-based modules. | The trigger + R:R gate arms so rarely (~1 trade/yr/32 names) that forward paper can't accrue enough trades for RULE B to ever promote — the standing deferral. Fast Mode trades entry precision for the validation throughput the whole RULE B / LD-8 path depends on, at zero live risk. The standard path keeps LD-3 intact. |
| **LD-12** *(v1.5)* | Validation throughput vs signal strength | **Haste Mode — auto paper-trade the WATCH+ARMED watchlist, no arming threshold.** An operator-armed Haste-Mode auto-trader extends Fast Mode (LD-11) by **dropping the `ARMED@70` arming cut**: it enters every name in the `{WATCH ∪ ARMED}` set — phase C/D + no veto, *any* internal SMS — on the same triggerless marketable-limit entry (§6), and manages each with the §8 exit. RULE A (phase gate) and §5 veto still bind; only the SMS threshold is relaxed, Haste-only. **Paper only.** Promotes a dedicated `haste_mode` lane — never the trigger-based modules, never `fast_mode`. | Even Fast Mode's ARMED-only set arms too rarely (~1 trade/yr/32 names) for forward paper to accrue → RULE B can't promote, LD-8 can't open. The WATCH cohort is already phase-gated + veto-clean and far larger, so it supplies the validation throughput the whole path depends on, at zero live risk. A separate lane keeps every existing claim honest. |

---

## 2. Pipeline (locked)

```
SCHEDULER (fires on broker-summary publication, ~T+0 evening / T+1)
   │   look-ahead control: every datum stamped with availability_ts (as_of);
   │   a signal may use a datum ONLY IF availability_ts < decision_ts
   ▼
[1] INGEST   OHLCV · broker summary · NBSA foreign flow · corp actions ·
             halt/suspend flags · KSEI ownership · free-float · financials (TTM)
   │   integrity checks flag gaps — missing data never read as zero flow
   ▼
[2] UNIVERSE GATE (§3)        — hard liquidity floor; assign Track A / B; tag sector
   ▼
[3] PHASE CLASSIFIER (RULE A) — Wyckoff phase; PASS only if Phase C or D accumulation
   ▼
[4] SMART MONEY SCORE (§4)    — track-specific weights → SMS 0–100 (INTERNAL until validated)
   ▼
[5] VETO FILTERS (§5)         — kill single-bandar / distribution / markup / wash / rotation / news
   ▼
   SMS ≥ 70  AND  phase ∈ {C,D}  AND  no veto  →  state = ARMED  (watchlist)
   │                                                    │
   │  standard path (LD-3)                              │  Fast/Haste Mode (LD-11/12, operator-armed, paper-only)
   ▼                                                    ▼
[6] TECHNICAL TRIGGER (LD-3)  — Spring-test OR LPS;    [6′] FAST/HASTE AUTO-ENTRY — no trigger; entry =
     compute stop + R:R; require R:R ≥ 2:1                  marketable limit; stop = range support;
   │                                                        target = range res/measured move; R:R
   │                                                        observed, NOT gated
   ▼                                                    ▼
[7] FUNDAMENTAL TILT (LD-6/7) — MF rank (or sector proxy) → conviction & hold horizon
   │   (both [6] and [6′] converge here, then on [8]→[9]→[10] — the shared fill engine + exit mgr)
   ▼
[8] ORDER GEN                 — limit @ trigger; size to 1% risk (§6)
   ▼
[9] PAPER FILL ENGINE         — next-open + slippage; lot=100; tick bands; ARA/ARB reject;
                                FULL fee stack (broker + levy + VAT + 0.1% sell tax)
   ▼
[10] RISK / EXIT MGR (§6,§7)  — stop · target · trailing · signal-decay exit
   ▼
[11] BACKTEST ⇄ FORWARD-PAPER (separate code paths, shared fill engine)
   │   promotes per-module validation state → flips observation↔claim (RULE B)
   ▼
[12] TERMINAL UI (§9)         — observation modules · replay · heatmap · risk monitor ·
                                P&L / armed list / attribution vs benchmark
```

> **Fast Mode vs Haste Mode cohort (LD-11 / LD-12).** Both auto-enter via **[6′]** (no trigger, R:R
> observed-not-gated) and are **paper-only**. **Fast Mode** enters the **ARMED** subset (SMS ≥ 70).
> **Haste Mode** drops the arming threshold and enters the whole `{WATCH ∪ ARMED}` set (phase ∈ {C,D}
> AND no veto, *any* SMS). Steps **[3]** (phase gate, RULE A) and **[5]** (veto) are **upstream** of
> the WATCH/ARMED split, so neither auto-trader can enter a `GATE_REJECTED` or `VETOED` name — RULE A
> and every veto hold **by construction**. Fast → `fast_mode` lane; Haste → `haste_mode` lane (RULE B).

---

## 3. Universe Gate (LD-4) — locked thresholds

**Hard floor (all must pass):**
- 20-day avg daily value traded **≥ IDR 10 bn**
- Last price **≥ IDR 100**
- Not suspended; not IPO with < 60 trading days of history
- Did not close ARA/ARB-pinned on the signal day (no fillable band → reject)
- Complete broker summary for the day (integrity check passed)
- No corporate action within ±5 days (adjust levels; exclude window)

**Track assignment:**
- **Track A** — member of LQ45/IDX80 AND ADV ≥ IDR 25 bn → foreign-flow-reliable
- **Track B** — passes hard floor, not Track A → broker-concentration-reliable

**Index-rebalancing filter:** if a candidate's move is explained by index/sector beta (rolling β-adjusted return ≈ sector return, flow concentrated on index-tracker brokers near rebalance dates), **down-weight SMS by 30%**. Track MSCI free-float / FIF-cut review calendar (2026: BBCA, GOTO, AMMN…) as event risk. Don't reject — just stop paying alpha prices for beta.

---

## 4. Smart Money Score (LD-1) — locked weights (0–100), INTERNAL until validated

| Component | Track A (large-cap) | Track B (lapis-2) |
|---|---|---|
| **Price-Volume Divergence** (high vol, ≤ ±0.5% price; corr < 0.3 on high-vol bars) | **30** | **30** |
| Broker concentration (top-2 net-buy share, ≥ N consecutive days, on flat/down bars) | 20 | **35** |
| NBSA foreign-flow accumulation (net buy > 2× 20d avg, rising) | **25** | 0 |
| Volume anomaly / RVOL (> 3× 20d avg) | 10 | 15 |
| Block-trade footprint (> IDR 1B or > 1% ADV) | 5 | 10 |
| Wyckoff phase-alignment bonus (Spring/LPS proximity) | 10 | 10 |

`SMS ≥ 70` = ARMED threshold (locked). Weights are the **only** tunable surface, and only via backtest Sharpe maximization with walk-forward — never hand-edited live.

> **Component-scoring note (v1.3):** the conditions and weights above are locked; how each
> component *maps its observation to a [0,1] sub-score* is implementation, tuned against real
> data (never to hit a trade count — RULE B). Divergence is measured over a **recent window**
> with a **graduated** corr factor; the block-trade footprint grades by **%ADV** (not a fixed
> IDR floor). See the v1.3 changelog + PROGRESS decisions log (2026-07-08).

**Presentation (RULE B / LD-9):**
- **Pre-validation:** SMS is computed and drives internal `ARMED` state, but the **number is not displayed**. The screener renders the score's *components* as raw observation (divergence bars, broker concentration, foreign-flow Z, RVOL, blocks) and labels the watchlist "highest flow-signal names today — observation, not a recommendation." No % probability, no buy/sell verb.
- **Post-validation** (module has ≥ `PAPER_VALIDATION_MONTHS` fill-realistic forward paper): the numeric SMS and stronger ranking language may be displayed for that module only.

---

## 5. Veto Filters (hard reject regardless of SMS)

*Manipulation / trap detectors:*

- **Single-bandar monopoly** — one broker > 60% of net-buy concentration (gameable).
- **Distribution-dressed-as-accumulation** — high volume + up-bars closing in lower half / UTAD / no-demand rallies / dominant buyer flipping to net sell.
- **Markup-on-thin-volume** — price spiking on low value traded (pump signature, not real demand).
- **Wash / churn** — same broker showing high buy AND high sell (manufactured volume to bait followers).
- **Broker rotation** — buying baton passing between related/correlated broker codes (one player disguised as many; flag correlated-broker behaviour).

*Noise / context filters:*

- **Retail-FOMO** — retail buy ratio > 60% of volume.
- **Event-driven** — material news in window (flow is reacting, not leading).
- **Phase mismatch** — anything not Phase C/D (enforced at [3], restated here).

---

## 6. Entry, Sizing, Risk (LD-3) — locked

- **Trigger:** close of Spring-*test* bar (narrow spread, low vol, holds above spring low) **OR** LPS pullback after SOS.
- **Order:** limit at/below trigger price. No market orders.
- **Stop:** below spring low / swing low (thesis-invalidation level). Never widened.
- **R:R:** ≥ 2:1 to first structural target (AR high / next HVN) or **skip**.
- **Position size:** `qty = (equity × 1%) / (entry − stop)`, rounded down to whole lots. **Risk locked at 1%** (IDX manipulation tax).
- **Conviction multiplier from §7:** compounder ×1.0; speculative ×0.5.
- **Exposure caps:** ≤ 10% equity per name; ≤ 30% per sector; correlated-pair / crowding check (§9 Risk Monitor).
- **Circuit breakers:** halt new entries at −3% daily P&L; pause system at −10% peak-to-trough drawdown.

**Fast Mode entry (LD-11, v1.4 — paper only, operator-armed):** when Fast Mode is armed, an ARMED
name is entered **immediately, without waiting for a Spring/LPS trigger** — the relaxation of LD-3.
Geometry is derived from the Wyckoff trading range the phase gate already established (never invented):

- **Order:** still a **LIMIT** (never a market order) — a *marketable* limit at the ARMED-day
  reference price so it fills at next-open under the paper engine's limit/ARA-ARB discipline (§12).
- **Stop:** just below **range support** (the accumulation-range low) — the thesis-invalidation level,
  never widened. If no coherent range exists (`stop ≥ entry`) the name is **skipped** (missing ≠ invented).
- **Target:** **range resistance** (Phase C) or **resistance + one range span** (Phase D measured move) —
  identical to the standard first target.
- **R:R:** computed and surfaced as an **observation, not a gate** — a Fast-Mode entry is **not skipped
  for R:R < 2:1** (this is the LD-11 relaxation). The standard [6] path still requires R:R ≥ 2:1.
- **Sizing / caps / circuit-breakers:** **unchanged** — 1% risk, conviction multiplier (§7), the ≤10%/name
  & ≤30%/sector caps, and the −3%/−10% breakers all bind exactly as above. Contested slots are ranked by
  internal SMS (RULE B: ordering only; the score is never displayed).
- **Scope:** every ARMED name, subject to the caps (emergent deployment, no gross-exposure target).

Fast Mode is **auto paper execution only — never live** (§15). Its trades earn a **dedicated `fast_mode`
validation lane** (RULE B); they do not promote the trigger-based modules.

**Haste Mode entry (LD-12, v1.5 — paper only, operator-armed):** Haste Mode is Fast Mode with a **wider
candidate set**. Everything about the entry is identical to Fast Mode above — same triggerless
**marketable LIMIT**, same stop = **range support** (invalidation, skipped if `stop ≥ entry`), same
target = **range resistance** (C) / **measured move** (D), same R:R **observed but not gated**, same 1%
risk × conviction multiplier, the same ≤10%/name & ≤30%/sector caps and −3%/−10% circuit-breakers, and
the same §8 exit — with **one** difference:

- **Candidate set = `WATCH ∪ ARMED`, not `ARMED`.** Haste drops the `SMS ≥ 70` arming cut and enters
  every name that reaches state `WATCH` **or** `ARMED` — i.e. phase ∈ {C,D} AND no §5 veto, at *any*
  internal SMS. **RULE A and the §5 veto are unchanged and still bind:** a `GATE_REJECTED` (non-C/D) or
  `VETOED` name is a distinct engine state and is **never** entered (the phase gate [3] and veto [5] run
  upstream of the WATCH/ARMED split). Contested slots are still ranked by internal SMS (RULE B: ordering
  only; the score is never displayed).

Haste Mode is **auto paper execution only — never live** (§15). Its trades earn a **dedicated
`haste_mode` validation lane** (RULE B) — never `fast_mode`, never the trigger-based modules, because a
different entry policy earns its own validation. Only one auto-trader (Fast **xor** Haste) is armed at a
time over the shared paper book / circuit state.

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

**Exit (any one triggers):** stop hit · target hit · trailing stop · **signal-decay** (NBSA flips negative / dominant broker flips to net sell / VPA prints UTAD or no-demand / phase rolls to distribution). **Divergence is the single best exit signal:** price rising while CMF / foreign-flow / A-D all fall.

> **Fast Mode (LD-11) and Haste Mode (LD-12) use this exact exit ladder — no new exit logic.** A Fast-Mode or Haste-Mode buy is managed by the same stop → target → trailing → signal-decay manager as any other position; only its *entry* differs (§6) — and between the two auto-traders, only the entry *cohort* differs.

**Benchmark:** Track A → LQ45. Track B → relevant sector index (or IDX SMC index). **Never IHSG** as headline benchmark. Bar to beat: buy-and-hold the applicable benchmark.

**Metrics tracked:** total/annualized return, Sharpe, max drawdown, hit rate, **turnover** (flow strategies churn; fees punish churn), and **return net of full fee stack** (the only number that counts).

**Validation gate to advance a phase / promote a module (RULE B):** walk-forward + out-of-sample only; backtest and forward-paper are separate code paths sharing the fill engine; survivorship + look-ahead controls mandatory.

---

## 9. Terminal / observation layer (from vector-lab §5) — the operator-facing shell

The pipeline (§2) is the engine; this is the workbench over it. Modules are tiered by whether they may show a number (RULE B). Single-user, keyboard-driven, dark, paned.

**Ships now — pure observation (no gate):**
- **Broker Flow Analyzer** *(the differentiator)* — per-stock broker net buy/sell; broker DNA classification (Foreign Inst / Local Inst / Smart Money / Retail / Prop); concentration (top-N share / Herfindahl); persistence over rolling window; custom "syndicate" grouping; broker-stock matrix (top buyers vs sellers).
- **Foreign Flow Dashboard** — foreign net-buy magnitude & persistence vs float; foreign-vs-domestic split; market/stock/sector levels; flow-reversal detection; KSEI ownership trend overlay.
- **Institutional Accumulation Detector** — stealth divergence (price flat/down while net accumulation rises), accumulator VWAP estimate, absorption (if depth), volume dry-up + price-tightness during consolidation.
- **Money Flow Replay (timeline)** — scrub historical flow/price evolution for any name; overlay price/volume/foreign/broker on one axis. **The audit tool for every signal — build it early.**

**Ships now — derived visualizations (rendering, not new signal):**
- **Smart Money Heatmap** — aggregate over Flow/Foreign/Accumulation signals; color = direction, intensity = flow-as-%-of-cap; sector→stock→broker drill-down; divergence alerts (local buy + foreign sell).
- **Sector Rotation Map** — flow aggregated by sector; RS-vs-flow quadrant (Leaders / Early Recovery / Distribution Warning / Avoid); foreign/domestic tide framing.

**Ships now — observation-only (risk observations, not return predictions):**
- **Portfolio Risk Monitor** — crowding ("same bandar" correlation), beta vs IHSG, sector concentration (Herfindahl), VaR, liquidity / days-to-exit, gap/volatility & event risk, scenario stress tests. Feeds the §6 exposure caps and correlated-pair check.

**Gated behind paper-trade validation — NO number until earned (RULE B):**
- **Smart Money Score / Breakout components** — pre-validation show raw components; post-validation may show the SMS number (§4).
- **AI Buy/Sell Ranking** — pre-validation a "flow-derived ranking, not a recommendation"; stronger language only once forward hit-rate is paper-validated (LD-8 also governs the ML ranker).
- **Daily Top Opportunities** — "highest flow-signal names today," observation framing; narrative digest of what the flow shows.
- **Fast Mode Auto-Trader** *(v1.4, LD-11)* — the operator-armed auto paper-trader over the ARMED watchlist (§6/§8). Shows the open **book** (positions, stops, targets) and **closed trades** with per-trade net-of-fee realized P&L (a *factual* observation, not a forecast — allowed once an entry price exists). The strategy's **aggregate** claim (hit-rate / expectancy / the promotable number) is **withheld** (`•••`) until the `fast_mode` module clears `PAPER_VALIDATION_MONTHS` of forward paper (RULE B). Closed positions surface in the Signal Pipeline as the `EXITED` verdict.
- **Haste Mode Auto-Trader** *(v1.5, LD-12)* — the same operator-armed auto paper-trader over the **wider** `WATCH ∪ ARMED` watchlist (§6/§8) — no arming threshold. Same panel as Fast Mode: the open **book** and **closed trades** with per-trade net-of-fee realized P&L are *factual* observations (allowed once an entry price exists); the strategy's **aggregate** claim (hit-rate / expectancy) is **withheld** (`•••`) until the **`haste_mode`** module clears `PAPER_VALIDATION_MONTHS` of forward paper (RULE B) — a **dedicated lane**, never `fast_mode` or the trigger-based modules. Closed positions surface in the Signal Pipeline as the `EXITED` verdict.

### 9.1 Session gate (in-app login flow, v1.2)

*(v1.2 re-scope of the auth surface. No locked-behavior change to the engine — auth plumbing only. Still the operator's own session, local-only, never republished — LD-10 / §10 / §15 hold. The wire contract is **verified** from `login-stockbit.har` and pinned in DATA_SOURCES §4.1; the two unconfirmed items below gate implementation.)*

The launcher (`run.sh`) always starts the server; the auth gate lives in the terminal shell. On load the app reads session status from the Keychain (no network). No valid session → the shell renders the **login flow instead of the modules** — never a blank or stale terminal (DAL "fail loud, never emit stale/empty"). The flow (verified endpoints, all `POST` to `exodus.stockbit.com`; full field shapes in DATA_SOURCES §4.1):

1. **`/login/v6/username`** — `{user, password, recaptcha_token, recaptcha_version, player_id}` → on a new device returns `multi_factor.{login_token, verification_token}`.
2. **`/mfa/verification/v1/challenge/start`** — `{verification_token}` → `next_challenge` + OTP channels (email / WhatsApp / SMS).
3. **`/mfa/.../otp/send`** then **`/otp/verify`** — operator picks a channel, receives a code, enters it. **The verify step loops**: it may return another `CHALLENGE_OTP` (a second channel) before `CHALLENGE_FINISH`. The form drives send→verify until finished.
4. **`/login/v6/new-device/verify`** — `{multi_factor:{login_token}}` → `data.access.{token, expired_at}` + `data.refresh.{token, expired_at}` (access ≈ 24h, refresh ≈ 7d). Both tokens are stored in the OS Keychain and the app reruns into the terminal.

- **Bad credentials / failed OTP → in-browser error**, nothing stored.
- **Valid session → terminal.** Modules render as today. A masked account/session status and a **sign-out** control (clears the Keychain tokens → returns to the login form) live in the top bar.
- **Auth loss mid-session.** On a DAL `401` the app first attempts a **refresh** using the stored refresh token (the token-refresh path DATA_SOURCES §4 requires); if refresh fails it returns the operator to the login form. Never a silent fallback to stale/empty data.

**Open items gating implementation (DATA_SOURCES §4.1):** (a) `recaptcha_token` is a **reCAPTCHA v3** token — invisible (no challenge is presented; the operator sees only the OTP steps) and silently browser-minted. The open question is **server-side enforcement**, settled by a cheap probe (login with the token omitted / junked): if unenforced, a pure-Python login works; if enforced, mint it via a headless browser or an assisted step, else fall back to the slice-10 Bearer paste. (b) The **refresh endpoint** shape was not in the capture. Until these are settled, the slice-10 out-of-band Bearer capture remains the working fallback (§10).

**Credential handling (posture):** username/password and any OTP are held **transiently in memory** for the duration of the login exchange and are **never persisted, logged, or written to disk** — only the returned access/refresh tokens reach the Keychain. Login talks to the exodus auth endpoints and nowhere else; nothing leaves the machine beyond that own-session authentication (§10/§15).

The login flow is auth only — it establishes the operator's *own* session. It does not gate or alter any signal, number, or RULE A/B behavior.

---

## 10. Data posture & stack (LD-10) — locked

**Data:** feed consumed from the operator's own authenticated session (e.g. HAR-derived endpoints). Likely violates provider ToS; done at operator's own risk for personal use only. **Local persistence only — nothing leaves the machine, nothing is republished.** Parser breakage on endpoint changes is expected maintenance, not a crisis. Third-party APIs (OHLC.Dev, Sectors, Invezgo, iTick, RTI, KSEI) are reference-only and not redistributed.

**Required feeds:** broker summary (core), OHLCV, foreign/domestic classification, corporate actions + suspension/halt + ARA/ARB state, free-float / shares-outstanding, financials (TTM). Order-book depth is an optional tier — some modules degrade gracefully without it.

**Stack (single-user, local-first):** Python (Pandas/Polars, TA-Lib) for analytics & signals; local time-series store (SQLite / DuckDB — no cloud for one user); **Streamlit** prototype UI (richer later); feature-store schema with `as_of` stamps so gated modules have clean inputs when they earn validation. *(Supersedes the v1.0 thesis's TimescaleDB/Postgres/Docker-cloud sketch — over-built for one operator.)*

**Session / auth surface (v1.2):** the operator signs in with **username + password (+ MFA)** via the in-app login flow (§9.1), which authenticates against the exodus auth endpoint (`login/v6` + MFA) and stores the returned **access + refresh** tokens in the OS Keychain; the access token is read fresh per request and refreshed on `401`, never written to disk in plaintext. Credentials/OTP are transient in-memory only — never persisted (§9.1). *(The slice-10 CLI `login paste` of a hand-captured Bearer remains a fallback for capturing a session out-of-band, but the primary surface is the credential login.)* Own authenticated session only, own risk — §15.

---

## 11. Build Order (locked phasing)

Each slice is a full vertical (data → signal → view → test). Bootstraps off the existing paper-trade system.

1. **Data layer + integrity checks** — broker summary + OHLCV + NBSA ingestion, `as_of` stamps, gap detection. Nothing renders until trustworthy.
2. **Universe gate (§3) + Broker Flow Analyzer** — first end-to-end vertical slice; proves the pipeline. Manual armed-list alerts; validate signal quality 2–4 weeks before automating.
3. **Foreign Flow Dashboard + Money Flow Replay** — replay early; it is the audit tool for everything downstream.
4. **Phase classifier + SMS (internal) + veto filters** — Institutional Accumulation Detector + Smart Money Heatmap over them; backtest 2+ yrs with fees & look-ahead controls.
5. **Stage-2 distribution/trap layer** — the credibility layer; wire trap/veto flags into every view.
6. **Sector Rotation Map + Portfolio Risk Monitor** — Stage-4 gates surfaced as risk observations; feed §6 caps.
7. **Execution** — technical-trigger logic, fundamental tilt, fill engine (fees / ARA-ARB / lots / ticks), risk mgr; run forward-paper.
8. **Paper-trade validation wiring** — connect forward results to per-module validation state; implement the observation↔claim presentation switch (RULE B). Gated modules ship observation-only from step 4 and earn claims only as this step promotes them.
9. **Scale / ML (gated)** — only after ≥3 months positive forward-paper walk-forward Sharpe; ML as ranker / weight-optimizer with purged CV (LD-8).

*Operational slices after the build order (bootstraps off the paper-trade system; not new engine phases):*

- **Fast Mode auto paper-trader (v1.4, LD-11)** — wire the existing portfolio paper-trader to a hands-off scheduler job that auto-enters every ARMED name (§6 Fast Mode entry) and manages it with the §8 exit, persisting a durable book and feeding the server-authoritative `ValidationLedger` (`fast_mode` lane). This is the mechanism that finally makes a real multi-month forward-paper run accrue, so RULE B can promote and the LD-8 gate can open.

- **Haste Mode auto paper-trader (v1.5, LD-12)** — generalizes the Fast Mode auto-trader (same driver, persistence, scheduler job, and §8 exit) to a **wider candidate cohort**: it drops the `ARMED@70` arming cut and auto-enters the `WATCH ∪ ARMED` set (phase C/D + no veto, any SMS), feeding a **dedicated `haste_mode` lane**. Where Fast Mode's ARMED-only set still arms too rarely to forward-validate, Haste supplies the throughput — the WATCH cohort is far larger while staying phase-gated + veto-clean (RULE A / §5 intact).

**Governing filter throughout:** if a signal can't survive fill-realistic paper trading, it does not earn the right to show a number.

---

## 12. Paper broker (IDX-aware) — locked

Lots of 100 shares · auto-reject bands (±7% main board / ±10–25% dev board / ±35% first 15d post-IPO) · fees ~0.15–0.25% + levy + VAT + 0.1% sell tax · T+2 settlement · IDR · WIB hours (09:00–11:30, 13:30–15:00). Next-open fills with slippage (LQ45 0.05–0.15%, mid-cap 0.2–0.5%, small-cap >1%). Bar to beat: buy-and-hold the applicable benchmark (§8). Personal account only.

---

## 13. Acceptance Criteria (definition of done for v1.1)

- [ ] No signal consumes data with `availability_ts ≥ decision_ts` (look-ahead test passes).
- [ ] Phase gate rejects all non-C/D candidates (unit-tested on labeled charts).
- [ ] Veto filters reject markup-on-thin-volume / wash-churn / broker-rotation cases (unit-tested on labeled examples).
- [ ] **No unvalidated module displays a number** — SMS, probabilities, and buy/sell verbs are hidden until the module clears `PAPER_VALIDATION_MONTHS` (RULE B test passes).
- [ ] Per-module validation state exists and drives the observation↔claim UI switch.
- [ ] Every order is a limit order with a defined stop and R:R ≥ 2:1.
- [ ] Fill engine reproduces lot/tick/ARA-ARB/fee math against hand-checked cases.
- [ ] Backtest and forward-paper share one fill engine; results reconcile.
- [ ] Reported return is net of the full fee stack and benchmarked to LQ45/sector, not IHSG.
- [ ] Money Flow Replay can reconstruct any past signal from stored `as_of` data (audit test).
- [ ] All data stays local; nothing is republished (posture check).
- [ ] No live hand-editing of SMS weights; tuning only via walk-forward optimizer.
- [ ] **(v1.4)** Fast Mode enters an ARMED name with no trigger and R:R < 2:1 (LD-11), while the standard [6] path still skips it; the Fast-Mode buy exits via the same §8 ladder; its trades promote only the `fast_mode` lane (not `sms`/`ai_ranking`/`daily_top`) and its aggregate number stays withheld until validated (RULE B).
- [ ] **(v1.5)** Haste Mode enters a `WATCH` name (phase C/D, no veto, SMS < 70) with no trigger/R:R (LD-12), while Fast Mode does **not** enter it and the standard [6] path skips it; a `GATE_REJECTED` (non-C/D) or `VETOED` name is **never** entered by either auto-trader (RULE A / §5 firewall); the Haste-Mode buy exits via the same §8 ladder; its trades promote only the `haste_mode` lane (not `fast_mode`/`sms`/`ai_ranking`/`daily_top`) and its aggregate number stays withheld until validated (RULE B).

---

## 14. Companion files (to create)

- `PLAN.md` — slice-by-slice execution plan from §11.
- `CLAUDE.md` — repo conventions: stack (§10), architecture, TDD loop, and RULES A & B restated as hard constraints (never render a number for an unvalidated module).
- `PROGRESS.md` — durable log of shipped slices and each module's current validation state.

---

## 15. Disclaimers (embed in-app, operator-facing)

- Private personal-use analytics tool. Not a product, not a service, not for redistribution.
- Not investment advice. All outputs are observations for the operator's own decisions.
- Data consumed from the operator's own session; used at own risk; not republished.
- No live execution. Paper trading only. Paper results do not guarantee live performance. **Fast Mode (v1.4, §6/LD-11) and Haste Mode (v1.5, §6/LD-12) auto-execute on the ARMED (Fast) / WATCH+ARMED (Haste) watchlist in *paper* only — they never place a live order.**
- The operator's own Stockbit credentials/OTP are entered locally, used only to authenticate against the exodus endpoint, and never persisted, logged, or transmitted anywhere else (§9.1); only the resulting session tokens are stored (OS Keychain). Own-session login, own risk.

---

*v1.5 (2026-07-14): LD-12 Haste Mode — operator-armed auto paper-trader over the WATCH+ARMED watchlist; drops the `ARMED@70` arming threshold (Haste Mode only); §1/§2/§6/§8/§9/§11/§13/§15 updated; paper-only, RULE A (phase gate) + §5 veto + RULE B unchanged, dedicated `haste_mode` validation lane.*
*v1.4 (2026-07-14): LD-11 Fast Mode — operator-armed auto paper-trader over the ARMED watchlist; relaxes LD-3 (no trigger / no R:R gate) for Fast Mode only; §2/§6/§8/§9/§11/§15 updated; paper-only, RULE A/B unchanged, `fast_mode` validation lane.*
*v1.3 (2026-07-08): §4 SMS component-scoring calibration (divergence recency + graduated corr; block-trade %ADV); no decision/weight/threshold/gate change.*
*v1.2 (2026-07-03): in-app username/password (+MFA) login flow re-scope (§9.1/§10/§15); auth plumbing only, engine unchanged.*
*v1.1 consolidated from: `LOCKED_SPEC.md` v1.0 (engine core) + `vector-lab.md` (terminal re-frame, presentation gate, data posture). Upstream: `CONSOLIDATED_THESIS.md` and the six source drafts.*
