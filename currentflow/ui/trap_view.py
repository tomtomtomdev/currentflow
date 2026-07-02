"""Trap / decay ribbon view-model — pure data shaping, no Streamlit imports.

The credibility layer surfaced across every module (slice 5): it merges the slice-4
**veto** trap taxonomy (§5) with the slice-5 **decay** signals (§8) into one ordered
list of flags, most-severe first, so the same trap/decay picture is wired into every
view from one place.

RULE B: every string here is a categorical reason + the observation that tripped it —
no score, no probability, no buy/sell verb. Severity is a *word* (INFO/WATCH/WARN),
never a number.
"""

from __future__ import annotations

from currentflow.signals.distribution import DecaySeverity, TrapMonitor

# A veto is a hard trap — always shown at the top severity.
_TRAP_SEVERITY = "WARN"

SEVERITY_ICON = {"INFO": "○", "WATCH": "◆", "WARN": "⚠"}
_SEVERITY_RANK = {"INFO": 0, "WATCH": 1, "WARN": 2}

CLEAN_LABEL = "no trap or decay flags — clean"


def ribbon_rows(monitor: TrapMonitor) -> list[dict]:
    """Unified veto + decay flags as display rows, most-severe first. Empty when the
    name is clean."""
    rows: list[dict] = [
        {
            "category": "TRAP",
            "kind": v.reason.value,
            "severity": _TRAP_SEVERITY,
            "icon": SEVERITY_ICON[_TRAP_SEVERITY],
            "detail": v.detail,
        }
        for v in monitor.veto.vetoes
    ]
    rows += [
        {
            "category": "DECAY",
            "kind": f.kind.value,
            "severity": f.severity.value,
            "icon": SEVERITY_ICON[f.severity.value],
            "detail": f.detail,
        }
        for f in monitor.decay.flags
    ]
    rows.sort(key=lambda r: _SEVERITY_RANK[r["severity"]], reverse=True)
    return rows


def ribbon_banner(monitor: TrapMonitor) -> str | None:
    """One-line summary for the top-of-module ribbon, or None when clean."""
    rows = ribbon_rows(monitor)
    if not rows:
        return None
    lead = rows[0]
    extra = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
    return f"{lead['icon']} {lead['kind']} — {lead['detail']}{extra}"


def max_severity(monitor: TrapMonitor) -> str | None:
    """Highest severity across all flags as a word, or None when clean (RULE B)."""
    rows = ribbon_rows(monitor)
    if not rows:
        return None
    return max((r["severity"] for r in rows), key=lambda s: _SEVERITY_RANK[s])
