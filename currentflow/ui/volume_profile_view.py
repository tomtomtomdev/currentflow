"""Approximate volume-profile view-model (LD-13, slice 19) — pure data shaping, no Streamlit.

Observation only (RULE B): the profile's output is *structure* — labeled price levels on a
chart the operator is already reading in rupiah (POC / VAH / VAL) and named regions (HVN /
LVN). No score, no confidence, no verb.

**Every surface built from this module carries `ANNOTATION`.** A point of control estimated
from daily bars is not the intraday one, and the §4.1 fidelity-honesty rule says the view
must never render or imply otherwise — so the caveat travels with the data, not as optional
chrome a caller may forget.
"""

from __future__ import annotations

from currentflow.signals import volume_profile as vp_mod
from currentflow.signals.volume_profile import ANNOTATION, NodeKind, VolumeProfile

# Per-level short label + semantic color key (shell.TOKENS keys) + the plain reading.
LEVEL_COPY: dict[str, tuple[str, str, str]] = {
    "POC": ("POC", "armed", "point of control — the price this window did most of its business at"),
    "VAH": ("VAH", "text_muted",
            "value-area high — the top of the band that held most of the window's volume"),
    "VAL": ("VAL", "text_muted", "value-area low — the bottom of that band"),
}

NODE_COPY: dict[str, tuple[str, str]] = {
    "HVN": ("high-volume node", "smart"),
    "LVN": ("low-volume node", "text_faint"),
}

FRAMING = f"volume at price · {ANNOTATION} · observation"
EMPTY_LABEL = "not enough complete bars to estimate a volume distribution — no profile"


def levels(profile: VolumeProfile) -> list[dict]:
    """POC / VAH / VAL as chart-overlay rows, price-descending (VAH → POC → VAL), or []
    when the profile is unavailable — never a midpoint stand-in (`missing ≠ zero`)."""
    if not profile.available:
        return []
    out = []
    for key, price in (("VAH", profile.vah), ("POC", profile.poc), ("VAL", profile.val)):
        if price is None:
            continue
        label, color_key, note = LEVEL_COPY[key]
        out.append({
            "level": key, "label": label, "price": price,
            "color_key": color_key, "note": note, "annotation": ANNOTATION,
        })
    return out


def histogram_rows(profile: VolumeProfile) -> list[dict]:
    """One row per price bucket, price-descending — the horizontal VAP lane beside the
    chart. `share` drives the bar length only; it is never printed as a figure."""
    if not profile.available or profile.total_volume <= 0:
        return []
    peak = max(b.volume for b in profile.buckets) or 1.0
    rows = []
    for b in reversed(profile.buckets):
        rows.append({
            "low": b.low, "high": b.high, "mid": b.mid,
            "kind": b.kind.value,
            "share": b.volume / profile.total_volume,
            "extent": b.volume / peak,
            "in_value_area": (
                profile.val is not None and profile.vah is not None
                and b.high > profile.val and b.low < profile.vah
            ),
            "is_poc": profile.poc is not None and b.low <= profile.poc <= b.high,
        })
    return rows


def node_rows(profile: VolumeProfile) -> list[dict]:
    """The named HVN / LVN regions, price-descending. Ordinary runs are dropped — they are
    the absence of a node, not a node of their own."""
    if not profile.available:
        return []
    out = []
    for n in reversed(profile.nodes):
        if n.kind is NodeKind.ORDINARY:
            continue
        label, color_key = NODE_COPY[n.kind.value]
        out.append({
            "kind": n.kind.value, "label": label, "color_key": color_key,
            "low": n.low, "high": n.high, "share": n.share,
        })
    return out


def profile_panel(profile: VolumeProfile) -> dict:
    """The panel reading: the three levels, how many bars they were estimated from, and
    the caveat. `bars_profiled` is a data-availability count, not a measurement."""
    if not profile.available:
        return {
            "available": False,
            "headline": EMPTY_LABEL,
            "note": profile.note,
            "annotation": ANNOTATION,
            "levels": [],
            "nodes": [],
            "bars_profiled": len(profile.bars),
            "window": None,
        }
    window = None
    if profile.start is not None and profile.end is not None:
        window = f"{profile.start:%d %b %Y} → {profile.end:%d %b %Y}"
    return {
        "available": True,
        "headline": profile.note,
        "note": profile.note,
        "annotation": ANNOTATION,
        "levels": levels(profile),
        "nodes": node_rows(profile),
        "bars_profiled": len(profile.bars),
        "window": window,
    }


def confluence_notes(profile: VolumeProfile, events=()) -> list[str]:
    """The Spring@VAL / UTAD@VAH / LPS@POC readings for a classification's events, as
    plain lines. Corroboration text only — the phase verdict is decided elsewhere and is
    not affected by what this returns (RULE A)."""
    return [c.note for c in vp_mod.confluences(profile, events)]
