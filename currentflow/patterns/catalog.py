"""Seed pattern definitions (slice 21, PATTERN-CATALOG-SPEC §6).

Each seed is a falsifiable machine definition (a predicate over the §4 feature vocabulary)
plus an outcome spec (forward horizon(s) + outcome predicate). Thresholds are proposed at
DEFINED stage from the existing config constants where they exist — so the catalog measures
the *system's own* current beliefs first (e.g. the §5 veto's flip, SCR-1C's divergence).

A pattern is defined per track and never blended (LD-1). Entries are append-only versioned;
a definition change bumps `version` (never mutate a recorded rate's definition).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Callable

from currentflow import config
from currentflow.signals.pattern_features import FeatureSet


@dataclass(frozen=True, slots=True)
class OutcomeSpec:
    """Forward outcome. `direction="up"` → HIT when forward return reaches `target`
    (markup); `direction="down"` → HIT when it falls to `target` (drawdown, ≤ 0). A name
    that suspends/delists inside the horizon is a terminal outcome (§5.6), counted as a
    HIT for adverse (down) patterns and as a resolved non-hit for up patterns."""

    horizons: tuple[int, ...]      # forward trading days, each estimated separately
    target: float                  # e.g. +0.20 (up) or -0.10 (down)
    direction: str = "up"          # "up" | "down"


@dataclass(frozen=True, slots=True)
class Pattern:
    pattern_id: str
    version: int
    track: str
    status: str
    folk_claim: str
    source: str
    definition_text: str
    outcome: OutcomeSpec
    predicate: Callable[[FeatureSet], bool]
    feature_window: int = config.SMS_DIVERGENCE_WINDOW_DAYS
    provenance: str = "[DERIVED: patterns/catalog.py seed]"

    def spec_json(self) -> str:
        """The serialisable definition (everything but the code predicate) → `spec_json`."""
        spec = {
            "folk_claim": self.folk_claim,
            "source": self.source,
            "definition": self.definition_text,
            "outcome_spec": asdict(self.outcome),
            "feature_window": self.feature_window,
            "stability": "UNKNOWN (current regime only)",   # REGIME.md §4
            "provenance": self.provenance,
        }
        return json.dumps(spec, sort_keys=True)


# --- seed predicates (over the §4 feature vocabulary) -------------------------------

_QUIET_TOP3_MAX = 0.50            # "low" top-3 concentration (quiet, not obvious)
_MARKUP_TOP1_MIN = 0.40           # a single-broker-led markup (below the 0.60 veto monopoly)
_FOREIGN_STREAK_MIN = 5           # NBSA buy streak ≥ K
_FOREIGN_Z_MIN = 2.0              # … with z ≥ z₀


def _quiet_accumulation(f: FeatureSet) -> bool:
    return (
        f.top3_buy_share is not None and f.top3_buy_share <= _QUIET_TOP3_MAX
        and f.cum_net_broker_pct_float is not None and f.cum_net_broker_pct_float > 0
        and f.price_range_pct is not None and f.price_range_pct <= config.PHASE_RANGE_MAX_WIDTH
    )


def _concentrated_markup(f: FeatureSet) -> bool:
    return (
        f.top1_buy_share is not None and f.top1_buy_share >= _MARKUP_TOP1_MIN
        and f.price_return is not None and f.price_return > 0
        and f.value_rising
    )


def _dominant_broker_flip(f: FeatureSet) -> bool:
    return f.dominant_broker_flip


def _foreign_streak(f: FeatureSet) -> bool:
    return (
        f.nbsa_buy_streak >= _FOREIGN_STREAK_MIN
        and f.nbsa_zscore is not None and f.nbsa_zscore >= _FOREIGN_Z_MIN
    )


def _stealth_divergence(f: FeatureSet) -> bool:
    return f.stealth_divergence


SEED_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        pattern_id="quiet-accumulation", version=1, track="B", status="DEFINED",
        folk_claim="Low broker concentration + persistent net inflow vs float + range-bound "
                   "price precedes markup.",
        source="operator source-drafts; wyckoff-2 (accumulation range); bandarmology",
        definition_text=(
            f"top3_buy_share ≤ {_QUIET_TOP3_MAX} AND cum_net_broker_pct_float > 0 AND "
            f"price_range_pct ≤ {config.PHASE_RANGE_MAX_WIDTH}"
        ),
        outcome=OutcomeSpec(horizons=(40, 60), target=0.20, direction="up"),
        predicate=_quiet_accumulation, feature_window=60,
    ),
    Pattern(
        pattern_id="concentrated-markup", version=1, track="B", status="DEFINED",
        folk_claim="High top-1 share on rising price + rising value = continuation.",
        source="operator source-drafts; bandarmology (hajar kanan)",
        definition_text=(
            f"top1_buy_share ≥ {_MARKUP_TOP1_MIN} AND price_return > 0 AND value_rising"
        ),
        outcome=OutcomeSpec(horizons=(20, 40), target=0.20, direction="up"),
        predicate=_concentrated_markup, feature_window=20,
    ),
    Pattern(
        pattern_id="dominant-broker-flip", version=1, track="B", status="ESTIMATED",
        folk_claim="An accumulator that turns net seller for N days precedes drawdown "
                   "(the §5 veto's claim, now with a base rate).",
        source="LOCKED_SPEC §5 veto; config.VETO_FLIP_MIN_DAYS",
        definition_text=(
            f"the window's top net buyer is net-selling every one of the last "
            f"{config.VETO_FLIP_MIN_DAYS} days"
        ),
        outcome=OutcomeSpec(horizons=(10, 20), target=-0.10, direction="down"),
        predicate=_dominant_broker_flip,
        provenance="[RULE] LOCKED_SPEC §5 veto — measured as a base rate",
    ),
    Pattern(
        pattern_id="foreign-streak", version=1, track="A", status="DEFINED",
        folk_claim="An NBSA buy streak ≥ K with z ≥ z₀ precedes outperformance vs LQ45.",
        source="operator source-drafts; §4 foreign-flow component",
        definition_text=(
            f"nbsa_buy_streak ≥ {_FOREIGN_STREAK_MIN} AND nbsa_zscore ≥ {_FOREIGN_Z_MIN}"
        ),
        outcome=OutcomeSpec(horizons=(20, 60), target=0.15, direction="up"),
        predicate=_foreign_streak, feature_window=20,
    ),
    Pattern(
        pattern_id="stealth-divergence", version=1, track="B", status="ESTIMATED",
        folk_claim="SCR-1C's stealth divergence (price rising while flow falls) precedes "
                   "drawdown — does the flag mean anything?",
        source="screeners.md SCR-1C; signals/distribution bearish-divergence",
        definition_text="bearish flow/price divergence over the decay window (distribution)",
        outcome=OutcomeSpec(horizons=(10, 20), target=-0.10, direction="down"),
        predicate=_stealth_divergence,
        provenance="[DERIVED: signals/distribution._bearish_divergence]",
    ),
    Pattern(
        pattern_id="ksei-confirmed-accum", version=1, track="B", status="DEFINED",
        folk_claim="Quiet accumulation AND next-month KSEI local-inst share up precedes "
                   "markup (the confirmation-layer test).",
        source="operator source-drafts; ksei_ownership overlay",
        definition_text=(
            "quiet-accumulation AND (following-month KSEI local share rising — the "
            "forward-confirmation leg is deferred; DEFINED only until wired)"
        ),
        outcome=OutcomeSpec(horizons=(40, 60), target=0.20, direction="up"),
        predicate=_quiet_accumulation, feature_window=60,
    ),
)


def seed_by_id(pattern_id: str) -> Pattern:
    for p in SEED_PATTERNS:
        if p.pattern_id == pattern_id:
            return p
    raise KeyError(pattern_id)
