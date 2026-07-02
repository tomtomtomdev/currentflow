"""Per-module validation state — drives the observation↔claim UI switch (RULE B).

A module may display a confidence number, probability, Smart Money Score, or ranked
buy/sell claim ONLY when it is `VALIDATED`. Until then it is `OBSERVATION_ONLY`: the
UI renders components/raw flow with no number attached.

This slice ships the switch with every gated module defaulted to `OBSERVATION_ONLY`.
The paper-trade engine (slice 8) is the sole authority that advances a module to
`VALIDATING` and then `VALIDATED` after `config.PAPER_VALIDATION_MONTHS` of
fill-realistic forward paper. Nothing here ever promotes a module on its own — a
client toggle must never flip it (the state is server-authoritative; CLAUDE.md).
"""

from __future__ import annotations

from enum import Enum


class ModuleState(str, Enum):
    OBSERVATION_ONLY = "OBSERVATION_ONLY"   # RULE B: components only, no number
    VALIDATING = "VALIDATING"               # accruing forward paper, still no number
    VALIDATED = "VALIDATED"                 # earned the right to show its number


# Gated modules (spec §9). All start observation-only — no number until earned.
GATED_MODULES = ("sms", "ai_ranking", "daily_top")

_DEFAULT_STATES: dict[str, ModuleState] = {m: ModuleState.OBSERVATION_ONLY for m in GATED_MODULES}


def module_state(
    module: str, registry: dict[str, ModuleState] | None = None
) -> ModuleState:
    """Current validation state of `module` (defaults to OBSERVATION_ONLY)."""
    reg = _DEFAULT_STATES if registry is None else registry
    return reg.get(module, ModuleState.OBSERVATION_ONLY)


def may_display_number(
    module: str, registry: dict[str, ModuleState] | None = None
) -> bool:
    """RULE B gate: True only when the module has cleared paper validation."""
    return module_state(module, registry) is ModuleState.VALIDATED
