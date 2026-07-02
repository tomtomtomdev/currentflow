"""Trap / decay ribbon view-model — unifies §5 veto traps with §8 decay flags, orders
them most-severe first, and never leaks a score / probability / recommendation (RULE B).
"""

from __future__ import annotations

from datetime import datetime

from currentflow.signals.distribution import (
    DecayFlag,
    DecayKind,
    DecayReport,
    DecaySeverity,
    TrapMonitor,
)
from currentflow.signals.veto import Veto, VetoReason, VetoResult
from currentflow.ui.trap_view import max_severity, ribbon_banner, ribbon_rows

TS = datetime(2026, 7, 1, 9, 0)


def _monitor(vetoes=(), decay=()):
    return TrapMonitor(
        symbol="X", decision_ts=TS,
        veto=VetoResult("X", TS, vetoes=tuple(vetoes)),
        decay=DecayReport("X", TS, flags=tuple(decay)),
    )


def test_clean_monitor_has_empty_ribbon():
    mon = _monitor()
    assert ribbon_rows(mon) == []
    assert ribbon_banner(mon) is None
    assert max_severity(mon) is None


def test_ribbon_merges_veto_and_decay_severity_first():
    mon = _monitor(
        vetoes=[Veto(VetoReason.WASH_CHURN, "BQ bought and sold near-equally")],
        decay=[
            DecayFlag(DecayKind.NO_DEMAND, DecaySeverity.WATCH, "up bar on shrinking volume"),
            DecayFlag(DecayKind.BEARISH_DIVERGENCE, DecaySeverity.WARN, "price up while flow falls"),
        ],
    )
    rows = ribbon_rows(mon)
    assert len(rows) == 3
    assert {r["category"] for r in rows} == {"TRAP", "DECAY"}
    # WARN flags (the veto + the divergence) sort ahead of the WATCH no-demand flag.
    assert rows[0]["severity"] == "WARN"
    assert rows[-1]["kind"] == "NO_DEMAND"
    assert max_severity(mon) == "WARN"


def test_banner_summarizes_lead_flag_with_overflow_count():
    mon = _monitor(
        vetoes=[Veto(VetoReason.SINGLE_BANDAR_MONOPOLY, "top broker holds 70% of net buying")],
        decay=[DecayFlag(DecayKind.FOREIGN_OUTFLOW, DecaySeverity.WATCH, "foreign net selling 3 days")],
    )
    banner = ribbon_banner(mon)
    assert banner.startswith("⚠ SINGLE_BANDAR_MONOPOLY")
    assert "(+1 more)" in banner


def test_ribbon_is_rule_b_safe():
    mon = _monitor(
        decay=[DecayFlag(DecayKind.BEARISH_DIVERGENCE, DecaySeverity.WARN, "price +5% while flow falls")],
    )
    blob = " ".join(str(v) for r in ribbon_rows(mon) for v in r.values()).lower()
    # observation only — no score, no probability, no advice verb.
    for forbidden in ("probability", "confidence", "recommend", "sms "):
        assert forbidden not in blob
    # severity is a word, never a number.
    assert all(r["severity"] in {"INFO", "WATCH", "WARN"} for r in ribbon_rows(mon))
