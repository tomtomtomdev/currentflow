"""Approximate volume profile (spec §4.1, LD-13 v1.6; slice 19) — PURE OBSERVATION.

Where `vpa` (slice 18) reads one bar's character *in time*, this reads the whole window's
volume *in price*: how much trade happened at each price level, regardless of when. That
is the structure Wyckoff's causes are built on — the price the Composite Man kept coming
back to (POC), the band that held most of the business (the value area), the shelves that
were fought over (HVN) and the air the price fell through (LVN).

**Fidelity honesty — this is an APPROXIMATION and says so everywhere.** A true point of
control and value area are built from intraday prints (tick or minute depth). This system
ingests and backtests daily OHLCV, so the profile spreads each bar's volume **uniformly
across that bar's own high–low range** and buckets the result. That is a defensible
estimate of where trade occurred; it is not the real distribution, and no view may render
or imply more precision than daily bars support (`ANNOTATION`, carried on every reading).

Everything is expressed in **buckets of the window's own range**, never in rupiah, so the
same structure reads identically on a Rp 50 and a Rp 50,000 name.

`missing ≠ zero`: a non-TRADED or incomplete bar is dropped loudly, never folded in as a
zero-volume bar (which would silently thin a price shelf). Too few bars, a flat window
with no range, or no volume at all → `available=False` and **no** POC — never a fabricated
level at the middle of nothing.

RULE A is untouched. The Spring@VAL / UTAD@VAH / LPS@POC confluences are handed to the
phase detectors as *corroboration notes* (`corroboration()`, attached by
`phase._corroborate` **after** the verdict is decided), exactly as slice 18's bar character
is — the C/D tradeability decision rule is not altered, by construction.

RULE B (LD-9): a level is rendered as a labeled line on a chart the operator is already
reading in rupiah; the *derived* magnitudes (a node's share of window volume, the value
area's width) stay on the dataclass as audit measurements. The §4.1 `vp_confluence`
candidate — the phase-bonus refinement this slice contributes — is **pinned at weight 0**
(`config.SMS_WEIGHTS`), so the running score is unchanged until the walk-forward optimizer
earns it a weight under RULE B.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import DailyBar, RowStatus
from currentflow.store.db import Store

log = logging.getLogger(__name__)

# The one line every surface that shows a level must carry. Not decoration — it is the
# §4.1 fidelity-honesty requirement in text form.
ANNOTATION = "approximate — built from daily bars, not intraday depth"


class NodeKind(str, Enum):
    """A bucket's role in the profile — a word, never a magnitude (RULE B)."""

    HVN = "HVN"          # a shelf: far more volume than the window's average bucket
    LVN = "LVN"          # air: far less — price moved through without doing business
    ORDINARY = "ORDINARY"


@dataclass(frozen=True, slots=True)
class PriceBucket:
    """One price band of the profile and the volume estimated to have traded in it."""

    low: float
    high: float
    volume: float
    kind: NodeKind

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass(frozen=True, slots=True)
class VolumeNode:
    """Adjacent buckets of the same kind, merged — a shelf or a gap, not a bucket."""

    kind: NodeKind
    low: float
    high: float
    volume: float
    share: float          # this node's share of the window's volume (audit measurement)

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """One symbol's approximate volume-at-price at one decision moment.

    `poc` / `vah` / `val` are None when nothing was readable — never a midpoint stand-in.
    """

    symbol: str
    decision_ts: datetime
    poc: float | None
    vah: float | None
    val: float | None
    buckets: tuple[PriceBucket, ...]      # ascending by price
    nodes: tuple[VolumeNode, ...]         # ascending by price; ORDINARY runs included
    bars: tuple[DailyBar, ...]            # the window the profile was built from
    total_volume: float
    value_area_volume: float
    available: bool
    note: str
    annotation: str = ANNOTATION

    @property
    def bucket_width(self) -> float:
        return self.buckets[0].high - self.buckets[0].low if self.buckets else 0.0

    @property
    def value_area_share(self) -> float | None:
        """Share of window volume inside [VAL, VAH] — an audit measurement, ≥ 70% by
        construction whenever the profile is available."""
        if not self.available or self.total_volume <= 0:
            return None
        return self.value_area_volume / self.total_volume

    @property
    def hvn(self) -> tuple[VolumeNode, ...]:
        return tuple(n for n in self.nodes if n.kind is NodeKind.HVN)

    @property
    def lvn(self) -> tuple[VolumeNode, ...]:
        return tuple(n for n in self.nodes if n.kind is NodeKind.LVN)

    @property
    def start(self) -> Date | None:
        return self.bars[0].date if self.bars else None

    @property
    def end(self) -> Date | None:
        return self.bars[-1].date if self.bars else None

    def bar_on(self, day: Date) -> DailyBar | None:
        for b in self.bars:
            if b.date == day:
                return b
        return None


# --- bar hygiene ---------------------------------------------------------------------


def _complete(bars: list[DailyBar]) -> list[DailyBar]:
    """Only TRADED bars carrying a full high/low/close + volume. A suspended or
    unpublished day is dropped loudly — folding it in as zero volume would silently
    thin whatever price shelf it belonged to (`missing ≠ zero`)."""
    out, dropped = [], 0
    for b in sorted(bars, key=lambda b: b.date):
        if b.status is RowStatus.TRADED and None not in (b.high, b.low, b.close, b.volume):
            out.append(b)
        else:
            dropped += 1
    if dropped:
        log.info("volume_profile: dropped %d incomplete/non-TRADED bar(s) (missing ≠ zero)", dropped)
    return out


def _unavailable(symbol: str, decision_ts: datetime, note: str,
                 bars: list[DailyBar]) -> VolumeProfile:
    return VolumeProfile(
        symbol=symbol, decision_ts=decision_ts, poc=None, vah=None, val=None,
        buckets=(), nodes=(), bars=tuple(bars), total_volume=0.0,
        value_area_volume=0.0, available=False, note=note,
    )


# --- the profile ---------------------------------------------------------------------


def _allocate(bars: list[DailyBar], lo: float, width: float, n: int) -> list[float]:
    """Spread each bar's volume across the buckets its own high–low range covers.

    THIS is the approximation (see the module docstring): within a bar we assume trade
    was uniform across its range, because daily OHLCV cannot say otherwise. A locked
    print (high == low) has no range to spread over, so its volume lands whole in the one
    bucket that contains it — that is exact, not an estimate."""
    vols = [0.0] * n

    def index_of(price: float) -> int:
        i = int((price - lo) / width)
        return 0 if i < 0 else n - 1 if i >= n else i

    for b in bars:
        volume = float(b.volume or 0)
        if volume <= 0:
            continue
        span = b.high - b.low
        if span <= 0:
            vols[index_of(b.low)] += volume
            continue
        first, last = index_of(b.low), index_of(b.high)
        for i in range(first, last + 1):
            b_lo, b_hi = lo + i * width, lo + (i + 1) * width
            overlap = min(b.high, b_hi) - max(b.low, b_lo)
            if overlap > 0:
                vols[i] += volume * overlap / span
    return vols


def _value_area(vols: list[float], poc_idx: int, total: float) -> tuple[int, int, float]:
    """Grow outward from the POC, always taking the heavier neighbour, until the band
    holds `VP_VALUE_AREA_PCT` of the window's volume. Returns (low_idx, high_idx, volume).

    One bucket at a time (not the classic two), and **ties go to the upper side** — both
    are arbitrary conventions, pinned here so the profile is deterministic: the same bars
    must always produce the same value area, in a live read and in a replay of it."""
    target = total * config.VP_VALUE_AREA_PCT
    lo = hi = poc_idx
    acc = vols[poc_idx]
    while acc < target and (lo > 0 or hi < len(vols) - 1):
        below = vols[lo - 1] if lo > 0 else -1.0
        above = vols[hi + 1] if hi < len(vols) - 1 else -1.0
        if above >= below:
            hi += 1
            acc += vols[hi]
        else:
            lo -= 1
            acc += vols[lo]
    return lo, hi, acc


def _classify(vols: list[float]) -> list[NodeKind]:
    """HVN / LVN against the window's own mean bucket — relative, never an absolute lot
    count, so a thin lapis-2 name and a large-cap read on the same scale."""
    live = [v for v in vols if v > 0]
    mean = sum(live) / len(live) if live else 0.0
    if mean <= 0:
        return [NodeKind.ORDINARY] * len(vols)
    out = []
    for v in vols:
        if v >= config.VP_HVN_MULT * mean:
            out.append(NodeKind.HVN)
        elif v <= config.VP_LVN_MULT * mean:
            out.append(NodeKind.LVN)
        else:
            out.append(NodeKind.ORDINARY)
    return out


def _merge(buckets: list[PriceBucket], total: float) -> tuple[VolumeNode, ...]:
    """Collapse adjacent same-kind buckets into nodes — a shelf is a price *region*, and
    reporting it bucket-by-bucket would imply a resolution daily bars do not have."""
    nodes: list[VolumeNode] = []
    for b in buckets:
        if nodes and nodes[-1].kind is b.kind:
            prev = nodes[-1]
            nodes[-1] = VolumeNode(
                kind=prev.kind, low=prev.low, high=b.high,
                volume=prev.volume + b.volume,
                share=(prev.volume + b.volume) / total if total > 0 else 0.0,
            )
        else:
            nodes.append(VolumeNode(
                kind=b.kind, low=b.low, high=b.high, volume=b.volume,
                share=b.volume / total if total > 0 else 0.0,
            ))
    return tuple(nodes)


def build_profile(
    symbol: str,
    bars: list[DailyBar],
    *,
    decision_ts: datetime,
    window: int | None = None,
) -> VolumeProfile:
    """Build the approximate profile over the last `window` (default `VP_WINDOW_BARS`)
    complete bars. `bars` must already be look-ahead-safe
    (`store.read_daily_bars(symbol, decision_ts)`); no bar after the last one given is
    ever consulted, so the profile is safe bar-by-bar as well as at the store boundary."""
    usable = _complete(bars)
    span = config.VP_WINDOW_BARS if window is None else window
    usable = usable[-span:] if span > 0 else []

    if len(usable) < config.VP_MIN_BARS:
        return _unavailable(
            symbol, decision_ts,
            "too few complete bars to estimate a volume distribution — no profile "
            "(no base ≠ an empty profile)",
            usable,
        )

    lo = min(b.low for b in usable)
    hi = max(b.high for b in usable)
    if hi <= lo:
        return _unavailable(
            symbol, decision_ts,
            "the window has no price range to distribute volume across — nothing to "
            "profile (a locked window is not a point of control)",
            usable,
        )

    n = config.VP_BUCKETS
    width = (hi - lo) / n
    vols = _allocate(usable, lo, width, n)
    total = sum(vols)
    if total <= 0:
        return _unavailable(
            symbol, decision_ts,
            "no traded volume in the window — no profile (silence is not a price shelf)",
            usable,
        )

    # POC ties resolve to the LOWER price — pinned so a replay reproduces a live read.
    poc_idx = max(range(n), key=lambda i: (vols[i], -i))
    va_lo, va_hi, va_vol = _value_area(vols, poc_idx, total)
    kinds = _classify(vols)
    buckets = tuple(
        PriceBucket(low=lo + i * width, high=lo + (i + 1) * width, volume=vols[i], kind=kinds[i])
        for i in range(n)
    )
    return VolumeProfile(
        symbol=symbol, decision_ts=decision_ts,
        poc=buckets[poc_idx].mid,
        vah=buckets[va_hi].high,
        val=buckets[va_lo].low,
        buckets=buckets, nodes=_merge(list(buckets), total), bars=tuple(usable),
        total_volume=total, value_area_volume=va_vol, available=True,
        note=(
            "volume-at-price estimated by spreading each daily bar's volume across its "
            "own high–low range"
        ),
    )


def analyze(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    start: Date | None = None,
    end: Date | None = None,
) -> VolumeProfile:
    """Read look-ahead-safe bars and build the approximate volume profile."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    return build_profile(symbol, bars, decision_ts=decision_ts)


# --- phase-context confluence (RULE A decision rule unchanged) ------------------------

# Which profile level each Wyckoff event is read against, and which end of the event's own
# bar has to reach it. Corroboration only: the note is attached after the classifier has
# already decided the phase, so it can never promote or demote a candidate
# (`phase._corroborate`). Exactly the three confluences the slice names — a spring that
# shakes out below the value area, an upthrust that fails at the top of it, and a
# last-point-of-support that comes to rest on the price the range did most business at.
_LEVELS: dict[str, tuple[str, str, str]] = {
    # kind: (level attribute, which price on the event bar, the plain-English reading)
    "SPRING": ("val", "low", "the spring shook out at the low edge of the value area"),
    "UTAD": ("vah", "high", "the upthrust failed at the high edge of the value area"),
    "LPS": ("poc", "close", "the last point of support came to rest on the point of control"),
}


@dataclass(frozen=True, slots=True)
class VpConfluence:
    """One event landing on one profile level — categorical, plus the levels themselves
    (which the chart already renders in rupiah) for audit."""

    kind: str        # SPRING | UTAD | LPS
    level: str       # VAL | VAH | POC
    date: Date
    price: float     # the event bar's price that met the level
    level_price: float
    note: str


def confluence(profile: VolumeProfile | None, kind: str, day: Date) -> VpConfluence | None:
    """The profile confluence for a phase event of `kind` printed on `day`, or None.

    `None in → None out`: no profile, an unavailable one, an event the slice does not tie
    to a level, a bar outside the profile window, or a level the bar simply did not reach
    all yield nothing. Tolerance is one bucket width — the resolution daily bars can
    honestly claim (`VP_CONFLUENCE_BUCKETS`)."""
    if profile is None or not profile.available:
        return None
    spec = _LEVELS.get(kind)
    if spec is None:
        return None
    attr, price_attr, reading = spec
    level = getattr(profile, attr)
    bar = profile.bar_on(day)
    if level is None or bar is None:
        return None
    price = getattr(bar, price_attr)
    if price is None:
        return None
    tol = config.VP_CONFLUENCE_BUCKETS * profile.bucket_width
    if attr == "val":
        met = price <= level + tol            # reached down to (or through) the value low
    elif attr == "vah":
        met = price >= level - tol            # reached up to (or through) the value high
    else:
        met = abs(price - level) <= tol       # came to rest on the point of control
    if not met:
        return None
    return VpConfluence(
        kind=kind, level=attr.upper(), date=day, price=price, level_price=level,
        note=f"{reading} ({ANNOTATION})",
    )


def corroboration(profile: VolumeProfile | None, kind: str, day: Date) -> str | None:
    """The confluence as the one line `phase._corroborate` hangs on the event. Digit-free
    by design — a phase event is a gate verdict, and RULE B keeps magnitudes off it."""
    conf = confluence(profile, kind, day)
    return None if conf is None else f"Volume profile confirms: {conf.note}"


def confluences(
    profile: VolumeProfile | None, events, /,
) -> tuple[VpConfluence, ...]:
    """Every confluence among `events` (a `PhaseClassification.events` tuple)."""
    if profile is None:
        return ()
    found = (confluence(profile, e.kind, e.date) for e in events)
    return tuple(c for c in found if c is not None)


# --- §4.1 candidate component ---------------------------------------------------------

# Which confluences speak for the accumulation side. UTAD@VAH is a distribution tell: it
# is surfaced by the observation view as a warning and is never negative strength inside
# the simplex, exactly as slice 18 treats its weakness side.
_DEMAND_SIDE = frozenset({"SPRING", "LPS"})


def subscore(
    profile: VolumeProfile | None, events=(), /,
) -> tuple[float, bool]:
    """(strength, available) for the §4.1 `vp_confluence` candidate — the phase-bonus
    refinement this slice contributes.

    `_phase_bonus` today credits a spring or an LPS for *existing*. This grades the same
    events on *where in the profile they happened*: a spring that shook out below the
    value area and an LPS that settled on the point of control are structurally better
    versions of the events the bonus already counts. No profile → `available=False`
    (never a silent 0-strength)."""
    if profile is None or not profile.available:
        return 0.0, False
    hits = sum(1 for c in confluences(profile, events) if c.kind in _DEMAND_SIDE)
    strength = hits / config.VP_FULL_CREDIT_CONFLUENCES
    return (0.0 if strength < 0 else 1.0 if strength > 1 else strength), True
