# Data Sources — Stockbit `exodus` API mapping (appendix to LOCKED_SPEC v1.1)

**Status:** LOCKED companion to `LOCKED_SPEC.md`. Pins the primary data source and maps every spec §10 feed to a concrete endpoint. Derived from a HAR capture of an authenticated Stockbit Pro web session (2026-07-01).

**Posture (spec §10 / LD-10):** consumed from the operator's **own authenticated session**. Local-only, never redistributed. Likely violates Stockbit ToS — personal use, own risk. Parser/endpoint breakage is expected maintenance.

**Primary source decision:** **Stockbit `exodus` is the primary DAL source** — it alone covers the core (broker summary w/ accumulator VWAP + history to 2019) and the fundamentals (Greenblatt metrics served). Invezgo / Sectors.app / official IDX are demoted to **fallback only**. All endpoints below are `https://exodus.stockbit.com/…`, Bearer-auth.

---

## 1. Feed → endpoint map

| Spec feed (§10) | Endpoint | Key fields | Notes |
|---|---|---|---|
| **Broker summary (CORE)** | `marketdetectors/{sym}?from=&to=&transaction_type=TRANSACTION_TYPE_NET&market_board=MARKET_BOARD_REGULER&investor_type=INVESTOR_TYPE_ALL` | `broker_summary.brokers_buy[]/brokers_sell[]`: `netbs_broker_code`, `netbs_buy_avg_price` (**accumulator VWAP**), `bval`/`sval`, `blot`/`slot`, `freq`, `type` (Asing/Lokal/Pemerintah), `netbs_date`; `bandar_detector` (acc/dist for top1/3/5/10, `broker_accdist`, `number_broker_buysell`); `data_last_updated` | `from`/`to` → **history to 2019**. `type` gives foreign/domestic per broker. `period=BROKER_SUMMARY_PERIOD_LATEST` for latest. **Behind paywall counter.** |
| Broker summary (market-wide) | `order-trade/broker/top?period=&market_type=&eod_only=` | per-broker `net_value`, `buy_value`, `sell_value`, `total_volume`, `total_frequency`, `group` (FOREIGN/LOCAL/GOVERNMENT) | Market-level top brokers; broker-DNA reference. |
| Broker distribution (per stock) | `order-trade/broker/distribution?date=&symbol=&data_type=BROKER_…` | `by_value`/`by_volume` → `top_broker_buy/sell` | Takes explicit `date=`. |
| Broker activity (per broker) | `order-trade/broker/activity` , `…/activity-chart?period=&brokers_code=&investor_type=&market_board=` | per-minute net value by symbol for a broker code | Enables **syndicate / broker-DNA** tracking (§9). Intraday resolution. |
| **OHLCV + foreign flow** | `company-price-feed/historical/summary/{sym}?period=HS_PERIOD_DAILY&start_date=&end_date=&limit=&page=` | per day: `open/high/low/close`, `volume`, `value`, `frequency`, `average` (VWAP), `foreign_buy`, `foreign_sell`, `net_foreign`, `change_percentage` | **Best single EOD feed** — OHLCV + foreign + VWAP in one call, date-ranged + paginated. |
| Foreign/domestic series | `findata-view/foreign-domestic/v1/chart-data/{sym}` | foreign vs domestic time series | Dedicated; redundant with above. |
| Intraday price line | `charts/{sym}/daily?timeframe=today` | per-minute `value` (no OHLCV) | For **Money Flow Replay** intraday overlay only, not EOD signal. |
| **Corporate actions** | `corpaction/{sym}` and `corpaction/{dividend,stocksplit,rightissue,reversesplit,bonus,warrant,tenderoffer,ipo,rups,pubex,economic}` | action type, dates, ratios | Drives ±5-day exclusion window (§3) + level adjustment. |
| **Suspend / halt / UMA / notation** | `emitten/{sym}/info` | `status`, `market_hour.suspend_info`, `notation[]`, `tradeable`; orderbook adds `uma`, `corp_action.active` | Universe-gate state flags (§3). |
| **Track A/B assignment** | `emitten/{sym}/info` → `indexes[]`; server-side scoping via `screener/universe` scopeIDs | membership: `LQ45`, `IDX80`, `IDX30`, `KOMPAS100`, `IDXSMC-LIQ`… | **Direct** — no derivation. Track A = LQ45 (scopeID `550`) / IDX80 (`1000003288`); Track B universe = IDXSMC-LIQ (`1000003583`). See `screeners.md` §1. |
| Board type (dev/special) | `emitten/indexes/special-board` | special/development board membership | Feeds ARA/ARB band selection (§ derivation 2). |
| **Free float / shares out** | `comparison/{sym}/ratios` or `keystats/ratio/v1/{sym}` (values by `fitem_id`); `fundachart/metrics` (metric catalog) | Free Float `21535`, Free Float Mkt Cap `21543`, Shares Out `2899` | **Served fields** — no derivation. |
| Shareholding composition (KSEI) | `insider/shareholding/composition/companies/{sym}` ; `emitten-metadata/shareholders/{sym}/chart?value_year=&shareholder_type=` | `total_shares`, composition by holder class; monthly **Local vs Foreign %** series | KSEI monthly, ~lagged. Feeds foreign-ownership-trend overlay (§9). |
| Major-holder moves | `insider/company/majorholder?date_start=&date_end=` | holder buy/sell, `nationality`, `action_type`, price | Insider flow. |
| **Fundamentals (Magic Formula)** | `comparison/{sym}/ratios` , `keystats/ratio/v1/{sym}` | see §2 fitem table | Clean JSON. **Greenblatt metrics served** — see §2. |
| Financial statements (historical) | `findata-view/company/financial?symbol=&data_type=&report_type=&statement_type=` | **HTML** table, ~73 quarterly periods since 2008 | Only source for **point-in-time / backtest** fundamentals. HTML parse required (§ derivation 1). |
| **Order-book depth** | `company-price-feed/v2/orderbook/companies/{sym}` | `bid[]`, `offer[]`, `fnet`/`fbuy`/`fsell`, `notation`, `uma`, `corp_action` | **Live only, not stored historically** → absorption is forward-only. |
| Running trade / big money | `order-trade/running-trade` , `order-trade/trade-book[/chart]` | `big_money_net_values`, `big_money_buy/sell`, tick prints | Intraday whale/block signal. Not historical. |
| **Regime gate** | `charts/{USDIDR,COPPER,NIKKEI,SP500,BTC}/daily` ; IHSG via `…/orderbook/companies/IHSG` | daily series; IHSG `fnet`, breadth (`up`/`down`/`unchanged`) | All regime inputs (§ spec regime gate) present. |
| Screener pre-filter | `screener/{templates,templates/{id},universe,metric,preset,favorites}` | saved templates incl. `bandar-accumulating`, `foreign-flow-3m`, `earnings-yield` | Can offload Stage 0/1 to Stockbit's own screener. |
| Market session / timing | `company-price-feed/market-time` , `…/market-time/session` | session state, break timers | Scheduler alignment (§2). |

---

## 2. Fundamental `fitem_id` reference (Magic Formula, §7)

Values come from `comparison/{sym}/ratios` (`data_value[].fitem_id → value`) or `keystats/ratio/v1/{sym}`.

| fitem_id | Name | Use |
|---|---|---|
| **13411** | **ROC Greenblatt** | **ROC directly — spec §7 (no derivation)** |
| **2897** | EV to EBIT (TTM) | **EY (Greenblatt) = 1 / 2897**; or EBIT = `2895 / 2897` |
| **13424** | Rank (Earnings Yield) | Pre-computed Greenblatt EY rank |
| 13423 | Rank (Market Cap) | — |
| 2895 | Enterprise Value | EV |
| 2892 | Market Cap | — |
| 21456 | EBITDA (TTM) | — |
| 21457 | EV to EBITDA (TTM) | — |
| 2898 | Earnings Yield (TTM) | ⚠️ **net-income/price, NOT Greenblatt** — do not use as EBIT/EV |
| 13447 | Return On Invested Capital (TTM) | cross-check |
| 1462 | Return on Capital Employed (TTM) | cross-check |
| 1461 | Return on Equity (TTM) | **bank/FLOW_ONLY sector proxy (§7)** — threshold ROE > 12% |
| 1460 | Return on Assets (TTM) | — |
| 21535 / 21543 | Free Float / Free Float Mkt Cap | universe + float-rotation |
| 2899 | Current Share Outstanding | float-rotation denominator |
| 1486 / 1488 | Total Debt / Net Debt (Q) | EV cross-check |
| 1557 / 1518 | Cash (Q) / Working Capital (Q) | ROC denom cross-check |
| 3076 / 3098 / 3091 | Total Current Assets / Current Liabilities / Non-Current Assets | NWC + NFA build (backtest) |
| 3063 / 2997 | Net Income (TTM) / Revenue (TTM) | — |

**Magic Formula (§7) is served for live scoring:** rank the universe on `EY = 1/2897` and `ROC = 13411`; combined rank → COMPOUNDER/NEUTRAL/SPECULATIVE tercile. No statement parsing needed for the *current* snapshot.

---

## 3. Derivation recipes (the remaining data gaps)

### 3.1 Historical / point-in-time fundamentals (backtest look-ahead) — REAL GAP
The `ratios`/`keystats` endpoints return **current TTM snapshot only**. A look-ahead-safe backtest needs fundamentals *as they were known* at each historical decision date.
- **Recipe:** parse `findata-view/company/financial` HTML (73 quarterly periods since 2008) → line items → recompute EBIT / EV / NWC+NFA per period. **Apply a reporting-publication lag** (only mark a quarter's financials available ~N days after period end, per actual IDX filing dates) so backtest fundamentals respect `availability_ts < decision_ts` (spec §1).
- Live scoring uses the clean JSON (§2); backtest uses parsed historical statements. Keep them as separate code paths feeding the same feature store.

### 3.2 ARA/ARB band state (§3, §12) — derive
No served auto-reject flag.
- **Recipe:** board type from `emitten/indexes/special-board` + previous close → band % (main ±7% / dev ±10–25% / first-15d-IPO ±35%, per spec §12) → `pinned = abs(last − prev)/prev ≥ band − ε`. Reject if closed pinned (no fillable band).

### 3.3 Free-float % — served, but validate
`Free Float` (21535) is served directly. Cross-check against `insider/shareholding/composition` (public float = total_shares − controlling/strategic holders) when 21535 looks stale.

### 3.4 Absorption / order-book depth (§9) — forward-only
Order book is **live only**; Stockbit does not serve historical L2. Absorption detection runs forward from go-live; **it cannot be backtested**. Spec §9 already allows graceful degradation — flag absorption signals as "live-only, not in historical validation set."

---

## 4. Operational constraints (must-handle in the DAL)

| Risk | Detail | Mitigation |
|---|---|---|
| **Paywall counters** | `paywall/eligibility/check`, `paywall/counter/increment` gate `marketdetectors` + broker history behind Pro with **usage counters**. Backtest = ~150 names × 2yr daily broker summary = tens of thousands of calls. | Throttle; **ingest once, cache to DuckDB keyed `(symbol, date, as_of)`; never re-pull a stored datum.** Nightly incremental only. |
| **Auth token lifecycle** | Bearer token from logged-in session (`login/v6` + MFA). Access ~24h, refresh ~7d (see §4.1). Expires. | DAL needs a token-refresh / re-capture path; fail loud on 401, never silently emit stale/empty. |
| **Look-ahead timing** | `netbs_date` / `data_last_updated` let you stamp `as_of`, but HAR can't reveal **when EOD broker summary actually publishes** vs next-session open. | **Measure publish latency empirically** before trusting any same-day-broker signal. Scheduler fires on observed publication, not clock (spec LD-5). |
| **Empty ≠ zero** | Illiquid names return all-zero rows (observed on XBIG). | Integrity check: distinguish "no trades" from "not yet published" from "gap"; never read a gap as zero flow (spec §2). |
| **HTML fragility** | Financial statements are rendered HTML. | Isolate the parser; treat breakage as routine maintenance; snapshot raw HTML so re-parsing is possible without re-fetch. |
| **ToS** | Own-session scraping. | Local-only, no redistribution, personal use (spec §10 / §15). |

### 4.1 Login flow — verified wire contract (`login-stockbit.har`, 2026-07-03)

Pinned from a real own-session capture. All `POST … application/json` against `https://exodus.stockbit.com`.
Values below are **field shapes only** — secrets (password, OTP, tokens, recaptcha) are never reproduced here.
The final `access.token` is the `Authorization: Bearer …` the rest of the DAL already uses (JWT, RS256).

**1 · `POST /login/v6/username`**
- req: `{ user, password, recaptcha_token, recaptcha_version: "RECAPTCHA_VERSION_3", player_id }`
- resp (new-device / MFA branch, observed): `data.new_device.multi_factor.{ login_token, verification_token }` (both 36-char).
  *(Trusted-device path — where this call returns `access`/`refresh` directly with no MFA — was **not** captured; HAR-to-confirm.)*

**2 · `POST /mfa/verification/v1/challenge/start`**
- req: `{ verification_token }`
- resp: `data.next_challenge` (e.g. `CHALLENGE_OTP`), `data.supporting_data.otp.{ channels:[{channel,target}], default_channel }`. Channels seen: `CHANNEL_EMAIL`, `CHANNEL_WHATSAPP`, `CHANNEL_SMS` (target masked, e.g. `tom****@gmail.com`).

**3 · `POST /mfa/verification/v1/challenge/otp/send`**
- req: `{ verification_token, channel }`
- resp: `data.{ channel, target(masked), next_attempt_in: 60 }` (resend cooldown, seconds).

**4 · `POST /mfa/verification/v1/challenge/otp/verify`**  ← **loops**
- req: `{ verification_token, otp }`  (6-digit)
- resp: `data.next_challenge`. **This is a loop**: a verify can return another `CHALLENGE_OTP` with a *new* channel set (the capture required two rounds — email, then WhatsApp/SMS) before finally returning `CHALLENGE_FINISH`. Drive: repeat send→verify until `next_challenge == CHALLENGE_FINISH`.

**5 · `POST /login/v6/new-device/verify`**  (only after `CHALLENGE_FINISH`)
- req: `{ multi_factor: { login_token } }`  (the `login_token` from step 1)
- resp: `data.access.{ token, expired_at }`, `data.refresh.{ token, expired_at }`, plus `data.user.{ id, username, email, exchange, privilege, … }`. Observed lifetimes: **access ≈ 24h, refresh ≈ 7d** (ISO-8601 `expired_at`).

**Resolved — `recaptcha_token` is ENFORCED (probe closed 2026-07-03):**
- reCAPTCHA **v3** (`recaptcha_version: RECAPTCHA_VERSION_3`), a ~2148-char token the browser generates *silently* via `grecaptcha.execute(siteKey, {action})`. v3 is **invisible** — no challenge is shown — but the token is required: a `login/v6/username` posted with an empty token is rejected **`400 "Permintaan tidak valid"`** (confirmed by comparing a failed empty-token attempt against a successful capture that carried a full token). A pure-Python login is therefore **not** possible.
- **Site key** (public, client-side by design): `6LeBXZYqAAAAAIAqBYdAV5HuBc6i0YeVziSYrXAZ`. Pinned in `config.AUTH_RECAPTCHA_SITE_KEY`; action `"login"` (advisory — affects v3 score analytics, not validity).
- **Chosen fork = operator-assisted token** (not a headless browser — that's a heavy dep against the stdlib-core posture). `dal/recaptcha.py` renders a DevTools-console snippet (`grecaptcha.execute(SITE_KEY,{action}).then(copy)`) that the operator runs on an open stockbit.com tab to mint a fresh token and paste it into the login prompt (CLI `login` and the Streamlit sign-in form). Token is **single-use, ~2 min TTL** — mint it right before submitting. The slice-10 Bearer `paste` remains as the fallback.
- The login views **refuse an empty token** before any request fires (`LoginController.submit_credentials` / CLI), so an enforced-reject 400 can't be triggered by omission.

**Still open (not resolved by this HAR — do not guess in code):**
- **`player_id`** — a OneSignal push id (UUID); the successful capture carried one (`dc98dc8f-…`). Whether it is *required* vs. accepts an arbitrary/empty UUID is still **unconfirmed** (reCAPTCHA was the decisive gate for the 400). Carried through verbatim; defaults empty.
- **Refresh endpoint** — access was valid for the whole capture, so the refresh route + request/response shape are **unconfirmed**. Capture a token-refresh exchange (or an expiry) before wiring `dal/auth.refresh`.

---

## 5. Coverage summary vs spec §10

| Feed | Status |
|---|---|
| Broker summary + accumulator VWAP + foreign tag + 2019 history | ✅ Full (paywall-throttled) |
| Daily OHLCV + foreign flow + VWAP | ✅ Full |
| Corporate actions / suspend / halt / UMA | ✅ Full |
| Track A/B (index membership) | ✅ Direct |
| Free float / shares outstanding | ✅ Served |
| Magic Formula (live: ROC Greenblatt + EV/EBIT served) | ✅ Live served |
| Magic Formula (backtest, point-in-time) | ⚠️ HTML parse + reporting lag (§3.1) |
| ARA/ARB band state | ⚠️ Derive (§3.2) |
| Order-book depth / absorption | ⚠️ Live-only, no historical (§3.4) |
| Regime gate (USDIDR, commodities, global, IHSG breadth) | ✅ Full |

**Net:** ~90% of the data layer is a direct endpoint pull; the three ⚠️ items are bounded derivations, not blockers. Stockbit `exodus` is sufficient as the sole primary source.

---

## 6. DAL adapter surface (spec §7/§11 step 1)

Thin async client over `exodus`; one method per feed; every write stamped with `as_of`, persisted to DuckDB, gap-checked.

```python
class ExodusClient:
    # auth: bearer token + refresh; paywall/rate-limit backoff; 401 -> fail loud
    async def broker_summary(sym, date_from, date_to) -> list[BrokerNet]   # marketdetectors/{sym}
    async def ohlcv_foreign(sym, date_from, date_to)  -> list[DailyBar]    # historical/summary/{sym}
    async def corp_actions(sym)                        -> list[CorpAction]  # corpaction/*
    async def status_flags(sym)                        -> StatusFlags       # emitten/{sym}/info
    async def index_membership(sym)                    -> list[str]         # emitten/{sym}/info.indexes
    async def fundamentals_live(sym)                   -> Ratios            # comparison/{sym}/ratios
    async def fundamentals_hist(sym)                   -> list[Statement]   # findata-view (HTML parse)
    async def float_shares(sym)                        -> FloatShares       # fundachart 21535/2899
    async def orderbook(sym)                           -> OrderBook         # v2/orderbook (live)
    async def regime()                                 -> RegimeSnapshot    # charts/{USDIDR,COPPER,...}
    # every returned record carries availability_ts; DAL enforces availability_ts < decision_ts
```

Build order maps 1:1 to spec §11: step 1 = `broker_summary` + `ohlcv_foreign` + integrity/gap checks; step 2 = universe gate (`index_membership`, `status_flags`) + Broker Flow Analyzer (off `broker_summary`); step 3 = Foreign Flow + Replay (off `ohlcv_foreign`).

---

*Source: HAR capture `stockbit.com_Archive [26-07-01 12-38-52].har`, authenticated Pro session. fitem_ids verified against `fundachart/metrics` + `keystats/ratio/v1`.*
