"""End-to-end ingest vertical: fetch-if-missing → store → coverage, ingest-once."""

from __future__ import annotations

from datetime import date, datetime

from currentflow.dal.client import ExodusClient
from currentflow.ingest.pipeline import ingest_symbol, refresh_membership
from tests.conftest import broker_payload, ohlcv_payload, scripted_transport


async def test_refresh_membership_snapshots_indexes(store):
    """§3 Track source: emitten/{sym}/info → a stored membership snapshot (not a gate)."""
    calls: list = []
    client = ExodusClient(scripted_transport(
        [(200, {"data": {"status": "active", "indexes": [{"name": "LQ45"}, {"name": "IDX80"}]}})],
        calls,
    ))

    inserted = await refresh_membership(client, store, "BBCA", now=datetime(2026, 6, 4, 9, 0))

    assert inserted == 1
    assert calls[0][0] == "emitten/BBCA/info"
    row = store.read_symbol_index_latest("BBCA", datetime(2030, 1, 1))
    assert row is not None and row.indexes == ("LQ45", "IDX80")


def _ohlcv_rows(days: list[int]) -> list[dict]:
    return [
        {
            "date": f"2026-06-{d:02d}", "open": 100, "high": 101, "low": 99,
            "close": 100 + d, "volume": 1000, "value": 100000, "frequency": 10,
            "average": 100.0, "foreign_buy": 1, "foreign_sell": 0, "net_foreign": 1,
            "change_percentage": 0.1,
        }
        for d in days
    ]


async def test_ingest_stores_and_reports_coverage(store):
    # Mon 2026-06-01 .. Wed 2026-06-03 traded; nothing else in range.
    # Broker rows are fetched one call per missing day (server aggregates ranges),
    # then bars land last (the ingest-once commit marker).
    transport = scripted_transport([
        (200, broker_payload(
            buys=[{"netbs_broker_code": "YP", "type": "Asing", "bval": 1, "blot": 1,
                   "netbs_date": "2026-06-01"}],
            sells=[],
            data_last_updated="2026-06-01T17:30:00",
        )),
        (200, broker_payload([], [])),
        (200, broker_payload([], [])),
        (200, ohlcv_payload(_ohlcv_rows([1, 2, 3]))),
    ])
    client = ExodusClient(transport)

    result = await ingest_symbol(
        client, store, "BBCA",
        date(2026, 6, 1), date(2026, 6, 3),
        now=datetime(2026, 6, 4, 9, 0),
    )
    assert result.bars_inserted == 3
    assert result.broker_rows_inserted == 1
    assert result.days_skipped_cached == 0
    assert result.coverage.traded == [date(2026, 6, d) for d in (1, 2, 3)]
    assert not result.coverage.has_gaps


async def test_second_ingest_skips_cached_and_fetches_nothing(store):
    first = scripted_transport([
        (200, broker_payload([], [])),
        (200, broker_payload([], [])),
        (200, broker_payload([], [])),
        (200, ohlcv_payload(_ohlcv_rows([1, 2, 3]))),
    ])
    await ingest_symbol(
        ExodusClient(first), store, "BBCA",
        date(2026, 6, 1), date(2026, 6, 3), now=datetime(2026, 6, 4, 9, 0),
    )

    # Second run over the same range: everything cached → transport must NOT be called.
    calls: list = []
    second = scripted_transport([], calls)  # empty script: any call would StopIteration
    result = await ingest_symbol(
        ExodusClient(second), store, "BBCA",
        date(2026, 6, 1), date(2026, 6, 3), now=datetime(2026, 6, 4, 9, 0),
    )
    assert calls == []  # ingest-once: nothing re-pulled
    assert result.bars_inserted == 0
    assert result.days_skipped_cached == 3


async def test_ingest_catches_truncated_broker_feed_per_day(store):
    # A truncated (top-N) broker day: buy side present, sell side dropped → the
    # gross buy/sell conservation breaks. This must be caught AT INGEST (RULE:
    # missing data is never zero flow), not left to surface as a wrong number on
    # screen (the AK@MEDC regression).
    transport = scripted_transport([
        (200, broker_payload(  # 06-01: balanced, clears
            buys=[{"netbs_broker_code": "YP", "type": "Asing", "bval": 500_000,
                   "netbs_date": "2026-06-01"}],
            sells=[{"netbs_broker_code": "CC", "type": "Asing", "sval": -500_000,
                    "netbs_date": "2026-06-01"}],
            data_last_updated="2026-06-01T17:30:00",
        )),
        (200, broker_payload(  # 06-02: truncated — sell side missing, does NOT clear
            buys=[{"netbs_broker_code": "YP", "type": "Asing", "bval": 900_000,
                   "netbs_date": "2026-06-02"}],
            sells=[],
            data_last_updated="2026-06-02T17:30:00",
        )),
        (200, ohlcv_payload(_ohlcv_rows([1, 2]))),
    ])
    client = ExodusClient(transport)

    result = await ingest_symbol(
        client, store, "MEDC",
        date(2026, 6, 1), date(2026, 6, 2),
        now=datetime(2026, 6, 3, 9, 0),
    )

    assert result.has_imbalance
    unclear = {c.date: c for c in result.unclear}
    assert list(unclear) == [date(2026, 6, 2)]  # only the truncated day flagged
    assert not unclear[date(2026, 6, 2)].clears
