"""Walk-forward Sharpe weight optimizer (LD-1 / LD-8) — the sole writer of SMS weights.

The SMS weights are the **only** tunable surface (§4) and CLAUDE.md forbids hand-editing them
live: they may change only via backtest-Sharpe maximization with walk-forward. This is that
optimizer. It searches the integer weight simplex by deterministic coordinate ascent —
repeatedly shifting `step` from one component to another and keeping a move only if it lifts
the **in-sample (train-fold) Sharpe** — then reports the **out-of-sample (worst test-fold)
walk-forward Sharpe** as the acceptance gate. Search fits on train; the OOS gate guards it.

Hard rules honoured:
  - **LD-8 admission** — refuses to run until the rules system is VALIDATED (`require_admission`).
  - **Locked §4 structure** — weights stay non-negative integers summing to `ML_WEIGHT_SUM`,
    and the `ML_LOCKED_ZEROS` components (Track B foreign flow, LD-1) are never funded.
  - **Never degrade** — a proposal is only `improved` when its OOS Sharpe is positive and at
    least the incumbent's; the optimizer proposes, it does not silently mutate the live surface
    (that is `weights_store.apply_proposal`, which re-checks admission).

Purge + embargo come from `ml.cv`: folds are built from each candidate's realised paper trades
(label span = entry→exit), so no train trade whose outcome overlaps a test window is scored.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass

from currentflow import config
from currentflow.ml.admission import require_admission
from currentflow.ml.cv import Fold, InsufficientSamplesError, Sample, purged_walk_forward
from currentflow.signals.sms import COMPONENT_KEYS
from currentflow.validation.promotion import ValidationLedger
from currentflow.validation.trade import PaperTrade

# A backtest under a candidate weight vector → the realised closed paper trades.
Evaluate = Callable[[dict[str, int]], list[PaperTrade]]


@dataclass(frozen=True, slots=True)
class WeightProposal:
    """The optimizer's output — a candidate weight surface with full provenance. Never applied
    by the optimizer itself; `weights_store.apply_proposal` decides whether it may go live."""

    track: str
    weights: dict[str, int]
    incumbent: dict[str, int]
    train_sharpe: float | None            # in-sample search objective (mean train-fold Sharpe)
    oos_walk_forward_sharpe: float | None  # worst purged test-fold Sharpe (the acceptance gate)
    incumbent_oos_sharpe: float | None
    n_trades: int
    improved: bool                         # OOS positive AND ≥ incumbent OOS → safe to apply


def validate_weights(track: str, weights: dict[str, int]) -> None:
    """Assert `weights` respect the locked §4 structure (raises ValueError otherwise)."""
    if set(weights) != set(COMPONENT_KEYS):
        raise ValueError(f"weights must key exactly {COMPONENT_KEYS}")
    if any(not isinstance(w, int) or w < 0 for w in weights.values()):
        raise ValueError("weights must be non-negative integers")
    if sum(weights.values()) != config.ML_WEIGHT_SUM:
        raise ValueError(f"weights must sum to {config.ML_WEIGHT_SUM}")
    for locked in config.ML_LOCKED_ZEROS.get(track, frozenset()):
        if weights.get(locked, 0) != 0:
            raise ValueError(f"component {locked!r} is locked to 0 on track {track!r} (LD-1)")


def _sharpe(returns: list[float]) -> float | None:
    """Per-trade Sharpe, matching `validation.metrics` semantics (None if undefined)."""
    if len(returns) < 2:
        return None
    sd = statistics.stdev(returns)
    return None if sd == 0 else statistics.fmean(returns) / sd


def _folds(trades: list[PaperTrade], *, folds: int, embargo_frac: float) -> list[Fold]:
    samples = [Sample(t0=t.entry_date, t1=t.exit_date) for t in trades]
    return purged_walk_forward(samples, folds=folds, embargo_frac=embargo_frac)


def _train_objective(trades: list[PaperTrade], folds_: list[Fold]) -> float | None:
    """Mean over folds of each fold's TRAIN Sharpe — the in-sample search objective.

    `None` if any fold's train Sharpe is undefined (too few/zero-dispersion train trades) —
    an unscoreable candidate, never treated as 0 (missing ≠ zero)."""
    fold_sharpes: list[float] = []
    for f in folds_:
        s = _sharpe([trades[i].net_return for i in f.train])
        if s is None:
            return None
        fold_sharpes.append(s)
    return statistics.fmean(fold_sharpes) if fold_sharpes else None


def _oos_worst(trades: list[PaperTrade], folds_: list[Fold]) -> float | None:
    """Worst (min) over folds of each fold's TEST Sharpe — the out-of-sample acceptance gate."""
    fold_sharpes: list[float] = []
    for f in folds_:
        s = _sharpe([trades[i].net_return for i in f.test])
        if s is None:
            return None
        fold_sharpes.append(s)
    return min(fold_sharpes) if fold_sharpes else None


def _score(
    weights: dict[str, int], evaluate: Evaluate, *, folds: int, embargo_frac: float
) -> tuple[float | None, float | None, int]:
    """Return (train objective, OOS worst-fold, n_trades) for a candidate — Nones if unscoreable."""
    trades = evaluate(weights)
    try:
        fs = _folds(trades, folds=folds, embargo_frac=embargo_frac)
    except InsufficientSamplesError:
        return None, None, len(trades)
    return _train_objective(trades, fs), _oos_worst(trades, fs), len(trades)


def _neighbours(track: str, weights: dict[str, int], step: int) -> list[dict[str, int]]:
    """Coordinate moves: shift `step` from one component to another, preserving sum and the
    locked zeros. Deterministic iteration order (by component key)."""
    locked = config.ML_LOCKED_ZEROS.get(track, frozenset())
    movable = [k for k in COMPONENT_KEYS if k not in locked]
    out: list[dict[str, int]] = []
    for src in movable:
        if weights[src] < step:
            continue
        for dst in movable:
            if dst == src:
                continue
            cand = dict(weights)
            cand[src] -= step
            cand[dst] += step
            out.append(cand)
    return out


def optimize_weights(
    track: str,
    *,
    evaluate: Evaluate,
    ledger: ValidationLedger,
    incumbent: dict[str, int] | None = None,
    folds: int = config.ML_CV_FOLDS,
    embargo_frac: float = config.ML_EMBARGO_FRAC,
    step: int = config.ML_WEIGHT_STEP,
    max_iters: int = 200,
) -> WeightProposal:
    """Search the weight simplex for the track by walk-forward Sharpe (LD-1/LD-8).

    Refuses to run until the rules system is VALIDATED (LD-8). Returns a `WeightProposal`;
    it never mutates `config.SMS_WEIGHTS` — applying a proposal is a separate, re-gated step."""
    require_admission(ledger)  # LD-8 — raises MLNotAdmittedError if the rules aren't validated

    incumbent = dict(incumbent) if incumbent is not None else dict(config.SMS_WEIGHTS[track])
    validate_weights(track, incumbent)

    current = dict(incumbent)
    best_obj, _, _ = _score(current, evaluate, folds=folds, embargo_frac=embargo_frac)

    for _ in range(max_iters):
        improved_here = False
        for cand in _neighbours(track, current, step):
            obj, _, _ = _score(cand, evaluate, folds=folds, embargo_frac=embargo_frac)
            if obj is not None and (best_obj is None or obj > best_obj):
                current, best_obj, improved_here = cand, obj, True
        if not improved_here:
            break

    validate_weights(track, current)  # invariant held throughout the search
    train_sharpe, oos, n = _score(current, evaluate, folds=folds, embargo_frac=embargo_frac)
    _, incumbent_oos, _ = _score(incumbent, evaluate, folds=folds, embargo_frac=embargo_frac)

    improved = (
        oos is not None
        and oos > 0
        and (incumbent_oos is None or oos >= incumbent_oos)
        and current != incumbent
    )
    return WeightProposal(
        track=track, weights=current, incumbent=incumbent,
        train_sharpe=train_sharpe, oos_walk_forward_sharpe=oos,
        incumbent_oos_sharpe=incumbent_oos, n_trades=n, improved=improved,
    )
