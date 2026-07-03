"""ML ranker over engineered features (LD-8, §9 AI Buy/Sell Ranking) — doubly gated.

LD-8 states ML also governs the AI ranker. This is a **transparent, linear** ranker over the
engineered SMS features (LD-8: engineered features only) — the score is the weighted feature
sum, i.e. the same locked §4 combination, used to *order* candidate names. No black box, no
new signal.

Two independent gates apply and neither is a client toggle:
  1. **LD-8 admission** — the ranker refuses to run until the rules system is VALIDATED
     (`require_admission`); the ML layer may not operate ahead of the rules.
  2. **RULE B display gate** — even when admitted, the ranker's numeric score and position are
     withheld (`•••`) until the `ai_ranking` module itself is VALIDATED in the ledger. The
     *ordering* is observation ("flow-derived ranking"); the *number* is a claim. Framing is
     always observation, never advice.
"""

from __future__ import annotations

from dataclasses import dataclass

from currentflow.ml.admission import require_admission
from currentflow.ml.features import FEATURE_KEYS, FeatureRow
from currentflow.validation.promotion import ValidationLedger
from currentflow.validation.state import ModuleState, gated_display

MODULE = "ai_ranking"
OBSERVATION_FRAMING = "flow-derived ML ranking — observation, not a recommendation"
CLAIM_FRAMING = "ML flow ranking (paper-validated)"


@dataclass(frozen=True, slots=True)
class RankedName:
    symbol: str
    track: str
    internal_score: float   # weighted engineered-feature sum — INTERNAL, gated for display


def score_row(row: FeatureRow, weights: dict[str, int]) -> float:
    """Linear score = Σ weightᵢ · featureᵢ over the engineered features (missing ≠ zero:
    an absent feature contributes nothing rather than a fabricated value)."""
    return sum(weights.get(k, 0) * row.features.get(k, 0.0) for k in FEATURE_KEYS)


def rank(
    rows: list[FeatureRow],
    *,
    weights: dict[str, int],
    ledger: ValidationLedger,
    registry: dict[str, ModuleState] | None = None,
) -> list[dict]:
    """Order candidate names by ML score. Requires LD-8 admission to run; the score/position
    number is withheld until the `ai_ranking` module is VALIDATED (RULE B).

    `registry` drives the display gate (defaults to the ledger's own states, which is
    server-authoritative). The *ordering* is always shown; only the *number* is gated."""
    require_admission(ledger)  # LD-8 — the ML layer may not run ahead of the rules system
    reg = registry if registry is not None else ledger.states()

    ranked = sorted(
        (RankedName(r.symbol, r.track, score_row(r, weights)) for r in rows),
        key=lambda x: x.internal_score,
        reverse=True,
    )
    return [
        {
            "position": gated_display(MODULE, i + 1, registry=reg),
            "symbol": rn.symbol,
            "track": rn.track,
            "score": gated_display(MODULE, round(rn.internal_score, 1), registry=reg, fmt="{:.1f}"),
        }
        for i, rn in enumerate(ranked)
    ]


def framing(*, registry: dict[str, ModuleState] | None = None) -> str:
    from currentflow.validation.state import may_display_number

    return CLAIM_FRAMING if may_display_number(MODULE, registry) else OBSERVATION_FRAMING
