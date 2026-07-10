"""Track resolver (§3 / LD-1) — the offline watchlist's per-name A/B assignment.

Track A needs BOTH LQ45/IDX80 membership AND ADV ≥ 25 bn; missing membership is B
(never invent A from absent data); and the resolver must reconcile with the gate."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from currentflow.dal.models import SymbolIndexRow
from currentflow.universe import track as track_mod
from currentflow.universe.track import Track, assign_track, resolve_track

# reuse the gate's builders — reconciliation must be against the real gate inputs
from test_universe_gate import mk_history, mk_info, run_gate

NOW = datetime(2026, 7, 1, 9, 0)
SNAP = datetime(2026, 6, 30, 8, 0)   # roster as_of, visible at NOW

HI = 30e9   # ADV above the 25 bn Track-A floor
LO = 20e9   # ADV below it


def _seed(store, symbol: str, indexes: tuple[str, ...]) -> None:
    store.write_symbol_index([SymbolIndexRow(symbol=symbol, as_of=SNAP, indexes=indexes)])


# --- the pure rule ------------------------------------------------------------------


def test_assign_track_needs_membership_and_adv():
    assert assign_track(("LQ45",), HI) is Track.A
    assert assign_track(("IDX80",), HI) is Track.A
    assert assign_track(("LQ45",), LO) is Track.B      # member but ADV too thin
    assert assign_track(("IDXSMC-LIQ",), HI) is Track.B  # liquid but not an A index
    assert assign_track((), HI) is Track.B
    assert assign_track(("LQ45",), None) is Track.B    # unknown ADV never arms A


# --- resolver over the stored roster ------------------------------------------------


def test_resolver_a_for_member_with_liquidity(store):
    _seed(store, "TEST", ("LQ45",))
    assert resolve_track(store, "TEST", NOW, mk_history(value=HI)) == "A"


def test_resolver_b_for_member_below_adv_floor(store):
    _seed(store, "TEST", ("LQ45",))
    assert resolve_track(store, "TEST", NOW, mk_history(value=LO)) == "B"


def test_resolver_b_for_non_member(store):
    _seed(store, "TEST", ("IDXSMC-LIQ",))
    assert resolve_track(store, "TEST", NOW, mk_history(value=HI)) == "B"


def test_resolver_defaults_to_b_when_no_roster_row(store):
    # missing ≠ zero: a large-cap with no stored membership must NOT be scored as A
    assert resolve_track(store, "UNKNOWN", NOW, mk_history(value=HI)) == "B"


def test_resolver_ignores_future_only_membership(store):
    # roster stamped after the decision is invisible → falls back to B
    store.write_symbol_index([SymbolIndexRow("TEST", datetime(2026, 7, 5), ("LQ45",))])
    assert resolve_track(store, "TEST", NOW, mk_history(value=HI)) == "B"


# --- reconciliation with the universe gate ------------------------------------------


def test_resolver_reconciles_with_gate_track(store):
    """Same membership + bars → resolver and gate must assign the identical track.

    Distinct symbols per case: the roster is keyed (symbol, as_of) and ingest-once, so
    reusing one symbol+as_of would keep the first write and read stale membership."""
    cases = [("AAAA", ("LQ45",), HI), ("BBBB", ("LQ45",), LO), ("CCCC", ("IDXSMC-LIQ",), HI)]
    for symbol, indexes, value in cases:
        _seed(store, symbol, indexes)
        bars = mk_history(value=value)
        gate = run_gate(bars=bars, info=mk_info(indexes=indexes))
        assert gate.passed and gate.track is not None
        assert resolve_track(store, symbol, NOW, bars) == gate.track.value
