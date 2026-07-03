"""Daily Top Opportunities view-model — a gated module (spec §9, RULE B).

"Highest flow-signal names today" — a narrative digest of what the flow shows. Framing is
always observation, never advice. Pre-validation the digest lists the day's ARMED names
with a state word and their SMS *components*, but the composite score and any ranking
number are **withheld** (`•••`). Post-validation the top-N may show its numbers.

The switch is server-authoritative via `validation.state`. Pure data shaping.
"""

from __future__ import annotations

from currentflow.signals.engine import EngineResult, EngineState
from currentflow.validation.state import ModuleState, gated_display, may_display_number

MODULE = "daily_top"

OBSERVATION_FRAMING = "highest flow-signal names today — observation, not a recommendation"
CLAIM_FRAMING = "top opportunities today (paper-validated)"


def digest(
    results: list[EngineResult],
    *,
    top_n: int = 10,
    registry: dict[str, ModuleState] | None = None,
) -> dict:
    """The day's digest. Pre-validation it surfaces ARMED names as observation (state +
    components), the composite score withheld; post-validation the ranked top-N shows."""
    armed = [r for r in results if r.state is EngineState.ARMED]
    ordered = sorted(armed, key=lambda r: r.sms.internal_score, reverse=True)[:top_n]
    rows = [
        {
            "symbol": r.symbol,
            "track": r.track,
            "score": gated_display(MODULE, round(r.sms.internal_score), registry=registry, fmt="{:.0f}"),
            # components are always observation-safe (they are the parts, not the score)
            "components": [
                {"component": c.key, "strength_pct": round(c.subscore * 100), "observation": c.observation}
                for c in r.sms.components
            ],
        }
        for r in ordered
    ]
    return {
        "count": len(armed),
        "framing": CLAIM_FRAMING if may_display_number(MODULE, registry) else OBSERVATION_FRAMING,
        "names": rows,
    }
