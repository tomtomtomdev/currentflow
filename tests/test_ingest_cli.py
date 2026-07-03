"""The ingest CLI (`python -m currentflow.ingest`) — the runnable entry point behind
the terminal's 'run the ingest pipeline first' warning. Driven with a scripted
transport + in-memory store via injectable factories, so no network / Keychain."""

from __future__ import annotations

from datetime import date, datetime

from currentflow.dal.client import ExodusClient
from currentflow.dal.errors import AuthError
from currentflow.ingest.__main__ import _resolve_range, main
from currentflow.store.db import Store
from tests.conftest import broker_payload, ohlcv_payload, scripted_transport


def _bars(days: list[int]) -> list[dict]:
    return [
        {
            "date": f"2026-06-{d:02d}", "open": 100, "high": 101, "low": 99,
            "close": 100 + d, "volume": 1000, "value": 100000, "frequency": 10,
            "average": 100.0, "foreign_buy": 1, "foreign_sell": 0, "net_foreign": 1,
            "change_percentage": 0.1,
        }
        for d in days
    ]


class _NoopTransport:
    """Stands in for HttpxTransport — the client is transport-injected, so this only
    needs the `aclose()` the CLI calls in its finally block."""

    async def aclose(self) -> None:
        return None


def _client_factory(steps: list):
    """A factory the CLI can call to get (client, transport): the client is backed by
    a one-shot scripted transport, the transport is the no-op stub."""

    def factory():
        return ExodusClient(scripted_transport(steps)), _NoopTransport()

    return factory


# --- date-range resolution --------------------------------------------------------


def test_resolve_range_explicit():
    start, end = _resolve_range("2026-04-01", "2026-07-03", days=90)
    assert start == date(2026, 4, 1) and end == date(2026, 7, 3)


def test_resolve_range_days_lookback():
    start, end = _resolve_range(None, "2026-07-03", days=10)
    assert end == date(2026, 7, 3) and start == date(2026, 6, 23)


# --- happy path: data lands in the store the terminal reads -----------------------


def test_cli_ingests_into_the_store_the_terminal_reads(monkeypatch):
    store = Store(":memory:")
    # Keep the CLI's finally from closing our in-memory store before we assert on it.
    monkeypatch.setattr(store, "close", lambda: None)
    # One broker call per missing day (server aggregates ranges), then bars last.
    steps = [
        (200, broker_payload(
            buys=[{"netbs_broker_code": "YP", "type": "Asing", "bval": 1, "blot": 1,
                   "netbs_date": "2026-06-01"}],
            sells=[],
            data_last_updated="2026-06-01T17:30:00",
        )),
        (200, broker_payload([], [])),
        (200, broker_payload([], [])),
        (200, ohlcv_payload(_bars([1, 2, 3]))),
    ]

    rc = main(
        ["BBCA", "--from", "2026-06-01", "--to", "2026-06-03"],
        client_factory=_client_factory(steps),
        store_factory=lambda _p: store,
    )

    assert rc == 0
    bars = store.read_daily_bars(
        "BBCA", decision_ts=datetime(2026, 6, 4, 9, 0),
        start=date(2026, 6, 1), end=date(2026, 6, 3),
    )
    assert len(bars) == 3  # the "run the ingest pipeline first" warning would now clear
    Store.close(store)


def test_cli_reports_inserts_and_upcases_symbol(capsys):
    store = Store(":memory:")
    steps = [
        (200, broker_payload(buys=[], sells=[])),
        (200, broker_payload(buys=[], sells=[])),
        (200, broker_payload(buys=[], sells=[])),
        (200, ohlcv_payload(_bars([1, 2, 3]))),
    ]

    rc = main(
        ["bbca", "--from", "2026-06-01", "--to", "2026-06-03"],
        client_factory=_client_factory(steps),
        store_factory=lambda _p: store,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "BBCA: +3 bars" in out  # upcased + reported (no silent inserts)


# --- failure modes: fail loud, non-zero exit --------------------------------------


def test_cli_bad_date_returns_1(capsys):
    assert main(["BBCA", "--from", "not-a-date"]) == 1
    assert "bad date" in capsys.readouterr().err


def test_cli_inverted_range_returns_1(capsys):
    assert main(["BBCA", "--from", "2026-07-03", "--to", "2026-06-01"]) == 1
    assert "empty range" in capsys.readouterr().err


def test_cli_401_fails_loud(capsys):
    store = Store(":memory:")
    monkeypatch_close = lambda: None
    store.close = monkeypatch_close
    # A 401 makes ExodusClient raise AuthError (fail loud, no retry) — the CLI must
    # translate that to a non-zero exit with a re-login hint, never a stale/empty store.
    rc = main(
        ["BBCA", "--from", "2026-06-01", "--to", "2026-06-03"],
        client_factory=_client_factory([401]),
        store_factory=lambda _p: store,
    )
    assert rc == 1
    assert "AUTH FAILED" in capsys.readouterr().err
    Store.close(store)
