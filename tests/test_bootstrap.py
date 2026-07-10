"""First-run bootstrap (slice 13) — SCR-0 universe resolution → initial ingest,
progress events, partial-failure capture, AuthError fail-loud. Scripted transports
+ in-memory store; no network, no Streamlit."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

import pytest

from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import AuthError, TransportError
from currentflow.ingest.bootstrap import (
    BootstrapProgress,
    bootstrap_ingest,
    last_weekday,
)
from tests.conftest import broker_payload, ohlcv_payload, scripted_transport

# Friday 2026-07-03 — bootstrap runs "now", so the fixture dates track it.
NOW = datetime(2026, 7, 3, 19, 0)
# days=1 keeps the scripted transports small: missing weekdays = Thu 07-02 + Fri
# 07-03 → per symbol [broker, broker, ohlcv] (broker per day first, bars last).
DAYS = 1


def _scr0_payload(symbols: list[str]) -> dict:
    return {
        "data": {
            "calcs": [
                {"symbol": s, "results": [{"item": 2661, "raw": 4500}]}
                for s in symbols
            ]
        }
    }


def _info_payload(indexes: list[str]) -> dict:
    return {"data": {"status": "active", "indexes": [{"name": n} for n in indexes]}}


def _bars(symbol_day: int) -> list[dict]:
    return [
        {
            "date": "2026-07-03", "open": 100, "high": 101, "low": 99,
            "close": 100 + symbol_day, "volume": 1000, "value": 100000,
            "frequency": 10, "average": 100.0, "foreign_buy": 1,
            "foreign_sell": 0, "net_foreign": 1, "change_percentage": 0.1,
        }
    ]


def _client(
    get_steps: list,
    post_steps: list,
    get_calls: list | None = None,
    max_retries: int | None = None,
):
    """`max_retries=0` makes a scripted transient failure (network/5xx) exhaust
    immediately instead of entering the client's real backoff loop."""
    kwargs: dict = {} if max_retries is None else {"max_retries": max_retries}
    return ExodusClient(
        scripted_transport(get_steps, get_calls),
        post_transport=scripted_transport(post_steps),
        **kwargs,
    )


# --- trading-day proxy --------------------------------------------------------------


def test_last_weekday():
    assert last_weekday(Date(2026, 7, 1)) == Date(2026, 7, 1)  # Wed → Wed
    assert last_weekday(Date(2026, 7, 4)) == Date(2026, 7, 3)  # Sat → Fri
    assert last_weekday(Date(2026, 7, 5)) == Date(2026, 7, 3)  # Sun → Fri


# --- happy path ---------------------------------------------------------------------


async def test_bootstrap_happy_path(store):
    client = _client(
        get_steps=[
            (200, broker_payload([], [])), (200, broker_payload([], [])),
            (200, ohlcv_payload(_bars(1))),  # BBRI
            (200, broker_payload([], [])), (200, broker_payload([], [])),
            (200, ohlcv_payload(_bars(2))),  # BRMS
            # membership phase runs after all ingests: one emitten/{sym}/info per name
            (200, _info_payload(["LQ45", "IDX80"])),  # BBRI → Track A eligible
            (200, _info_payload(["IDXSMC-LIQ"])),      # BRMS → Track B
        ],
        post_steps=[(200, _scr0_payload(["BBRI", "BRMS"]))],
    )

    summary = await bootstrap_ingest(client, store, now=NOW, days=DAYS)

    assert summary.eligible == ["BBRI", "BRMS"]
    assert [r.symbol for r in summary.results] == ["BBRI", "BRMS"]
    assert summary.error is None and summary.failed_symbol is None
    assert summary.end == NOW.date()
    assert summary.start == NOW.date() - timedelta(days=DAYS)
    assert summary.trading_day == Date(2026, 7, 3)

    # Data landed in the store the terminal reads (bars + scr0 cache).
    later = NOW + timedelta(minutes=1)
    for sym in ("BBRI", "BRMS"):
        bars = store.read_daily_bars(
            sym, decision_ts=later, start=summary.start, end=summary.end
        )
        assert len(bars) == 1
    cached = store.read_scr0_eligible(summary.trading_day, decision_ts=later)
    assert [r.symbol for r in cached] == ["BBRI", "BRMS"]

    # Index-membership roster landed (§3 Track source for the offline watchlist).
    assert store.read_symbol_index_latest("BBRI", later).indexes == ("LQ45", "IDX80")
    assert store.read_symbol_index_latest("BRMS", later).indexes == ("IDXSMC-LIQ",)


async def test_bootstrap_progress_callbacks(store):
    client = _client(
        get_steps=[
            (200, broker_payload([], [])), (200, broker_payload([], [])),
            (200, ohlcv_payload(_bars(1))),
            (200, broker_payload([], [])), (200, broker_payload([], [])),
            (200, ohlcv_payload(_bars(2))),
            (200, _info_payload(["LQ45"])), (200, _info_payload([])),  # membership phase
        ],
        post_steps=[(200, _scr0_payload(["BBRI", "BRMS"]))],
    )
    events: list[BootstrapProgress] = []

    await bootstrap_ingest(client, store, now=NOW, days=DAYS, on_progress=events.append)

    assert (events[0].stage, events[0].index, events[0].total) == ("screener", 0, 0)
    ingest_events = [e for e in events if e.stage == "ingest"]
    # pre/post pair per symbol, in order, post carrying the result
    assert [(e.index, e.symbol, e.result is None) for e in ingest_events] == [
        (0, "BBRI", True), (0, "BBRI", False),
        (1, "BRMS", True), (1, "BRMS", False),
    ]
    assert all(e.total == 2 for e in ingest_events)
    assert ingest_events[1].result.bars_inserted == 1


# --- empty universe -----------------------------------------------------------------


async def test_bootstrap_empty_scr0(store):
    get_calls: list = []
    client = _client(
        get_steps=[], post_steps=[(200, {"data": {"calcs": []}})], get_calls=get_calls
    )

    summary = await bootstrap_ingest(client, store, now=NOW, days=DAYS)

    assert summary.eligible == [] and summary.results == []
    assert summary.error is None
    assert get_calls == []  # nothing fetched for an empty universe


# --- failure modes ------------------------------------------------------------------


async def test_bootstrap_partial_failure_keeps_ingested(store):
    client = _client(
        get_steps=[
            (200, broker_payload([], [])), (200, broker_payload([], [])),
            (200, ohlcv_payload(_bars(1))),  # BBRI ok
            TransportError("boom"),  # BRMS dies on its first fetch
        ],
        post_steps=[(200, _scr0_payload(["BBRI", "BRMS"]))],
        max_retries=0,
    )

    summary = await bootstrap_ingest(client, store, now=NOW, days=DAYS)  # must not raise

    assert [r.symbol for r in summary.results] == ["BBRI"]
    assert summary.failed_symbol == "BRMS"
    assert summary.error is not None
    assert summary.eligible == ["BBRI", "BRMS"]
    # BBRI's data survived the partial run (ingest-once: a retry resumes at BRMS).
    bars = store.read_daily_bars(
        "BBRI", decision_ts=NOW + timedelta(minutes=1),
        start=summary.start, end=summary.end,
    )
    assert len(bars) == 1


async def test_bootstrap_screener_failure_captured(store):
    client = _client(
        get_steps=[], post_steps=[TransportError("screener down")], max_retries=0
    )

    summary = await bootstrap_ingest(client, store, now=NOW, days=DAYS)

    assert summary.eligible == [] and summary.results == []
    assert summary.failed_symbol is None
    assert summary.error is not None


async def test_bootstrap_auth_error_propagates(store):
    # 401 on the screener itself → fail loud (operator must re-login).
    client = _client(get_steps=[], post_steps=[401])
    with pytest.raises(AuthError):
        await bootstrap_ingest(client, store, now=NOW, days=DAYS)

    # 401 mid-universe (on a symbol fetch) → also fail loud, never captured.
    client = _client(get_steps=[401], post_steps=[(200, _scr0_payload(["BBRI"]))])
    with pytest.raises(AuthError):
        await bootstrap_ingest(client, store, now=NOW, days=DAYS)
