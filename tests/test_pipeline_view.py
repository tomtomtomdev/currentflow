"""Signal Pipeline view-model (design v2) — the sole top-level screen.

Track A / Track B lanes; each candidate row carries all four locked stages
(gate → phase → sig → veto) and a verdict. A candidate stops at the first failing
stage — downstream cells are NOT EVALUATED. RULE A: a non-C/D name is rejected at
the phase cell. RULE B: the signal-components cell shows a categorical pass/low and
component observations, never the composite SMS number.

adv20 is injected per candidate (the view-model is pure over it); the builder charts
carry token volumes, so the real ADV floor is exercised here with explicit values.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from builders import (
    distribution_bars,
    phase_b_bars,
    phase_c_bars,
    strong_phase_c_bars,
    two_buyer_rows,
)

from currentflow.signals import engine
from currentflow.signals.engine import EngineState
from currentflow.ui import pipeline_view

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]
ABOVE_FLOOR = 38e9   # ≥ IDR 10 bn hard floor
BELOW_FLOOR = 8.2e9  # < IDR 10 bn hard floor


def _cand(result, *, name="Name", price=100.0, chg=0.5, adv20=ABOVE_FLOOR, sector="Energy"):
    return {"result": result, "name": name, "price": price, "chg": chg,
            "adv20": adv20, "sector": sector}


def _armed(store, sym="STRONG", track="B"):
    store.write_daily_bars(strong_phase_c_bars(sym))
    store.write_broker_net(two_buyer_rows(sym, BDAYS))
    return engine.evaluate(store, sym, TS, track=track)


def _phase_rejected(store, sym="PHB"):
    store.write_daily_bars(phase_b_bars(sym))
    store.write_broker_net(two_buyer_rows(sym, BDAYS))
    return engine.evaluate(store, sym, TS, track="B")


def _cells(row):
    return {c["stage"]: c for c in row["cells"]}


# --- lane grouping ------------------------------------------------------------------

def test_two_lanes_group_by_track(store):
    a = _armed(store, "AAA", track="A")
    b = _armed(store, "BBB", track="B")
    lanes = pipeline_view.build_lanes([_cand(a), _cand(b)])

    assert [ln["track"] for ln in lanes] == ["A", "B"]
    assert [r["ticker"] for r in lanes[0]["rows"]] == ["AAA"]
    assert [r["ticker"] for r in lanes[1]["rows"]] == ["BBB"]
    # both lanes always rendered even when one is empty
    lanes_b_only = pipeline_view.build_lanes([_cand(b)])
    assert lanes_b_only[0]["rows"] == [] and len(lanes_b_only[1]["rows"]) == 1


def test_count_string_reports_verdict_mix(store):
    armed = _armed(store, "AR")
    rej = _phase_rejected(store, "RJ")
    lanes = pipeline_view.build_lanes([_cand(armed), _cand(rej)])
    b_lane = next(ln for ln in lanes if ln["track"] == "B")
    assert b_lane["count_str"] == "1 armed · 0 watch · 1 rejected"


# --- RULE A: phase gate rejects non-C/D, skips downstream ---------------------------

def test_phase_reject_skips_signal_and_veto(store):
    for bars_fn, sym in ((phase_b_bars, "PB"), (distribution_bars, "DS")):
        store.write_daily_bars(bars_fn(sym))
        store.write_broker_net(two_buyer_rows(sym, BDAYS))
        res = engine.evaluate(store, sym, TS, track="B")
        row = pipeline_view.build_lanes([_cand(res)])[1]["rows"][0]
        cells = _cells(row)

        assert row["result"] == pipeline_view.REJECTED
        assert cells["gate"]["state"] == pipeline_view.PASS
        assert cells["phase"]["state"] == pipeline_view.FAIL
        assert cells["sig"]["state"] == pipeline_view.SKIP
        assert cells["veto"]["state"] == pipeline_view.SKIP
        assert "RULE A" in row["note"]


# --- §3 gate: sub-floor ADV rejects before phase is ever read -----------------------

def test_gate_below_floor_rejects_and_skips_all_downstream(store):
    armed = _armed(store)  # would otherwise be ARMED…
    row = pipeline_view.build_lanes([_cand(armed, adv20=BELOW_FLOOR)])[1]["rows"][0]
    cells = _cells(row)

    assert row["result"] == pipeline_view.REJECTED
    assert cells["gate"]["state"] == pipeline_view.FAIL
    assert cells["phase"]["state"] == pipeline_view.SKIP
    assert cells["sig"]["state"] == pipeline_view.SKIP
    assert cells["veto"]["state"] == pipeline_view.SKIP
    assert "hard floor" in cells["gate"]["reason"]


def test_gate_missing_adv_is_low_not_a_reject(store):
    armed = _armed(store)
    row = pipeline_view.build_lanes([_cand(armed, adv20=None)])[1]["rows"][0]
    cells = _cells(row)
    # missing ≠ zero: floor unconfirmed, so the gate is caution (low), not a fail,
    # and the phase stage is still evaluated
    assert cells["gate"]["state"] == pipeline_view.LOW
    assert cells["phase"]["state"] != pipeline_view.SKIP


# --- ARMED: every stage passes ------------------------------------------------------

def test_armed_passes_every_stage(store):
    armed = _armed(store)
    assert armed.state is EngineState.ARMED  # guards the fixture
    row = pipeline_view.build_lanes([_cand(armed)])[1]["rows"][0]
    cells = _cells(row)

    assert row["result"] == pipeline_view.ARMED
    assert all(cells[s]["state"] == pipeline_view.PASS for s in pipeline_view.STAGES)
    assert cells["phase"]["tag"] in ("PHASE C", "PHASE D")


# --- RULE B: no composite SMS number / probability / verb leaks ---------------------

def test_rule_b_signal_cell_withholds_the_number(store):
    armed = _armed(store)
    score = round(armed.sms.internal_score)
    lanes = pipeline_view.build_lanes([_cand(armed)])
    flat = repr(lanes).lower()

    assert "internal_score" not in flat
    for banned in ("probability", "buy these", "sell these", "•••"):
        assert banned not in flat, f"pipeline leaked {banned!r}"
    # the composite value itself must not appear in the signal cell text
    sig = _cells(lanes[1]["rows"][0])["sig"]
    assert str(score) not in sig["tag"] and str(score) not in sig["reason"]
    assert sig["state"] in (pipeline_view.PASS, pipeline_view.LOW)  # a state, not a number
