"""ARMED watchlist rail view-model (design/README.md right rail) — RULE B safe.

The rail shows a state WORD (ARMED/WATCH) and the five design spark-bar component
strengths (DIV BRK FF RVOL BLK). It must never emit the composite SMS number, a rank,
or a buy/sell verb; gate-rejected and vetoed names never appear on it.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from builders import phase_b_bars, strong_phase_c_bars, two_buyer_rows

from currentflow.signals import engine
from currentflow.ui import watchlist_view

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _armed(store, sym="STRONG"):
    store.write_daily_bars(strong_phase_c_bars(sym))
    store.write_broker_net(two_buyer_rows(sym, BDAYS))
    return engine.evaluate(store, sym, TS, track="B")


def _gate_rejected(store, sym="PHB"):
    store.write_daily_bars(phase_b_bars(sym))
    store.write_broker_net(two_buyer_rows(sym, BDAYS))
    return engine.evaluate(store, sym, TS, track="B")


def test_armed_shown_gate_rejected_excluded(store):
    rejected = _gate_rejected(store)
    armed = _armed(store)
    data = watchlist_view.rows([rejected, armed])

    assert [r["symbol"] for r in data["rows"]] == ["STRONG"]
    assert data["rows"][0]["state"] == "ARMED"
    assert data["total"] == 1 and data["dropped"] == 0


def test_rule_b_no_composite_number_rank_or_verb(store):
    res = _armed(store)
    data = watchlist_view.rows([res])
    row = data["rows"][0]

    assert "score" not in row and "position" not in row
    assert row["state"] in ("ARMED", "WATCH")            # a word, never a number
    assert set(row["components"]) == {"DIV", "BRK", "FF", "RVOL", "BLK"}
    flat = repr(data).lower()
    for banned in ("buy", "sell", "probability", "internal_score"):
        assert banned not in flat, f"watchlist leaked {banned!r}"


def test_cap_is_reported_never_silent(store):
    res = _armed(store)
    data = watchlist_view.rows([res], limit=0)
    assert data["rows"] == []
    assert data["total"] == 1 and data["dropped"] == 1


def test_spark_line_marks_unavailable_components():
    line = watchlist_view.spark_line({"components": {"DIV": 72, "BRK": None}})
    assert line == "DIV 72 · BRK —"   # missing ≠ zero
