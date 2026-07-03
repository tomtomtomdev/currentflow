"""Per-module validation state machine (spec §8/§11, RULE B / LD-9) — the sole authority.

The paper-trade engine is the **only** thing that may advance a module's validation state.
This ledger encodes that: state changes only through `record_forward_paper`, which takes a
module's accrued forward-paper trades and months, computes the walk-forward Sharpe, and
resolves the state. There is deliberately **no public setter** to mark a module VALIDATED —
the switch is server-authoritative, never a client toggle (CLAUDE.md / LD-9).

Promotion rule (§8 validation gate):

    OBSERVATION_ONLY   months == 0                      (no forward paper yet)
    VALIDATING         0 < months < PAPER_VALIDATION_MONTHS,  OR
                       months ≥ PAPER_VALIDATION_MONTHS but walk-forward Sharpe ≤ 0 / None
    VALIDATED          months ≥ PAPER_VALIDATION_MONTHS  AND  positive walk-forward Sharpe

Only a `VALIDATED` module may display its number; the resolved states plug straight into
`validation.state` as the registry the gated view-models read.
"""

from __future__ import annotations

from dataclasses import dataclass

from currentflow import config
from currentflow.validation import metrics
from currentflow.validation.state import GATED_MODULES, ModuleState
from currentflow.validation.trade import PaperTrade


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    """A module's accrued forward-paper evidence and the state it resolves to."""

    module: str
    months_accrued: float
    n_trades: int
    walk_forward_sharpe: float | None
    state: ModuleState


def resolve_state(
    months_accrued: float,
    walk_forward_sharpe: float | None,
    *,
    required_months: int = config.PAPER_VALIDATION_MONTHS,
) -> ModuleState:
    """The §8 gate as a pure function of accrued months + walk-forward Sharpe."""
    if months_accrued <= 0:
        return ModuleState.OBSERVATION_ONLY
    if months_accrued >= required_months and walk_forward_sharpe is not None and walk_forward_sharpe > 0:
        return ModuleState.VALIDATED
    # Enough flow to be accruing, but not yet earned the number.
    return ModuleState.VALIDATING


class ValidationLedger:
    """Server-authoritative registry of per-module validation state.

    Seeded with every gated module OBSERVATION_ONLY. The only writer is
    `record_forward_paper`; `states()` yields the registry the UI consumes read-only.
    """

    def __init__(self, *, required_months: int = config.PAPER_VALIDATION_MONTHS) -> None:
        self._required_months = required_months
        self._records: dict[str, ValidationRecord] = {
            m: ValidationRecord(m, 0.0, 0, None, ModuleState.OBSERVATION_ONLY)
            for m in GATED_MODULES
        }

    def record_forward_paper(
        self,
        module: str,
        *,
        trades: list[PaperTrade],
        months_accrued: float,
        folds: int = 3,
    ) -> ValidationRecord:
        """Ingest a module's forward-paper record and (re)resolve its state.

        THE sole promotion path (LD-9): computes the walk-forward Sharpe from `trades`,
        applies the §8 gate, and stores the new record. Returns it."""
        wf = metrics.walk_forward_sharpe(trades, folds=folds)
        state = resolve_state(months_accrued, wf, required_months=self._required_months)
        rec = ValidationRecord(
            module=module, months_accrued=months_accrued, n_trades=len(trades),
            walk_forward_sharpe=wf, state=state,
        )
        self._records[module] = rec
        return rec

    def record(self, module: str) -> ValidationRecord:
        return self._records.get(
            module, ValidationRecord(module, 0.0, 0, None, ModuleState.OBSERVATION_ONLY)
        )

    def state(self, module: str) -> ModuleState:
        return self.record(module).state

    def states(self) -> dict[str, ModuleState]:
        """The registry passed to the gated view-models (read-only snapshot)."""
        return {m: r.state for m, r in self._records.items()}
