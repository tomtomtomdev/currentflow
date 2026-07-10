"""Index-membership roster (§3 Track source) — store round-trip + look-ahead firewall."""

from __future__ import annotations

from datetime import datetime

from currentflow.dal.models import SymbolIndexRow


def _row(sym: str, as_of: datetime, indexes: tuple[str, ...]) -> SymbolIndexRow:
    return SymbolIndexRow(symbol=sym, as_of=as_of, indexes=indexes)


def test_round_trip_splits_indexes(store):
    store.write_symbol_index([_row("BBCA", datetime(2026, 7, 1, 8), ("LQ45", "IDX80"))])
    got = store.read_symbol_index_latest("BBCA", datetime(2026, 7, 2))
    assert got is not None
    assert got.indexes == ("LQ45", "IDX80")   # comma-join → split, order preserved


def test_empty_membership_round_trips_to_empty_tuple(store):
    store.write_symbol_index([_row("XXXX", datetime(2026, 7, 1, 8), ())])
    got = store.read_symbol_index_latest("XXXX", datetime(2026, 7, 2))
    assert got is not None and got.indexes == ()   # "" never becomes ("",)


def test_read_latest_respects_as_of_firewall(store):
    # a snapshot stamped in the future must be invisible at an earlier decision_ts
    store.write_symbol_index([_row("BBRI", datetime(2026, 7, 5, 8), ("LQ45",))])
    assert store.read_symbol_index_latest("BBRI", datetime(2026, 7, 1)) is None
    assert store.read_symbol_index_latest("BBRI", datetime(2026, 7, 6)) is not None


def test_read_latest_picks_newest_visible_snapshot(store):
    store.write_symbol_index([
        _row("TLKM", datetime(2026, 4, 1, 8), ("IDX80",)),        # dropped from LQ45
        _row("TLKM", datetime(2026, 7, 1, 8), ("LQ45", "IDX80")),  # reconstitution: back in
    ])
    got = store.read_symbol_index_latest("TLKM", datetime(2026, 7, 2))
    assert got.indexes == ("LQ45", "IDX80") and got.as_of == datetime(2026, 7, 1, 8)


def test_missing_symbol_returns_none(store):
    assert store.read_symbol_index_latest("NONE", datetime(2030, 1, 1)) is None
