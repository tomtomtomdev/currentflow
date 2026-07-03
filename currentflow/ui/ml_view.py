"""ML layer status view-model (slice 9) — surfaces the LD-8 gate, RULE-B-safe.

The ML layer is gated shut until the rules system earns its number (LD-8). This view-model
reports that admission state and, once open, the provenance of any applied weight proposal —
purely operational diagnostics (OOS Sharpe of a *proposal*, trade counts), never a per-name
confidence, probability, SMS, or buy/sell claim. It shows the operator *why* ML is or isn't
running; it never emits a predictive number for a name (that stays behind the ranker's own
RULE B gate). Pure data shaping — no Streamlit.
"""

from __future__ import annotations

from currentflow import config
from currentflow.ml.admission import check_admission
from currentflow.ml.optimizer import WeightProposal
from currentflow.validation.promotion import ValidationLedger

LOCKED_BANNER = "ML LAYER LOCKED — LD-8: rules system not yet paper-validated"
OPEN_BANNER = "ML LAYER ADMITTED — rules system validated (LD-8)"


def status(
    ledger: ValidationLedger, *, history: list[WeightProposal] | None = None
) -> dict:
    """The ML layer's operational status for the terminal (admission + weight provenance)."""
    decision = check_admission(ledger)
    hist = history or []
    return {
        "admitted": decision.admitted,
        "banner": OPEN_BANNER if decision.admitted else LOCKED_BANNER,
        "detail": decision.reason,
        "admission_module": decision.module,
        "required_months": config.PAPER_VALIDATION_MONTHS,
        "weight_updates": len(hist),
        "last_update": _provenance(hist[-1]) if hist else None,
    }


def _provenance(p: WeightProposal) -> dict:
    """Auditable provenance of an applied weight proposal — a diagnostic, not a name claim."""
    return {
        "track": p.track,
        "weights": dict(p.weights),
        "incumbent": dict(p.incumbent),
        "oos_walk_forward_sharpe": p.oos_walk_forward_sharpe,
        "incumbent_oos_sharpe": p.incumbent_oos_sharpe,
        "n_trades": p.n_trades,
    }
