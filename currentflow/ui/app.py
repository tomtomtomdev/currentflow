"""CurrentFlow terminal — Streamlit prototype (spec §10; design/ handoff).

Run:  streamlit run currentflow/ui/app.py -- --db <path.duckdb>

Slice 2 ships the Broker Flow Analyzer (observation). Other modules render as
locked placeholders. RULE B is enforced by construction: nothing here computes or
displays a score, probability, or buy/sell verb.
"""

from __future__ import annotations

import sys
from datetime import datetime

import streamlit as st

from currentflow.signals.broker_flow import analyze, buyer_seller_matrix
from currentflow.store.db import Store
from currentflow.ui.broker_flow_view import (
    DISCLAIMER,
    OBSERVATION_BADGE,
    broker_table,
    concentration_panel,
    matrix_table,
)

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


def _db_path() -> str:
    args = sys.argv[1:]
    return args[args.index("--db") + 1] if "--db" in args else "currentflow.duckdb"


@st.cache_resource
def _store(path: str) -> Store:
    return Store(path)


def main() -> None:
    st.set_page_config(page_title="CurrentFlow", layout="wide")
    store = _store(_db_path())

    module = st.sidebar.radio("Module", MODULES, index=0)
    if module != MODULES[0]:
        st.subheader(module)
        if "SMS" in module:
            st.info(
                "Locked (RULE B): no number until this module survives "
                "PAPER_VALIDATION_MONTHS of fill-realistic forward paper trading. "
                "Components ship as observation from slice 4."
            )
        else:
            st.info("Not built yet — lands in a later slice (see PLAN.md).")
        st.caption(DISCLAIMER)
        return

    st.title("Broker Flow Analyzer")
    st.caption(f":green[{OBSERVATION_BADGE}] — here is the flow, you decide.")

    symbols = [
        r[0]
        for r in store._con.execute(
            'SELECT DISTINCT "symbol" FROM broker_net ORDER BY "symbol"'
        ).fetchall()
    ]
    if not symbols:
        st.warning("No broker data ingested yet — run the ingest pipeline first.")
        st.caption(DISCLAIMER)
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

    st.divider()
    st.caption(DISCLAIMER)


if __name__ == "__main__":
    main()
