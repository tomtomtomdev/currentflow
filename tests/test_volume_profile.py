"""Approximate volume profile (§4.1, LD-13 v1.6; slice 19) — volume at price.

Acceptance for the slice: POC / VAH / VAL / HVN / LVN reproduce a **hand-checked**
daily-bar profile; the value area brackets 70% of the window's volume; the read is
look-ahead-safe (no future bar in the profile); a bar with no data is dropped rather than
zeroed; the §4 candidate contributes **0** to the running SMS until the optimizer raises
it; RULE A's C/D decisions are **identical** with the confluence wired; and every surface
labels the approximation.
"""

from __future__ import annotations

import re
from datetime import date as Date
from datetime import datetime

import pytest
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
from currentflow.signals import broker_flow, engine, phase, volume_profile as vp
from currentflow.signals.sms import COMPONENT_KEYS, compute_sms
from currentflow.signals.volume_profile import NodeKind
from currentflow.ui import charts, shell, volume_profile_view as vp_view

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _profile(bars, **kw):
    return vp.build_profile("X", bars, decision_ts=TS, **kw)


# --- a hand-checked profile ------------------------------------------------------------
#
# The whole geometry is chosen so every number below can be checked with arithmetic:
#
#   window range   lows min = 100, highs max = 124   → span 24 over VP_BUCKETS = 24
#   bucket width   24 / 24 = 1.0, so bucket i covers exactly [100 + i, 101 + i)
#   12 bars        low 110, high 111, volume 1000    → all 12,000 lands in bucket 10
#   1 spanning bar low 100, high 124, volume 2400    → 100 into EACH of the 24 buckets
#
# POC = bucket 10's midpoint = 110.5. Bucket 10 holds 12,100 of 14,400 = 84% of the
# window, which already clears the 70% value area, so VAL = 110.0 and VAH = 111.0.


def _hand_checked() -> list:
    ch = Chart("X")
    for _ in range(12):
        ch.add(110.5, 111.0, 110.0, 110.5, 1000)
    ch.add(112.0, 124.0, 100.0, 112.0, 2400)      # one bar spanning the whole range
    return ch.bars


def test_poc_value_area_and_buckets_reproduce_the_hand_checked_profile():
    p = _profile(_hand_checked())

    assert p.available is True
    assert len(p.buckets) == config.VP_BUCKETS
    assert p.bucket_width == pytest.approx(1.0)
    assert p.buckets[0].low == pytest.approx(100.0)
    assert p.buckets[-1].high == pytest.approx(124.0)

    assert p.poc == pytest.approx(110.5)
    assert p.val == pytest.approx(110.0)
    assert p.vah == pytest.approx(111.0)

    assert p.total_volume == pytest.approx(14_400.0)
    assert p.buckets[10].volume == pytest.approx(12_100.0)   # 12 × 1000 + the spanning 100
    assert p.buckets[0].volume == pytest.approx(100.0)       # uniform spread, exactly 1/24
    assert p.buckets[23].volume == pytest.approx(100.0)


def test_the_value_area_brackets_seventy_percent_of_the_windows_volume():
    """The defining property, asserted on shapes that force the band to actually grow —
    not just on the single-bucket case above."""
    shapes = [
        _hand_checked(),
        phase_c_bars("X"), phase_d_bars("X"), strong_phase_c_bars("X"),
        distribution_bars("X"), downtrend_bars("X"),
        Chart("X").oscillate(40).bars,
    ]
    for bars in shapes:
        p = _profile(bars)
        assert p.available is True
        assert p.value_area_share >= config.VP_VALUE_AREA_PCT
        # …and it is a contiguous band containing the point of control
        assert p.val <= p.poc <= p.vah
        inside = sum(
            b.volume for b in p.buckets if b.high > p.val and b.low < p.vah
        )
        assert inside == pytest.approx(p.value_area_volume)


def test_the_value_area_is_the_tight_band_not_the_whole_range():
    """A 70% band that swallowed the window would be true and useless — on a chart with a
    clear shelf the value area must be a small slice of the range."""
    p = _profile(_hand_checked())
    assert (p.vah - p.val) < 0.1 * (p.buckets[-1].high - p.buckets[0].low)


def test_hvn_and_lvn_name_the_shelf_and_the_air():
    p = _profile(_hand_checked())
    # bucket 10 carries 12,100 against a mean live bucket of 600 — the shelf
    assert p.buckets[10].kind is NodeKind.HVN
    # every other bucket carries exactly 100, well under VP_LVN_MULT × the mean — air
    assert all(b.kind is NodeKind.LVN for i, b in enumerate(p.buckets) if i != 10)

    assert [n.kind for n in p.hvn] == [NodeKind.HVN]
    assert p.hvn[0].low == pytest.approx(110.0) and p.hvn[0].high == pytest.approx(111.0)
    # adjacent same-kind buckets merge into ONE region either side of the shelf
    assert len(p.lvn) == 2
    assert p.lvn[0].low == pytest.approx(100.0) and p.lvn[0].high == pytest.approx(110.0)
    assert p.lvn[1].low == pytest.approx(111.0) and p.lvn[1].high == pytest.approx(124.0)


def test_a_locked_print_lands_whole_in_one_bucket():
    """`high == low` has no range to spread across — that volume is exact, not estimated,
    and it must not vanish."""
    ch = Chart("X")
    for _ in range(12):
        ch.add(110.5, 111.0, 110.0, 110.5, 1000)
    ch.add(112.0, 124.0, 100.0, 112.0, 2400)
    ch.add(105.5, 105.5, 105.5, 105.5, 5000)          # a locked (ARA/ARB) print
    p = _profile(ch.bars)

    assert p.total_volume == pytest.approx(19_400.0)
    landed = next(b for b in p.buckets if b.low <= 105.5 < b.high)
    assert landed.volume == pytest.approx(5100.0)      # 5000 locked + 100 from the span bar


def test_the_profile_is_scale_free():
    """The same shape at 1k and at 50m lots, and at Rp 100 and Rp 10,000, must produce the
    same structure — buckets are the window's own range, never an absolute quantity."""
    base = _profile(_hand_checked())

    heavy = Chart("X")
    for _ in range(12):
        heavy.add(110.5, 111.0, 110.0, 110.5, 50_000_000)
    heavy.add(112.0, 124.0, 100.0, 112.0, 120_000_000)
    scaled = _profile(heavy.bars)
    assert scaled.poc == pytest.approx(base.poc)
    assert [b.kind for b in scaled.buckets] == [b.kind for b in base.buckets]

    priced = Chart("X")
    for _ in range(12):
        priced.add(11050, 11100, 11000, 11050, 1000)
    priced.add(11200, 12400, 10000, 11200, 2400)
    big = _profile(priced.bars)
    assert big.poc == pytest.approx(base.poc * 100)
    assert [b.kind for b in big.buckets] == [b.kind for b in base.buckets]


# --- missing ≠ zero --------------------------------------------------------------------


def test_a_non_traded_bar_is_dropped_not_folded_in_as_zero_volume():
    """A suspended day is not a zero-volume day: folding it in would silently thin
    whatever price shelf it belonged to."""
    bars = list(_hand_checked())
    ref = _profile(bars)
    for status in (RowStatus.GAP, RowStatus.NOT_PUBLISHED, RowStatus.NO_TRADES):
        absent = bars[-1].__class__(
            **{**{f: getattr(bars[-1], f) for f in bars[-1].__slots__},
               "status": status, "volume": None, "date": Date(2026, 2, 2)}
        )
        p = _profile(bars + [absent])
        assert absent.date not in {b.date for b in p.bars}
        assert p.total_volume == pytest.approx(ref.total_volume)
        assert p.poc == pytest.approx(ref.poc)

    # a bar carrying a status but no OHLC is likewise dropped, never read as a price of 0 —
    # a phantom bucket at zero would drag the whole window's range down with it
    partial = bars[-1].__class__(
        **{**{f: getattr(bars[-1], f) for f in bars[-1].__slots__},
           "low": None, "high": None, "date": Date(2026, 2, 3)}
    )
    p = _profile(bars + [partial])
    assert partial.date not in {b.date for b in p.bars}
    assert p.buckets[0].low == pytest.approx(ref.buckets[0].low)


def test_too_few_bars_is_no_profile_never_an_empty_one():
    p = _profile(Chart("X").oscillate(config.VP_MIN_BARS - 2).bars)
    assert p.available is False
    assert (p.poc, p.vah, p.val) == (None, None, None)     # never a midpoint stand-in
    assert p.buckets == () and p.value_area_share is None
    assert "no profile" in p.note


def test_a_window_with_no_range_has_no_point_of_control():
    ch = Chart("X")
    for _ in range(20):
        ch.add(100.0, 100.0, 100.0, 100.0, 1000)           # every day locked at one price
    p = _profile(ch.bars)
    assert p.available is False and p.poc is None
    assert "no price range" in p.note


def test_a_window_with_no_volume_has_no_profile():
    ch = Chart("X")
    for _ in range(20):
        ch.add(104.0, 108.0, 100.0, 106.0, 0)
    p = _profile(ch.bars)
    assert p.available is False and p.poc is None
    assert "no traded volume" in p.note


def test_empty_input_reads_as_unavailable():
    p = _profile([])
    assert p.available is False and p.bars == () and p.poc is None


# --- look-ahead safety -----------------------------------------------------------------


def test_analyze_is_look_ahead_safe(store):
    """No future bar reaches the profile: at a decision moment before the last bar was
    knowable, that bar's price simply is not in the distribution."""
    ch = Chart("X")
    for _ in range(20):
        ch.add(104.0, 108.0, 100.0, 106.0, 1000)
    ch.add(150.0, 160.0, 148.0, 158.0, 90_000)             # a huge shelf far above
    bars = ch.bars
    store.write_daily_bars(bars)
    last = bars[-1]

    before = vp.analyze(store, "X", last.as_of)
    assert last.date not in {b.date for b in before.bars}
    assert before.buckets[-1].high <= 108.0                # the future shelf is absent
    assert before.poc < 110

    after = vp.analyze(store, "X", TS)
    assert after.buckets[-1].high == pytest.approx(160.0)
    assert after.poc > 140                                 # …and present once knowable


def test_the_profile_only_ever_reads_bars_at_or_before_the_last_visible_one():
    bars = strong_phase_c_bars("X")
    for cut in range(config.VP_MIN_BARS + 2, len(bars)):
        p = _profile(bars[:cut])
        assert max(b.date for b in p.bars) == bars[cut - 1].date
        assert all(b.date <= bars[cut - 1].date for b in p.bars)


def test_the_window_is_bounded_and_says_so():
    """No silent caps: the window is the last VP_WINDOW_BARS, and the drop of an
    incomplete bar is logged rather than absorbed."""
    bars = Chart("X").oscillate(config.VP_WINDOW_BARS + 30).bars
    p = _profile(bars)
    assert len(p.bars) == config.VP_WINDOW_BARS
    assert p.end == bars[-1].date                          # the LAST bars, not the first


# --- phase confluence: annotation, never a gate ----------------------------------------


def test_spring_at_the_value_low_is_confluent_and_a_bar_above_it_is_not():
    """Spring@VAL: the event bar's low has to actually reach the value-area low. A bar
    trading comfortably inside the band yields nothing to attach."""
    bars = phase_c_bars("X")
    p = _profile(bars)
    tol = config.VP_CONFLUENCE_BUCKETS * p.bucket_width

    reaching = next(b for b in p.bars if b.low <= p.val + tol)
    conf = vp.confluence(p, "SPRING", reaching.date)
    assert conf is not None and conf.level == "VAL"
    assert conf.price == pytest.approx(reaching.low)

    above = next(b for b in p.bars if b.low > p.val + tol)
    assert vp.confluence(p, "SPRING", above.date) is None


def test_lps_on_the_point_of_control_and_utad_at_the_value_high():
    """On the hand-checked profile the shelf bars close at 110.5 — the POC itself — while
    the one spanning bar closes at 112.0, a full bucket and a half away."""
    p = _profile(_hand_checked())
    assert p.poc == pytest.approx(110.5) and p.vah == pytest.approx(111.0)

    on_poc = p.bars[0]
    assert on_poc.close == pytest.approx(110.5)
    assert vp.confluence(p, "LPS", on_poc.date).level == "POC"

    off_poc = p.bars[-1]
    assert off_poc.close == pytest.approx(112.0)
    assert vp.confluence(p, "LPS", off_poc.date) is None

    at_vah = vp.confluence(p, "UTAD", on_poc.date)     # high 111.0 reaches the value high
    assert at_vah is not None and at_vah.level == "VAH"


def test_confluence_is_none_in_none_out():
    bars = phase_c_bars("X")
    p = _profile(bars)
    day = p.bars[-1].date
    assert vp.confluence(None, "SPRING", day) is None
    assert vp.confluence(_profile(bars[:4]), "SPRING", day) is None     # unavailable profile
    assert vp.confluence(p, "SPRING", Date(2020, 1, 1)) is None         # bar outside the window
    assert vp.confluence(p, "SOS", day) is None                         # not a level this slice ties
    assert vp.confluence(p, "NOT_AN_EVENT", day) is None
    assert vp.corroboration(None, "SPRING", day) is None


def _decision(cls):
    """The gate's decision surface. Corroborator text is deliberately NOT part of it."""
    return (
        cls.phase, cls.tradeable, cls.reason, cls.bars_used,
        tuple((e.kind, e.date, e.detail) for e in cls.events),
        None if cls.trading_range is None else
        (cls.trading_range.support, cls.trading_range.resistance),
    )


ARCHETYPES = {
    "downtrend": downtrend_bars("X"), "A": phase_a_bars("X"), "B": phase_b_bars("X"),
    "C": phase_c_bars("X"), "D": phase_d_bars("X"), "E": phase_e_bars("X"),
    "dist": distribution_bars("X"), "strongC": strong_phase_c_bars("X"),
}


def test_phase_decisions_are_identical_with_the_confluence_wired():
    """RULE A: every labeled archetype classifies identically with and without a profile —
    the C/D gate decision rule is untouched (corroboration, not a new gate)."""
    for label, bars in ARCHETYPES.items():
        bare = phase.classify("X", bars, TS)
        wired = phase.classify("X", bars, TS, vp=_profile(bars))
        assert _decision(bare) == _decision(wired), label


def test_a_confluent_spring_is_annotated_and_still_just_phase_c():
    bars = phase_c_bars("X")
    bare = phase.classify("X", bars, TS)
    wired = phase.classify("X", bars, TS, vp=_profile(bars))

    assert bare.phase is phase.WyckoffPhase.C and bare.tradeable is True
    assert _decision(wired) == _decision(bare)
    assert all(e.corroborators == () for e in bare.events)
    spring = next(e for e in wired.events if e.kind == "SPRING")
    assert any(n.startswith("Volume profile confirms:") for n in spring.corroborators)
    assert any(vp.ANNOTATION in n for n in spring.corroborators)


def test_both_corroborators_coexist_on_one_event():
    """Slice 18's bar character and slice 19's profile confluence stack on the same event
    without either displacing the other."""
    from currentflow.signals import vpa as vpa_mod

    bars = phase_c_bars("X")
    reading = vpa_mod.build_reading("X", bars, decision_ts=TS, window=len(bars))
    wired = phase.classify("X", bars, TS, vpa=reading, vp=_profile(bars))
    spring = next(e for e in wired.events if e.kind == "SPRING")
    assert any("Volume profile" in n for n in spring.corroborators)
    assert _decision(wired) == _decision(phase.classify("X", bars, TS))


def test_analyze_with_and_without_the_profile_agrees_on_the_gate(store):
    store.write_daily_bars(strong_phase_c_bars("X"))
    assert _decision(phase.analyze(store, "X", TS)) == _decision(
        phase.analyze(store, "X", TS, with_vp=False)
    )


# --- §4 candidate component: inert at weight 0 -----------------------------------------


def test_the_candidate_is_registered_at_weight_zero_in_both_tracks():
    assert "vp_confluence" in COMPONENT_KEYS
    assert "vp_confluence" in config.SMS_CANDIDATE_COMPONENTS
    for track, weights in config.SMS_WEIGHTS.items():
        assert weights["vp_confluence"] == 0, track
        assert sum(weights.values()) == 100, track      # the simplex is untouched
    # unearned, not structural — the optimizer must stay free to fund it (RULE B)
    assert "vp_confluence" not in config.ML_LOCKED_ZEROS.get("A", {})
    assert "vp_confluence" not in config.ML_LOCKED_ZEROS.get("B", {})


def _sms(profile, phase_cls=None):
    bars = phase_c_bars("X")
    snap = broker_flow.build_snapshot("X", concentrated_buyer_rows("X", BDAYS), decision_ts=TS)
    return compute_sms(
        "X", track="B", bars=bars, broker=snap, foreign=None,
        phase_cls=phase_cls or phase.classify("X", bars, TS),
        decision_ts=TS, vp=profile,
    )


def test_the_candidate_contributes_nothing_to_the_running_score():
    bars = phase_c_bars("X")
    with_profile = _sms(_profile(bars))
    without = _sms(None)

    assert with_profile.components_by_key["vp_confluence"].weight == 0
    assert with_profile.components_by_key["vp_confluence"].contribution == 0.0
    assert with_profile.internal_score == without.internal_score


def test_the_candidate_is_measured_even_though_it_is_unfunded():
    """Inert is not absent: the sub-score is computed and observed so the optimizer has a
    series to walk forward over when RULE B eventually opens the gate."""
    bars = phase_c_bars("X")
    cls = phase.classify("X", bars, TS)
    component = _sms(_profile(bars), cls).components_by_key["vp_confluence"]
    assert component.available is True
    assert component.subscore > 0                       # the spring landed at the value low
    assert component.observation["confluences"][0]["event"] == "SPRING"
    assert component.observation["fidelity"] == vp.ANNOTATION


def test_no_profile_is_unavailable_never_a_silent_zero_strength():
    strength, available = vp.subscore(None)
    assert (strength, available) == (0.0, False)
    strength, available = vp.subscore(_profile(phase_c_bars("X")[:4]))
    assert (strength, available) == (0.0, False)

    component = _sms(None).components_by_key["vp_confluence"]
    assert component.available is False


def test_the_subscore_grades_the_accumulation_side_only():
    """A UTAD at the value high is a distribution tell — surfaced by the observation, never
    negative strength inside the simplex (the same posture as slice 18's weakness side)."""
    bars = distribution_bars("X")
    p = _profile(bars)
    cls = phase.classify("X", bars, TS, vp=p)
    assert any(e.kind == "UTAD" for e in cls.events)
    strength, available = vp.subscore(p, cls.events)
    assert available is True and strength == 0.0


def test_engine_carries_the_profile_without_changing_the_decision(store):
    store.write_daily_bars(strong_phase_c_bars("STRONG"))
    store.write_broker_net(two_buyer_rows("STRONG", BDAYS))
    result = engine.evaluate(store, "STRONG", TS, track="B")

    assert result.vp is not None and result.vp.available is True
    reference = compute_sms(
        "STRONG", track="B",
        bars=store.read_daily_bars("STRONG", TS),
        broker=broker_flow.analyze(store, "STRONG", TS), foreign=None,
        phase_cls=result.phase, decision_ts=TS,
        adv20=engine._adv20(store.read_daily_bars("STRONG", TS)),
        ownership=result.ownership, vpa=result.vpa, vp=None,
    )
    assert result.sms.internal_score == reference.internal_score
    assert result.sms.components_by_key["vp_confluence"].contribution == 0.0


# --- RULE B / §4.1 fidelity honesty: the view labels the approximation ------------------


def _text(html: str) -> str:
    """Rendered text only — tags and their style attributes stripped."""
    return re.sub(r"<[^>]*>", " ", html)


def test_every_surface_labels_the_approximation():
    p = _profile(_hand_checked())
    panel = vp_view.profile_panel(p)
    assert panel["annotation"] == vp.ANNOTATION
    assert vp.ANNOTATION in vp_view.FRAMING
    assert p.annotation == vp.ANNOTATION

    html = shell.volume_profile_html(
        vp_view.histogram_rows(p), panel["levels"],
        empty_label=vp_view.EMPTY_LABEL, annotation=panel["annotation"],
    )
    assert vp.ANNOTATION in _text(html)

    # an unavailable profile still says why, and still says nothing precise
    empty = vp_view.profile_panel(_profile([]))
    assert empty["available"] is False and empty["levels"] == []
    assert vp_view.EMPTY_LABEL in shell.volume_profile_html(
        [], [], empty_label=vp_view.EMPTY_LABEL, annotation=vp.ANNOTATION
    )


def test_the_chart_overlay_marks_every_level_as_estimated():
    p = _profile(_hand_checked())
    layers = charts.volume_profile_layers(vp_view.levels(p))
    assert layers                                          # band + rule/label per level
    labels = [
        row["label"]
        for layer in layers
        for row in getattr(layer, "data", []).to_dict("records")
        if "label" in getattr(layer, "data", []).columns
    ]
    assert set(labels) == {"POC~", "VAH~", "VAL~"}         # `~` = estimated, on every one
    assert charts.volume_profile_layers([]) == []


def test_the_observation_carries_no_score_and_no_verb():
    p = _profile(phase_c_bars("X"))
    cls = phase.classify("X", phase_c_bars("X"), TS)
    panel = vp_view.profile_panel(p)
    copy = " ".join([
        vp_view.FRAMING, vp_view.EMPTY_LABEL, panel["headline"], panel["annotation"],
        *(lv["note"] for lv in panel["levels"]),
        *(n["label"] for n in panel["nodes"]),
        *vp_view.confluence_notes(p, cls.events),
    ])
    for banned in ("%", "score", "probability", "confidence", "buy ", "sell ", "target"):
        assert banned not in copy.lower(), f"copy leaked '{banned}': {copy!r}"
    # the level COPY is digit-free; the level PRICES are prices, not claims
    assert not any(ch.isdigit() for ch in copy), copy

    html = _text(shell.volume_profile_html(
        vp_view.histogram_rows(p), panel["levels"],
        empty_label=vp_view.EMPTY_LABEL, annotation=panel["annotation"],
    ))
    for banned in ("%", "score", "probability", "buy ", "sell ", "target"):
        assert banned not in html.lower(), f"lane leaked '{banned}': {html!r}"


def test_every_level_and_node_has_view_copy():
    for key in ("POC", "VAH", "VAL"):
        label, color_key, note = vp_view.LEVEL_COPY[key]
        assert label and note and color_key in shell.TOKENS
    for kind in (NodeKind.HVN, NodeKind.LVN):
        label, color_key = vp_view.NODE_COPY[kind.value]
        assert label and color_key in shell.TOKENS


def test_histogram_rows_are_price_descending_and_print_no_volume():
    p = _profile(_hand_checked())
    rows = vp_view.histogram_rows(p)
    assert len(rows) == config.VP_BUCKETS
    assert [r["mid"] for r in rows] == sorted((r["mid"] for r in rows), reverse=True)
    assert all("volume" not in r for r in rows)            # extent drives the bar, not a figure
    assert sum(1 for r in rows if r["is_poc"]) == 1
    assert sum(1 for r in rows if r["in_value_area"]) == 1
    assert vp_view.histogram_rows(_profile([])) == []
