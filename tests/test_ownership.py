"""Institutional-ownership delta (§4.1, LD-13 v1.6; slice 17) — the KSEI composition
wired into detection as slow-money confirmation.

Acceptance for the slice: the reading fires on labeled accumulation/distribution
composition series; a stale composition degrades to neutral (never a false distribution
flag); the read is look-ahead-safe; the §4 candidate component contributes **0** to the
running SMS until the optimizer raises it; the §5 corroborator never creates a veto; and
the observation panel renders no number (RULE B).
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from builders import (
    Chart,
    brow,
    concentrated_buyer_rows,
    distribution_bars,
    phase_c_bars,
    strong_phase_c_bars,
    two_buyer_rows,
)

from currentflow import config
from currentflow.dal.models import OwnershipSlice, Side
from currentflow.signals import broker_flow, distribution, engine, ownership, phase
from currentflow.signals.ownership import OwnershipKind, OwnershipSeverity
from currentflow.signals.sms import COMPONENT_KEYS, compute_sms
from currentflow.signals.veto import VetoReason, evaluate_vetoes
from currentflow.ui.foreign_flow_view import ownership_panel

TS = datetime(2026, 7, 1, 9, 0)
FETCHED = datetime(2026, 6, 30, 20, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _slices(pcts: list[float], *, months: list[Date] | None = None, as_of=FETCHED):
    months = months or [Date(2026, 3, 31), Date(2026, 4, 30), Date(2026, 5, 31)][-len(pcts):]
    return tuple(
        OwnershipSlice("X", m, as_of, pct, round(100 - pct, 2))
        for m, pct in zip(months, pcts)
    )


def _flat_bars(n: int = 30, *, start: Date = Date(2026, 3, 30), close: float = 100.0):
    ch = Chart("X", start=start)
    for _ in range(n):
        ch.add(close, close + 1, close - 1, close, 1000)
    return ch.bars


def _marked_up_bars(n: int = 30, *, start: Date = Date(2026, 3, 30), rise: float = 0.4):
    ch = Chart("X", start=start)
    c = 100.0
    for _ in range(n):
        ch.add(c, c + 1, c - 1, c, 1000)
        c += rise
    return ch.bars


def _falling_bars(n: int = 30, *, start: Date = Date(2026, 3, 30)):
    ch = Chart("X", start=start)
    c = 130.0
    for _ in range(n):
        ch.add(c, c + 1, c - 1, c, 1000)
        c -= 0.8
    return ch.bars


# --- labeled composition series ------------------------------------------------------


def test_rising_composition_confirms_accumulation():
    """Institutions (the slow bandar) adding across the range → confirmation, not advice."""
    d = ownership.build_delta("X", _slices([41.0, 42.2, 43.4]), decision_ts=TS, bars=_flat_bars())
    assert d.kind is OwnershipKind.ACCUMULATION_CONFIRMED
    assert d.severity is OwnershipSeverity.WATCH
    assert d.available is True
    assert d.delta_pp == 2.4
    assert d.confirms_accumulation is True
    assert d.corroborates_distribution is False


def test_falling_composition_while_marked_up_is_a_distribution_tell():
    d = ownership.build_delta("X", _slices([44.0, 42.5, 41.0]), decision_ts=TS, bars=_marked_up_bars())
    assert d.kind is OwnershipKind.DISTRIBUTION_TELL
    assert d.severity is OwnershipSeverity.WARN
    assert d.corroborates_distribution is True
    assert d.price_change > 0


def test_falling_composition_with_a_falling_price_is_not_a_distribution_tell():
    """Holders leaving a falling market is an exit, not a markup being dressed."""
    d = ownership.build_delta("X", _slices([44.0, 42.5, 41.0]), decision_ts=TS, bars=_falling_bars())
    assert d.kind is OwnershipKind.OWNERSHIP_EASING
    assert d.corroborates_distribution is False


def test_noise_sized_change_reads_flat():
    d = ownership.build_delta("X", _slices([42.0, 42.1, 42.2]), decision_ts=TS, bars=_flat_bars())
    assert d.kind is OwnershipKind.OWNERSHIP_FLAT
    assert d.severity is OwnershipSeverity.INFO
    assert abs(d.delta_pp) < config.OWNERSHIP_MATERIAL_PP


def test_falling_composition_without_price_context_makes_no_distribution_claim():
    """No bars across the span → no price context → the tell is not claimed (missing ≠ zero)."""
    d = ownership.build_delta("X", _slices([44.0, 41.0]), decision_ts=TS, bars=[])
    assert d.kind is OwnershipKind.OWNERSHIP_EASING
    assert d.price_change is None
    assert d.corroborates_distribution is False


# --- degraded inputs: stale / missing / unpublished -----------------------------------


def test_stale_composition_degrades_to_neutral_not_distribution():
    """A composition older than the stale bound says nothing about today's markup — the
    exact shape that would otherwise fabricate a distribution flag."""
    old = [Date(2025, 12, 31), Date(2026, 1, 31), Date(2026, 2, 28)]
    d = ownership.build_delta(
        "X", _slices([44.0, 42.5, 41.0], months=old), decision_ts=TS, bars=_marked_up_bars()
    )
    assert d.kind is OwnershipKind.STALE_COMPOSITION
    assert d.severity is OwnershipSeverity.INFO
    assert d.stale is True
    assert d.available is False                  # never scored as 0-strength
    assert d.corroborates_distribution is False
    assert d.age_days > config.OWNERSHIP_STALE_DAYS


def test_no_composition_is_unavailable_never_flat():
    for slices in ((), _slices([42.0])):
        d = ownership.build_delta("X", slices, decision_ts=TS, bars=_flat_bars())
        assert d.kind is OwnershipKind.NO_COMPOSITION
        assert d.available is False
        assert d.delta_pp is None


def test_unpublished_percentage_is_dropped_not_read_as_zero():
    slices = (
        OwnershipSlice("X", Date(2026, 4, 30), FETCHED, None, None),   # not yet published
        OwnershipSlice("X", Date(2026, 5, 31), FETCHED, 43.0, 57.0),
    )
    d = ownership.build_delta("X", slices, decision_ts=TS, bars=_flat_bars())
    assert d.kind is OwnershipKind.NO_COMPOSITION      # one usable slice, not a −43pp collapse
    assert d.delta_pp is None


def test_window_is_bounded_to_the_accumulation_span():
    """Only the last `OWNERSHIP_WINDOW_SLICES` monthly slices are read — an ancient
    composition does not stretch the delta across an unrelated regime."""
    months = [Date(2025, 8, 31), Date(2025, 9, 30), Date(2026, 2, 28),
              Date(2026, 3, 31), Date(2026, 4, 30), Date(2026, 5, 31)]
    d = ownership.build_delta(
        "X", _slices([10.0, 20.0, 41.0, 41.5, 42.0, 42.6], months=months),
        decision_ts=TS, bars=_flat_bars(),
    )
    assert d.slices_used == config.OWNERSHIP_WINDOW_SLICES
    assert d.first_date == Date(2026, 2, 28)
    assert d.delta_pp == 1.6


# --- look-ahead safety ----------------------------------------------------------------


def test_analyze_is_look_ahead_safe(store):
    store.write_daily_bars(_flat_bars())
    store.write_ksei_ownership([
        OwnershipSlice("X", Date(2026, 4, 30), FETCHED, 41.0, 59.0),
        OwnershipSlice("X", Date(2026, 5, 31), FETCHED, 43.4, 56.6),
    ])
    before = ownership.analyze(store, "X", datetime(2026, 6, 30, 19, 0))   # pre-fetch
    assert before.kind is OwnershipKind.NO_COMPOSITION
    assert before.available is False

    after = ownership.analyze(store, "X", TS)
    assert after.kind is OwnershipKind.ACCUMULATION_CONFIRMED
    assert after.delta_pp == 2.4


# --- §4 candidate component: inert at weight 0 ----------------------------------------


def _sms(ownership_delta):
    bars = phase_c_bars("X")
    snap = broker_flow.build_snapshot("X", concentrated_buyer_rows("X", BDAYS), decision_ts=TS)
    return compute_sms(
        "X", track="B", bars=bars, broker=snap, foreign=None,
        phase_cls=phase.classify("X", bars, TS), decision_ts=TS, ownership=ownership_delta,
    )


def test_candidate_is_pinned_at_weight_zero_in_both_tracks():
    assert "ownership_delta" in COMPONENT_KEYS
    for track in ("A", "B"):
        assert config.SMS_WEIGHTS[track]["ownership_delta"] == 0
        assert sum(config.SMS_WEIGHTS[track].values()) == 100   # §4 simplex intact
    assert "ownership_delta" in config.SMS_CANDIDATE_COMPONENTS
    # A candidate's 0 is UNEARNED, not structural: the optimizer must be free to fund it
    # (unlike LD-1's permanent Track-B foreign_flow lock).
    for track in ("A", "B"):
        assert "ownership_delta" not in config.ML_LOCKED_ZEROS[track]


def test_candidate_contributes_zero_running_score_unchanged():
    strong = ownership.build_delta("X", _slices([41.0, 42.2, 44.0]), decision_ts=TS, bars=_flat_bars())
    with_own, without = _sms(strong), _sms(None)

    comp = with_own.components_by_key["ownership_delta"]
    assert comp.subscore > 0            # the signal is genuinely measured …
    assert comp.weight == 0             # … and funded with nothing
    assert comp.contribution == 0.0
    # the running score is unchanged on landing — the whole point of a candidate
    assert with_own.internal_score == without.internal_score


def test_candidate_availability_tracks_the_data_not_a_silent_zero():
    stale_months = [Date(2026, 1, 31), Date(2026, 2, 28)]
    stale = ownership.build_delta(
        "X", _slices([44.0, 41.0], months=stale_months), decision_ts=TS, bars=_flat_bars()
    )
    assert _sms(stale).components_by_key["ownership_delta"].available is False
    assert _sms(None).components_by_key["ownership_delta"].available is False
    fresh = ownership.build_delta("X", _slices([41.0, 43.0]), decision_ts=TS, bars=_flat_bars())
    assert _sms(fresh).components_by_key["ownership_delta"].available is True


# --- §5 corroborator: strengthens, never rejects --------------------------------------


def _veto(bars, *, own=None):
    snap = broker_flow.build_snapshot("X", concentrated_buyer_rows("X", BDAYS), decision_ts=TS)
    return evaluate_vetoes("X", broker=snap, bars=bars, phase_cls=phase.classify("X", bars, TS),
                           decision_ts=TS, ownership=own)


def test_ownership_never_creates_a_veto_on_its_own():
    """A clean Phase C name with a textbook distribution composition is still not
    rejected — coarse monthly data can corroborate, never hard-reject (§4.1)."""
    tell = ownership.build_delta("X", _slices([44.0, 42.5, 41.0]), decision_ts=TS, bars=_marked_up_bars())
    assert tell.corroborates_distribution is True

    clean = phase_c_bars("X")
    assert _veto(clean).reasons == _veto(clean, own=tell).reasons == frozenset()


def test_ownership_corroborates_a_distribution_veto_that_already_fired():
    tell = ownership.build_delta("X", _slices([44.0, 42.5, 41.0]), decision_ts=TS, bars=_marked_up_bars())
    bars = distribution_bars("X")
    bare, corroborated = _veto(bars), _veto(bars, own=tell)

    assert bare.reasons == corroborated.reasons          # the decision is identical
    dist = next(v for v in corroborated.vetoes if v.reason is VetoReason.DISTRIBUTION_DRESSED)
    assert len(dist.corroborators) == 1
    assert "KSEI composition corroborates" in dist.detail
    assert next(v for v in bare.vetoes if v.reason is VetoReason.DISTRIBUTION_DRESSED).corroborators == ()


def test_stale_composition_cannot_corroborate():
    old = [Date(2025, 12, 31), Date(2026, 1, 31)]
    stale = ownership.build_delta(
        "X", _slices([44.0, 41.0], months=old), decision_ts=TS, bars=_marked_up_bars()
    )
    bars = distribution_bars("X")
    dist = next(v for v in _veto(bars, own=stale).vetoes if v.reason is VetoReason.DISTRIBUTION_DRESSED)
    assert dist.corroborators == ()


def test_trap_monitor_carries_the_same_corroborated_veto(store):
    """The trap ribbon reads the veto through `distribution.monitor` — it must show the
    same corroborated evidence the pipeline does, never a divergent story."""
    store.write_daily_bars(_flat_bars(30))
    store.write_broker_net([
        brow("DX", Side.BUY, 40e9, BDAYS[0], symbol="X"),
        brow("DX", Side.SELL, 12e9, BDAYS[1], symbol="X"),
        brow("DX", Side.SELL, 12e9, BDAYS[2], symbol="X"),
        brow("KI", Side.BUY, 2e9, BDAYS[2], symbol="X"),
        brow("CC", Side.BUY, 1e9, BDAYS[2], symbol="X"),
    ])
    store.write_ksei_ownership([
        OwnershipSlice("X", Date(2026, 3, 31), FETCHED, 44.0, 56.0),
        OwnershipSlice("X", Date(2026, 4, 30), FETCHED, 41.0, 59.0),
    ])
    mon = distribution.monitor(store, "X", TS)
    dist = next(v for v in mon.veto.vetoes if v.reason is VetoReason.DISTRIBUTION_DRESSED)
    assert dist.corroborators and "KSEI composition corroborates" in dist.detail


# --- engine wiring: decisions unchanged -----------------------------------------------


def _armed(store, symbol="STRONG"):
    store.write_daily_bars(strong_phase_c_bars(symbol))
    store.write_broker_net(two_buyer_rows(symbol, BDAYS))
    return engine.evaluate(store, symbol, TS, track="B")


def test_engine_carries_the_reading_without_changing_the_decision(store):
    before = _armed(store)
    assert before.ownership.kind is OwnershipKind.NO_COMPOSITION   # nothing ingested yet

    store.write_ksei_ownership([
        OwnershipSlice("STRONG", Date(2026, 4, 30), FETCHED, 41.0, 59.0),
        OwnershipSlice("STRONG", Date(2026, 5, 31), FETCHED, 43.4, 56.6),
    ])
    after = engine.evaluate(store, "STRONG", TS, track="B")

    assert after.ownership.kind is OwnershipKind.ACCUMULATION_CONFIRMED
    assert after.state is before.state                              # RULE A/B untouched
    assert after.sms.internal_score == before.sms.internal_score    # running score unchanged
    assert after.veto.reasons == before.veto.reasons


# --- RULE B: the observation panel shows no number ------------------------------------


def test_ownership_panel_renders_no_number(store):
    cases = [
        ownership.build_delta("X", _slices([41.0, 43.4]), decision_ts=TS, bars=_flat_bars()),
        ownership.build_delta("X", _slices([44.0, 41.0]), decision_ts=TS, bars=_marked_up_bars()),
        ownership.build_delta("X", _slices([44.0, 41.0]), decision_ts=TS, bars=_falling_bars()),
        ownership.build_delta("X", _slices([42.0, 42.1]), decision_ts=TS, bars=_flat_bars()),
        ownership.build_delta("X", (), decision_ts=TS, bars=_flat_bars()),
        ownership.build_delta(
            "X", _slices([44.0, 41.0], months=[Date(2025, 12, 31), Date(2026, 1, 31)]),
            decision_ts=TS, bars=_marked_up_bars(),
        ),
    ]
    seen = set()
    for delta in cases:
        panel = ownership_panel(delta)
        seen.add(panel["kind"])
        copy = panel["headline"] + " " + panel["detail"]
        assert not any(ch.isdigit() for ch in copy), f"panel leaked a number: {copy!r}"
        for banned in ("%", "score", "probability", "buy ", "sell ", "target"):
            assert banned not in copy.lower(), f"panel leaked '{banned}': {copy!r}"
        assert panel["severity"] in {"INFO", "WATCH", "WARN"}       # a word, never a number
    assert len(seen) == len(cases)                                  # every kind has copy
