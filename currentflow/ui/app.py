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
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from currentflow import config
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
from currentflow.ui.accumulation_view import (
    accumulation_panel,
    chart_rows as accum_chart_rows,
    stealth_callout,
)
from currentflow.ui.broker_flow_view import (
    OBSERVATION_BADGE,
    broker_table,
    concentration_panel,
    matrix_table,
    stock_header,
    veto_checks,
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
from currentflow.ui import charts
from currentflow.ui.heatmap_view import (
    divergence_rows,
    grid_rows,
    heatmap_rows,
    sector_totals,
)
from currentflow.ui.replay_view import phase_box, playhead_panel, visible_rows
from currentflow.ui.risk_view import (
    FRAMING as RISK_FRAMING,
    crowding_rows,
    liquidity_rows,
    metric_cards,
    name_exposure_rows,
    scenario_rows,
    sector_exposure_rows,
)
from currentflow.ui import shell
from currentflow.ui.sector_view import scatter_points, sector_rows
from currentflow.ui.sms_view import GATE_BANNER, WATCHLIST_FRAMING, component_rows, score_display, state_label
from currentflow.ui import daily_top_view, ml_view, ranking_view, watchlist_view
from currentflow.validation.promotion import ValidationLedger

# (icon glyph, title, gated?) — the nav rail. Rendered with st.radio's native
# `captions=`: the icon is the option label, the title its caption directly below,
# so the item stacks vertically (icon over title) — design: leftmost rail. The
# stable full "icon title" string is the option value / renderer key.
_MODULE_DEFS = [
    ("⇄", "Broker Flow", False),
    ("⌖", "Foreign Flow", False),
    ("◱", "Accum. Detect", False),
    ("⟲", "Money Replay", False),
    ("▦", "Smart Heatmap", False),
    ("✦", "Sector Rotate", False),
    ("◈", "Risk Monitor", False),
    ("∑", "SMS / Rank", True),
    ("◇", "AI Ranking", True),
    ("☰", "Daily Top", True),
    ("⚙", "ML Layer", True),
]
MODULES = [f"{icon} {title}" for icon, title, _ in _MODULE_DEFS]
_MODULE_ICON = {k: icon for k, (icon, _, _) in zip(MODULES, _MODULE_DEFS)}
_MODULE_CAPTION = {
    k: f"{title}{' 🔒' if locked else ''}"
    for k, (_, title, locked) in zip(MODULES, _MODULE_DEFS)
}

# IHSG (Jakarta Composite) is display-only top-bar chrome — signals never benchmark
# to it (§8). Read a real level if a composite series happens to be ingested under
# any of these symbols; otherwise the top bar shows it absent (never faked, §10).
_IHSG_SYMBOLS = ("IHSG", "COMPOSITE", "^JKSE", "JKSE")

# Operator sector map — ILLUSTRATIVE, seeded from the design handoff ticker reference
# (design/SCREENS_terminal.md). Like the broker-DNA registry, it is operator knowledge to verify
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


@st.cache_data(ttl=600, show_spinner="Evaluating ARMED watchlist…")
def _watchlist_data(db_path: str, day: str, n_symbols: int) -> dict:
    """One engine pass over every ingested name for the sidebar rail. Keyed on the
    day and the symbol count so a bootstrap / new ingest invalidates the cache; the
    TTL bounds intraday staleness. Returns primitives only (RULE-B-safe rows)."""
    return watchlist_view.rows(_all_results(_store(db_path), "B", datetime.now()))


def _select_symbol(symbol: str) -> None:
    """`on_click` for a rail card — runs before the rerun renders, so the module
    pane and the card highlight both see the new selection in the same pass."""
    st.session_state["cf_symbol"] = symbol


def _selected_symbol(store: Store, *, table: str = "daily_bar") -> str | None:
    """The rail-selected symbol, validated against what `table` actually holds.
    Selection lives in the right rail (design: click a watchlist card) — there is
    no sidebar dropdown. Falls back to the first ingested name when nothing valid
    is selected; None when the table is empty."""
    symbols = _symbols(store, table)
    if not symbols:
        return None
    sel = st.session_state.get("cf_symbol")
    return sel if sel in symbols else symbols[0]


def _render_watchlist_rail(store: Store) -> None:
    """The design's ARMED-watchlist right rail (design/screens: right 296px band):
    state word + the five component spark-bars (DIV BRK FF RVOL BLK) — observation,
    never a number or a verb (RULE B; the composite stays with the gated SMS/Rank
    module). Each card is the terminal's symbol selector: a keyed container with an
    invisible full-card button (shell CSS overlay); the selected card carries the
    design's brightened border."""
    syms = _symbols(store, "daily_bar")
    if not syms:
        st.markdown(
            '<div class="cf-railhead">ARMED WATCHLIST</div>'
            '<div class="cf-railnote">no data ingested yet</div>',
            unsafe_allow_html=True,
        )
        return
    data = _watchlist_data(_db_path(), f"{datetime.now():%Y-%m-%d}", len(syms))
    if data["rows"] and st.session_state.get("cf_symbol") not in syms:
        # design default: the top rail name (ARMED first, strongest flow first)
        st.session_state["cf_symbol"] = data["rows"][0]["symbol"]
    selected = st.session_state.get("cf_symbol")

    st.markdown(shell.rail_head_html(data["framing"]), unsafe_allow_html=True)
    for row in data["rows"]:
        sym = row["symbol"]
        with st.container(key=f"cfwatch-{sym}"):
            st.markdown(
                shell.watchlist_card_html(row, selected=sym == selected),
                unsafe_allow_html=True,
            )
            st.button(
                sym, key=f"cfsel-{sym}", on_click=_select_symbol, args=(sym,),
                help=f"View {sym} in the active module",
            )
    if not data["rows"]:
        st.markdown(
            '<div class="cf-railnote">— nothing ARMED or watching today</div>',
            unsafe_allow_html=True,
        )
    st.markdown(shell.rail_foot_html(data), unsafe_allow_html=True)


def _module_header(title: str, subtitle: str, kind: str, badge: str) -> None:
    """Design module-header ribbon: title + framing subtitle + status pill."""
    st.markdown(shell.module_header_html(title, subtitle, kind, badge), unsafe_allow_html=True)


def _as_of(store: Store) -> str | None:
    """Latest ingested trading day — the top bar's as-of stamp ('—' when empty;
    a missing stamp is shown as absent, never faked)."""
    row = store._con.execute('SELECT max("date") FROM daily_bar').fetchone()
    return str(row[0]) if row and row[0] else None


def _ihsg(store: Store) -> tuple[float | None, float | None]:
    """Latest IHSG level + day-over-day %-change for the top bar, read from a
    composite series if one is ingested (any of `_IHSG_SYMBOLS`). Not a benchmark
    (§8) — display chrome only. Absent when not ingested (never faked, §10)."""
    for sym in _IHSG_SYMBOLS:
        rows = store._con.execute(
            'SELECT "close" FROM daily_bar WHERE "symbol" = ? '
            'ORDER BY "date" DESC LIMIT 2',
            [sym],
        ).fetchall()
        if not rows or rows[0][0] is None:
            continue
        last = _as_float(rows[0][0])
        if last is None:  # corrupt close (e.g. a broker code leaked in) — skip, never fake
            continue
        prev = _as_float(rows[1][0]) if len(rows) > 1 else None
        pct = (last - prev) / prev * 100 if prev else None
        return last, pct
    return None, None


def _as_float(value: object) -> float | None:
    """Coerce a stored cell to float, or None if it isn't numeric (corrupt column)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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
    _module_header(
        "Broker Flow Analyzer",
        "Per-stock broker net buy/sell · DNA classification · concentration & "
        "persistence. The differentiator — pure observation, ships now.",
        "observation", OBSERVATION_BADGE,
    )

    symbols = _symbols(store, "broker_net")
    if not symbols:
        st.warning("No broker data ingested yet — run the ingest pipeline first.")
        return

    symbol = _selected_symbol(store, table="broker_net")
    picked = st.session_state.get("cf_symbol")
    if picked and picked != symbol:
        st.caption(
            f"{picked} has no broker rows ingested — showing {symbol}. "
            "Pick another name from the ARMED rail."
        )
    decision_ts = datetime.now()
    snap = analyze(store, symbol, decision_ts)
    _trap_ribbon(store, symbol, decision_ts)

    # design stock-header row: ticker · TRACK chip · sector chip · price/Δ% · 20d ADV
    st.markdown(
        shell.stock_header_html(
            symbol=symbol, track="B", sector=OPERATOR_SECTOR_MAP.get(symbol),
            **stock_header(store.read_daily_bars(symbol, decision_ts)),
        ),
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.35, 1])
    with left:
        st.markdown(
            shell.panel_html(
                "BROKER NET FLOW",
                # every broker rides the panel; the fixed-height body scrolls
                # (design proportions without a silent cap)
                f'<div class="cf-scrollbody">{shell.broker_table_html(broker_table(snap))}</div>',
                note=f"{snap.start} → {snap.end} · net value, IDR bn · as visible "
                f"at {decision_ts:%Y-%m-%d %H:%M}",
            ),
            unsafe_allow_html=True,
        )

    with right:
        st.markdown(
            shell.concentration_html(concentration_panel(snap)), unsafe_allow_html=True
        )
        # §5 veto checks for this name (full pipeline pass — phase gate + vetoes)
        res = engine.evaluate(store, symbol, decision_ts, track="B")
        st.markdown(shell.veto_panel_html(veto_checks(res.veto)), unsafe_allow_html=True)

    # design matrix: columns = the (≤7) watchlist names, never the whole universe;
    # the cap is annotated, not silent. The selected symbol is always a column.
    watch = _watchlist_data(_db_path(), f"{decision_ts:%Y-%m-%d}", len(_symbols(store, "daily_bar")))
    cols = [symbol] + [r["symbol"] for r in watch["rows"]
                       if r["symbol"] != symbol and r["symbol"] in symbols]
    cols = cols[:7]
    snaps = {s: (snap if s == symbol else analyze(store, s, decision_ts)) for s in cols}
    matrix_rows = matrix_table(buyer_seller_matrix(snaps), cols)
    dna_map = {b.broker_code: b.dna.value for sn in snaps.values() for b in sn.brokers}
    for r in matrix_rows:
        r["dna"] = dna_map.get(r["broker"])
    st.markdown(
        shell.panel_html(
            "BROKER × STOCK MATRIX",
            shell.matrix_html(matrix_rows, cols, selected=symbol),
            note=f"net direction across the watchlist · {len(cols)} of "
            f"{len(symbols)} ingested names shown (selected + ARMED/WATCH)",
        ),
        unsafe_allow_html=True,
    )


def _render_foreign_flow(store: Store) -> None:
    _module_header(
        "Foreign Flow Dashboard",
        "Foreign net-buy magnitude & persistence vs float · foreign/domestic split · "
        "KSEI ownership trend · flow-reversal detection.",
        "observation", "OBSERVATION · ships now",
    )

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No OHLCV/foreign data ingested yet — run the ingest pipeline first.")
        return

    symbol = _selected_symbol(store)
    decision_ts = datetime.now()
    snap = foreign_flow.analyze(store, symbol, decision_ts)
    _trap_ribbon(store, symbol, decision_ts)
    stats = stats_panel(snap)

    # design header: ticker + FOREIGN-INST LENS chip, IDX-wide tide note right
    tide = tide_table(foreign_flow.market_tide(store, symbols, decision_ts, day=snap.end))
    market = next((r for r in tide if r["scope"] == "MARKET"), None)
    fgn = shell.TOKENS["foreign"]
    note = ""
    if stats["net_today_bn"] is not None:
        word = "buyers" if stats["net_today_bn"] >= 0 else "sellers"
        note = f"Foreign net {word} on this name"
        if market and market["net_foreign_bn"] is not None:
            note += f"; IDX-wide foreign {market['net_foreign_bn']:+,.0f} IDR bn today"
        note += "."
    st.markdown(
        '<div class="cf-stockhead">'
        f'<span class="cf-sym">{symbol}</span>'
        f'<span class="cf-chip" style="border:1px solid {fgn}44; '
        f'background:{fgn}14; color:{fgn}">FOREIGN-INST LENS</span>'
        '<span style="flex:1"></span>'
        f'<span class="cf-mono" style="font-size:11px; '
        f'color:{shell.TOKENS["text_muted"]}">{note}</span></div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.9, 1], gap="medium")
    with left:
        cum, daily = cumulative_series(snap), daily_series(snap)
        with st.container(key="cfpanel_ffcharts"):
            if cum:
                st.altair_chart(charts.themed(charts.foreign_cumulative(cum)),
                                use_container_width=True, theme=None)
            if daily:
                st.altair_chart(charts.themed(charts.foreign_daily(daily)),
                                use_container_width=True, theme=None)
            if not cum and not daily:
                st.caption("No foreign-flow series visible in the window.")

        callout = reversal_callout(snap)
        if callout:
            st.markdown(
                shell.callout_html("FLOW-REVERSAL DETECTION", callout),
                unsafe_allow_html=True,
            )

        split = split_bar(snap)
        if split["foreign_net_bn"] is not None:
            share = split["foreign_turnover_share_pct"]
            st.markdown(
                shell.panel_html(
                    "FOREIGN vs DOMESTIC — today",
                    shell.split_bar_html(split["foreign_net_bn"], split["domestic_net_bn"]),
                    note=(f"foreign turnover share {share}%" if share is not None else None),
                ),
                unsafe_allow_html=True,
            )

    with right:
        buy, sell = shell.TOKENS["buy"], shell.TOKENS["sell"]

        def _signed(v, unit=" bn"):
            if v is None:
                return None, None
            return f"{v:+,.1f}{unit}", (buy if v >= 0 else sell)

        net_s, net_c = _signed(stats["net_today_bn"])
        cum_s, cum_c = _signed(stats["cum_5d_bn"])
        side = f" {stats['persistence_side']}" if stats["persistence_side"] else ""
        rows = [
            {"label": "Foreign net · today", "value": net_s, "color": net_c},
            {"label": "5-day cumulative", "value": cum_s, "color": cum_c},
            {"label": "Net-buy persistence", "value": f"{stats['persistence']} d{side}"},
            {
                "label": f"vs {stats['avg_window_used'] or 20}-day avg",
                "value": None if stats["vs_20d_avg"] is None else f"{stats['vs_20d_avg']}×",
                "color": shell.TOKENS["armed_text"],
            },
        ]
        st.markdown(
            shell.panel_html("FOREIGN FLOW STATS", shell.kv_rows_html(rows)),
            unsafe_allow_html=True,
        )

        ksei = ksei_panel(snap)
        if ksei["foreign_own_pct"] is not None:
            # Design 04: "FOREIGN OWN vs FREE-FLOAT" — own% of free-float%, bar fills to
            # own's share of the float. Fall back to KSEI-holdings framing when SCR-0 has
            # no free-float on file (missing data is never zeroed).
            if ksei["free_float_pct"] is not None:
                own_note = f"of {ksei['free_float_pct']:g}% free-float"
                own_frac = ksei["own_of_float_pct"]
            else:
                own_note = "of KSEI-reported holdings · latest month"
                own_frac = ksei["foreign_own_pct"]
            st.markdown(
                shell.panel_html(
                    "FOREIGN OWN vs FREE-FLOAT",
                    shell.bigstat_bar_html(
                        f"{ksei['foreign_own_pct']:.0f}%",
                        own_note,
                        own_frac, fgn,
                    )
                    + (
                        shell.kv_rows_html([{
                            "label": "Window NBSA as % of float value",
                            "value": f"{ksei['nbsa_pct_of_float']}%",
                        }])
                        if ksei["nbsa_pct_of_float"] is not None
                        else ""
                    ),
                ),
                unsafe_allow_html=True,
            )
        if len(ksei["series"]) >= 2:
            trend = ksei["trend"]
            tag = (
                f'<span class="cf-mono" style="font-size:10px; color:{fgn}">{trend}</span>'
                if trend
                else None
            )
            st.markdown(
                shell.panel_html(
                    "KSEI OWNERSHIP",
                    shell.sparkline_svg([p["foreign_pct"] for p in ksei["series"]]),
                    note=f"{len(ksei['series'])}mo", right=tag,
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                shell.panel_html(
                    "KSEI OWNERSHIP",
                    f'<div class="cf-statlabel" style="color:{shell.TOKENS["text_faint"]}">'
                    "no monthly ownership slices ingested yet</div>",
                ),
                unsafe_allow_html=True,
            )

    with st.expander("Market tide — aggregate NBSA by scope (observation)"):
        if tide:
            st.dataframe(pd.DataFrame(tide), use_container_width=True)
        else:
            st.caption("No visible flow to aggregate for the latest day.")


def _render_replay(store: Store) -> None:
    _module_header(
        "Money Flow Replay",
        "Scrub the historical flow/price evolution for any name — the audit tool for "
        "every signal. Reconstructed from stored as_of data.",
        "observation", "OBSERVATION",
    )

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return

    symbol = _selected_symbol(store)
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

    maxidx = len(series.frames) - 1
    scrub_key, play_key = f"cf_replay_scrub::{symbol}", f"cf_replay_play::{symbol}"
    if scrub_key not in st.session_state or not (0 <= st.session_state[scrub_key] <= maxidx):
        st.session_state[scrub_key] = maxidx

    # Advance one frame per rerun while playing. Mutating the slider-keyed state must
    # happen *before* the slider widget is instantiated below (Streamlit rule).
    playing = st.session_state.get(play_key, False)
    if playing:
        if st.session_state[scrub_key] < maxidx:
            st.session_state[scrub_key] += 1
        else:
            playing = st.session_state[play_key] = False

    playhead = st.session_state[scrub_key]
    frame = series.frames[playhead]
    panel = playhead_panel(frame)

    st.markdown(
        '<div class="cf-stockhead">'
        f'<span class="cf-sym">{symbol}</span>'
        '<span style="flex:1"></span>'
        f'<span class="cf-mono" style="font-size:11px; color:{shell.TOKENS["text_faint"]}">'
        f'reconstructing from stored <span style="color:{shell.TOKENS["accent"]}">as_of</span> · '
        f'<span style="color:{shell.TOKENS["text"]}">{frame.date:%d %b %Y}</span></span></div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.9, 1], gap="medium")
    with left:
        with st.container(key="cfpanel_replaychart"):
            st.altair_chart(
                charts.themed(charts.replay_price_flow(visible_rows(series, playhead))),
                use_container_width=True, theme=None,
            )
    with right:
        buy, sell = shell.TOKENS["buy"], shell.TOKENS["sell"]

        def _bn_row(label, v):
            if v is None:
                return {"label": label, "value": None}
            return {"label": label, "value": f"{v:+,.1f} bn", "color": buy if v >= 0 else sell}

        chg = panel["change_pct"]
        readout = [
            {"label": "Close",
             "value": None if panel["close"] is None else f"{panel['close']:,.0f}"},
            {"label": "Δ vs prev", "value": None if chg is None else f"{chg:+.2f}%",
             "color": None if chg is None else (buy if chg >= 0 else sell)},
            {"label": "Volume (RVOL)",
             "value": None if panel["rvol_20d"] is None else f"{panel['rvol_20d']}×",
             "color": shell.TOKENS["armed_text"]},
            _bn_row("Foreign net", panel["net_foreign_bn"]),
            _bn_row("Broker net (SM)", panel["smart_money_net_bn"]),
        ]
        box = phase_box(frame.phase)
        st.markdown(
            shell.panel_html(
                "AT PLAYHEAD",
                shell.kv_rows_html(readout)
                + shell.phase_box_html(box["title"], box["note"], box["color"]),
            ),
            unsafe_allow_html=True,
        )
        st.caption(
            f"as knowable at {panel['as_knowable_at']:%Y-%m-%d %H:%M} WIB "
            "(next-session pre-open · LD-5 conservative)"
        )

    # Transport bar: circular accent play button + scrubber + date scale (design 06).
    with st.container(key="cfpanel_replaytransport"):
        if maxidx == 0:
            st.caption("Only one frame in range — nothing to scrub.")
        else:
            bcol, scol = st.columns([1, 22], vertical_alignment="center")
            with bcol, st.container(key="cfreplayplay"):
                if st.button("❚❚" if playing else "▶", key=f"{play_key}_btn",
                             help="Play / pause the reconstruction"):
                    if not playing and st.session_state[scrub_key] >= maxidx:
                        st.session_state[scrub_key] = 0  # replay from the open
                    st.session_state[play_key] = not playing
                    st.rerun()
            with scol:
                st.slider("playhead", 0, maxidx, key=scrub_key, label_visibility="collapsed")
                st.markdown(
                    '<div class="cf-replayscale">'
                    f'<span>{series.frames[0].date:%d %b %Y}</span>'
                    f'<span class="cf-mid">day {st.session_state[scrub_key]} / {maxidx}</span>'
                    f'<span>{series.frames[-1].date:%d %b %Y}</span></div>',
                    unsafe_allow_html=True,
                )

    # Pace the animation, then rerun to draw the next frame (best-effort in Streamlit).
    if playing:
        time.sleep(0.4)
        st.rerun()


def _render_accumulation(store: Store) -> None:
    _module_header(
        "Institutional Accumulation Detector",
        "Stealth divergence: price flat/down while net accumulation rises · "
        "accumulator VWAP · volume dry-up in consolidation. Measured, not scored.",
        "observation", "OBSERVATION · ships now",
    )

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    symbol = _selected_symbol(store)
    decision_ts = datetime.now()
    _trap_ribbon(store, symbol, decision_ts)
    bars = store.read_daily_bars(symbol, decision_ts)
    broker_snap = analyze(store, symbol, decision_ts)
    snap = accumulation.build_snapshot(symbol, bars, broker_snap, decision_ts=decision_ts)
    panel = accumulation_panel(snap)

    # design header row: ticker + window chip, amber detection pill right
    pill = (
        shell.badge_html("gated", "STEALTH ACCUMULATION DETECTED")
        if snap.stealth_divergence
        else shell.badge_html("observation", "no stealth divergence in window")
    )
    st.markdown(
        '<div class="cf-stockhead">'
        f'<span class="cf-sym">{symbol}</span>'
        f'<span class="cf-chip" style="border:1px solid rgba(255,255,255,0.10); '
        f'color:{shell.TOKENS["text_muted"]}">{panel["window"]}</span>'
        f'<span style="flex:1"></span>{pill}</div>',
        unsafe_allow_html=True,
    )

    rows = accum_chart_rows(bars, broker_snap)
    left, right = st.columns([1.9, 1], gap="medium")
    with left:
        with st.container(key="cfpanel_accum"):
            if rows:
                zone = None
                if snap.stealth_divergence:
                    # the divergence read compares the window's 2nd half to its 1st —
                    # shade the half where rising accumulation was measured
                    dates = [r["date"] for r in rows]
                    zone = (dates[len(dates) // 2], dates[-1])
                st.altair_chart(
                    charts.themed(charts.accumulation_combined(
                        rows, vwap=snap.accumulator_vwap, stealth_zone=zone,
                    )),
                    use_container_width=True, theme=None,
                )
                st.markdown(
                    f'<div class="cf-legend"><span><span class="cf-swatch" '
                    f'style="background:{shell.TOKENS["accent"]}"></span>price</span>'
                    f'<span><span class="cf-swatch" style="background:{shell.TOKENS["smart"]}">'
                    "</span>cumulative accumulation</span></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No complete traded bars in the window.")

        callout = stealth_callout(snap)
        if callout:
            st.markdown(
                shell.callout_html(
                    "STEALTH-DIVERGENCE DETECTION", callout,
                    color=shell.TOKENS["armed"],
                ),
                unsafe_allow_html=True,
            )

    with right:
        buy, sell = shell.TOKENS["buy"], shell.TOKENS["sell"]
        pc = panel["price_change_pct"]
        dryup = panel["volume_dryup_ratio"]
        tight = panel["price_tightness_pct"]
        metric_rows = [
            {
                "label": "Price Δ over window",
                "value": None if pc is None else f"{pc:+.1f}%",
                "color": None if pc is None else (buy if pc >= 0 else sell),
            },
            {
                "label": "Accumulation rising",
                "value": "yes" if panel["accumulation_rising"] else "no",
                "color": buy if panel["accumulation_rising"] else shell.TOKENS["text_muted"],
            },
            {
                "label": "Volume dry-up (recent/early)",
                "value": None if dryup is None else
                f"{dryup}×{' · dried up' if dryup < 0.8 else ''}",
                "color": shell.TOKENS["accent"],
            },
            {
                "label": "Consolidation tightness",
                "value": None if tight is None else
                f"{tight:.1f}%{' · tight' if tight < 12 else ' · wide'}",
                "color": buy if tight is not None and tight < 12 else shell.TOKENS["text_muted"],
            },
        ]
        st.markdown(
            shell.panel_html(
                "STEALTH METRICS", shell.kv_rows_html(metric_rows),
                note="measured, not scored",
            ),
            unsafe_allow_html=True,
        )

        vwap = panel["accumulator_vwap"]
        pvv = panel["price_vs_vwap_pct"]
        if vwap is not None:
            who = panel["accumulator"] or "top broker"
            net = panel["net_accumulation_bn"]
            body = shell.bigstat_bar_html(
                f"{vwap:,.0f}",
                f"{who} est. buy VWAP"
                + (f" · net {net:+,.1f} bn over window" if net is not None else ""),
                None, shell.TOKENS["smart"],
            )
            if pvv is not None:
                body += shell.kv_rows_html([{
                    "label": "Last close vs VWAP",
                    "value": f"{pvv:+.1f}%",
                    "color": buy if pvv >= 0 else sell,
                }])
            st.markdown(
                shell.panel_html("ACCUMULATOR VWAP", body), unsafe_allow_html=True
            )
        st.markdown(
            shell.callout_html(
                "ABSORPTION",
                f"{panel['absorption']} — degrades gracefully, never faked (§10).",
                color=shell.TOKENS["text_faint"],
            ),
            unsafe_allow_html=True,
        )


def _render_heatmap(store: Store) -> None:
    _module_header(
        "Smart Money Heatmap",
        "Aggregate flow across the universe. Colour = direction, intensity = flow "
        "as % of cap. Sector → stock drill-down with divergence alerts.",
        "derived", "DERIVED VIEW · rendering, no new claim",
    )

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    decision_ts = datetime.now()
    cells = heatmap.heatmap(store, symbols, decision_ts)

    st.markdown(
        shell.panel_html("SECTOR × STOCK", shell.heatmap_grid_html(grid_rows(cells))),
        unsafe_allow_html=True,
    )
    st.markdown(
        shell.divergence_panel_html(divergence_rows(cells)), unsafe_allow_html=True
    )

    with st.expander("Distribution / decay watch + full tables (observation)"):
        watch_rows = []
        for s in symbols:
            banner = ribbon_banner(distribution.monitor(store, s, decision_ts))
            if banner:
                watch_rows.append({"symbol": s, "flag": banner})
        if watch_rows:
            st.dataframe(pd.DataFrame(watch_rows), use_container_width=True)
        else:
            st.caption(":green[✓ no trap or decay flags across the heatmap]")
        st.dataframe(pd.DataFrame(sector_totals(cells)), use_container_width=True)
        st.dataframe(pd.DataFrame(heatmap_rows(cells)), use_container_width=True)


def _render_sms(store: Store) -> None:
    from currentflow.validation.state import ModuleState

    rec = _ledger().record("sms")
    validated = rec.state is ModuleState.VALIDATED
    _module_header(
        "Smart Money Score / AI Ranking",
        "Pre-validation: score computed internally, number withheld. Components shown "
        "as observation; ranking framed as flow-derived, not a recommendation.",
        "observation" if validated else "gated",
        "CLAIM · paper-validated" if validated else "GATED · number withheld (RULE B)",
    )
    st.markdown(
        shell.validation_bar_html(rec.months_accrued, config.PAPER_VALIDATION_MONTHS, validated),
        unsafe_allow_html=True,
    )
    st.caption(GATE_BANNER)

    symbols = _symbols(store, "daily_bar")
    if not symbols:
        st.warning("No data ingested yet — run the ingest pipeline first.")
        return
    symbol = _selected_symbol(store)
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
    registry = _ledger().states()
    _module_header(
        "AI Buy/Sell Ranking",
        f"{ranking_view.framing(registry=registry)}.",
        "gated", "GATED · number withheld (RULE B)",
    )

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
    _module_header(
        "Daily Top Opportunities", f"{dig['framing']}.",
        "gated", "GATED · number withheld (RULE B)",
    )
    st.metric("ARMED names today", dig["count"])
    for row in dig["names"]:
        st.subheader(f"{row['symbol']} · {row['track']}")
        st.metric("SMS (composite)", row["score"])   # •••  until VALIDATED
        st.dataframe(pd.DataFrame(row["components"]), use_container_width=True)


_QUADRANT_ORDER = {"LEADERS": 0, "EARLY_RECOVERY": 1, "DISTRIBUTION_WARN": 2, "AVOID": 3}


def _render_sector(store: Store) -> None:
    _module_header(
        "Sector Rotation Map",
        "Flow aggregated by sector on a relative-strength × flow quadrant. "
        "Leaders / Early Recovery / Distribution Warning / Avoid.",
        "derived", "DERIVED VIEW · rendering, no new claim",
    )

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

    left, right = st.columns([1.9, 1], gap="medium")
    with left:
        pts = scatter_points(rotations)
        with st.container(key="cfpanel_quadrant"):
            if pts:
                st.altair_chart(
                    charts.themed(charts.sector_quadrant(pts)),
                    use_container_width=True, theme=None,
                )
            else:
                st.caption("No sector carries both axes yet (flow + RS) — nothing to place.")
        st.caption(
            f"{start} → {max_date} · sectors from operator map (illustrative); "
            "RS vs the universe (proxy), not IHSG."
        )
    with right:
        cards = sorted(
            sector_rows(rotations),
            key=lambda r: (
                _QUADRANT_ORDER.get(r["quadrant"], 9),
                -(r["net_foreign_flow_bn"] or 0),
            ),
        )
        for row in cards:
            st.markdown(shell.sector_card_html(row), unsafe_allow_html=True)

    with st.expander("By sector — full table (observation)"):
        st.dataframe(pd.DataFrame(sector_rows(rotations)), use_container_width=True)


def _render_risk(store: Store) -> None:
    _module_header(
        "Portfolio Risk Monitor",
        "Crowding · beta vs universe proxy · sector concentration · VaR · "
        f"days-to-exit. {RISK_FRAMING.capitalize()} — they feed the §6 exposure caps.",
        "observation", "OBSERVATION · ships now",
    )

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
    beta = cards["portfolio_beta"]
    beta_sub = (
        None if beta is None
        else "high-beta tilt" if beta > 1.1 else "low-beta tilt" if beta < 0.9 else "market-like"
    )
    var_pct = cards["var_1d_pct"]
    st.markdown(
        shell.stat_cards_html([
            {"label": "Portfolio β vs universe proxy",
             "value": None if beta is None else f"{beta:.2f}", "sub": beta_sub},
            {"label": "VaR (95% · 1d)",
             "value": None if var_pct is None else f"−{abs(var_pct):.1f}%",
             "sub": None if cards["var_1d_bn"] is None else f"IDR {cards['var_1d_bn']} bn at risk",
             "color": shell.TOKENS["sell"]},
            {"label": "Sector HHI",
             "value": cards["sector_hhi"], "sub": cards["sector_hhi_label"]},
            {"label": "Invested / cash",
             "value": f"{cards['invested_bn']} / {cards['cash_bn']}", "sub": "IDR bn"},
        ]),
        unsafe_allow_html=True,
    )

    name_rows = name_exposure_rows(report)
    dte = {r["symbol"]: r["days_to_exit"] for r in liquidity_rows(report)}
    positions_rows = [
        {
            "symbol": e["key"],
            "sector": OPERATOR_SECTOR_MAP.get(e["key"], "UNKNOWN"),
            "weight_pct": e["weight_pct"],
            "cap_pct": e["cap_pct"],
            "status": e["status"],
            "days_to_exit": dte.get(e["key"]),
        }
        for e in name_rows
    ]

    left, right = st.columns([1.25, 1], gap="medium")
    with left:
        st.markdown(
            shell.panel_html(
                "OPEN PAPER POSITIONS",
                f'<div class="cf-scrollbody">{shell.positions_table_html(positions_rows)}</div>',
                note="preview book · equal lots · %-equity vs the §6 10% cap · "
                "P&L withheld (no fills yet)",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            shell.panel_html(
                "EXPOSURE CAPS", shell.cap_bars_html(sector_exposure_rows(report)),
                note="§6 · sector ≤ 30%",
            ),
            unsafe_allow_html=True,
        )
    with right:
        # design crowding matrix — bounded to the heaviest names, cap annotated
        matrix_syms = [r["symbol"] for r in positions_rows[:6]]
        matrix = risk_monitor.crowding_matrix(store, matrix_syms, decision_ts)
        crowd = crowding_rows(report)
        crowd_note = (
            f'{crowd[0]["pair"]} share lead broker {crowd[0]["shared_lead_broker"]} — '
            f'ρ={crowd[0]["rho"]}. The §6 correlated-pair check flags this.'
            if crowd
            else "No same-bandar pair at or above the §6 threshold."
        )
        st.markdown(
            shell.panel_html(
                "CROWDING MATRIX",
                shell.crowding_matrix_html(
                    matrix, threshold=config.CROWDING_CORR_THRESHOLD
                )
                + f'<div class="cf-statlabel" style="margin-top:8px">{crowd_note}</div>',
                note=f"same-bandar ρ · top {len(matrix_syms)} of {len(positions_rows)} "
                "names by weight",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            shell.panel_html(
                "SCENARIO STRESS", shell.scenario_rows_html(scenario_rows(report)),
                note="hypothetical shocks, not forecasts",
            ),
            unsafe_allow_html=True,
        )

    with st.expander("Full tables — exposures, crowding pairs, liquidity (observation)"):
        st.dataframe(pd.DataFrame(name_rows), use_container_width=True)
        st.dataframe(pd.DataFrame(sector_exposure_rows(report)), use_container_width=True)
        crowd_df = crowding_rows(report)
        if crowd_df:
            st.dataframe(pd.DataFrame(crowd_df), use_container_width=True)
        st.dataframe(pd.DataFrame(liquidity_rows(report)), use_container_width=True)


def _render_ml(store: Store) -> None:
    _module_header(
        "ML Layer",
        "Signal-weight optimizer / ranker; runs only after the rules system earns "
        "≥3mo positive forward-paper walk-forward Sharpe.",
        "gated", "GATED · LD-8",
    )

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


def _set_login_view(view) -> None:
    """Store the next login view; entering an OTP round (re)starts the resend cooldown
    from the server's `next_attempt_in` (design: 60s countdown, then resend enables)."""
    from currentflow.ui import login_view as lv

    st.session_state["login_view"] = view
    if view.state == lv.OTP and view.otp_next_attempt_in:
        st.session_state["_otp_cooldown_until"] = time.monotonic() + view.otp_next_attempt_in


def _otp_cooldown() -> int:
    """Seconds until resend re-enables (0 = enabled). Recomputed per rerun — the
    caption is a snapshot, not a live countdown (Streamlit reruns on interaction)."""
    until = st.session_state.get("_otp_cooldown_until", 0.0)
    return max(0, int(until - time.monotonic()))


def _render_login() -> None:
    """The login flow — rendered instead of the modules when there is no session.
    Fail loud: never a blank/stale terminal (§9.1). Credentials/OTP/Bearer held only
    in this run, never persisted or echoed back. Layout follows the session-gate
    handoff (design/SCREENS_login.md): hero left, floating operator card right."""
    from currentflow.dal.session import verify_bearer
    from currentflow.dal.token_store import KeychainTokenStore
    from currentflow.ui import login_view as lv

    ctl = _login_controller()
    store = KeychainTokenStore()
    view = st.session_state.get("login_view") or lv.initial_view(store)

    # minimal session-gate top bar (design: no as-of / RULE-B chrome before auth)
    st.markdown(
        '<div class="cf-topbar">'
        '<div class="cf-logo">V</div>'
        '<div><div class="cf-word">VECTOR·LAB</div>'
        '<div class="cf-sub">IDX SMART-MONEY FLOW TERMINAL</div></div>'
        '<div style="flex:1"></div>'
        f'<div><span class="cf-livedot" style="background:{shell.TOKENS["armed"]}"></span>'
        "&nbsp; Session gate — sign-in required</div>"
        '<div class="cf-mono">LOCAL · SINGLE-USER · PAPER</div></div>',
        unsafe_allow_html=True,
    )

    hero_col, card_col = st.columns([1.5, 1], gap="large")
    with hero_col:
        st.markdown(shell.login_hero_html(), unsafe_allow_html=True)

    card_heads = {
        lv.CREDENTIALS: ("Operator sign-in", "Use your own Stockbit credentials.", "1"),
        lv.OTP: ("One-time code", "Multi-factor challenge — enter the code to clear the gate.", "2"),
        lv.BEARER: ("Session Bearer", "Fallback — advanced (§10). Verified with a live ping before it is accepted.", "1"),
        lv.FINISH: ("Signed in", "", "2"),
    }
    title, sub, step = card_heads.get(view.state, card_heads[lv.CREDENTIALS])

    with card_col, st.container(key="cflogincard"):
        st.markdown(
            f'<div style="font-size:16px; font-weight:700; color:{shell.TOKENS["text"]}">'
            f"{title}"
            f'<span class="cf-mono" style="float:right; font-size:9.5px; '
            f'color:{shell.TOKENS["text_faint"]}">STEP {step} · 2</span></div>'
            f'<div class="cf-statlabel" style="margin-bottom:10px">{sub}</div>',
            unsafe_allow_html=True,
        )
        if view.error:
            st.error(view.error)

        if view.state == lv.CREDENTIALS:
            with st.form("credentials"):
                user = st.text_input("Username", placeholder="username or email")
                password = st.text_input("Password", type="password", placeholder="········")
                if st.form_submit_button("Sign in", type="primary", use_container_width=True):
                    _set_login_view(_run(ctl.submit_credentials(user, password)))
                    st.rerun()
            st.caption(
                "First sign-in on this machine sends a one-time OTP to trust the "
                "device; after that, login is direct."
            )
            if st.button("Prefer a token? Paste a session Bearer instead →"):
                _set_login_view(lv.LoginView(lv.BEARER))
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
                if st.form_submit_button("Verify", type="primary", use_container_width=True):
                    _set_login_view(_run(ctl.verify_otp(code)))
                    st.rerun()

            # design State B footer: channel choice + resend, disabled during the cooldown
            cooldown = _otp_cooldown()
            options = [c.channel for c in view.channels if c.channel] or ([channel] if channel else [])
            if options:
                left, right = st.columns([1.6, 1])
                pick = left.selectbox(
                    "Resend via", options,
                    index=options.index(channel) if channel in options else 0,
                )
                if right.button("Resend code", disabled=cooldown > 0):
                    _set_login_view(_run(ctl.send_otp(pick)))
                    st.rerun()
                if cooldown > 0:
                    st.caption(f"Resend available in ~{cooldown}s")
            if st.button("← Different account"):
                _set_login_view(lv.LoginView(lv.CREDENTIALS))
                st.rerun()
        elif view.state == lv.BEARER:
            with st.form("bearer"):
                token = st.text_input(
                    "Session Bearer", type="password",
                    placeholder="Bearer eyJhbGciOi… — paste session token",
                )
                st.caption("A leading `Bearer ` prefix is stripped automatically.")
                if st.form_submit_button(
                    "Verify & open terminal", type="primary", use_container_width=True
                ):
                    _set_login_view(
                        _run(lv.submit_bearer(token, store=store, ping=verify_bearer))
                    )
                    st.rerun()
            if st.button("← Use username & password"):
                _set_login_view(lv.LoginView(lv.CREDENTIALS))
                st.rerun()
        elif view.state == lv.FINISH:
            st.session_state["login_view"] = view
            st.success(f"Signed in as {view.username or 'operator'} — loading terminal…")
            st.rerun()

        st.markdown(
            f'<div style="font-size:10px; color:{shell.TOKENS["text_faint"]}; '
            'line-height:1.5; margin-top:6px">On success, access + refresh tokens are '
            "written to the Keychain and the terminal reruns. Credentials and the "
            "one-time code are held only for this attempt — never persisted, rendered "
            "back, or logged.</div>",
            unsafe_allow_html=True,
        )


def _session_info() -> dict | None:
    """Masked session status: `{who, preview, source}` while a valid session exists,
    None when there is no token (→ the login gate). No rendering — the operator head
    and sign-out control render at the top of the watchlist rail (`_render_session_head`)."""
    from currentflow.dal.session import session_status
    from currentflow.dal.token_store import KeychainTokenStore

    st_status = session_status(KeychainTokenStore())
    if not st_status["has_token"]:
        return None
    return {
        "who": st_status.get("username") or "operator",
        "preview": st_status["preview"],
        "source": st_status["source"],
    }


def _render_session_head(info: dict) -> None:
    """Operator identity + sign-out at the top of the ARMED watchlist rail (design:
    masked token above the rail, not in the sidebar). Sign-out clears the local
    session and drops back to the login gate (fail loud — same path as a 401)."""
    from currentflow.dal.token_store import KeychainTokenStore
    from currentflow.ui import login_view as lv

    st.markdown(
        shell.operator_head_html(info["who"], info["preview"], info["source"]),
        unsafe_allow_html=True,
    )
    with st.container(key="cfsignout"):
        if st.button("Sign out", key="cf_signout_btn", use_container_width=True):
            (_login_controller().sign_out() if "login_ctl" in st.session_state
             else KeychainTokenStore().clear())
            st.session_state["login_view"] = lv.LoginView(lv.CREDENTIALS)
            st.rerun()


def _maybe_bootstrap(store: Store) -> None:
    """First authed run with an empty store → auto-resolve the SCR-0 universe and
    ingest 90 days (slice 13), so a fresh machine never lands on empty modules.

    Keyed on store emptiness, not a "just logged in" event — so a session-restored
    launch bootstraps too. One-shot per Streamlit session via `_bootstrap_done`
    (set BEFORE running); a browser refresh mid-run re-fires, but ingest-once makes
    that a cheap resume, never a re-pull. The first run is the app's largest pull —
    ~100–150 names × (one paywall-counted broker call per trading day + paginated
    OHLCV) for 90 days — sequential by design and paced by the shared backoff, so
    expect it to run for a while; per-symbol progress renders as it goes.
    Partial failure degrades to the per-module "run the ingest pipeline first"
    warnings — never a bricked terminal; only AuthError sends the operator back
    to the login form (fail loud, same mechanism as sign-out)."""
    if st.session_state.get("_bootstrap_done"):
        return
    if _symbols(store, "daily_bar"):  # already populated — nothing to bootstrap
        st.session_state["_bootstrap_done"] = True
        return
    st.session_state["_bootstrap_done"] = True

    from currentflow.dal.errors import AuthError
    from currentflow.dal.session import build_live_client
    from currentflow.dal.token_store import KeychainTokenStore
    from currentflow.ingest.bootstrap import DEFAULT_DAYS, bootstrap_ingest
    from currentflow.ui import login_view as lv

    with st.status(
        f"First run — resolving universe (SCR-0) + ingesting {DEFAULT_DAYS} days…",
        expanded=True,
    ) as status:
        bar = st.progress(0.0)

        def on_progress(p) -> None:  # runs synchronously on the script thread
            if p.stage == "screener":
                st.write("Running SCR-0 eligibility screener…")
            elif p.result is None:
                bar.progress(p.index / p.total, text=f"{p.symbol} ({p.index + 1}/{p.total})")
            else:
                r = p.result
                line = f"{r.symbol}: +{r.bars_inserted} bars, +{r.broker_rows_inserted} broker rows"
                if r.coverage.has_gaps:
                    line += f", GAPS on {len(r.coverage.gaps)} day(s)"
                if r.has_imbalance:
                    line += f", BROKER IMBALANCE on {len(r.unclear)} day(s)"
                st.write(line)

        async def _do():
            # No refresher: a 401 fails loud (AuthError) — same as the ingest CLI.
            client, transport = build_live_client()
            try:
                return await bootstrap_ingest(
                    client, store, now=datetime.now(), on_progress=on_progress
                )
            finally:
                await transport.aclose()  # close the pool on the same session loop

        try:
            summary = _run(_do())
        except AuthError as exc:
            status.update(label="Session rejected", state="error")
            st.session_state["_bootstrap_done"] = False  # re-triggers after re-login
            KeychainTokenStore().clear()  # gate goes False → login form (player_id kept)
            st.session_state["login_view"] = lv.LoginView(lv.CREDENTIALS)
            st.error(f"Session rejected during first-run ingest — sign in again: {exc}")
            st.rerun()

        if summary.error is None and summary.eligible:
            bar.progress(1.0, text=f"{len(summary.results)} symbols ingested")
            status.update(label="Initial data ingested", state="complete")

    if summary.error is not None:
        where = summary.failed_symbol or "the SCR-0 screener"
        st.warning(
            f"Initial fetch stopped at {where}: {summary.error}. Kept "
            f"{len(summary.results)}/{len(summary.eligible)} symbols already ingested "
            f"(ingest-once — a retry resumes there). Manual fallback: ./run.sh ingest SYM …"
        )
    elif not summary.eligible:
        st.warning(
            "SCR-0 returned no eligible names — nothing ingested. "
            "Manual fallback: ./run.sh ingest SYM …"
        )
    else:
        st.rerun()  # modules re-render from the now-populated store
        return
    if st.button("Retry initial fetch"):
        st.session_state["_bootstrap_done"] = False
        st.rerun()


def main() -> None:
    configure_logging()  # persist dal `net-error` lines to logs/net.log
    st.set_page_config(page_title="CurrentFlow", layout="wide")
    st.markdown(shell.shell_css(), unsafe_allow_html=True)

    # Auth gate (slice 11): no valid session → the login flow, never blank modules.
    info = _session_info()
    if info is None:
        _render_login()
        st.markdown(shell.ticker_html(), unsafe_allow_html=True)
        return

    store = _store(_db_path())
    ihsg, ihsg_chg = _ihsg(store)
    st.markdown(
        shell.top_bar_html(as_of=_as_of(store), ihsg=ihsg, ihsg_change_pct=ihsg_chg),
        unsafe_allow_html=True,
    )
    _maybe_bootstrap(store)  # slice 13: first-run auto-ingest into an empty store

    module = st.sidebar.radio(
        "Module", MODULES, index=0,
        format_func=lambda k: _MODULE_ICON[k],   # icon is the label…
        captions=[_MODULE_CAPTION[k] for k in MODULES],  # …title stacks beneath it
        label_visibility="collapsed",
    )
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
    # design shell: main module pane + the 296px ARMED-watchlist right rail
    # (operator token + sign-out sit at the top of the rail, above the cards)
    main_col, rail_col = st.columns([2.55, 1], gap="medium")
    with rail_col:
        _render_session_head(info)
        _render_watchlist_rail(store)
    with main_col:
        if module in renderers:
            renderers[module](store)
        else:
            st.subheader(module)
            st.info("Not built yet — lands in a later slice (see PLAN.md).")

    st.markdown(shell.ticker_html(), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
