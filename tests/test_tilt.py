"""Fundamental tilt (§7) — tercile assignment, FLOW_ONLY dual track, negative-EBIT
override, and the missing-≠-zero neutral default. Fundamentals never gate; they only
size (LD-6)."""

from __future__ import annotations

from currentflow import config
from currentflow.fundamentals.tilt import (
    HoldHorizon,
    TiltKind,
    TrailProfile,
    classify_tilt,
    is_flow_only,
)


def test_top_tercile_is_compounder():
    t = classify_tilt("TLKM", sector="INFRASTRUCTURE_TELCO", mf_rank_pct=90)
    assert t.kind is TiltKind.COMPOUNDER
    assert t.multiplier == config.CONVICTION_COMPOUNDER == 1.0
    assert t.hold is HoldHorizon.THROUGH_MARKUP
    assert t.trail is TrailProfile.WIDE
    assert t.trail_pct == config.TRAIL_WIDE


def test_mid_tercile_is_neutral():
    t = classify_tilt("X", sector="CONSUMER", mf_rank_pct=50)
    assert t.kind is TiltKind.NEUTRAL
    assert t.multiplier == 0.75
    assert t.trail is TrailProfile.STANDARD


def test_bottom_tercile_is_speculative():
    t = classify_tilt("X", sector="CONSUMER", mf_rank_pct=10)
    assert t.kind is TiltKind.SPECULATIVE
    assert t.multiplier == 0.5
    assert t.hold is HoldHorizon.FIRST_TARGET
    assert t.trail is TrailProfile.TIGHT


def test_negative_ebit_forces_speculative_even_with_high_rank():
    # §7: negative EBIT is speculative regardless of the combined rank.
    t = classify_tilt("X", sector="CONSUMER", mf_rank_pct=95, ev_ebit=-8.0)
    assert t.kind is TiltKind.SPECULATIVE
    assert t.multiplier == 0.5


def test_missing_rank_is_neutral_not_zero():
    # missing ≠ zero: absent MF rank → the un-tilted NEUTRAL default, never bottom tercile.
    t = classify_tilt("X", sector="CONSUMER", mf_rank_pct=None)
    assert t.kind is TiltKind.NEUTRAL
    assert t.multiplier == 0.75


def test_financials_are_flow_only_default():
    t = classify_tilt("BBRI", sector="Financials", mf_rank_pct=95)  # rank ignored for FLOW_ONLY
    assert t.kind is TiltKind.FLOW_ONLY
    assert t.multiplier == config.CONVICTION_FLOW_ONLY == 0.75
    assert t.hold is HoldHorizon.SHORT
    assert t.trail is TrailProfile.TIGHT


def test_flow_only_promoted_by_roe_proxy_never_compounder_hold():
    # A healthy bank proxy (ROE > 12%) lifts the multiplier to ×1.0 but keeps the
    # FLOW_ONLY short hold / tight trail — never COMPOUNDER hold rules (§7).
    t = classify_tilt("BBCA", sector="BANK", roe=0.18)
    assert t.kind is TiltKind.FLOW_ONLY
    assert t.multiplier == 1.0
    assert t.hold is HoldHorizon.SHORT
    assert t.trail is TrailProfile.TIGHT


def test_flow_only_weak_roe_stays_default():
    t = classify_tilt("BANK2", sector="BANK", roe=0.08)
    assert t.multiplier == 0.75


def test_is_flow_only_helper():
    assert is_flow_only("Utilities")
    assert is_flow_only("FINANCE")
    assert not is_flow_only("Consumer")
    assert not is_flow_only(None)
