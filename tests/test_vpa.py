"""VPA bar-character (§4.1, LD-13 v1.6; slice 18) — close position within the spread.

Acceptance for the slice: each character fires on its labeled bar (No-Demand / No-Supply /
Absorption / Stopping volume / effort-vs-result); the volume+spread calibration is
**relative** (the same shape reads the same at any absolute volume); a clean trend stays
clean (no false No-Demand); the read is look-ahead-safe; the §4 candidate contributes
**0** to the running SMS until the optimizer raises it; RULE A's C/D decisions are
**identical** with the corroborator wired; and the observation renders no number.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from builders import (
    Chart,
    concentrated_buyer_rows,
    distribution_bars,
    downtrend_bars,
    phase_a_bars,
    phase_b_bars,
    phase_c_bars,
    phase_d_bars,
    phase_e_bars,
    strong_phase_c_bars,
    two_buyer_rows,
)

from currentflow import config
from currentflow.dal.models import RowStatus
from currentflow.signals import broker_flow, engine, phase, vpa
from currentflow.signals.sms import COMPONENT_KEYS, compute_sms
from currentflow.signals.vpa import BarCharacter, VpaSeverity
from currentflow.ui import shell, vpa_view
from currentflow.ui.vpa_view import character_panel, effort_note, ribbon_cells

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


# --- labeled bar shapes ---------------------------------------------------------------
#
# Every helper builds the SAME quiet base (flat closes, spread 4, volume `v`) and then
# appends one labeled bar, so the character under test is the only thing that differs.


def _base(ch: Chart, n: int = 12, *, close: float = 100.0, v: float = 1000) -> Chart:
    """A range base whose lows are repeatedly probed and bought back (a long lower wick) —
    the context an absorption bar has to be read against."""
    for _ in range(n):
        ch.add(close, close + 2, close - 8, close, v)
    return ch


def _rally(ch: Chart, n: int = 8, *, start: float = 100.0, step: float = 1.5, v: float = 1000) -> Chart:
    c = start
    for _ in range(n):
        ch.add(c, c + 2, c - 2, c, v)
        c += step
    return ch


def _decline(ch: Chart, n: int = 8, *, start: float = 120.0, step: float = 1.5, v: float = 1000) -> Chart:
    c = start
    for _ in range(n):
        ch.add(c, c + 2, c - 2, c, v)
        c -= step
    return ch


def _read(bars, *, window: int | None = None):
    return vpa.build_reading("X", bars, decision_ts=TS, window=window)


def _latest(bars):
    return vpa.classify_bar(vpa._complete(bars))


def no_demand_bars(v: float = 1000):
    """Up bar, narrow spread, volume under the average, after a rally."""
    ch = _base(Chart("X"), 6, v=v)
    ch = _rally(ch, 8, start=100.0, v=v)
    ch.add(112.0, 112.6, 112.0, 112.5, v * 0.4)          # the No-Demand bar
    return ch.bars


def no_supply_bars(v: float = 1000):
    """Down bar, narrow spread, volume under the average, after a decline."""
    ch = _base(Chart("X"), 6, close=122.0, v=v)
    ch = _decline(ch, 8, start=120.0, v=v)
    ch.add(108.5, 108.6, 108.0, 108.0, v * 0.4)          # the No-Supply bar
    return ch.bars


def absorption_bars(v: float = 1000):
    """Heavy volume, lower close, bar finishes near its high — and NOT on a new low, so it
    is absorption inside the range rather than stopping volume at its floor."""
    ch = _base(Chart("X"), 14, v=v)
    ch.add(100.0, 100.5, 93.0, 99.5, v * 3)              # low above the base's low
    return ch.bars


def stopping_volume_bars(v: float = 1000):
    """Heavy volume makes a NEW low and closes near the high — selling stopped."""
    ch = _base(Chart("X"), 14, v=v)
    ch.add(98.0, 100.0, 90.0, 99.0, v * 4)               # new low, close on the high
    return ch.bars


def supply_present_bars(v: float = 1000):
    """Heavy volume lifted the close, bar finishes in its lower third — effort, no result."""
    ch = _base(Chart("X"), 14, v=v)
    ch.add(100.5, 108.0, 100.0, 100.8, v * 3)            # up vs prior close, closes low
    return ch.bars


def churn_bars(v: float = 1000):
    """Heavy volume on a narrow spread — effort with no price result at all."""
    ch = _base(Chart("X"), 14, v=v)
    ch.add(100.0, 100.5, 100.0, 100.2, v * 3)
    return ch.bars


def demand_confirmed_bars(v: float = 1000):
    """Heavy volume, wide spread, close on the high — effort WITH result."""
    ch = _base(Chart("X"), 14, v=v)
    ch.add(100.5, 114.0, 100.0, 113.5, v * 3)
    return ch.bars


def spring_on_stopping_volume_bars():
    """A textbook Phase C: a range, volume drying up inside it, then a shakeout below
    support that closes back inside on volume heavy *versus the dried-up recent bars* yet
    still non-climactic versus the range — i.e. a spring whose own bar prints stopping
    volume. The one chart where the corroborator has something to confirm on the
    TRADEABLE side of the gate."""
    ch = Chart("X").oscillate(32, v=1000)
    for i in range(8):                                   # volume dry-up, tight bars
        ch.add(107, 110, 106, 108, 300) if i % 2 == 0 else ch.add(108, 111, 107, 109, 300)
    ch.add(102, 103.2, 98.2, 101.6, 1250)                # the spring
    return ch.bars


def clean_uptrend_bars(v: float = 1000):
    """A clean, orderly advance: steady volume, ordinary spreads, closes near the high."""
    ch = Chart("X")
    c = 100.0
    for _ in range(24):
        ch.add(c, c + 2.4, c - 0.6, c + 2.0, v)
        c += 2.0
    return ch.bars


# --- each character fires on its labeled bar -------------------------------------------


def test_no_demand_fires_on_a_narrow_low_volume_up_bar_after_a_rally():
    bar = _latest(no_demand_bars())
    assert bar.character is BarCharacter.NO_DEMAND
    assert bar.severity is VpaSeverity.WARN
    assert bar.up is True
    assert bar.available is True


def test_no_supply_fires_on_a_narrow_low_volume_down_bar_after_a_decline():
    bar = _latest(no_supply_bars())
    assert bar.character is BarCharacter.NO_SUPPLY
    assert bar.severity is VpaSeverity.WATCH
    assert bar.up is False


def test_absorption_fires_when_heavy_volume_closes_off_the_low():
    bar = _latest(absorption_bars())
    assert bar.character is BarCharacter.ABSORPTION
    assert bar.close_position >= config.VPA_CLOSE_HIGH
    assert bar.volume_ratio >= config.VPA_HIGH_VOL_MULT


def test_stopping_volume_fires_when_absorption_makes_a_new_low():
    bar = _latest(stopping_volume_bars())
    assert bar.character is BarCharacter.STOPPING_VOLUME


def test_supply_present_is_effort_up_without_result():
    bar = _latest(supply_present_bars())
    assert bar.character is BarCharacter.SUPPLY_PRESENT
    assert bar.effort_without_result is True
    assert bar.close_position <= config.VPA_CLOSE_LOW


def test_churn_is_effort_with_no_result_at_all():
    bar = _latest(churn_bars())
    assert bar.character is BarCharacter.CHURN
    assert bar.effort_without_result is True
    assert bar.spread_ratio <= config.VPA_NARROW_SPREAD_MULT


def test_demand_confirmed_is_effort_with_result():
    bar = _latest(demand_confirmed_bars())
    assert bar.character is BarCharacter.DEMAND_CONFIRMED
    assert bar.effort_without_result is False


def test_result_without_effort_flags_a_wide_move_nobody_paid_for():
    ch = _base(Chart("X"), 14)
    ch.add(100.0, 114.0, 100.0, 110.0, 300)    # wide spread, volume well under the average
    bar = _latest(ch.bars)
    assert bar.result_without_effort is True
    assert bar.effort_without_result is False


# --- calibration is RELATIVE, never absolute -------------------------------------------


def test_the_same_shape_reads_the_same_at_any_absolute_volume():
    """Coulling's calibration is against the recent 10–20 bars, so a thin lapis-2 name and
    a large-cap with the same shape must produce identical characters."""
    for builder in (no_demand_bars, no_supply_bars, absorption_bars,
                    stopping_volume_bars, supply_present_bars, churn_bars):
        thin, thick = _latest(builder(1_000)), _latest(builder(50_000_000))
        assert thin.character is thick.character, builder.__name__
        assert thin.volume_ratio == thick.volume_ratio
        assert thin.spread_ratio == thick.spread_ratio


def test_a_high_volume_bar_is_relative_not_a_fixed_lot_threshold():
    """The identical absolute volume is 'heavy' against a quiet base and 'low' against a
    busy one — the reading follows the context, not a hard-coded number."""
    quiet = _base(Chart("X"), 14, v=1_000)
    quiet.add(100.0, 100.5, 93.0, 99.5, 3_000)
    busy = _base(Chart("X"), 14, v=30_000)
    busy.add(100.0, 100.5, 93.0, 99.5, 3_000)
    assert _latest(quiet.bars).character is BarCharacter.ABSORPTION
    assert _latest(busy.bars).character is not BarCharacter.ABSORPTION


# --- a clean trend stays clean ---------------------------------------------------------


def test_a_clean_uptrend_prints_no_no_demand():
    reading = _read(clean_uptrend_bars())
    assert reading.available is True
    assert reading.count([BarCharacter.NO_DEMAND]) == 0
    assert reading.count([BarCharacter.SUPPLY_PRESENT]) == 0


def test_a_quiet_base_prints_no_character_at_all():
    """Unremarkable bars stay unremarkable — every readable bar of a flat, even-volume
    base is NEUTRAL, and the bars with no calibration base yet are UNREADABLE, not a
    manufactured character."""
    reading = _read(_base(Chart("X"), 24).bars)
    readable = [b for b in reading.bars if b.available]
    assert readable and all(b.character is BarCharacter.NEUTRAL for b in readable)
    assert all(b.character is BarCharacter.UNREADABLE
               for b in reading.bars if not b.available)


# --- missing ≠ zero -------------------------------------------------------------------


def test_a_bar_with_no_spread_is_unreadable_never_mid_range():
    """A locked ARA/ARB print has no range for the close to sit in — reading it as
    'closed mid-spread' would invent the very datum the module exists to measure."""
    ch = _base(Chart("X"), 14)
    ch.add(103.0, 103.0, 103.0, 103.0, 4000)
    bar = _latest(ch.bars)
    assert bar.character is BarCharacter.UNREADABLE
    assert bar.available is False
    assert bar.close_position is None


def test_too_little_context_is_unreadable_never_neutral():
    ch = Chart("X")
    for _ in range(config.VPA_MIN_CONTEXT_BARS):
        ch.add(100.0, 102.0, 98.0, 100.0, 1000)
    bar = _latest(ch.bars)          # only MIN-1 prior bars precede it
    assert bar.character is BarCharacter.UNREADABLE
    assert bar.available is False


def test_incomplete_bars_are_dropped_not_read_as_zero_volume():
    """A gap day is not a zero-volume, zero-spread bar — it is dropped, not characterised."""
    bars = list(absorption_bars())
    missing = bars[-2]
    bars[-2] = type(missing)(
        **{**{f: getattr(missing, f) for f in missing.__slots__},
           "status": RowStatus.GAP, "open": None, "high": None,
           "low": None, "close": None, "volume": None},
    )
    reading = _read(bars)
    assert reading.available is True
    assert all(b.date != missing.date for b in reading.bars)
    # the absorption bar is still read — against the bars that DO exist
    assert reading.latest.character is BarCharacter.ABSORPTION


def test_reading_with_no_bars_is_unavailable():
    reading = _read([])
    assert reading.bars == ()
    assert reading.available is False
    assert reading.latest is None


# --- look-ahead safety ----------------------------------------------------------------


def test_analyze_is_look_ahead_safe(store):
    bars = absorption_bars()
    store.write_daily_bars(bars)
    last = bars[-1]

    before = vpa.analyze(store, "X", last.as_of)          # the absorption bar not yet knowable
    assert last.date not in before.by_date
    assert before.count([BarCharacter.ABSORPTION]) == 0

    after = vpa.analyze(store, "X", TS)
    assert after.latest.date == last.date
    assert after.latest.character is BarCharacter.ABSORPTION


def test_each_ribbon_cell_is_classified_from_its_own_past_only():
    """The ribbon is bar-by-bar look-ahead-safe: truncating the history after a bar must
    not change that bar's character."""
    bars = strong_phase_c_bars("X")
    full = {b.date: b.character for b in _read(bars, window=len(bars)).bars}
    for cut in range(len(bars) - 6, len(bars)):
        partial = _read(bars[:cut], window=cut)
        for b in partial.bars:
            assert full[b.date] is b.character


# --- §4 candidate component: inert at weight 0 ----------------------------------------


def _sms(reading):
    bars = phase_c_bars("X")
    snap = broker_flow.build_snapshot("X", concentrated_buyer_rows("X", BDAYS), decision_ts=TS)
    return compute_sms(
        "X", track="B", bars=bars, broker=snap, foreign=None,
        phase_cls=phase.classify("X", bars, TS), decision_ts=TS, vpa=reading,
    )


def test_candidate_is_pinned_at_weight_zero_in_both_tracks():
    assert "bar_character" in COMPONENT_KEYS
    for track in ("A", "B"):
        assert config.SMS_WEIGHTS[track]["bar_character"] == 0
        assert sum(config.SMS_WEIGHTS[track].values()) == 100      # §4 simplex intact
    assert "bar_character" in config.SMS_CANDIDATE_COMPONENTS
    # A candidate's 0 is UNEARNED, not structural — the optimizer must stay free to fund it.
    for track in ("A", "B"):
        assert "bar_character" not in config.ML_LOCKED_ZEROS[track]


def test_candidate_contributes_zero_running_score_unchanged():
    strong = _read(stopping_volume_bars() + absorption_bars()[-1:])
    with_vpa, without = _sms(strong), _sms(None)

    comp = with_vpa.components_by_key["bar_character"]
    assert comp.subscore > 0            # genuinely measured …
    assert comp.weight == 0             # … and funded with nothing
    assert comp.contribution == 0.0
    assert with_vpa.internal_score == without.internal_score


def test_candidate_availability_tracks_the_data_not_a_silent_zero():
    assert _sms(None).components_by_key["bar_character"].available is False
    assert _sms(_read([])).components_by_key["bar_character"].available is False
    assert _sms(_read(absorption_bars())).components_by_key["bar_character"].available is True


def test_candidate_subscore_grades_the_demand_side_only():
    weak = _read(no_demand_bars())                 # the weakness side is never a strength
    assert vpa.subscore(weak)[0] == 0.0
    assert vpa.subscore(weak)[1] is True           # available — measured, just not credited
    strength, available = vpa.subscore(_read(stopping_volume_bars()))
    assert 0 < strength <= 1.0 and available is True


# --- RULE A: the corroborator annotates, it never gates --------------------------------


def _decision(cls):
    """The gate's decision surface — phase, tradeability, reason, and the events' own
    identities. Corroborator text is deliberately NOT part of it."""
    return (
        cls.phase, cls.tradeable, cls.reason, cls.bars_used,
        tuple((e.kind, e.date, e.detail) for e in cls.events),
        None if cls.trading_range is None else
        (cls.trading_range.support, cls.trading_range.resistance),
    )


def test_phase_decisions_are_identical_with_the_corroborator_wired():
    """RULE A: every labeled archetype classifies identically with and without a VPA
    reading — the C/D gate decision rule is untouched (corroboration, not a new gate)."""
    charts = {
        "downtrend": downtrend_bars("X"), "A": phase_a_bars("X"), "B": phase_b_bars("X"),
        "C": phase_c_bars("X"), "D": phase_d_bars("X"), "E": phase_e_bars("X"),
        "dist": distribution_bars("X"), "strongC": strong_phase_c_bars("X"),
        "no_demand": no_demand_bars(), "absorption": absorption_bars(),
        "clean": clean_uptrend_bars(), "springSV": spring_on_stopping_volume_bars(),
    }
    for label, bars in charts.items():
        bare = phase.classify("X", bars, TS)
        wired = phase.classify("X", bars, TS, vpa=_read(bars, window=len(bars)))
        assert _decision(bare) == _decision(wired), label


def test_a_spring_on_stopping_volume_is_confirmed_without_changing_the_verdict():
    """The tradeable side: a spring whose own bar prints stopping volume draws the
    confirming note — and the Phase C verdict is exactly the one reached without it."""
    bars = spring_on_stopping_volume_bars()
    reading = _read(bars, window=len(bars))
    wired = phase.classify("X", bars, TS, vpa=reading)
    bare = phase.classify("X", bars, TS)

    assert bare.phase is phase.WyckoffPhase.C and bare.tradeable is True
    assert _decision(wired) == _decision(bare)
    assert all(e.corroborators == () for e in bare.events)
    spring = next(e for e in wired.events if e.kind == "SPRING")
    assert spring.corroborators == (
        "VPA confirms: stopping volume on the bar (close position within its spread)",
    )


def test_an_unconfirmed_event_says_so_rather_than_staying_silent():
    """The other direction: a UTAD printing supply-present is corroborated, and an SOS
    printing churn is explicitly *not* confirmed. Either way the phase is unchanged."""
    utad_bars = distribution_bars("X")
    utad = phase.classify("X", utad_bars, TS, vpa=_read(utad_bars, window=len(utad_bars)))
    assert utad.phase is phase.WyckoffPhase.DISTRIBUTION
    assert any("VPA confirms: supply present" in n
               for e in utad.events for n in e.corroborators)

    sos_bars = phase_d_bars("X")
    sos = phase.classify("X", sos_bars, TS, vpa=_read(sos_bars, window=len(sos_bars)))
    assert sos.phase is phase.WyckoffPhase.D and sos.tradeable is True
    note = next(n for e in sos.events if e.kind == "SOS" for n in e.corroborators)
    assert note.startswith("VPA does not confirm:")   # …and D is still tradeable (RULE A)


def test_corroboration_is_none_in_none_out():
    bars = phase_c_bars("X")
    spring = next(e for e in phase.classify("X", bars, TS).events if e.kind == "SPRING")
    assert vpa.corroboration(None, "SPRING", spring.date) is None
    # a day with no bar in the ribbon, and an event kind the framework does not tie to a
    # character, both yield nothing to attach
    assert vpa.corroboration(_read(bars), "SPRING", Date(2020, 1, 1)) is None
    assert vpa.corroboration(_read(bars), "NOT_AN_EVENT", spring.date) is None


def test_analyze_with_and_without_vpa_agrees_on_the_gate(store):
    store.write_daily_bars(strong_phase_c_bars("X"))
    assert _decision(phase.analyze(store, "X", TS)) == _decision(
        phase.analyze(store, "X", TS, with_vpa=False)
    )


# --- engine wiring: decisions unchanged, reading carried -------------------------------


def test_engine_carries_the_reading_without_changing_the_decision(store):
    store.write_daily_bars(strong_phase_c_bars("STRONG"))
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))
    result = engine.evaluate(store, "STRONG", TS, track="B")

    assert result.vpa is not None and result.vpa.available is True
    # the reading is carried and scored at weight 0 → the score equals the same run
    # computed without it
    reference = compute_sms(
        "STRONG", track="B",
        bars=store.read_daily_bars("STRONG", TS),
        broker=broker_flow.analyze(store, "STRONG", TS), foreign=None,
        phase_cls=result.phase, decision_ts=TS,
        adv20=engine._adv20(store.read_daily_bars("STRONG", TS)),
        ownership=result.ownership, vpa=None,
    )
    assert result.sms.internal_score == reference.internal_score
    assert result.sms.components_by_key["bar_character"].contribution == 0.0


# --- RULE B: the observation renders no number ----------------------------------------


def test_ribbon_and_panel_render_no_number():
    cases = [
        _read(no_demand_bars()), _read(no_supply_bars()), _read(absorption_bars()),
        _read(stopping_volume_bars()), _read(supply_present_bars()), _read(churn_bars()),
        _read(demand_confirmed_bars()), _read(_base(Chart("X"), 24).bars), _read([]),
    ]
    seen = set()
    for reading in cases:
        panel = character_panel(reading)
        seen.add(panel["character"])
        copy = f'{panel["label"]} {panel["headline"]} {panel["note"]}'
        note = effort_note(reading)
        if note:
            copy += " " + note
        assert not any(ch.isdigit() for ch in copy), f"panel leaked a number: {copy!r}"
        for banned in ("%", "score", "probability", "buy ", "sell ", "target"):
            assert banned not in copy.lower(), f"panel leaked '{banned}': {copy!r}"
        assert panel["severity"] in {"INFO", "WATCH", "WARN"}    # a word, never a number
        for cell in ribbon_cells(reading):
            body = f'{cell["label"]} {cell["note"]}'
            assert not any(ch.isdigit() for ch in body), body
            assert set(cell) & {"close_position", "spread_ratio", "volume_ratio"} == set()
    assert len(seen) == len(cases)                              # every kind has copy


def test_every_character_has_view_copy():
    for character in BarCharacter:
        assert character.value in vpa_view.CHARACTER_COPY
        glyph, label, color, note = vpa_view.CHARACTER_COPY[character.value]
        assert glyph and label and note
        assert color in shell.TOKENS


def test_ribbon_html_marks_unreadable_bars_and_never_prints_a_ratio():
    ch = _base(Chart("X"), 14)
    ch.add(103.0, 103.0, 103.0, 103.0, 4000)            # locked print → unreadable
    cells = ribbon_cells(_read(ch.bars))
    html = shell.vpa_ribbon_html(cells, empty_label=vpa_view.EMPTY_LABEL)
    assert "×" in html                                   # the unreadable slot is shown …
    assert "0.0" not in html and "%" not in html         # … and no ratio leaks into it
    assert vpa_view.EMPTY_LABEL in shell.vpa_ribbon_html([], empty_label=vpa_view.EMPTY_LABEL)
