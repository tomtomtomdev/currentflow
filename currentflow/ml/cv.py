"""Purged + embargoed walk-forward cross-validation (LD-8, mandatory).

Financial ML leaks trivially: a label spans time (a trade's entry→exit), so a training
sample whose label overlaps the test window has seen the future. LD-8 makes purged/embargoed
CV mandatory. This module builds **walk-forward** folds (train strictly precedes test — the
only honest out-of-sample direction for a live-accruing strategy) and then:

  - **purge** — drops any train sample whose label span [t0, t1] spills into the test window
    (its outcome is co-determined by the test period), and
  - **embargo** — drops an additional buffer of the most-recent train samples adjacent to the
    test start (serial correlation leaks across the boundary even without label overlap).

`missing ≠ zero`: too few samples to form honest folds raises rather than fabricating a fold.
The output is deterministic — index math only, no randomness (walk-forward, not shuffled).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as Date

from currentflow import config


@dataclass(frozen=True, slots=True)
class Sample:
    """A labeled training sample. `t0` = when the label starts (decision/entry date),
    `t1` = when it resolves (exit date). `t1 is None` → an as-yet-unresolved label (treated
    as spanning to the far future for purge purposes — conservatively leaky, so purged)."""

    t0: Date
    t1: Date | None = None

    @property
    def label_end(self) -> Date:
        return self.t1 if self.t1 is not None else Date.max


@dataclass(frozen=True, slots=True)
class Fold:
    train: tuple[int, ...]   # indices into the (t0-sorted) sample list
    test: tuple[int, ...]


class InsufficientSamplesError(ValueError):
    """Too few samples to form the requested purged walk-forward folds (missing ≠ zero)."""


def purged_walk_forward(
    samples: list[Sample],
    *,
    folds: int = config.ML_CV_FOLDS,
    embargo_frac: float = config.ML_EMBARGO_FRAC,
) -> list[Fold]:
    """Build `folds` anchored walk-forward folds, purged and embargoed.

    The samples are cut into `folds + 1` contiguous segments in `t0` order; segment 0 is the
    initial train-only warm-up, and each later segment k∈[1, folds] is a test block whose train
    is *all prior* samples, then purged of label-overlap and embargoed at the boundary. Every
    test fold therefore has a genuine, look-ahead-safe prior train set.

    Returns folds with indices into the **t0-sorted** ordering of `samples`. Raises
    `InsufficientSamplesError` when there are too few samples for `folds + 1` non-empty
    segments (can't honestly CV — missing ≠ zero).
    """
    if folds < 1:
        raise ValueError("folds must be ≥ 1")
    n = len(samples)
    if n < folds + 1:
        raise InsufficientSamplesError(
            f"{n} samples cannot form {folds}+1 walk-forward segments"
        )

    order = sorted(range(n), key=lambda i: (samples[i].t0, samples[i].label_end))
    segments = _contiguous_segments(order, folds + 1)
    embargo = math.ceil(embargo_frac * n)

    out: list[Fold] = []
    for k in range(1, len(segments)):
        test = segments[k]
        if not test:
            continue
        prior = [i for seg in segments[:k] for i in seg]  # all samples before the test block
        train = _purge_and_embargo(prior, test, samples, embargo)
        out.append(Fold(train=tuple(train), test=tuple(test)))
    return out


def _contiguous_segments(order: list[int], parts: int) -> list[list[int]]:
    """Split `order` into `parts` contiguous, near-equal segments (remainder to the front)."""
    n = len(order)
    base, extra = divmod(n, parts)
    segments, cursor = [], 0
    for p in range(parts):
        size = base + (1 if p < extra else 0)
        segments.append(order[cursor : cursor + size])
        cursor += size
    return segments


def _purge_and_embargo(
    prior: list[int], test: list[int], samples: list[Sample], embargo: int
) -> list[int]:
    """Remove label-overlap (purge) then a boundary buffer (embargo) from a prior-train set.

    `prior` is already in t0 order and strictly precedes `test`. Purge drops any prior sample
    whose label runs to/after the test's earliest t0 (its outcome overlaps the test window).
    Embargo then drops the last `embargo` survivors adjacent to the boundary."""
    test_start = min(samples[i].t0 for i in test)
    purged = [i for i in prior if samples[i].label_end < test_start]
    if embargo > 0 and purged:
        purged = purged[: max(0, len(purged) - embargo)]
    return purged
