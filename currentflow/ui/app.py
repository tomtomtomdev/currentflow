"""CurrentFlow terminal — Streamlit prototype (spec §10; design/ handoff).

Run:  streamlit run currentflow/ui/app.py -- --db <path.duckdb>

Slice 2 shipped the Broker Flow Analyzer; slice 3 added the Foreign Flow Dashboard
and Money Flow Replay; slice 4 adds the Institutional Accumulation Detector, the
Smart Money Heatmap, and the SMS/Rank module (components only — RULE B withholds the
number). The remaining modules render as locked placeholders. RULE B is enforced by
construction: the SMS number, probabilities, and buy/sell verbs are never displayed
until the module is VALIDATED.
"""

from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from currentflow.logging_setup import configure_logging
from currentflow.signals import (
    accumulation,
    distribution,
    engine,
    foreign_flow,
    heatmap,
    replay,
    risk_monitor,
    sector_rotation,
)
from currentflow.signals.broker_flow import analyze, buyer_seller_matrix
from currentflow.signals.risk_monitor import Portfolio, Position
from currentflow.store.db import Store
from currentflow.ui.trap_view import ribbon_banner, ribbon_rows
from currentflow.ui.accumulation_view import accumulation_panel, stealth_callout
from currentflow.ui.broker_flow_view import (
    DISCLAIMER,
    OBSERVATION_BADGE,
    broker_table,
    concentration_panel,
    matrix_table,
)
from currentflow.ui.foreign_flow_view import (
    cumulative_series,
    daily_series,
    ksei_panel,
    reversal_callout,
    split_bar,
    stats_panel,
    tide_table,
)
from currentflow.ui.heatmap_view import divergence_alerts, heatmap_rows, sector_totals
from currentflow.ui.replay_view import playhead_panel, visible_rows
from currentflow.ui.risk_view import (
    FRAMING as RISK_FRAMING,
    crowding_rows,
    liquidity_rows,
    metric_cards,
    name_exposure_rows,
    scenario_rows,
    sector_exposure_rows,
)
from currentflow.ui.sector_view import scatter_points, sector_rows
from currentflow.ui.sms_view import GATE_BANNER, WATCHLIST_FRAMING, component_rows, score_display, state_label
from currentflow.ui import daily_top_view, ml_view, ranking_view
from currentflow.validation.promotion import ValidationLedger

MODULES = [
    "⇄ Broker Flow",
    "⌖ Foreign Flow",
    "◱ Accum. Detect",
    "⟲ Money Replay",
    "▦ Smart Heatmap",
    "✦ Sector Rotate",
    "◈ Risk Monitor",
    "∑ SMS / Rank 🔒",
    "◇ AI Ranking 🔒",
    "☰ Daily Top 🔒",
    "⚙ ML Layer 🔒",
]

# Operator sector map — ILLUSTRATIVE, seeded from the design handoff ticker reference
# (design/README.md). Like the broker-DNA registry, it is operator knowledge to verify
# and extend; unmapped symbols fall back to UNKNOWN (never silently grouped).
OPERATOR_SECTOR_MAP = {
    "BRMS": "Basic Materials", "NCKL": "Basic Materials", "MBMA": "Basic Materials",
    "PTRO": "Energy", "RAJA": "Energy", "CUAN": "Energy", "DEWA": "Energy",
}


def _db_path() -> str:
    args = sys.argv[1:]
    return args[args.index("--db") + 1] if "--db" in args else "currentflow.duckdb"


@st.cache_resource
def _store(path: str) -> Store:
    return Store(path)


@st.cache_resource
def _ledger() -> ValidationLedger:
    """Server-authoritative per-module validation state (RULE B). Seeded OBSERVATION_ONLY;
    only the paper-trade engine (`validation.promotion`) promotes a module — never the UI."""
    return ValidationLedger()


def _all_results(store: Store, track: str, decision_ts: datetime) -> list:
    """Evaluate every ingested name for the ranking / daily-top gated modules."""
    return [
        engine.evaluate(store, s, decision_ts, track=track)
        for s in _symbols(store, "daily_bar")
    ]


def _symbols(store: Store, table: str) -> list[str]:
    return [
        r[0]
        for r in store._con.execute(
            f'SELECT DISTINCT "symbol" FROM {table} ORDER BY "symbol"'
        ).fetchall()
    ]


def _trap_ribbon(store: Store, symbol: str, decision_ts: datetime) -> None:
    """Stage-2 trap/decay ribbon (slice 5) — wired into every symbol-scoped view.
    Surfaces §5 veto traps + §8 signal-decay flags as observation, most-severe first."""
    mon = distribution.monitor(store, symbol, decision_ts)
    banner = ribbon_banner(mon)
    if banner is None:
        st.caption(":green[✓ no trap or decay flags — clean]")
        return
    st.warning("Trap / decay — " + banner)
    with st.expander("All trap & decay flags (observation, not a recommendation)"):
        st.dataframe(pd.DataFrame(ribbon_rows(mon)), use_container_width=True)


def _render_broker_flow(store: Store) -> None:
    st.title("Broker Flow Analyzer")
    st.caption(f":green[{OBSERVATION_BADGE}] — here is the flow, you decide.")

    symbols = _symbols(store, "broker_net")
    if not symbols:
        st.warning("No broker data ingested yet — run the ingest pipeline first.")
        return

    symbol = st.sidebar.selectbox("Symbol", symbols)
    decision_ts = datetime.now()
    snap = analyze(store, symbol, decision_ts)
    _trap_ribbon(store, symbol, decision_ts)

    left, right = st.columns([1.35, 1])
    with left:
        st.subheader(f"{symbol} — broker net flow")
        st.caption(f"{snap.start} → {snap.end} · as visible at {decision_ts:%Y-%m-%d %H:%M}")
        st.dataframe(broker_table(snap), use_container_width=True)

    with right:
        st.subheader("Concentration")
        panel = concentration_panel(snap)
        if panel["top2_share_pct"] is not None:
            st.metric("Top-2 net-buy share", f"{panel['top2_share_pct']}%")
            st.progress(min(panel["top2_share_pct"] / 100, 1.0))
        if panel["hhi"] is not None:
            st.metric("Herfindahl (HHI)", f"{panel['hhi']:.2f}", panel["hhi_label"])
        if panel["top2_names"]:
            st.caption(f"Top-2 buyers: {panel['top2_names']}")

    st.subheader("Broker × Stock matrix")
    snaps = {s: analyze(store, s, decision_ts) for s in symbols}
    st.dataframe(
        matrix_table(buyer_seller_matrix(snaps), symbols), use_container_width=True
    )


def _render_foreign_flow(store: Store) -> None:
    st.title("Foreign Flow Dashboard")
    st.caption(f":green[{OBSERVATION_BADGE}] — foreign-inst lens; here is the flow, you decide.")

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No OHLCV/foreign data ingested yet — run the ingest pipeline first.")
        return

    symbol = st.sidebar.selectbox("Symbol", symbols)
    decision_ts = datetime.now()
    snap = foreign_flow.analyze(store, symbol, decision_ts)
    _trap_ribbon(store, symbol, decision_ts)

    callout = reversal_callout(snap)
    if callout:
        st.info(callout)

    left, right = st.columns([1.6, 1])
    with left:
        st.subheader(f"{symbol} — cumulative foreign net (NBSA)")
        cum = pd.DataFrame(cumulative_series(snap))
        if not cum.empty:
            st.area_chart(cum.set_index("date")["cumulative_bn"])
        st.subheader("Daily foreign net")
        daily = pd.DataFrame(daily_series(snap))
        if not daily.empty:
            st.bar_chart(daily.set_index("date")["net_foreign_bn"])

        split = split_bar(snap)
        if split["foreign_net_bn"] is not None:
            st.caption(
                f"Today: foreign net {split['foreign_net_bn']} bn · domestic net "
                f"{split['domestic_net_bn']} bn"
                + (
                    f" · foreign turnover share {split['foreign_turnover_share_pct']}%"
                    if split["foreign_turnover_share_pct"] is not None
                    else ""
                )
            )

    with right:
        st.subheader("Foreign flow stats")
        stats = stats_panel(snap)
        st.metric("Net today (IDR bn)", stats["net_today_bn"])
        st.metric("5-day cumulative (IDR bn)", stats["cum_5d_bn"])
        side = f" ({stats['persistence_side']})" if stats["persistence_side"] else ""
        st.metric("Persistence", stats["persistence"] + side)
        if stats["vs_20d_avg"] is not None:
            st.metric(
                f"vs {stats['avg_window_used']}d avg", f"{stats['vs_20d_avg']}×",
                f"z = {stats['zscore_20d']}" if stats["zscore_20d"] is not None else None,
            )

        st.subheader("KSEI ownership")
        ksei = ksei_panel(snap)
        if ksei["series"]:
            spark = pd.DataFrame(ksei["series"])
            st.line_chart(spark.set_index("month")["foreign_pct"])
            st.caption(
                f"Foreign own {ksei['foreign_own_pct']}%"
                + (f" · {ksei['trend']}" if ksei["trend"] else "")
            )
        else:
            st.caption("No KSEI ownership slices ingested yet.")
        if ksei["nbsa_pct_of_float"] is not None:
            st.metric("Window NBSA as % of float value", f"{ksei['nbsa_pct_of_float']}%")

    st.subheader("Market tide")
    tide = tide_table(
        foreign_flow.market_tide(store, symbols, decision_ts, day=snap.end)
    )
    if tide:
        st.dataframe(tide, use_container_width=True)


def _render_replay(store: Store) -> None:
    st.title("Money Flow Replay")
    st.caption(f":green[{OBSERVATION_BADGE}] — reconstructing from stored `as_of`.")

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return

    symbol = st.sidebar.selectbox("Symbol", symbols)
    _trap_ribbon(store, symbol, datetime.now())
    dates = [
        r[0]
        for r in store._con.execute(
            'SELECT DISTINCT "date" FROM daily_bar WHERE "symbol" = ? ORDER BY "date"',
            [symbol],
        ).fetchall()
    ]
    series = replay.build_replay(store, symbol, dates[0], dates[-1])
    if not series.frames:
        st.warning("No frames in range.")
        return

    playhead = st.slider("Playhead (trading-day index)", 0, len(series.frames) - 1,
                         len(series.frames) - 1)
    frame = series.frames[playhead]
    panel = playhead_panel(frame)
    st.caption(
        f"{panel['date']} · as knowable at {panel['as_knowable_at']:%Y-%m-%d %H:%M} WIB "
        "(next-session pre-open, LD-5 conservative)"
    )

    rows = pd.DataFrame(visible_rows(series, playhead)).set_index("date")
    left, right = st.columns([1.6, 1])
    with left:
        st.line_chart(rows["close"], height=200)
        st.bar_chart(rows["volume"], height=120)
        st.line_chart(rows[["net_foreign_bn", "smart_money_net_bn"]], height=160)
    with right:
        st.subheader("At playhead")
        st.metric("Close", panel["close"], f"{panel['change_pct']}%" if panel["change_pct"] is not None else None)
        st.metric("Volume", panel["volume"],
                  f"RVOL {panel['rvol_20d']}×" if panel["rvol_20d"] is not None else None)
        st.metric("Foreign net (IDR bn)", panel["net_foreign_bn"])
        st.metric("Broker net (IDR bn)", panel["broker_net_bn"])
        st.metric("Smart-money net (IDR bn)", panel["smart_money_net_bn"])
        st.info(panel["phase"])


def _render_accumulation(store: Store) -> None:
    st.title("Institutional Accumulation Detector")
    st.caption(f":green[{OBSERVATION_BADGE}] — stealth accumulation; here is the flow, you decide.")

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    symbol = st.sidebar.selectbox("Symbol", symbols)
    decision_ts = datetime.now()
    _trap_ribbon(store, symbol, decision_ts)
    snap = accumulation.analyze(store, symbol, decision_ts)

    callout = stealth_callout(snap)
    if callout:
        st.info(callout)
    panel = accumulation_panel(snap)
    st.caption(f"{symbol} · {panel['window']}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Price Δ over window", f"{panel['price_change_pct']}%" if panel['price_change_pct'] is not None else "—")
    c2.metric("Accumulator", panel["accumulator"] or "—",
              f"net {panel['net_accumulation_bn']} bn" if panel['net_accumulation_bn'] is not None else None)
    c3.metric("Accum. VWAP", panel["accumulator_vwap"] or "—",
              f"px vs vwap {panel['price_vs_vwap_pct']}%" if panel['price_vs_vwap_pct'] is not None else None)
    st.write({k: panel[k] for k in ("accumulation_rising", "volume_dryup_ratio", "price_tightness_pct", "absorption")})


def _render_heatmap(store: Store) -> None:
    st.title("Smart Money Heatmap")
    st.caption(f":green[{OBSERVATION_BADGE}] — direction & flow-as-%-of-cap; a rendering, not a score.")

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    decision_ts = datetime.now()
    cells = heatmap.heatmap(store, symbols, decision_ts)
    for alert in divergence_alerts(cells):
        st.warning("◆ " + alert)

    st.subheader("Distribution / decay watch")
    watch_rows = []
    for s in symbols:
        banner = ribbon_banner(distribution.monitor(store, s, decision_ts))
        if banner:
            watch_rows.append({"symbol": s, "flag": banner})
    if watch_rows:
        st.dataframe(pd.DataFrame(watch_rows), use_container_width=True)
    else:
        st.caption(":green[✓ no trap or decay flags across the heatmap]")

    st.subheader("By sector")
    st.dataframe(pd.DataFrame(sector_totals(cells)), use_container_width=True)
    st.subheader("By stock")
    st.dataframe(pd.DataFrame(heatmap_rows(cells)), use_container_width=True)


def _render_sms(store: Store) -> None:
    st.title("SMS / Rank")
    st.caption(f":orange[GATED · RULE B] — {WATCHLIST_FRAMING}.")
    st.info(GATE_BANNER)

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    symbol = st.sidebar.selectbox("Symbol", symbols)
    track = st.sidebar.radio("Track", ["A", "B"], index=1)
    decision_ts = datetime.now()
    res = engine.evaluate(store, symbol, decision_ts, track=track)
    registry = _ledger().states()   # server-authoritative RULE B state
    _trap_ribbon(store, symbol, decision_ts)

    left, right = st.columns([1, 1.4])
    with left:
        st.metric("SMS (composite)", score_display(res.sms, registry=registry))  # •••  until VALIDATED
        st.metric("State", state_label(res))
        st.caption(f"Wyckoff phase: {res.phase.phase.value} · track {res.track}")
    with right:
        st.subheader("Score components — observation")
        st.dataframe(pd.DataFrame(component_rows(res.sms)), use_container_width=True)


def _render_ranking(store: Store) -> None:
    st.title("AI Buy/Sell Ranking")
    registry = _ledger().states()
    st.caption(f":orange[GATED · RULE B] — {ranking_view.framing(registry=registry)}.")

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    track = st.sidebar.radio("Track", ["A", "B"], index=1)
    results = _all_results(store, track, datetime.now())
    st.dataframe(pd.DataFrame(ranking_view.ranking(results, registry=registry)), use_container_width=True)


def _render_daily_top(store: Store) -> None:
    st.title("Daily Top Opportunities")
    registry = _ledger().states()
    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    track = st.sidebar.radio("Track", ["A", "B"], index=1)
    dig = daily_top_view.digest(_all_results(store, track, datetime.now()), registry=registry)
    st.caption(f":orange[GATED · RULE B] — {dig['framing']}.")
    st.metric("ARMED names today", dig["count"])
    for row in dig["names"]:
        st.subheader(f"{row['symbol']} · {row['track']}")
        st.metric("SMS (composite)", row["score"])   # •••  until VALIDATED
        st.dataframe(pd.DataFrame(row["components"]), use_container_width=True)


def _render_sector(store: Store) -> None:
    st.title("Sector Rotation Map")
    st.caption(":blue[DERIVED VIEW] — flow by sector on the RS-vs-flow quadrant; a rendering, not a score.")

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    decision_ts = datetime.now()
    max_date = store._con.execute('SELECT max("date") FROM daily_bar').fetchone()[0]
    start = sector_rotation.window_start(max_date) if max_date else None
    rotations = sector_rotation.build_sector_rotation(
        store, symbols, decision_ts, sector_map=OPERATOR_SECTOR_MAP, start=start
    )
    st.caption(
        f"{start} → {max_date} · sectors from operator map (illustrative); RS vs the universe (proxy), not IHSG."
    )

    pts = pd.DataFrame(scatter_points(rotations))
    if not pts.empty:
        st.scatter_chart(
            pts, x="x_relative_strength_pct", y="y_net_flow_bn",
            size="radius_flow_bn", color="quadrant",
        )
    st.subheader("By sector")
    st.dataframe(pd.DataFrame(sector_rows(rotations)), use_container_width=True)


def _render_risk(store: Store) -> None:
    st.title("Portfolio Risk Monitor")
    st.caption(f":green[OBSERVATION] — {RISK_FRAMING}.")

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    decision_ts = datetime.now()

    # No paper fills exist yet (the fill engine lands in slice 7). Preview the §6
    # exposure / crowding / β / VaR observations over an equal-lot book of the
    # ingested names, marked at the latest visible close. P&L stays withheld (no entry).
    st.info(
        "No paper positions yet — the IDX fill engine lands in slice 7. Previewing risk "
        "observations over an equal-lot book of ingested names (P&L withheld, no entry price)."
    )
    positions = []
    for sym in symbols:
        bars = store.read_daily_bars(sym, decision_ts)
        closes = [b.close for b in bars if b.close]
        if closes:
            positions.append(
                Position(sym, OPERATOR_SECTOR_MAP.get(sym, "UNKNOWN"), qty=100, last_price=closes[-1])
            )
    if not positions:
        st.warning("No priced names to build a preview book.")
        return

    portfolio = Portfolio(tuple(positions), cash=0.0)
    benchmark = risk_monitor.market_proxy_returns(store, symbols, decision_ts)  # proxy, not IHSG
    report = risk_monitor.build_risk_report(store, portfolio, decision_ts, benchmark_returns=benchmark)

    cards = metric_cards(report)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio β (universe proxy)", cards["portfolio_beta"] if cards["portfolio_beta"] is not None else "—")
    c2.metric("VaR (95% · 1d)", f"{cards['var_1d_pct']}%" if cards["var_1d_pct"] is not None else "—",
              f"{cards['var_1d_bn']} bn" if cards["var_1d_bn"] is not None else None)
    c3.metric("Sector HHI", cards["sector_hhi"] if cards["sector_hhi"] is not None else "—", cards["sector_hhi_label"])
    c4.metric("Invested / cash (bn)", f"{cards['invested_bn']} / {cards['cash_bn']}")

    left, right = st.columns(2)
    with left:
        st.subheader("Name exposure vs 10% cap (§6)")
        st.dataframe(pd.DataFrame(name_exposure_rows(report)), use_container_width=True)
        st.subheader("Sector exposure vs 30% cap (§6)")
        st.dataframe(pd.DataFrame(sector_exposure_rows(report)), use_container_width=True)
    with right:
        st.subheader("Crowding — same-bandar correlated pairs (§6)")
        crowd = crowding_rows(report)
        st.dataframe(pd.DataFrame(crowd), use_container_width=True) if crowd else st.caption(
            ":green[✓ no correlated pairs above threshold]"
        )
        st.subheader("Liquidity — days to exit")
        st.dataframe(pd.DataFrame(liquidity_rows(report)), use_container_width=True)

    st.subheader("Scenario stress (hypothetical shocks, not forecasts)")
    st.dataframe(pd.DataFrame(scenario_rows(report)), use_container_width=True)


def _render_ml(store: Store) -> None:
    st.title("ML Layer")
    st.caption(":red[GATED · LD-8] — signal-weight optimizer / ranker; runs only after the "
               "rules system earns ≥3mo positive forward-paper walk-forward Sharpe.")

    status = ml_view.status(_ledger())
    (st.success if status["admitted"] else st.error)(status["banner"])
    st.caption(status["detail"])

    c1, c2 = st.columns(2)
    c1.metric("Admission module", status["admission_module"])
    c2.metric("Required months (RULE B)", status["required_months"])
    st.metric("Applied weight updates", status["weight_updates"])
    if status["last_update"] is not None:
        st.subheader("Last weight update (provenance)")
        st.json(status["last_update"])
    else:
        st.info(
            "No weight updates — the optimizer is the sole writer of SMS weights and cannot "
            "run until the LD-8 gate opens. Weights are never hand-edited live (§4)."
        )


def _login_controller():
    """The login controller lives in session_state so MFA handles + the httpx client
    survive Streamlit reruns during the flow. Created lazily on first use."""
    from currentflow.dal.auth import AuthClient
    from currentflow.ui.login_view import LoginController

    if "login_ctl" not in st.session_state:
        st.session_state["login_ctl"] = LoginController(AuthClient())
        st.session_state["login_view"] = None
    return st.session_state["login_ctl"]


def _run(coro):
    """Drive a coroutine on ONE persistent per-session event loop.

    The login flow caches an `httpx.AsyncClient` in `session_state` across reruns;
    its connection pool binds to whatever loop first drives it. `asyncio.run` opens
    AND closes a fresh loop per call, orphaning that pool — the next submit then hits
    `RuntimeError: Event loop is closed` while closing stale connections. Reusing a
    single open loop keeps the cached client valid for the life of the session."""
    import asyncio

    loop = st.session_state.get("_event_loop")
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        st.session_state["_event_loop"] = loop
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _render_login() -> None:
    """The login flow — rendered instead of the modules when there is no session.
    Fail loud: never a blank/stale terminal (§9.1). Credentials/OTP held only in this
    run, never persisted or echoed back."""
    from currentflow.dal.token_store import KeychainTokenStore
    from currentflow.ui import login_view as lv

    ctl = _login_controller()
    store = KeychainTokenStore()
    view = st.session_state.get("login_view") or lv.initial_view(store)

    st.title("CurrentFlow — sign in")
    st.caption(
        "Your own Stockbit session (own risk, §15). Credentials and OTP are used only "
        "to sign in — never stored, never logged. Only the session token is kept."
    )
    if view.error:
        st.error(view.error)

    if view.state == lv.CREDENTIALS:
        st.caption(
            "First sign-in on this machine sends a one-time OTP to trust the device; "
            "after that, login is direct."
        )
        with st.form("credentials"):
            user = st.text_input("Username / email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in"):
                st.session_state["login_view"] = _run(
                    ctl.submit_credentials(user, password)
                )
                st.rerun()
    elif view.state == lv.OTP:
        # The code is sent immediately on entering each round (no "Send OTP" button).
        target = view.otp_target or (view.channels[0].target if view.channels else None)
        channel = view.default_channel or (view.channels[0].channel if view.channels else None)
        if target:
            st.caption(f"Code sent to {channel or 'your device'} → {target}. Enter it below.")
        with st.form("otp"):
            # Key the field by the channel/target of THIS round, so when the server asks
            # for a second factor (email → WhatsApp) the widget is re-created empty
            # instead of carrying the just-verified email code over.
            code = st.text_input("OTP code", key=f"otp_code_{target or channel or 'x'}")
            if st.form_submit_button("Verify"):
                st.session_state["login_view"] = _run(ctl.verify_otp(code))
                st.rerun()
    elif view.state == lv.FINISH:
        st.session_state["login_view"] = view
        st.success(f"Signed in as {view.username or 'operator'} — loading terminal…")
        st.rerun()


def _session_topbar() -> bool:
    """Masked session status + sign-out in the sidebar. Returns True while a valid
    session exists (module rendering proceeds), False after sign-out."""
    from currentflow.dal.session import session_status
    from currentflow.dal.token_store import KeychainTokenStore
    from currentflow.ui import login_view as lv

    store = KeychainTokenStore()
    st_status = session_status(store)
    if not st_status["has_token"]:
        return False
    who = st_status.get("username") or "operator"
    st.sidebar.caption(f"● {who} — {st_status['preview']} [{st_status['source']}]")
    if st.sidebar.button("Sign out"):
        _login_controller().sign_out() if "login_ctl" in st.session_state else store.clear()
        st.session_state["login_view"] = lv.LoginView(lv.CREDENTIALS)
        st.rerun()
    return True


def main() -> None:
    configure_logging()  # persist dal `net-error` lines to logs/net.log
    st.set_page_config(page_title="CurrentFlow", layout="wide")

    # Auth gate (slice 11): no valid session → the login flow, never blank modules.
    if not _session_topbar():
        _render_login()
        st.divider()
        st.caption(DISCLAIMER)
        return

    store = _store(_db_path())

    module = st.sidebar.radio("Module", MODULES, index=0)
    renderers = {
        MODULES[0]: _render_broker_flow,
        MODULES[1]: _render_foreign_flow,
        MODULES[2]: _render_accumulation,
        MODULES[3]: _render_replay,
        MODULES[4]: _render_heatmap,
        MODULES[5]: _render_sector,
        MODULES[6]: _render_risk,
        MODULES[7]: _render_sms,
        MODULES[8]: _render_ranking,
        MODULES[9]: _render_daily_top,
        MODULES[10]: _render_ml,
    }
    if module in renderers:
        renderers[module](store)
    else:
        st.subheader(module)
        st.info("Not built yet — lands in a later slice (see PLAN.md).")

    st.divider()
    st.caption(DISCLAIMER)


if __name__ == "__main__":
    main()
