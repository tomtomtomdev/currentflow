"""Engineered feature rows for the ML layer (LD-8: engineered features ONLY).

LD-8 confines ML to a weight-optimizer / ranker over *engineered* features — never a black
box over raw ticks. The engineered features ARE the existing, look-ahead-safe SMS component
sub-scores (§4); this module is a thin, honest adapter that packages a `SmsResult` into a
`FeatureRow` with the label span the CV needs. It introduces **no new signal** — it only
reshapes observations the rules layer already computes, so the ML layer stays a consumer of
the audited feature store, not a new source of look-ahead risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime

from currentflow.ml.cv import Sample
from currentflow.signals.sms import COMPONENT_KEYS, SmsResult

# The ML feature space is exactly the engineered SMS components — nothing more (LD-8).
FEATURE_KEYS = COMPONENT_KEYS


@dataclass(frozen=True, slots=True)
class FeatureRow:
    symbol: str
    decision_ts: datetime
    track: str
    features: dict[str, float]   # engineered component sub-scores in [0, 1]
    t0: Date                     # label start (decision date) — for purged CV
    t1: Date | None = None       # label end (trade exit) if resolved

    def as_sample(self) -> Sample:
        return Sample(t0=self.t0, t1=self.t1)

    def vector(self) -> tuple[float, ...]:
        return tuple(self.features.get(k, 0.0) for k in FEATURE_KEYS)


def features_from_sms(
    sms: SmsResult, *, t0: Date | None = None, t1: Date | None = None
) -> FeatureRow:
    """Package an SmsResult's engineered component sub-scores as a labeled feature row.

    `t0` defaults to the decision date (the SMS's `decision_ts.date()`); `t1` is the trade's
    exit date once known (`None` while the label is unresolved). Only components flagged
    `available` contribute a value — a missing detector is absent, never a silent 0 (RULE
    convention: missing ≠ zero)."""
    feats = {c.key: c.subscore for c in sms.components if c.available}
    return FeatureRow(
        symbol=sms.symbol,
        decision_ts=sms.decision_ts,
        track=sms.track,
        features=feats,
        t0=t0 if t0 is not None else sms.decision_ts.date(),
        t1=t1,
    )
