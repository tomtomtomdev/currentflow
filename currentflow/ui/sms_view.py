"""SMS / Rank view-model — the RULE B centerpiece. Pure data shaping, no Streamlit.

Pre-validation the module renders the score's **components** as raw observation and an
internal `ARMED` state, but the composite SMS number, any probability, and any
buy/sell verb are **withheld** (shown as `•••`). The presentation switch is driven by
`validation.state`, never a client toggle — post-validation the number may show.

Framing is always observation ("highest flow-signal names today — observation, not a
recommendation"), never advice.
"""

from __future__ import annotations

from currentflow.signals.engine import EngineResult, EngineState
from currentflow.signals.sms import SmsResult
from currentflow.validation.state import WITHHELD, ModuleState, gated_display, module_state

MODULE = "sms"

WATCHLIST_FRAMING = "highest flow-signal names today — observation, not a recommendation"
GATE_BANNER = (
    "SMS is computed internally to drive the ARMED watchlist, but its number stays "
    "hidden until this module clears PAPER_VALIDATION_MONTHS of forward paper (RULE B)."
)

# State → operator-facing label. None of these expose a number or a buy/sell verb.
_STATE_LABEL = {
    EngineState.ARMED: "ARMED — on watchlist (flow + phase aligned)",
    EngineState.WATCH: "watching — flow present, below internal bar",
    EngineState.VETOED: "vetoed — a §5 trap filter fired",
    EngineState.GATE_REJECTED: "not tradeable — phase gate (RULE A)",
}


def component_rows(result: SmsResult) -> list[dict]:
    """Per-component observation: strength bar + raw measurements. Allowed pre-
    validation — these are the *components*, not the composite score."""
    return [
        {
            "component": c.key,
            "weight": c.weight,                      # the locked §4 weight (a constant, not a score)
            "strength_pct": round(c.subscore * 100),  # this component's strength bar
            "available": c.available,
            "observation": c.observation,
        }
        for c in result.components
    ]


def score_display(
    result: SmsResult, *, registry: dict[str, ModuleState] | None = None
) -> str:
    """The composite SMS — a real number ONLY once the module is VALIDATED (RULE B)."""
    return gated_display(MODULE, round(result.internal_score), registry=registry, fmt="{:.0f}")


def state_label(engine_result: EngineResult) -> str:
    """The ARMED/WATCH/… state as a word — never a number (RULE B)."""
    return _STATE_LABEL.get(engine_result.state, engine_result.state.value)


def summary(
    engine_result: EngineResult, *, registry: dict[str, ModuleState] | None = None
) -> dict:
    """Everything the SMS/Rank panel would show. Pre-validation the `score` is `•••`."""
    return {
        "symbol": engine_result.symbol,
        "track": engine_result.track,
        "state": state_label(engine_result),
        "armed": engine_result.armed,
        "score": score_display(engine_result.sms, registry=registry),
        "validation_state": module_state(MODULE, registry).value,
        "components": component_rows(engine_result.sms),
        "vetoes": [v.reason.value for v in engine_result.veto.vetoes],
        "framing": WATCHLIST_FRAMING,
    }
