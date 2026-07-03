"""LD-8 admission gate for the ML layer — the ML analogue of RULE B.

ML is deferred and gated (LD-8): before ANY optimizer or ranker may run, the rules system
must have earned its number — ≥ `PAPER_VALIDATION_MONTHS` of fill-realistic forward paper
with a positive walk-forward Sharpe. That is exactly the `sms` module reaching `VALIDATED`
in the server-authoritative `ValidationLedger`. Rationale (LD-8): reflexive, non-stationary,
small-sample IDX flow data overfits trivially, so ML is admitted only once the *non-ML* rules
have demonstrably survived forward paper.

This gate reads the ledger's state, never a client flag — it is server-authoritative just
like the observation↔claim switch (LD-9). Every ML entry point calls `require_admission`
first; when the gate is closed it raises `MLNotAdmittedError`, so nothing in `currentflow.ml`
can run ahead of the rules system.
"""

from __future__ import annotations

from dataclasses import dataclass

from currentflow import config
from currentflow.validation.promotion import ValidationLedger
from currentflow.validation.state import ModuleState


class MLNotAdmittedError(RuntimeError):
    """Raised when an ML entry point is called before the LD-8 gate has opened."""


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    admitted: bool
    module: str
    state: ModuleState
    reason: str


def check_admission(
    ledger: ValidationLedger, *, module: str = config.ML_ADMISSION_MODULE
) -> AdmissionDecision:
    """Resolve whether the ML layer may run, from the ledger's rules-system state (LD-8).

    Admitted iff the admitting module (the rules system, `sms`) is `VALIDATED` — i.e. it has
    cleared ≥ `PAPER_VALIDATION_MONTHS` of forward paper with a positive walk-forward Sharpe.
    Pure read of the server-authoritative ledger; never self-admits."""
    state = ledger.state(module)
    admitted = state is ModuleState.VALIDATED
    reason = (
        f"rules system {module!r} is VALIDATED — ML admitted (LD-8)"
        if admitted
        else f"ML locked: rules system {module!r} is {state.value}, not VALIDATED "
        f"(LD-8 needs ≥{config.PAPER_VALIDATION_MONTHS}mo forward paper + positive walk-forward Sharpe)"
    )
    return AdmissionDecision(admitted=admitted, module=module, state=state, reason=reason)


def require_admission(
    ledger: ValidationLedger, *, module: str = config.ML_ADMISSION_MODULE
) -> AdmissionDecision:
    """Return the admission decision, or raise `MLNotAdmittedError` if the gate is closed.

    THE guard every ML entry point calls first (optimizer, ranker, weight application)."""
    decision = check_admission(ledger, module=module)
    if not decision.admitted:
        raise MLNotAdmittedError(decision.reason)
    return decision
