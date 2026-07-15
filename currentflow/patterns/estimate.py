"""Pattern base-rate estimator (slice 21, PATTERN-CATALOG-SPEC §5).

Per (pattern, horizon): n, hit rate, Wilson 90% CI, the unconditional null on the
identical window/universe/outcome, confound strata, the OOS split at the holdout seam,
and a decay flag. Pure over store reads → deterministic and re-runnable (byte-identical
over an unchanged store, §18.3). The null always travels with the rate (§5.4).
"""

from __future__ import annotations

import json
import math
from datetime import date as Date
from datetime import datetime

from currentflow import config
from currentflow.paper.fill import tier_for_adv
from currentflow.patterns.catalog import OutcomeSpec, Pattern
from currentflow.patterns.outcome import resolve_instance
from currentflow.signals.regime import classify_market_regime
from currentflow.store.schema import PatternCatalogRow, PatternInstanceRow
from currentflow.universe.pit import PitUniverse
from currentflow.dal.models import RowStatus

UniverseProvider = "Callable[[Date], PitUniverse]"


def wilson_interval(hits: int, n: int, *, z: float = config.CATALOG_CI_Z) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion. `n == 0` → the widest
    honest interval (0, 1) — small n renders wide, never hides (P4)."""
    if n <= 0:
        return (0.0, 1.0)
    phat = hits / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _is_hit(outcome: str, direction: str) -> bool:
    if outcome == "HIT":
        return True
    # A terminal (delist/suspend) is the ultimate adverse outcome → a hit for down patterns.
    return direction == "down" and outcome.startswith("TERMINAL")


def _resolved(insts: list[PatternInstanceRow]) -> list[PatternInstanceRow]:
    return [i for i in insts if i.outcome != "OPEN"]


def _decision_ts(day: Date) -> datetime:
    return datetime.combine(day, config.REPLAY_DECISION_TIME)


def _traded(store, symbol: str, now: datetime) -> list:
    raw = store.read_daily_bars(symbol, now)
    return sorted((b for b in raw if b.status is RowStatus.TRADED), key=lambda b: b.date)


def _unconditional_rates(
    store, pattern: Pattern, est_days: list[Date], universe, *, now: datetime
) -> dict[int, float | None]:
    """The null: the unconditional rate of the SAME outcome over the identical
    window/universe (every track-matching name on every est day), not just the flagged
    ones. A pattern is interesting only relative to this."""
    spec = pattern.outcome
    hits = {h: 0 for h in spec.horizons}
    n = {h: 0 for h in spec.horizons}
    market_latest = store.latest_bar_date()
    cache: dict[str, list] = {}
    for day in est_days:
        uni = universe(day)
        for symbol in uni.symbols:
            if uni.tracks.get(symbol) != pattern.track:
                continue
            traded = cache.get(symbol)
            if traded is None:
                traded = _traded(store, symbol, now)
                cache[symbol] = traded
            for h in spec.horizons:
                outcome, _ = resolve_instance(traded, day, h, spec, market_latest)
                if outcome == "OPEN":
                    continue
                n[h] += 1
                if _is_hit(outcome, spec.direction):
                    hits[h] += 1
    return {h: (hits[h] / n[h] if n[h] else None) for h in spec.horizons}


def _tier_at(store, symbol: str, flag_date: Date) -> str:
    scr0 = store.read_scr0_latest(symbol, _decision_ts(flag_date))
    return tier_for_adv(scr0.adv20 if scr0 else None).value


def _confounds(
    store, pattern: Pattern, resolved: list[PatternInstanceRow], universe, *, now: datetime
) -> dict:
    """Confound strata (§5.5): recompute the hit rate within named strata. Liquidity tier
    and market-regime label are computed here; sector has no point-in-time store sink today
    (same class as the PIT unchecked legs) and is named as deferred, never faked."""
    direction = pattern.outcome.direction
    by_tier: dict[str, list[int]] = {}
    by_regime: dict[str, list[int]] = {}
    regime_cache: dict[Date, str] = {}
    for inst in resolved:
        hit = 1 if _is_hit(inst.outcome, direction) else 0
        tier = _tier_at(store, inst.symbol, inst.flag_date)
        by_tier.setdefault(tier, []).append(hit)
        rl = regime_cache.get(inst.flag_date)
        if rl is None:
            uni = universe(inst.flag_date)
            try:
                read = classify_market_regime(store, list(uni.symbols), uni.decision_ts)
                rl = read.regime.value
            except Exception:  # pragma: no cover — regime is a best-effort stratum
                rl = "UNKNOWN"
            regime_cache[inst.flag_date] = rl
        by_regime.setdefault(rl, []).append(hit)

    def _rates(buckets: dict[str, list[int]]) -> dict[str, dict]:
        return {
            k: {"n": len(v), "rate": (sum(v) / len(v) if v else None)}
            for k, v in sorted(buckets.items())
        }

    return {
        "liquidity_tier": _rates(by_tier),
        "regime_label": _rates(by_regime),
        "sector": "deferred — no point-in-time sector sink (named, not faked)",
    }


def estimate(store, pattern: Pattern, days: list[Date], universe, *, now: datetime) -> PatternCatalogRow:
    """Compute the catalog results for `pattern` and return the updated catalog row
    (ESTIMATED, or OOS_CHECKED when OOS instances have resolved). Assumes the scanner +
    outcome resolver have already populated/resolved instances."""
    spec: OutcomeSpec = pattern.outcome
    est_days = sorted(d for d in days if d < config.CATALOG_HOLDOUT_START)
    null = _unconditional_rates(store, pattern, est_days, universe, now=now)

    all_insts = store.read_pattern_instances(pattern.pattern_id, pattern.version)
    est_resolved = _resolved([i for i in all_insts if i.window == "EST"])
    oos_resolved = _resolved([i for i in all_insts if i.window == "OOS"])

    horizons: dict[str, dict] = {}
    for h in spec.horizons:
        e = [i for i in est_resolved if i.horizon_days == h]
        n = len(e)
        hits = sum(1 for i in e if _is_hit(i.outcome, spec.direction))
        terminal = sum(1 for i in e if i.outcome.startswith("TERMINAL"))
        lo, hi = wilson_interval(hits, n)
        rate = hits / n if n else None

        o = [i for i in oos_resolved if i.horizon_days == h]
        n_oos = len(o)
        hits_oos = sum(1 for i in o if _is_hit(i.outcome, spec.direction))
        rate_oos = hits_oos / n_oos if n_oos else None
        decay = rate_oos is not None and rate_oos < lo  # OOS collapsed below the est CI

        horizons[str(h)] = {
            "n": n, "hits": hits, "terminal": terminal,
            "rate": rate, "ci90": [lo, hi],
            "rate_uncond": null[h],           # the null travels with the rate (§5.4)
            "n_oos": n_oos, "rate_oos": rate_oos, "decay_flag": decay,
        }

    results = {
        "window_est": [
            config.regime_start(pattern.track).isoformat(),
            config.CATALOG_HOLDOUT_START.isoformat(),
        ],
        "horizons": horizons,
        "confounds": _confounds(store, pattern, est_resolved, universe, now=now),
        "stability": "UNKNOWN (current regime only)",
    }
    status = "OOS_CHECKED" if oos_resolved else "ESTIMATED"
    row = PatternCatalogRow(
        pattern_id=pattern.pattern_id, version=pattern.version, track=pattern.track,
        status=status, spec_json=pattern.spec_json(),
        results_json=json.dumps(results, sort_keys=True), as_of=now,
    )
    store.upsert_pattern_catalog(row)
    return row
