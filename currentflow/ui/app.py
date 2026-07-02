"""CurrentFlow terminal — Streamlit prototype (spec §10; design/ handoff).

Run:  streamlit run currentflow/ui/app.py -- --db <path.duckdb>

Slice 2 shipped the Broker Flow Analyzer; slice 3 adds the Foreign Flow Dashboard
and Money Flow Replay (the audit tool). Other modules render as locked
placeholders. RULE B is enforced by construction: nothing here computes or
displays a score, probability, or buy/sell verb.
"""

from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from currentflow.signals import foreign_flow, replay
from currentflow.signals.broker_flow import analyze, buyer_seller_matrix
from currentflow.store.db import Store
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
from currentflow.ui.replay_view import playhead_panel, visible_rows

MODULES = [
    "⇄ Broker Flow",
    "⌖ Foreign Flow",
    "◱ Accum. Detect",
    "⟲ Money Replay",
    "▦ Smart Heatmap",
    "✦ Sector Rotate",
    "◈ Risk Monitor",
    "∑ SMS / Rank 🔒",
]
BUILT = {MODULES[0], MODULES[1], MODULES[3]}


def _db_path() -> str:
    args = sys.argv[1:]
    return args[args.index("--db") + 1] if "--db" in args else "currentflow.duckdb"


@st.cache_resource
def _store(path: str) -> Store:
    return Store(path)


def _symbols(store: Store, table: str) -> list[str]:
    return [
        r[0]
        for r in store._con.execute(
            f'SELECT DISTINCT "symbol" FROM {table} ORDER BY "symbol"'
        ).fetchall()
    ]


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


def main() -> None:
    st.set_page_config(page_title="CurrentFlow", layout="wide")
    store = _store(_db_path())

    module = st.sidebar.radio("Module", MODULES, index=0)
    if module not in BUILT:
        st.subheader(module)
        if "SMS" in module:
            st.info(
                "Locked (RULE B): no number until this module survives "
                "PAPER_VALIDATION_MONTHS of fill-realistic forward paper trading. "
                "Components ship as observation from slice 4."
            )
        else:
            st.info("Not built yet — lands in a later slice (see PLAN.md).")
    elif module == MODULES[0]:
        _render_broker_flow(store)
    elif module == MODULES[1]:
        _render_foreign_flow(store)
    else:
        _render_replay(store)

    st.divider()
    st.caption(DISCLAIMER)


if __name__ == "__main__":
    main()
