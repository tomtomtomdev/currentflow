"""Pattern Catalog view-model — pure data shaping, no Streamlit imports (slice 21, LD-14).

RENDER RULES (PATTERN-CATALOG-SPEC §1, non-negotiable):
  * P1 — this is a dedicated research view. Base rates render ONLY here, labelled
    `historical frequency · window · n · 90% CI`; never on a live candidate row, the
    pipeline, the ARMED rail, or an evidence tab (enforced structurally — no live view
    imports this module).
  * P2 — no buy/sell verbs, no cross-pattern composite score, a base rate is never
    multiplied into SMS or any displayed number. Strings here are observations.
  * P3 — attaching a pattern's stats to a live name is a claim → the standard RULE B
    path (a dedicated ValidationLedger lane), not this view.
  * P4 — `n < CATALOG_MIN_N` renders the interval only (no point estimate); a window
    with no instances renders "no instances", never 0%. Missing ≠ zero.

The catalog measures pattern classes, not names: it says nothing about what to do.
"""

from __future__ import annotations

import json

from currentflow import config

CATALOG_BADGE = "CATALOG · historical frequency"
DISCLAIMER = (
    "Historical frequency measurements about pattern CLASSES (window · n · 90% CI), "
    "shown beside their unconditional null. A measurement, not a forward probability and "
    "not advice; never attached to a live name (RULE B / P1–P4). Stability unknown — "
    "current regime only."
)
NO_INSTANCES = "no instances"
STABILITY = "UNKNOWN (current regime only)"


def _pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "—"


def _rate_cell(entry: dict) -> dict:
    """P4 rendering of one (pattern, horizon) rate cell."""
    n = entry.get("n", 0)
    lo, hi = entry.get("ci90", [0.0, 1.0])
    interval = f"[{_pct(lo)}–{_pct(hi)}]"
    if n == 0:
        return {"n": 0, "small_n": True, "point": None, "display": NO_INSTANCES}
    if n < config.CATALOG_MIN_N:
        # small n → interval only, no point estimate (renders wide, never hides)
        return {"n": n, "small_n": True, "point": None, "display": interval}
    return {"n": n, "small_n": False, "point": _pct(entry.get("rate")),
            "display": f"{_pct(entry.get('rate'))} {interval}"}


def _horizon_rows(results: dict) -> list[dict]:
    rows = []
    for h, entry in sorted(results.get("horizons", {}).items(), key=lambda kv: int(kv[0])):
        rows.append({
            "horizon_days": int(h),
            "rate": _rate_cell(entry),
            # The null ALWAYS travels with the rate (§5.4) — both shown side by side.
            "null": _pct(entry.get("rate_uncond")),
            "n": entry.get("n", 0),
            "n_oos": entry.get("n_oos", 0),
            "rate_oos": _pct(entry.get("rate_oos")),
            "decay_flag": bool(entry.get("decay_flag")),
        })
    return rows


def catalog_cards(store) -> list[dict]:
    """One card per catalog entry version, shaped for the dedicated catalog view."""
    cards: list[dict] = []
    for row in store.read_pattern_catalog():
        spec = json.loads(row.spec_json) if row.spec_json else {}
        results = json.loads(row.results_json) if row.results_json else None
        outcome = spec.get("outcome_spec") or {}
        cards.append({
            "pattern_id": row.pattern_id,
            "version": row.version,
            "track": row.track,
            "status": row.status,
            "folk_claim": spec.get("folk_claim", ""),
            "definition": spec.get("definition", ""),
            "source": spec.get("source", ""),
            "outcome_label": _outcome_label(outcome),
            "stability": STABILITY,
            "confounds": (results or {}).get("confounds"),
            "horizons": _horizon_rows(results) if results else [],
            "estimated": results is not None,
        })
    return cards


def _outcome_label(outcome: dict) -> str:
    """A neutral description of the measured forward event — never a buy/sell verb."""
    target = outcome.get("target")
    direction = outcome.get("direction", "up")
    if target is None:
        return ""
    move = "reaches" if direction == "up" else "falls"
    return f"{move} {abs(target) * 100:.0f}% within horizon"


def catalog_summary(store) -> dict:
    """Header/summary for the view (counts by status). No composite score (P2)."""
    cards = catalog_cards(store)
    by_status: dict[str, int] = {}
    for c in cards:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
    return {"badge": CATALOG_BADGE, "disclaimer": DISCLAIMER,
            "count": len(cards), "by_status": by_status}
