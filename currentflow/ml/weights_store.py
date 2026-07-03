"""The live SMS-weight surface with provenance — no hand-edit path exists (§4 / CLAUDE.md).

Weights are the only tunable surface and must never be hand-edited live: the walk-forward
optimizer is the sole writer. This store enforces that by construction — there is **no setter
that accepts raw weights**. The only mutation is `apply_proposal`, which takes a
`WeightProposal` from the optimizer and applies it only when:

  - the LD-8 gate is (still) open (`require_admission`), and
  - the proposal is `improved` — positive, non-degrading out-of-sample walk-forward Sharpe.

Every applied change is recorded with its provenance (OOS Sharpe, incumbent, trade count), so
the weight surface is always auditable back to the run that produced it. A proposal that fails
either check is refused (returns False) and the live surface is untouched — silent degradation
is impossible.
"""

from __future__ import annotations

import copy

from currentflow import config
from currentflow.ml.admission import require_admission
from currentflow.ml.optimizer import WeightProposal, validate_weights
from currentflow.validation.promotion import ValidationLedger


class WeightStore:
    """Provenance-tracked SMS weight surface. Optimizer proposals are the only writes."""

    def __init__(self, initial: dict[str, dict[str, int]] | None = None) -> None:
        src = initial if initial is not None else config.SMS_WEIGHTS
        self._live: dict[str, dict[str, int]] = copy.deepcopy(src)
        self._history: list[WeightProposal] = []

    def live(self, track: str) -> dict[str, int]:
        """The current weights for `track` (a copy — callers cannot mutate the surface)."""
        return dict(self._live[track])

    def apply_proposal(self, proposal: WeightProposal, ledger: ValidationLedger) -> bool:
        """Apply a proposal iff the LD-8 gate is open AND it does not degrade OOS Sharpe.

        Returns True when applied (and records provenance), False when refused. Raises
        `MLNotAdmittedError` if the ML layer is not admitted — application is re-gated, not
        assumed from the fact a proposal exists."""
        require_admission(ledger)  # LD-8 re-checked at apply time, never assumed
        if not proposal.improved:
            return False
        validate_weights(proposal.track, proposal.weights)  # locked §4 structure held
        self._live[proposal.track] = dict(proposal.weights)
        self._history.append(proposal)
        return True

    def history(self) -> list[WeightProposal]:
        """Applied proposals in order — the audit trail of every weight change."""
        return list(self._history)
