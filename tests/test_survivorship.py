"""Slice 22 (BACKTEST_PHASE0 §3.1 / E1) — LISTED roster + survivorship-bias disclosure.

The acceptance criteria this file encodes (BACKTEST_PHASE0 §6):
  * **Survivorship:** a name delisted in 2025 is present in the reconstructed universe
    on a 2024 day and gone on a 2026 day — the roster, not the store, decides that.
  * **Bias disclosure:** the unrecoverable-name count (and its market-cap share where
    the books supply caps) is reported, never silently dropped.
  * **Missing ≠ zero:** with no LISTED roster loaded, bias is UNMEASURED — never 0%.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta

import pytest

from builders import Chart, brow
from currentflow.dal.models import Side
from currentflow.universe import listing, survivorship
from currentflow.universe.listing import ListingValidationError
from currentflow.universe.pit import pit_universe
from currentflow.universe.roster import RosterValidationError, load_rosters, validate_periods
from currentflow.universe.track import resolve_track_pit

NOW = datetime(2026, 8, 9, 9, 0)

HEADER = "snapshot_date,symbol,source,market_cap_idr"


def _csv(*rows: str) -> str:
    return "\n".join((HEADER, *rows)) + "\n"


def _rows(text: str, filename: str = "idx.csv"):
    return listing.parse_snapshots(text, now=NOW, filename=filename)


# --- derivation: annual snapshots -> LISTED periods -----------------------------------


def test_still_listed_name_gets_an_open_period():
    rows = _rows(_csv(
        "2024-12-31,BBCA,IDX Statistics 2024,1000",
        "2025-12-31,BBCA,IDX Statistics 2025,1100",
    ))
    periods, report = listing.derive_periods(rows)
    assert len(periods) == 1
    p = periods[0]
    assert p.index_name == listing.LISTED_INDEX
    assert p.effective_from == Date(2024, 12, 31)
    assert p.effective_to is None            # still in the newest book → open period
    assert report.open_periods == 1 and report.closed_periods == 0


def test_delisted_name_closes_at_last_observation_and_names_its_precision():
    rows = _rows(_csv(
        "2024-12-31,GONE,IDX Statistics 2024,500",
        "2024-12-31,STAY,IDX Statistics 2024,900",
        "2025-12-31,STAY,IDX Statistics 2025,950",
    ))
    periods, report = listing.derive_periods(rows)
    gone = next(p for p in periods if p.symbol == "GONE")
    assert gone.effective_to == Date(2024, 12, 31)   # last book that still listed it
    assert report.closed_periods == 1
    # The delisting date is only known to within a snapshot interval — say so, in the
    # provenance itself (annual books cannot date a delisting to the day).
    assert "2025-12-31" in gone.source               # the far end of the uncertainty window
    assert listing.ANNUAL_PRECISION in gone.source
    assert "IDX Statistics 2024" in gone.source      # original provenance preserved


def test_relisting_yields_two_disjoint_periods_that_validate():
    rows = _rows(_csv(
        "2023-12-31,BACK,book 2023,100",
        "2024-12-31,OTHER,book 2024,100",          # BACK absent in 2024 → delisted
        "2025-12-31,BACK,book 2025,120",           # ... and relisted in 2025
        "2025-12-31,OTHER,book 2025,100",
    ))
    periods, report = listing.derive_periods(rows)
    back = sorted((p for p in periods if p.symbol == "BACK"), key=lambda p: p.effective_from)
    assert len(back) == 2 and report.relistings == 1
    assert back[0].effective_to == Date(2023, 12, 31)
    assert back[1].effective_from == Date(2025, 12, 31) and back[1].effective_to is None
    validate_periods(periods)                       # disjoint — the slice-20 rule holds


def test_transcribed_listing_date_gives_an_exact_start():
    rows = listing.parse_snapshots(
        "snapshot_date,symbol,source,listing_date\n"
        "2024-12-31,OLD,book 2024,2009-01-20\n",
        now=NOW,
    )
    periods, report = listing.derive_periods(rows)
    assert periods[0].effective_from == Date(2009, 1, 20)   # listed on, not first observed
    assert report.first_observed_starts == 0
    assert "first observed" not in periods[0].source


def test_relisting_start_is_clamped_when_the_book_reprints_the_original_listing_date():
    """IDX books print the *original* listing date, which would reach back through the
    delisted gap — the second period must not swallow the years the name was gone."""
    rows = listing.parse_snapshots(
        "snapshot_date,symbol,source,listing_date\n"
        "2023-12-31,BACK,book 2023,2005-06-01\n"
        "2024-12-31,OTHER,book 2024,2005-06-01\n"
        "2025-12-31,BACK,book 2025,2005-06-01\n"
        "2025-12-31,OTHER,book 2025,2005-06-01\n",
        now=NOW,
    )
    periods, report = listing.derive_periods(rows)
    back = sorted((p for p in periods if p.symbol == "BACK"), key=lambda p: p.effective_from)
    assert back[0].effective_to == Date(2023, 12, 31)
    assert back[1].effective_from == Date(2024, 1, 1)       # day after the prior period
    assert report.clamped_relistings == 1
    assert "clamped" in back[1].source
    validate_periods(periods)


def test_listing_date_after_the_book_is_rejected():
    with pytest.raises(ListingValidationError, match="after the book"):
        listing.parse_snapshots(
            "snapshot_date,symbol,source,listing_date\n"
            "2024-12-31,X,book 2024,2025-03-01\n",
            now=NOW,
        )


def test_snapshot_without_source_is_rejected():
    with pytest.raises(ListingValidationError, match="source"):
        _rows(_csv("2024-12-31,BBCA,,1000"))


def test_bad_header_and_bad_date_are_rejected():
    with pytest.raises(ListingValidationError, match="header"):
        listing.parse_snapshots("symbol,source\nBBCA,book\n", now=NOW)
    with pytest.raises(ListingValidationError, match="date"):
        _rows(_csv("31-12-2024,BBCA,book,1000"))


def test_market_cap_is_optional_and_blank_is_unknown_not_zero():
    rows = _rows(_csv(
        "2024-12-31,NOCAP,book 2024,",
        "2024-12-31,HASCAP,book 2024,1000",
    ))
    caps = {r.symbol: r.market_cap_idr for r in rows}
    assert caps["NOCAP"] is None          # blank → unknown, never 0.0
    assert caps["HASCAP"] == 1000.0
    _, report = listing.derive_periods(rows)
    assert report.unknown_market_cap == 1

    # the whole optional column may be absent from the header too
    bare = listing.parse_snapshots(
        "snapshot_date,symbol,source\n2024-12-31,X,book\n", now=NOW
    )
    assert bare[0].market_cap_idr is None


# --- load path -----------------------------------------------------------------------


def _write_listings(tmp_path, text: str):
    d = tmp_path / "listings"
    d.mkdir()
    (d / "idx_statistics.csv").write_text(text)
    return d


def test_load_writes_roster_periods_and_snapshot_rows(tmp_path, store):
    d = _write_listings(tmp_path, _csv(
        "2024-12-31,GONE,book 2024,500",
        "2024-12-31,STAY,book 2024,900",
        "2025-12-31,STAY,book 2025,950",
    ))
    rep = listing.load_listings(store, d, now=NOW)
    assert rep.roster_rows == 2 and rep.snapshot_rows == 3

    assert set(store.read_roster_members(listing.LISTED_INDEX, Date(2025, 3, 2))) == {"STAY"}
    assert set(store.read_roster_members(listing.LISTED_INDEX, Date(2024, 12, 31))) == {
        "GONE", "STAY"
    }
    # newest book on/before the day — 2026 reads the 2025 book, mid-2025 still the 2024 one
    assert store.read_listing_snapshot(Date(2026, 6, 1)) == {"STAY": 950.0}
    assert store.read_listing_snapshot(Date(2025, 6, 1)) == {"GONE": 500.0, "STAY": 900.0}

    # ingest-once: a second load writes nothing new
    again = listing.load_listings(store, d, now=NOW)
    assert again.roster_rows == 0


def test_load_of_a_missing_dir_fails_loud(tmp_path, store):
    with pytest.raises(ListingValidationError, match="not found"):
        listing.load_listings(store, tmp_path / "nope", now=NOW)


# --- the acceptance test: a delisted name lives in the past universe -------------------


def test_delisted_name_is_in_the_2024_universe_and_gone_later(store, tmp_path):
    # with the books' listing dates transcribed, a period starts where the name really did
    d = tmp_path / "listings"
    d.mkdir()
    (d / "books.csv").write_text(
        "snapshot_date,symbol,source,listing_date,market_cap_idr\n"
        "2024-12-31,GONE,book 2024,2010-05-11,500\n"
        "2024-12-31,STAY,book 2024,2009-01-20,900\n"
        "2025-12-31,STAY,book 2025,2009-01-20,950\n"
    )
    listing.load_listings(store, d, now=NOW)

    assert "GONE" in store.read_roster_members(listing.LISTED_INDEX, Date(2024, 8, 1))
    assert "GONE" not in store.read_roster_members(listing.LISTED_INDEX, Date(2026, 8, 1))
    assert "STAY" in store.read_roster_members(listing.LISTED_INDEX, Date(2026, 8, 1))


# --- LISTED is a listing fact, never an index membership ------------------------------


def test_listed_pseudo_index_neither_covers_a_roster_gap_nor_grants_track_a(store, tmp_path):
    d = _write_listings(tmp_path, _csv("2024-12-31,BIG,book 2024,900"))
    listing.load_listings(store, d, now=NOW)
    day = Date(2025, 3, 3)

    # LISTED spans the day, but the Track A/B roster does not — still a roster gap.
    assert store.read_roster_members(listing.LISTED_INDEX, day) == ("BIG",)
    assert store.roster_covers(day) is False

    # ... and a Track-A-sized ADV must not be promoted by the listing fact alone.
    ch = Chart("BIG", start=Date(2024, 9, 2))
    for _ in range(30):
        ch.add(1000, 1005, 995, 1000, v=50_000_000)     # value 50bn/day ≥ Track A floor
    store.write_daily_bars(ch.bars)
    assert resolve_track_pit(store, "BIG", day, ch.bars) == "B"


def test_listed_rows_do_not_break_the_slice20_roster_loader(tmp_path, store):
    """A LISTED period and an LQ45 period for the same name coexist (different index)."""
    rosters = tmp_path / "rosters"
    rosters.mkdir()
    (rosters / "lq45.csv").write_text(
        "index_name,symbol,effective_from,effective_to,source\n"
        "LQ45,STAY,2024-02-01,,IDX-PENG-1/BEI/2024\n"
    )
    listing.load_listings(store, _write_listings(tmp_path, _csv(
        "2024-12-31,STAY,book 2024,900",
    )), now=NOW)
    load_rosters(store, rosters, now=NOW)
    assert set(store.read_index_roster_pit("STAY", Date(2025, 1, 5))) == {"LQ45", "LISTED"}
    assert store.roster_covers(Date(2025, 1, 5)) is True     # LQ45 does cover it


# --- bias measurement ------------------------------------------------------------------


def _bars(store, symbol: str, start: Date, n: int, close: float = 1000.0):
    ch = Chart(symbol, start=start)
    for _ in range(n):
        ch.add(close, close + 5, close - 5, close, v=50_000_000)
    store.write_daily_bars(ch.bars)
    store.write_broker_net([brow("DX", Side.BUY, 5e9, ch.last_date, symbol=symbol)])
    return ch


def test_bias_is_unmeasured_without_a_roster_never_zero(store):
    bias = survivorship.measure_bias(store, Date(2025, 6, 2))
    assert bias.measured is False
    assert bias.listed == 0
    assert bias.unrecoverable == ()
    assert bias.count_share is None and bias.cap_share is None   # missing ≠ zero
    assert "UNMEASURED" in bias.line()


def test_bias_counts_listed_names_the_store_cannot_serve(store, tmp_path):
    d = _write_listings(tmp_path, _csv(
        "2024-12-31,HAVE,book 2024,900",
        "2024-12-31,LOST,book 2024,300",
        "2025-12-31,HAVE,book 2025,900",
        "2025-12-31,LOST,book 2025,300",
    ))
    listing.load_listings(store, d, now=NOW)
    _bars(store, "HAVE", Date(2025, 4, 1), 40)          # LOST is never ingested

    day = Date(2025, 5, 26)
    bias = survivorship.measure_bias(store, day)
    assert bias.measured is True
    assert bias.listed == 2 and bias.recoverable == 1
    assert bias.unrecoverable == ("LOST",)
    assert bias.count_share == pytest.approx(0.5)
    assert bias.cap_share == pytest.approx(300 / 1200)
    assert bias.cap_known == 2
    assert "LOST" in bias.line()


def test_bias_cap_share_is_none_when_the_books_carry_no_caps(store, tmp_path):
    d = _write_listings(tmp_path, _csv(
        "2024-12-31,HAVE,book 2024,",
        "2024-12-31,LOST,book 2024,",
    ))
    listing.load_listings(store, d, now=NOW)
    _bars(store, "HAVE", Date(2025, 1, 2), 40)

    bias = survivorship.measure_bias(store, Date(2025, 2, 26))
    assert bias.count_share == pytest.approx(0.5)
    assert bias.cap_share is None and bias.cap_known == 0   # unknown, never 0%
    assert bias.caveat is not None and "market cap" in bias.caveat


def test_bias_respects_the_look_ahead_firewall(store, tmp_path):
    """Bars published after the decision frame are not 'recoverable' on that day."""
    d = _write_listings(tmp_path, _csv("2024-12-31,LATE,book 2024,100"))
    listing.load_listings(store, d, now=NOW)
    ch = _bars(store, "LATE", Date(2025, 3, 3), 20)

    on_time = survivorship.measure_bias(store, ch.last_date + timedelta(days=1))
    assert on_time.unrecoverable == ()

    # a day BEFORE the name's first bar was published: nothing visible yet
    early = survivorship.measure_bias(store, Date(2025, 3, 3))
    assert early.unrecoverable == ("LATE",)


def test_bias_span_unions_the_unrecoverable_names(store, tmp_path):
    d = _write_listings(tmp_path, _csv(
        "2024-12-31,A,book 2024,100",
        "2024-12-31,B,book 2024,100",
        "2025-12-31,A,book 2025,100",
        "2025-12-31,B,book 2025,100",
    ))
    listing.load_listings(store, d, now=NOW)
    _bars(store, "A", Date(2025, 1, 2), 60)

    span = survivorship.measure_bias_span(store, Date(2025, 2, 3), Date(2025, 3, 31))
    assert span.measured is True
    assert span.days_sampled >= 2
    assert "B" in span.unrecoverable
    assert span.start == Date(2025, 2, 3) and span.end == Date(2025, 3, 31)


# --- surfaced downstream ----------------------------------------------------------------


def test_pit_universe_separates_unrecoverable_from_stopped_names(store, tmp_path):
    d = _write_listings(tmp_path, _csv(
        "2024-12-31,XLIVE,book 2024,100",
        "2024-12-31,XGONE,book 2024,100",
        "2024-12-31,XNEVER,book 2024,100",
        "2025-12-31,XLIVE,book 2025,100",
        "2025-12-31,XGONE,book 2025,100",
        "2025-12-31,XNEVER,book 2025,100",
    ))
    listing.load_listings(store, d, now=NOW)
    live = _bars(store, "XLIVE", Date(2025, 1, 2), 70)
    gone = Chart("XGONE", start=Date(2025, 1, 2))
    for _ in range(60):
        gone.add(1000, 1005, 995, 1000, v=50_000_000)
    store.write_daily_bars(gone.bars)                       # stops ~10 bars early

    uni = pit_universe(store, live.last_date + timedelta(days=1))
    assert "XLIVE" in uni.symbols
    assert "XGONE" in uni.known_missing                     # had bars, then stopped
    assert "XNEVER" in uni.unrecoverable                    # listed, never ingested
    assert "XNEVER" not in uni.known_missing
    assert uni.survivorship is not None and uni.survivorship.measured is True


def test_pit_universe_without_a_roster_reports_unmeasured_bias(store):
    _bars(store, "SOLO", Date(2025, 1, 2), 70)
    uni = pit_universe(store, Date(2025, 4, 7))
    assert uni.unrecoverable == ()
    assert uni.survivorship is not None and uni.survivorship.measured is False


def test_premise_report_carries_the_measured_bias(store, tmp_path):
    from currentflow.research import premise

    d = _write_listings(tmp_path, _csv(
        "2024-12-31,P1,book 2024,100",
        "2024-12-31,P2,book 2024,100",
        "2024-12-31,PLOST,book 2024,100",
    ))
    listing.load_listings(store, d, now=NOW)
    for sym, close in (("P1", 1000.0), ("P2", 900.0)):
        _bars(store, sym, Date(2025, 1, 2), 80, close=close)

    report = premise.run_premise(
        store, lambda bars, broker, day: float(len(bars)),
        name="dummy", horizon_days=5, buckets=2,
        start=Date(2025, 2, 3), end=Date(2025, 4, 1), now=NOW,
    )
    assert report.survivorship is not None and report.survivorship.measured is True
    assert "PLOST" in " ".join(report.caveats) or "1 of 3" in " ".join(report.caveats)
    # the generic "unmeasured" survivorship caveat is replaced by the measurement
    assert not any(c.startswith("Survivorship: the store holds") for c in report.caveats)


def test_premise_report_keeps_the_generic_caveat_without_a_roster(store):
    from currentflow.research import premise

    for sym, close in (("Q1", 1000.0), ("Q2", 900.0)):
        _bars(store, sym, Date(2025, 1, 2), 80, close=close)
    report = premise.run_premise(
        store, lambda bars, broker, day: float(len(bars)),
        name="dummy", horizon_days=5, buckets=2,
        start=Date(2025, 2, 3), end=Date(2025, 4, 1), now=NOW,
    )
    assert report.survivorship is not None and report.survivorship.measured is False
    assert any("Survivorship" in c for c in report.caveats)
