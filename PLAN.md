# PLAN.md — CurrentFlow execution plan

Slice-by-slice plan derived from `LOCKED_SPEC.md` §11 (Build Order), with data endpoints from
`DATA_SOURCES.md` §6 and server-side pre-filters from `screeners.md`.

**UI target:** each view slice below rebuilds its module from the hifi design handoff in
[`design/`](design/) (README + prototype). The 8 design modules map to slices as: Broker Flow →
S2, Foreign Flow + Money Replay → S3, Accumulation Detector + Smart Heatmap → S4, Sector Rotate +
Risk Monitor → S6, SMS/Rank (RULE B centerpiece) → gated, shipped observation-only from S4 and
promoted to claim at S8. Match the design's tokens/geometry; wire real `as_of` data, not the
prototype's seeded mock.

**Governing filter throughout:** if a signal can't survive fill-realistic paper trading, it does
not earn the right to show a number (RULE B). Each slice is a full vertical: **data → signal →
view → test**. Nothing renders until its data is trustworthy.

Legend: ⬜ not started · 🟡 in progress · ✅ done. Keep the box in sync with `PROGRESS.md`.

---

## Slice 1 — Data layer + integrity checks  ✅
**Goal:** trustworthy ingestion before anything renders.
- [x] `ExodusClient`: Bearer auth + token refresh/re-capture (one refresh on 401); 401 → fail loud, no retry; paywall/rate-limit/5xx exponential backoff (2,4,8,16s). Transport injected. `currentflow/dal/client.py`.
- [x] `broker_summary(sym, from, to)` — `marketdetectors/{sym}` → `list[BrokerNet]` (buy/sell, investor tag, accumulator VWAP).
- [x] `ohlcv_foreign(sym, from, to)` — `company-price-feed/historical/summary/{sym}` → `list[DailyBar]` (OHLCV + foreign + VWAP).
- [x] `as_of` (availability_ts) stamped on every record (`dal/timing.py`); DuckDB store keyed `(symbol, date, as_of)` (`store/db.py`, `store/schema.py`).
- [x] Ingest-once cache: `ingest_symbol` fetches only missing trading days; writes `ON CONFLICT DO NOTHING`; re-pull is a no-op (`ingest/pipeline.py`).
- [x] Integrity/gap checks: TRADED / NO_TRADES / NOT_PUBLISHED / GAP; missing never read as zero (`store/integrity.py`).
- [~] **Empirically measure broker-summary publish latency** (LD-5): measurement tool built (`ingest/publish_latency.py`); actual pinning of `config.BROKER_PUBLISH_LATENCY` awaits accrued live data — conservative next-day fallback in force until then.
- [x] **Tests (23 passing):** look-ahead (`as_of < decision_ts`, strict, latest-visible); gap-vs-zero; cache-idempotency; + parser/client/pipeline.
- [~] **Live transport not wired:** `login/v6` + MFA Bearer capture and the real httpx transport need operator credentials (out of scope for CI). The client is transport-agnostic and ready to accept it.

## Slice 2 — Universe gate (§3) + Broker Flow Analyzer  ✅
**Goal:** first end-to-end vertical; proves the pipeline.
- [x] Hard floor: ADV ≥ IDR 10bn (20d), price ≥ 100, not suspended, no IPO<60d, no ARA/ARB-pinned close, complete broker summary, no corp action ±5d (`universe/gate.py`; every reject carries reasons — no silent caps).
- [x] Track A/B assignment from `emitten/{sym}/info.indexes` (A = LQ45/IDX80 & ADV≥25bn; B = rest).
- [x] Index-rebalancing filter: down-weight pure-beta moves 30% (don't reject) — `universe/rebalance.py`; multiplier consumed by SMS at slice 4.
- [x] ARA/ARB band derivation (`DATA_SOURCES.md` §3.2): board type + prev close → pinned check (`universe/bands.py`; dev-board 10–25% resolved by price tier, see PROGRESS decisions).
- [x] **Broker Flow Analyzer** (observation, no gate): per-stock broker net buy/sell, broker DNA
      (Foreign/Local Inst / Smart Money / Retail / Prop), top-N share + Herfindahl, persistence,
      custom syndicate grouping, buyer-vs-seller matrix (`signals/broker_flow.py`; Streamlit view
      `ui/app.py` + `ui/broker_flow_view.py`). DNA registry seeded from design handoff — illustrative,
      operator-verified over time.
- [x] SCR-0 eligibility screener wired (server-side pre-filter → ~100–150 names): `screeners/scr0.py`,
      POST via `ExodusClient.run_screener`, cached to DuckDB `scr0_eligible` with `as_of`.
- [x] New DAL feeds: `symbol_info`, `corp_actions`, `special_board` + tolerant parsers.
- **Manual armed-list alerts; validate signal quality 2–4 weeks before automating.**
- [x] **Tests (60 new, 83 total passing):** universe-gate unit tests (each rule + multi-failure);
      ARA/ARB hand-checked math; broker concentration/Herfindahl hand-checked; persistence;
      look-ahead through the analyzer; SCR-0 template fidelity + ingest-once cache.

## Slice 3 — Foreign Flow Dashboard + Money Flow Replay  ✅
**Goal:** build the audit tool early.
- [x] **Foreign Flow Dashboard** (observation): NBSA magnitude (vs-20d multiple + z-score,
      both measurements), persistence & flow-reversal detection, NBSA-as-%-of-float (from
      latest visible SCR-0 row), foreign-vs-domestic split (net + turnover share),
      market/sector tide aggregate (operator sector map; skips logged, never zeroed),
      KSEI monthly ownership overlay (`signals/foreign_flow.py`; view `ui/foreign_flow_view.py`).
- [x] **Money Flow Replay** (timeline): one frame per trading day, each reconstructed by
      re-reading the store at that day's historical `decision_ts` (D+1 09:15 WIB — after
      D's EOD bar and the LD-5 conservative broker publish); price/volume/foreign/broker
      lanes; gaps render as empty frames; Wyckoff phase lane stays a placeholder until the
      slice-4 classifier exists (`signals/replay.py`; view `ui/replay_view.py`).
- [x] SCR-1A foreign-accumulation screener (Track A, LQ45 scope; IDX80 scope constant
      provided): `screeners/scr1a.py`, cached to DuckDB `scr1a_foreign_accum` with `as_of`.
- [x] New DAL feed: `ksei_ownership` (`emitten-metadata/shareholders/{sym}/chart`),
      `as_of` = fetch time (KSEI publish lag undisclosed — conservative by construction).
- [x] **Tests (27 new, 110 total passing):** replay/audit acceptance test — future invisible,
      revisions respect `as_of`, LD-5 broker availability honored, frames reconcile exactly
      with live `foreign_flow`/`broker_flow` signals at the same historical `decision_ts`;
      hand-checked NBSA stats; reversal/persistence; missing-≠-zero; SCR-1A template fidelity
      + ingest-once; KSEI parser shapes + look-ahead.

## Slice 4 — Phase classifier + SMS (internal) + veto filters  ✅
**Goal:** the core decision engine — internal only, gated by RULE B.
- [x] **Wyckoff phase classifier (RULE A HARD GATE):** detector-fed (selling climax →
      trading range → spring / SOS+LPS / UTAD); PASS only Phase C/D (`signals/phase.py`).
- [x] **SMS (§4)** track-specific weights (`config.SMS_WEIGHTS`, the only tunable surface)
      → components + internal 0–100; **number GATED, never displayed** (`signals/sms.py`).
- [x] Veto filters (§5) full v1.1 trap taxonomy: single-bandar monopoly (>60%),
      distribution-dressed / dominant-buyer flip / UTAD, markup-on-thin-volume (up-spike),
      wash/churn, broker rotation, retail-FOMO (>60%), event-driven, phase mismatch (`signals/veto.py`).
- [x] `ARMED` state: SMS≥70 AND phase∈{C,D} AND no veto → watchlist, **no score shown**
      (`signals/engine.py`; states GATE_REJECTED/VETOED/WATCH/ARMED).
- [x] Per-module validation state drives the observation↔claim switch (`validation/state.py`);
      SMS/Rank ships observation-only (components) — `ui/sms_view.py` withholds the number.
- [x] **Institutional Accumulation Detector** (`signals/accumulation.py`) + **Smart Money
      Heatmap** (`signals/heatmap.py`) as observation; wired into Streamlit; replay phase lane lit.
- [~] **Backtest 2+ yrs** — DEFERRED to slice 7: a fee-realistic backtest must share the
      IDX fill engine (§11/§13 "backtest and forward-paper share one fill engine"), which lands
      in slice 7. Engine + phase are already look-ahead-safe and replay-auditable; running a
      P&L backtest now (no fills/fees) would violate that discipline. Logged in PROGRESS decisions.
- [x] SCR-1B (bandar accum, IDXSMC-LIQ), SCR-1C (stealth divergence proxy), SCR-2 (RVOL) wired
      (`screeners/scr1b.py`, `scr1c.py`, `scr2.py`; cached to DuckDB with `as_of`).
- [x] **Tests (45 new, 155 total):** phase-gate rejects non-C/D on labeled charts; RULE B — SMS
      number hidden pre-validation and revealed only when VALIDATED; veto taxonomy per labeled case;
      engine state machine + look-ahead; SMS component math; accumulation/heatmap; screener fidelity.

## Slice 5 — Stage-2 distribution / trap layer  ✅
**Goal:** the credibility layer.
- [x] **§8 signal-decay detectors** (`signals/distribution.py`, pure observation): PHASE_ROLLOVER
      (phase → DISTRIBUTION / UTAD), NO_DEMAND (up bar, narrow spread, shrinking volume — VSA),
      BEARISH_DIVERGENCE (price up while net flow falls — "the single best exit signal", §8),
      FOREIGN_OUTFLOW (NBSA sell streak). Exit-side complement to the slice-4 §5 entry vetoes;
      categorical severities (INFO/WATCH/WARN), never a number (RULE B); `missing ≠ zero`.
- [x] **Wire trap/veto flags into every view:** `TrapMonitor` unifies §5 veto traps + §8 decay
      from one look-ahead-safe read; `ui/trap_view.py` ribbon (most-severe-first) is rendered at
      the top of every built module (Broker Flow, Foreign Flow, Accum, Replay, Heatmap, SMS).
- [x] **SCR-EXIT** distribution/mirror screener (`screeners/scr_exit.py`) exactly per screeners.md
      (14400<0 ∧ 13540<0 ∧ 13562>2), cached to DuckDB `scr_exit_distribution` with `as_of`,
      ingest-once, look-ahead-safe read; `exit_flags_for` intersects survivors with the open+ARMED
      watchlist (off-watch names logged, never silently dropped).
- [x] **Tests (17 new, 172 total):** each decay detector fires on its labeled chart; clean
      accumulation stays clean (no false alarms); look-ahead-safe monitor; missing≠zero; SCR-EXIT
      template fidelity + ingest-once + watchlist intersection; ribbon severity ordering + RULE B.

## Slice 6 — Sector Rotation Map + Portfolio Risk Monitor  ✅
**Goal:** Stage-4 gates surfaced as risk observations (not return predictions).
- [x] **Sector Rotation Map** (derived view): net-foreign flow by sector on the RS-vs-flow
      quadrant (LEADERS / EARLY_RECOVERY / DISTRIBUTION_WARN / AVOID — spec §9 labels),
      relative strength vs the universe (equal-weight proxy), foreign/domestic tide.
      Look-ahead-safe; `missing ≠ zero` (no-data symbols skipped+logged, a sector missing an
      axis carries `quadrant=None`). `signals/sector_rotation.py`, view `ui/sector_view.py`.
- [x] **Portfolio Risk Monitor** (observation, risk ≠ prediction): §6 exposure caps, sector
      Herfindahl, "same-bandar" crowding matrix (broker-overlap cosine) + correlated-pair check,
      β vs an injected benchmark, historical VaR (95%·1d), liquidity/days-to-exit, scenario
      stress (defined what-ifs), §6 circuit breakers. `signals/risk_monitor.py`, view
      `ui/risk_view.py`. Positions are an input (fill engine → slice 7); P&L withheld until an
      entry price exists.
- [x] Feed §6 exposure caps (≤10%/name, ≤30%/sector) + correlated-pair check (crowding ρ ≥ 0.7).
- [x] Both wired into the Streamlit nav (Sector Rotate, Risk Monitor).
- [x] **Tests (20 new, 192 total):** quadrant classification + RS/flow aggregation + look-ahead +
      missing≠zero; exposure caps, HHI, crowding/shared-broker, β (incl. zero-variance guard),
      historical VaR nearest-rank, days-to-exit, scenario impacts, circuit-breaker states,
      end-to-end report + look-ahead firewall on broker flow.

## Slice 7 — Execution  ✅
**Goal:** trigger → order → fill → risk; run forward-paper.
- [x] Technical trigger (LD-3): Spring-test (Phase C) OR LPS (Phase D); stop below spring/LPS swing low;
      first target = AR high (C) / measured move resistance+span (D); **R:R ≥ 2:1 or skip** (`execution/trigger.py`).
- [x] Fundamental tilt (§7): Magic Formula combined-rank tercile (fitem 13474; EY=1/2897, ROC=13411) →
      COMPOUNDER ×1.0 / NEUTRAL ×0.75 / SPECULATIVE ×0.5; negative EBIT → SPECULATIVE; FLOW_ONLY dual-track
      for financials/utilities (ROE proxy can lift ×0.75→×1.0, never COMPOUNDER hold) — never a gate
      (`fundamentals/tilt.py`).
- [x] Order gen: **LIMIT only**, size to 1% risk × conviction multiplier, §6 exposure caps (10%/name,
      30%/sector), §6 circuit breakers halt new entries (`execution/order.py`).
- [x] **IDX-aware paper fill engine (§12):** lot=100, tick bands (fraksi harga), ARA/ARB reject on the
      adverse side, next-open + liquidity-tiered slippage, limit discipline, FULL fee stack (commission +
      levy + VAT-on-commission + 0.1% sell tax), T+2 settlement — the ONE fill engine (`paper/fill.py`).
- [x] Risk/exit mgr (§8): stop → target → trailing (hold-profile width) → signal-decay exit (via
      `signals.distribution`; divergence = best exit signal), capital-first priority (`execution/risk.py`).
- [x] SCR-3 trend-confirm, SCR-4 fundamental-tilt screeners wired + cached with `as_of`
      (`screeners/scr3.py`, `scr4.py`; new `Scr3Row`/`Scr4Row` + store tables).
- [x] **Tests (54 new, 246 total):** fill-engine lot/tick/ARA-ARB/fee/slippage/T+2 hand-checked; tilt
      terciles + FLOW_ONLY + negative-EBIT; trigger geometry + R:R gate; order sizing/caps/breakers;
      exit priority + signal-decay; SCR-3/4 template fidelity + ingest-once + look-ahead; end-to-end
      ARMED→trigger→tilt→order→fill invariant (LIMIT, defined stop, R:R≥2:1).
- **Deferred within slice:** forward-paper *run* + the 2-yr backtest reconciliation land in slice 8
  (they wire this shared fill engine to the validation state machine). Live fundamentals DAL feed
  (`fundamentals_live`) not yet wired — the tilt is pure over injected/SCR-4 values.

## Slice 8 — Paper-trade validation wiring (RULE B switch)  ✅
**Goal:** connect forward results to per-module validation state.
- [x] **Paper-trade runner** (`validation/runner.py`) — the forward-paper *run* deferred from slice 7:
      walks a symbol through engine → trigger → tilt → order → **shared fill engine** → risk/exit,
      emitting closed `PaperTrade`s. `run_backtest` (batch) + `run_forward` (day-by-day accrual) are
      two code paths over the same `_attempt_entry`/`_attempt_exit` helpers (hence one `fill_order`),
      so they **reconcile** over identical data (§13). Look-ahead-safe (per-day `decision_ts`).
- [x] **`PaperTrade` atom** (`validation/trade.py`) — P&L net of the full fee stack by construction
      (built from the two engine fills' cash flows; no fee math redone).
- [x] **§8 metrics** (`validation/metrics.py`) — net-of-fee total/annualised return, Sharpe, max
      drawdown, hit rate, turnover, excess-vs-benchmark; `walk_forward_sharpe` (worst of N folds).
      **IHSG is refused as a benchmark** (raises) — never the composite (§8).
- [x] **Promotion engine** (`validation/promotion.py`) — `ValidationLedger`, the server-authoritative
      *sole authority*: `record_forward_paper` is the only writer; promotes OBSERVATION_ONLY →
      VALIDATING → VALIDATED only on ≥ `PAPER_VALIDATION_MONTHS` **and** positive walk-forward Sharpe.
- [x] **Observation↔claim switch across ALL gated modules** — shared `validation.state.gated_display`
      (`•••` until VALIDATED); `ui/sms_view` refactored onto it; new `ui/ranking_view` (AI Buy/Sell
      Ranking) + `ui/daily_top_view` (Daily Top). All three wired into the Streamlit nav, reading the
      ledger's states (never a client toggle).
- [x] Benchmark net-of-fees to LQ45 / sector index — never IHSG (metrics guard + §8 discipline).
- [x] **Tests (21 new, 267 total):** runner round-trip (entry→target) + backtest/forward reconciliation
      + look-ahead firewall; metrics hand-checked + IHSG-refusal + walk-forward folds; promotion state
      machine (months × walk-forward, per-module isolation); RULE B end-to-end across all three gated
      modules (withheld pre-validation, revealed only after the ledger promotes).
- **Deferred:** a *real* multi-month forward-paper run against live-session data (needs the live DAL
  transport + accrued time) — the harness is built and tested; production modules stay OBSERVATION_ONLY
  until an actual run promotes them. Live-fundamentals feed still injected (tilt), unchanged from slice 7.

## Slice 9 — Scale / ML (gated)  ✅
**Goal:** only after ≥3 months positive forward-paper walk-forward Sharpe (LD-8). Built as a
**harness gated shut** — exactly the slice-8 posture: the code is complete and tested, but an
LD-8 admission gate keeps it closed until the rules system actually earns validation. In
production nothing has cleared forward paper, so the whole ML layer refuses to run (correct).
- [x] **LD-8 admission gate** (`ml/admission.py`) — the ML analogue of RULE B: `check_admission`
      reads the server-authoritative `ValidationLedger`; ML is admitted ONLY when the rules
      system (`sms`) is VALIDATED (≥`PAPER_VALIDATION_MONTHS` + positive walk-forward Sharpe).
      `require_admission` raises `MLNotAdmittedError`; every ML entry point calls it first, so
      nothing in `currentflow.ml` can run ahead of the rules.
- [x] **Purged + embargoed walk-forward CV** (`ml/cv.py`, LD-8 mandatory): anchored forward
      folds (train strictly precedes test), **purge** of any train sample whose label span
      overlaps the test window, **embargo** buffer at the boundary; too-few-samples raises
      (missing ≠ zero). Deterministic index math, no shuffling.
- [x] **Engineered features only** (`ml/features.py`): the feature space IS the existing §4 SMS
      component sub-scores — a thin look-ahead-safe adapter, no new signal (LD-8).
- [x] **ML strictly as signal-weight optimizer** (`ml/optimizer.py`) — the sole writer of the
      weight surface: deterministic coordinate ascent on the integer weight simplex maximizing
      in-sample (train-fold) Sharpe, reporting **worst out-of-sample test-fold walk-forward
      Sharpe** as the acceptance gate; preserves the locked §4 structure (sum=100, LD-1 Track-B
      `foreign_flow` pinned 0); proposes only, `improved` iff OOS positive AND non-degrading.
- [x] **No live weight hand-editing** (`ml/weights_store.py`): a provenance-tracked surface with
      **no raw setter** — the only mutation is `apply_proposal`, which re-checks admission and
      refuses any non-improving proposal. `compute_sms(weights=…)` seam lets the optimizer score
      candidates without ever mutating `config.SMS_WEIGHTS`.
- [x] **ML ranker** (`ml/ranker.py`, §9 AI Buy/Sell): transparent linear ranker over engineered
      features, **doubly gated** — LD-8 admission to run + RULE B display gate (score/position
      `•••` until the `ai_ranking` module is VALIDATED). Ordering is observation, number is claim.
- [x] **View** (`ui/ml_view.py` + Streamlit "⚙ ML Layer 🔒" pane): surfaces the LD-8 gate + any
      applied weight provenance; operational diagnostics only, never a per-name predictive number.
- [x] **Tests (20 new, 287 total):** admission closed→open transition; purge/embargo/forward-only
      CV hand-checked + insufficient-samples raise; features == engineered components; optimizer
      requires admission, climbs the paying component, preserves the simplex + locked zeros, never
      proposes a degrading change; weight store has no hand-edit path + re-gated apply + never
      degrades; ranker requires admission + RULE B withhold→reveal; ML-view locked/open banner.
- **Standing deferral (unchanged from slice 8):** the LD-8 gate opens only on a **real
  multi-month forward-paper run** (needs the live DAL transport + accrued calendar time). Until
  then the harness stays closed by design — production modules are OBSERVATION_ONLY and the ML
  layer is LOCKED. Wiring the optimizer's `evaluate` to a full backtest-under-candidate-weights
  over the live store is the one integration that lands with that run (the seam exists).

## Slice 10 — Live DAL transport (closes the standing deferral)  ✅
**Goal:** wire the real network + own-session Bearer that every prior slice deferred. Not a
new spec §11 slice (the build order ends at 9) — it turns the transport-injected `ExodusClient`
into a production client so a *real* forward-paper run becomes possible (the one thing code
alone couldn't do). No locked behavior changes; no spec bump.
- [x] **Keychain token store** (`dal/token_store.py`): macOS `security` CLI via subprocess (zero
      new dep, stdlib core like slice 9). `get`/`set`/`clear`; strips a pasted `Bearer ` prefix;
      missing → `None` (never a blank header); refuses empty; injectable `runner` for tests.
- [x] **httpx transport** (`dal/transport.py`): `HttpxTransport.get/post` bind onto the client's
      injected `Transport`/`PostTransport`. Returns the raw `Response` (client maps status codes),
      raises `TransportError` on network failure (backoff engages) and `AuthError` rather than
      send a blank `Authorization`. Token read **fresh per request** so a refresh takes effect
      without rebuilding. Base URL from `config.EXODUS_BASE_URL`; injectable `AsyncClient`.
- [x] **Session factory** (`dal/session.py`): `build_live_client(store, prompt, client)` — the one
      production construction site: wires `token_provider` (Keychain) + `refresh` (optional
      re-paste on 401) + transport/post_transport. `session_status` gives a masked, no-network
      health check. Without a `prompt`, a 401 fails loud immediately (re-capture required).
- [x] **Operator CLI** (`dal/login.py`): `paste` (hidden `getpass` → Keychain), `status` (masked),
      `check` (live ping proves the token authenticates), `clear`. The 'view' of this vertical.
- [x] **Tests (14 new, 301 total):** store round-trip + Bearer-strip + empty-refusal + failure;
      masked status; transport injects Bearer/base-URL/params + JSON body, reads token fresh,
      fails loud on missing token, maps network errors, passes HTTP status through for the client;
      factory end-to-end through `ExodusClient` (auth+parse, 401-fail-loud, 401→prompt→refresh→ok).
- **Operator action (out of code):** capture the Bearer from your own authenticated Stockbit
  session and `python -m currentflow.dal.login paste`. Then a real multi-month forward-paper run
  can accrue — the event that promotes modules past `OBSERVATION_ONLY` (RULE B) and opens the
  LD-8 ML gate. The harness for all of that already exists (slices 8–9).

## Slice 11 — In-app username/password + MFA login flow  ✅  (spec v1.2, §9.1)
**Built as a harness against the verified §4.1 contract** (injected-transport tests, no live
network — the slice-8/9/10 posture). Two §4.1 open items are genuinely live-gated and stay
deferred to an operator probe, NOT guessed in code: (a) reCAPTCHA-v3 server *enforcement* —
`login_username` carries `recaptcha_token` as a pass-through param (empty = the pure-Python
attempt; paste an operator-minted token if the probe shows enforcement); (b) the refresh route —
`AuthClient.refresh` + the `build_session_refresh` seam **fail loud** (→ re-login) until
`config.AUTH_REFRESH_PATH` is pinned from a real capture. The Bearer **paste** stays as the
fallback (`./run.sh paste`). 23 new tests (324 total).

**Goal:** sign in with **credentials**, not a hand-pasted Bearer. `./run.sh` → browse always lands
honestly: the login form when there's no valid session, the terminal when authed. This is the
credential login the slice-1 deferral (`login/v6` + MFA) always pointed at; the transport,
Keychain store, and session factory from slice 10 are the substrate. Engine untouched — auth
plumbing only (spec v1.2 bump for the §9.1/§10/§15 posture change; no LD/weight/gate change).

**Wire contract: verified** from `login-stockbit.har` (2026-07-03), pinned in `DATA_SOURCES.md §4.1`.
The 5-step flow (all `POST … application/json` to `exodus.stockbit.com`): `login/v6/username` →
`mfa/verification/v1/challenge/{start, otp/send, otp/verify}` (verify **loops** on `next_challenge`
until `CHALLENGE_FINISH`) → `login/v6/new-device/verify` → `{access, refresh}` tokens.

> **① FIRST STEP — probe reCAPTCHA enforcement (decides the whole approach).** `login/v6/username`
> carries a `recaptcha_token` (reCAPTCHA **v3** — *invisible*, silently browser-minted; the operator
> only ever sees the OTP steps, never a challenge). The question is **not UX, it's server
> enforcement.** Probe (cheap): send `login/v6/username` with the token **omitted / empty / junk**
> and observe.
>   - **Not enforced** → pure-Python login works; recaptcha is a non-issue; skip the fork below.
>   - **Enforced** → then, and only then, pick one: (a) headless browser (Playwright) to run
>     `grecaptcha.execute(...)` — heavy dep vs. stdlib-core, fragile vs. bot scoring; (b) operator-
>     assisted token paste (~2 min TTL); (c) keep the slice-10 Bearer paste as the real auth path
>     (honest fallback). Pin the chosen path into §9.1 before coding `dal/auth.login`.
> Probe `player_id` (OneSignal UUID) in the same request — required / arbitrary-UUID / omittable is
> **unconfirmed**. Also **unconfirmed:** the refresh-endpoint route/shape (not exercised in the HAR —
> capture one). **Do not guess these in code.**

**DAL / session (the new plumbing):**
- [x] **`dal/auth.py`** — auth client over the exodus auth endpoints (own `HttpxTransport`, no
      Bearer yet), matching §4.1 exactly:
      `login_username(user, password, recaptcha_token, player_id)` → `{login_token, verification_token}`
      (new-device branch) or direct session (trusted-device — **unconfirmed**, guard for it);
      `challenge_start(verification_token)` → `next_challenge` + channels;
      `otp_send(verification_token, channel)`; `otp_verify(verification_token, otp)` → `next_challenge`
      (**caller loops** send→verify until `CHALLENGE_FINISH`);
      `new_device_verify(login_token)` → `{access:{token,expired_at}, refresh:{token,expired_at}, user}`;
      `refresh(refresh_token)` → new access (**route TBC — leave unimplemented/raising until captured**).
      Maps bad creds / failed OTP → `AuthError`, network → `TransportError`; **never logs** password,
      OTP, recaptcha, or token bodies.
- [x] **Extend the token store** (`dal/token_store.py`) to hold **access + refresh (+ expiries)** in
      the Keychain (one JSON blob) — `get_access`/`get_refresh`/`set_session`/`clear`; missing →
      `None`, never a blank header (unchanged contract).
- [x] **Wire `build_live_client`'s `refresh` seam** (`dal/session.py`) to `dal/auth.refresh` using
      the stored refresh token, so a `401` triggers a real token refresh (not a re-paste); on
      refresh failure it fails loud (→ UI returns to the login form). *(Depends on the refresh route.)*
- [x] **CLI** (`dal/login.py`): add a **`login`** subcommand (prompt username + hidden password,
      then drive the OTP challenge loop interactively → store session); keep `status`/`check`/`clear`;
      `paste` stays as the out-of-band fallback (§10 note / reCAPTCHA option 3).

**UI (the view):**
- [x] **`run.sh serve`**: drop the fail-loud token precondition (`run.sh:62-65`) so the server
      always starts; keep `login`/`check`/`test`. (May still `log` a hint, never block launch.)
- [x] **Auth gate in `ui/app.py`**: on load read session status (Keychain, no network); no valid
      session → render the **login flow instead of the modules** (fail loud, never blank/stale).
- [x] **Login view** (`ui/login_view.py`) — a small **state machine** matching the flow:
      `CREDENTIALS` (user + password[+recaptcha per the decision]) → `OTP` (channel picker + code
      entry, resend honoring `next_attempt_in`, **repeats** while `next_challenge==CHALLENGE_OTP`)
      → `FINISH` (`new_device_verify` → `store.set_session(...)` → rerun into terminal). On
      `AuthError` show an in-browser error and **store nothing**. Credentials/OTP held transiently
      in the run only — never persisted, rendered back, or logged (§9.1 posture).
- [x] **Top-bar session control**: masked account/session status (username + masked token) +
      **sign-out** → `store.clear()` + rerun back to the login form.
- [x] **Mid-session 401**: DAL 401 → attempt refresh (session seam above); refresh fail → back to
      the login form. Never a silent stale/empty fallback.
- [x] **Scope guardrail:** auth only — establishes the operator's *own* session; does not gate or
      alter any signal, number, or RULE A/B behavior; gated modules stay server-authoritative via
      the ledger.

**Tests** (injected transport — no live network, mirrors the slice-10 transport tests; use the
§4.1 recorded response shapes as fixtures):
- [x] `dal/auth`: username→new-device `{login_token, verification_token}`; challenge start→channels;
      **multi-round** otp verify loop (`CHALLENGE_OTP`→`CHALLENGE_OTP`→`CHALLENGE_FINISH`);
      new-device verify→`{access, refresh}`; bad creds / failed OTP→`AuthError`; network→`TransportError`;
      **assert password/OTP/recaptcha/token bodies never appear in logs**.
- [x] token store: access+refresh(+expiry) round-trip, clear, missing→None.
- [x] session factory: `401 → refresh → retry ok`, and `401 → refresh fail → AuthError` (fail loud)
      — *once the refresh route is confirmed; until then test the fail-loud-and-relogin path.*
- [x] login view-model (pure, Streamlit runtime not exercised): CREDENTIALS→OTP→FINISH transitions,
      OTP loop over two channels, rejected login stores nothing + surfaces error, sign-out clears →
      login; credentials/token never appear in rendered output.

- **Decisions-log entry** to add to `PROGRESS.md` when this lands: "in-app username/password + MFA
  login flow (verified `login/v6` + `mfa/verification/v1` contract, §4.1) replaces Bearer-paste as
  the primary auth surface; access+refresh in Keychain, credentials transient; **spec bumped
  v1.1 → v1.2** (§9.1/§10/§15), engine unchanged. reCAPTCHA-v3 / refresh-route resolution: <fill in>."

## Slice 12 — Automated per-feed ingestion scheduler  ✅
**Built as infra, not a new spec §11 slice** (the build order ends at 9; this is the slice-10
posture). It replaces the manual `run.sh ingest` / empty-store bootstrap with a scheduler that
fires each feed on its **own cadence** during Mon–Fri trading hours and writes to the DuckDB
cache. **No locked behavior changes; no spec bump.** The scheduler *writes cache only* — it never
scores, never touches RULE A/B, and `as_of` stamping is unchanged, so look-ahead safety is
untouched. The calc engine keeps reading only from the cache (already true). Ingest-once still
holds: a restart, a holiday, or a double-tick is a cheap no-op, never a re-pull.

> **Shipped 2026-07-11 (`currentflow/scheduler/`; 19 new tests, 490 total).** Two faithful
> deltas from the plan below, both forced by what actually has a persistence sink:
> **(1) `broker_summary`+`ohlcv_foreign` collapse into one `eod_ingest` feed** — `ingest_symbol`
> fetches both atomically (broker per day, bars written last as the ingest-once commit marker),
> so scheduling them separately would double-drive `ingest_universe`.
> **(2) 5 of the 8 planned feeds are wired; 3 are a documented deferral.** `corp_actions`,
> `special_board`, and `symbol_info`-status have **no store table and no cache consumer** today
> (`corp_actions` is an *injected* input to the RULE-A universe gate, not cached). Wiring them
> would either invent a table or change how a RULE-A gate input is sourced — outside this
> cache-only charter. They're named in `schedule.DEFERRED_FEEDS` (no silent caps); adding one is
> a one-line `FEED_SCHEDULES` entry + a dispatch action once its sink lands. The `Interval` /
> `ARMED_WATCHLIST` machinery for the deferred intraday status feed is built + tested regardless.

**Locked decisions (2026-07-11; all cadences are configurable, this is the default):** scope =
the **8 already-implemented feeds** (not the not-yet-built live overlays); mechanism = **launchd
agent** wrapping a standalone process (so it also runs as a bare daemon or in-process); EOD feeds
fetch the **prior completed trading day at 09:00** (matches `BROKER_CONSERVATIVE_AVAILABLE_TIME`,
stays inside the window); intraday state flags poll **every 15 min** over ARMED + watchlist only.

**Constraints that shaped the cadences:** (a) EOD feeds publish ~16:15 (`OHLCV_AVAILABLE_TIME`),
*after* the 09:00–16:00 window, so fetching in-window gets the prior day — which is exactly the
look-ahead stamp already in force. (b) Per-symbol feeds are paywall-counted, so signal feeds stay
1×/day and jobs run **sequentially** (the shared backoff paces the endpoint — the `ingest_universe`
rule). (c) Intraday polling is only for live-only overlays / mutable state flags — never the EOD
signal feeds (that would break the EOD/look-ahead model, RULE A/B).

**The cadence surface — `scheduler/schedule.py` (the only thing you edit to retune):**
- [x] Declarative `FEED_SCHEDULES` table: `FeedSchedule(feed, cadence, scope)`. Cadence kinds:
      `DailyAt(at, prior_trading_day)`, `WeeklyAt(weekday, at)`, `Interval(minutes, session_only)`.
      Scope: `UNIVERSE` (latest cached screener survivors, `store.scr0_universe`), `ARMED_WATCHLIST`,
      or `NONE` (market-wide). Shipped table (✅ = wired; ⏸ = deferred, no cache sink — see the
      Shipped note above and `schedule.DEFERRED_FEEDS`):

  | Feed key | DAL method(s) → sink | Cadence | Scope | |
  |---|---|---|---|---|
  | `eod_ingest` | `broker_summary`+`ohlcv_foreign` → `ingest_universe` | `DailyAt(09:00, prior_day)` | UNIVERSE | ✅ |
  | `universe_screener` | `run_screener` → `run_scr0`/`scr0_eligible` | `DailyAt(09:05)` | NONE | ✅ |
  | `index_membership` | `symbol_info.indexes` → `refresh_membership`/`symbol_index` | `WeeklyAt(MON, 09:00)` | UNIVERSE | ✅ |
  | `ksei_ownership` | `ksei_ownership` → `write_ksei_ownership` | `WeeklyAt(MON, 09:00)` | UNIVERSE | ✅ |
  | `corp_actions` | (injected gate input; no cache table) | `DailyAt(09:00)` | UNIVERSE | ⏸ |
  | `special_board` | (no consumer/table yet) | `DailyAt(09:00)` | NONE | ⏸ |
  | `symbol_status` | `symbol_info` flags (no sink yet) | `Interval(15m, session)` | ARMED_WATCHLIST | ⏸ |

**Trading-hours gate — `scheduler/calendar.py`:**
- [x] `is_trading_time(now)` → Mon–Fri, `SCHEDULER_WINDOW_OPEN`..`SCHEDULER_WINDOW_CLOSE` (09:00–16:00
      WIB, new `config` constants, inclusive); weekends skipped. Applicability is a separate
      `applies_now(cadence, now)` gate (so a session-only interval respects the window while a
      DAILY/WEEKLY instant lands in it by construction). IDX holidays are a **known gap** — a fire
      on a holiday finds no new data (ingest-once no-op) and is logged; `holidays.txt` deferred.
- [x] `next_fire(cadence, last_run, now)` / `is_due` — pure due-math with an **injectable clock**
      (tests pin it); daily/weekly/interval decisions are deterministic. Ignores weekends/window
      (that's `applies_now`) so a weekend-scheduled instant simply waits, never double-fires.

**The loop — `scheduler/runner.py`:**
- [x] Ticks every `SCHEDULER_TICK_SECONDS` (default 60), asks each feed "due?" against durable
      run-state, runs due feeds **sequentially** through the **existing** ingest surface:
      `eod_ingest` (broker+OHLCV) → `ingest.pipeline.ingest_universe`; `index_membership` →
      `ingest.pipeline.refresh_membership`; `universe_screener` → `screeners.scr0.run_scr0`;
      `ksei_ownership` → a thin action over `client.ksei_ownership` + `store.write_ksei_ownership`.
- [x] Universe/watchlist ordering per day: **screener → cached universe → per-symbol feeds over it**
      (screener/market-wide feeds ordered first in `FEED_SCHEDULES`; the EOD feed uses the *prior*
      cached screener set — `as_of < now` — a one-day lag by design). ARMED_WATCHLIST resolves ARMED
      + WATCH names (for the deferred status feed). Empty universe → SKIPPED_EMPTY + logged, never
      an invented universe (missing ≠ zero, no silent caps).
- [x] **Fail loud on 401** — `AuthError` propagates out of the tick (no run recorded → the feed
      stays due) and halts the daemon (exit 1); never stale/empty. Non-auth feed errors (already
      retried in the client) are logged + recorded ERROR and advance the clock (retry next cadence,
      not hammered every tick; manual `./run.sh ingest` backfills the missed day).

**Durable run-state — `store/`:**
- [x] New `scheduler_runs(feed, last_fired_at, rows_written, outcome)` table (added to the DDL;
      no versioning — `CREATE TABLE IF NOT EXISTS`). `write_scheduler_run` + `read_scheduler_run_latest`
      (latest per feed drives due-ness); survives restart → no double-fire; doubles as the audit trail.

**Entry point + launchd (hands-off, survives reboot):**
- [x] `python -m currentflow.scheduler` — standalone async daemon (`--once` = single tick, `--db`);
      new `run.sh schedule` (session-checked, mirrors `ingest`) reusing the slice-10/11 live session
      factory for auth.
- [x] `deploy/com.currentflow.scheduler.plist` LaunchAgent template (`RunAtLoad` + `KeepAlive`,
      stdout/err → `logs/`) + `launchctl load` install note (`__REPO_ROOT__` placeholder).

**Tests (TDD — mirrors the slice-1 look-ahead/ingest-once discipline; 19 new):**
- [x] `next_fire` due-math with a pinned clock (daily / weekly / interval).
- [x] trading-hours gate rejects weekends + outside-window; honors `session_only` intervals.
- [x] EOD feed fetches the **prior** completed trading day at 09:00.
- [x] durable state: a feed already fired today is skipped after a restart (no double-fire).
- [x] ingest-once invariant under the scheduler: a second fire makes **zero** network calls
      (the `tests/test_pipeline.py` `calls == []` pattern).
- [x] a 401 during a scheduled fire **fails loud** (never silently skips a feed; nothing recorded).
- [x] + scope resolution, universe-refresh round-trip, empty-universe skip, every-feed-has-an-action,
      deferred-feeds-documented-and-unscheduled, and the `run_loop` bounded-run / auth-halt paths.

**Deferred (out of this build):** Tier-1 live overlays (`orderbook`, `running-trade`), the regime
gate, and `fundamentals_live` — their DAL methods aren't built yet; the `FEED_SCHEDULES` table has
room and adding one is a one-line entry once the client method lands. Also deferred: the IDX
`holidays.txt` calendar; and moving the EOD fire to **post-close** once `BROKER_PUBLISH_LATENCY`
is empirically pinned (LD-5) — one cadence-entry edit, no code change.

- **Decisions-log entry (landed 2026-07-11, logged in `PROGRESS.md`):** automated per-feed ingestion
  scheduler (`currentflow/scheduler/`) replaces manual `run.sh ingest`; declarative `FEED_SCHEDULES`
  cadence table, Mon–Fri 09:00–16:00 WIB gate, EOD-at-open prior-day; launchd-driven; writes cache
  only (RULE A/B + `as_of` untouched), ingest-once preserved; **5 of 8 feeds wired, 3 deferred for
  lack of a cache sink** (`corp_actions`/`special_board`/`symbol_status`); no spec bump (infra,
  slice-10 posture).

---

## Slice 14 — v2 UI restructure: Signal Pipeline home + evidence tabs  ✅  (2026-07-13)

Rebuilt the terminal shell to the **VectorLab v2** design handoff (`design/HANDOFF_v2.md`).

- **Nav rail removed.** The left module rail is gone; the main column starts flush at the left edge
  (`shell.shell_css` hides the sidebar). **Signal Pipeline** is the sole top-level view.
- **Signal Pipeline** (`ui/pipeline_view.py` + `shell.pipeline_*`): Track A / Track B lanes; each
  candidate row shows all four locked stages (`gate → phase → sig → veto`) + a verdict, wired to
  **real `engine.evaluate()`** output (not mock). RULE A gate visible before the signal cell; RULE B
  holds — the signal cell shows a categorical `pass`/`low` + component observations, never the SMS
  number (tested: no `internal_score` leaks).
- **Evidence tabs:** clicking a pipeline row (or an ARMED-rail card) opens that name's Broker Flow /
  Foreign Flow / Accum. Detect / Money Replay as tabs with a contextual "Why {TICKER} …" header and a
  `‹ Pipeline` back button (the four existing renderers reused, `show_header=False`).
- **Seven modules retained, unlinked** (Smart Heatmap, Sector Rotate, Risk Monitor, SMS/Rank, AI
  Ranking, Daily Top, ML): code + tests kept; no longer top-level (§9 nav tension logged in PROGRESS).
- **Tests:** `tests/test_pipeline_view.py` (7, TDD: stage mapping, lane grouping, gate-fail/phase-fail
  skip, RULE B) + `tests/test_app_pipeline.py` (3, AppTest: pipeline home renders, row-click routing,
  back/tab). Full suite green (497). **No `LOCKED_SPEC.md` bump** — presentation reorganization only.

### Phase 2 — pipeline plumbing still to resolve (deferred, not orphaned)

The v2 pipeline currently emits three verdicts (`ARMED`/`WATCH`/`REJECTED`) and a gate cell covering
the §3 **liquidity-floor + track** leg. Two pieces are designed-for but **not yet wired** — resolve
these before the pipeline is considered complete:

- [ ] **EXITED verdict + `⤶` reversed-stage cell + realized P&L.** The design's fourth verdict — a
  position that cleared the pipeline, was entered, then sold on a broken thesis. This data lives in
  the **portfolio paper-trader** (`validation/portfolio_runner.py` closed positions: `SIGNAL_DECAY` /
  thesis-break exits + net-of-fee realized P&L). Wire those closed positions into `pipeline_view`
  (`rev` cell state + `EXITED` result + `exitPnl`). The view-model, shell (`_STAGE_STYLE['rev']`,
  `_RESULT_STYLE` slot) and `_count_str` already have the hooks; `ui/app.py:_candidate` carries the
  NOTE marking the seam.
- [ ] **Full §3 Universe Gate in the Gate cell.** Today the gate cell derives the ADV-floor + track
  leg from bars; the remaining §3 checks (history/IPO, data-gap, corp-action window, ARA/ARB bands
  via `universe.gate.evaluate_gate`) are not run in the live app path. Add a store→`evaluate_gate`
  assembly helper (SymbolInfo / corp_actions / board / coverage) and feed the real `GateDecision`
  into the gate cell so all §3 rejections surface, not just the floor.

## Acceptance criteria (definition of done — `LOCKED_SPEC.md` §13)

- [x] Look-ahead test passes (no `availability_ts >= decision_ts`).
- [x] Phase gate rejects all non-C/D candidates (unit-tested on labeled charts).
- [x] No unvalidated module displays a number (RULE B test — all 3 gated modules, slice 8).
- [x] Per-module validation state drives the observation↔claim UI switch (`ValidationLedger`, slice 8).
- [x] Every order is a limit order with a defined stop and R:R ≥ 2:1.
- [x] Fill engine reproduces lot/tick/ARA-ARB/fee math against hand-checked cases.
- [x] Backtest and forward-paper share one fill engine; results reconcile (slice 8).
- [x] Reported return is net of full fee stack, benchmarked to LQ45/sector (not IHSG — metrics guard, slice 8).
- [x] Money Flow Replay reconstructs any past signal from stored `as_of` data.
- [x] All data stays local; nothing republished.
- [x] No live hand-editing of SMS weights; tuning only via walk-forward optimizer.

> Code/test-complete for v1.1. The one thing code cannot satisfy on its own — a **real
> multi-month forward-paper run** that actually promotes a module (needs the live DAL
> transport + accrued calendar time) — is the standing deferral; the harness is built and
> tested, and modules correctly stay OBSERVATION_ONLY until such a run clears them.
