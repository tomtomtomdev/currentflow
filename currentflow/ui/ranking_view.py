"""AI Buy/Sell Ranking view-model — a gated module (spec §9, RULE B / LD-8).

Pre-validation this is a **"flow-derived ranking, not a recommendation"**: names are
ordered by their internal SMS (so the operator sees where the flow is strongest), but the
score number, any probability, and any buy/sell verb are **withheld** (`•••`). Stronger
ranking language is earned only once the module clears `PAPER_VALIDATION_MONTHS` of
fill-realistic forward paper (LD-8 also governs the ML ranker).

The presentation switch is server-authoritative via `validation.state`, never a client
toggle. Pure data shaping — no Streamlit.
"""

from __future__ import annotations

from currentflow.signals.engine import EngineResult
from currentflow.ui.sms_view import state_label
from currentflow.validation.state import ModuleState, gated_display

MODULE = "ai_ranking"

# Observation framing pre-validation; never advice.
OBSERVATION_FRAMING = "flow-derived ranking — observation, not a recommendation"
CLAIM_FRAMING = "flow ranking (paper-validated)"


def ranking(
    results: list[EngineResult], *, registry: dict[str, ModuleState] | None = None
) -> list[dict]:
    """Names ordered by internal flow strength. The score/rank number is withheld until
    the module is VALIDATED — the *ordering* is observation; the *number* is a claim."""
    ordered = sorted(results, key=lambda r: r.sms.internal_score, reverse=True)
    return [
        {
            "position": gated_display(MODULE, i + 1, registry=registry),
            "symbol": r.symbol,
            "track": r.track,
            "state": state_label(r),                                  # a word, never a verb
            "score": gated_display(MODULE, round(r.sms.internal_score), registry=registry, fmt="{:.0f}"),
            "armed": r.armed,
        }
        for i, r in enumerate(ordered)
    ]


def framing(*, registry: dict[str, ModuleState] | None = None) -> str:
    from currentflow.validation.state import may_display_number

    return CLAIM_FRAMING if may_display_number(MODULE, registry) else OBSERVATION_FRAMING
