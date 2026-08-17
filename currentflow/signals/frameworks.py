"""Framework lenses — the five source frameworks read SEPARATELY (observation layer).

The locked pipeline (§2) *fuses* these frameworks into one AND-chain of gates: a name is
ARMED only when the §3 floor, the RULE A phase gate, the §4 components and the §5 vetoes
all agree at once. That chain is the decision path and **nothing here changes it**.

This module is the complement the operator asked for: each framework read **on its own
terms**, so a name the chain rejects is still visible under whichever lens does see
something — plus a confluence surface naming which lenses happen to agree on the same
symbol. Five lenses, one per framework the spec draws on:

    WYCKOFF        structure — phase + the C/D events (`signals/phase.py`)
    WYCKOFF_2      volume-at-price — POC / VAH / VAL confluence (`signals/volume_profile.py`)
    VPA            bar character — close-position-in-spread, Coulling (`signals/vpa.py`)
    BANDARMOLOGY   broker-code footprint + KSEI ownership (`signals/broker_flow.py`,
                   `signals/ownership.py`, and the §5 bandar-family vetoes)
    MAGIC_FORMULA  Greenblatt EY/ROC conviction tilt (`fundamentals/tilt.py`, SCR-4)

**RULE A holds by construction.** Every read is derived from an already-computed
`EngineResult` — a lens is a pure function of a decision that has already been made. No
lens can arm a name, un-reject one, or re-open the C/D gate; `EngineState` is never
consulted for anything but display and never written.

**RULE B holds by construction.** A lens emits a *category* and a sentence — never a
score, probability, SMS value, or buy/sell verb. The confluence surface counts **set
membership** (which lenses flagged a name — the same species of fact as the pipeline's
"3 armed · 2 watch"), and that count is never weighted, never turned into a ranking of
quality, and never fed back into §4 (weights stay the walk-forward optimizer's alone).

**Missing ≠ zero.** `UNAVAILABLE` (the framework could not read this name — no data, no
profile, no fundamentals visible) is a distinct state from `NEUTRAL` (it read the name and
named nothing) and from `NOT_APPLICABLE` (the framework deliberately abstains, e.g. §7
skips Magic Formula on financials/utilities). A lens never reports silence as absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from currentflow import config
from currentflow.dal.models import Scr4Row
from currentflow.fundamentals import tilt as tilt_mod
from currentflow.fundamentals.tilt import TiltKind
from currentflow.signals import volume_profile as vp_mod
from currentflow.signals.engine import EngineResult
from currentflow.signals.phase import WyckoffPhase
from currentflow.signals.veto import VetoReason


class Lens(str, Enum):
    WYCKOFF = "WYCKOFF"
    WYCKOFF_2 = "WYCKOFF_2"
    VPA = "VPA"
    BANDARMOLOGY = "BANDARMOLOGY"
    MAGIC_FORMULA = "MAGIC_FORMULA"


class LensState(str, Enum):
    """What one framework saw. Categorical — never a magnitude (RULE B)."""

    FLAGGED = "FLAGGED"                # this framework's accumulation read is present
    NEUTRAL = "NEUTRAL"                # it read the name and named nothing
    CONTRARY = "CONTRARY"              # it reads the *other* side (distribution/weakness)
    UNAVAILABLE = "UNAVAILABLE"        # it could not read this name (missing ≠ zero)
    NOT_APPLICABLE = "NOT_APPLICABLE"  # it deliberately abstains here (e.g. §7 FLOW_ONLY)


# The lens order every surface uses: the three structure/tape frameworks, then the
# broker-code footprint, then the fundamental tilt (the §2 order they enter the system).
LENS_ORDER = (
    Lens.WYCKOFF, Lens.WYCKOFF_2, Lens.VPA, Lens.BANDARMOLOGY, Lens.MAGIC_FORMULA,
)


@dataclass(frozen=True, slots=True)
class LensMeta:
    label: str        # operator-facing section title
    framework: str    # the source framework, named
    scope: str        # what this lens reads, in one line
    source: str       # where the read comes from — provenance, not a claim


LENS_META: dict[Lens, LensMeta] = {
    Lens.WYCKOFF: LensMeta(
        "Wyckoff Structure",
        "Wyckoff — accumulation phases, Composite Man",
        "trading range, phase, and the C/D events (selling climax · spring · SOS · LPS · UTAD)",
        "signals/phase.py · LOCKED_SPEC §2 [3] (the RULE A classifier, read here as observation)",
    ),
    Lens.WYCKOFF_2: LensMeta(
        "Wyckoff 2.0 — Volume Profile",
        "Wyckoff 2.0 (Villahermosa) — volume at price",
        "where in the profile the structure happened: spring at VAL · LPS at POC · UTAD at VAH",
        "signals/volume_profile.py · LOCKED_SPEC §4.1 (c) — daily-bar approximation",
    ),
    Lens.VPA: LensMeta(
        "VPA — Bar Character",
        "Volume Price Analysis (Coulling) / VSA (Williams)",
        "where the bar closed inside its own spread: absorption · stopping volume · no supply",
        "signals/vpa.py · LOCKED_SPEC §4.1 (b)",
    ),
    Lens.BANDARMOLOGY: LensMeta(
        "Bandarmology — Broker Footprint",
        "Bandarmology — IDX broker-code flow",
        "who is buying and for how long: top-2 concentration, persistence, KSEI ownership drift",
        "signals/broker_flow.py + signals/ownership.py + the §5 bandar-family vetoes",
    ),
    Lens.MAGIC_FORMULA: LensMeta(
        "Magic Formula — Conviction Tilt",
        "Greenblatt — Magic Formula (EY = EBIT/EV, ROC)",
        "quality tercile that sizes conviction and hold horizon — never an entry gate (LD-6)",
        "fundamentals/tilt.py over SCR-4 (fitem 13474 Rank(Magic Formula)%)",
    ),
}

# §5 vetoes that are the *Bandarmology framework's own* disqualifiers — a single-bandar
# monopoly or a wash/churn print is that framework reading the footprint against itself.
# They are surfaced under the Bandarmology lens as CONTRARY; the veto's engine effect is
# unchanged and lives where it always did (pipeline step [5]).
_BANDAR_VETOES = frozenset({
    VetoReason.SINGLE_BANDAR_MONOPOLY,
    VetoReason.WASH_CHURN,
    VetoReason.BROKER_ROTATION,
    VetoReason.DISTRIBUTION_DRESSED,
})

_PHASE_TAG = {
    WyckoffPhase.A: "PHASE A",
    WyckoffPhase.B: "PHASE B",
    WyckoffPhase.C: "PHASE C",
    WyckoffPhase.D: "PHASE D",
    WyckoffPhase.E: "PHASE E · LATE",
    WyckoffPhase.DISTRIBUTION: "DISTRIBUTION",
    WyckoffPhase.DOWNTREND: "DOWNTREND",
    WyckoffPhase.UNKNOWN: "NO STRUCTURE",
}


@dataclass(frozen=True, slots=True)
class LensRead:
    """One framework's read of one symbol. Category + sentence, no number (RULE B)."""

    lens: Lens
    state: LensState
    tag: str        # the short categorical label for the cell
    detail: str     # one sentence in the framework's own vocabulary
    note: str = ""  # fidelity / provenance caveat that must travel with the read

    @property
    def flagged(self) -> bool:
        return self.state is LensState.FLAGGED


@dataclass(frozen=True, slots=True)
class SymbolRead:
    """All five lenses over one symbol, plus the engine verdict for context only."""

    symbol: str
    track: str
    reads: tuple[LensRead, ...]
    engine_state: str      # display context — the lens layer never writes it
    tradeable: bool        # RULE A verdict, carried so a surface can show the divergence

    @property
    def by_lens(self) -> dict[Lens, LensRead]:
        return {r.lens: r for r in self.reads}

    @property
    def flagged(self) -> tuple[Lens, ...]:
        return tuple(r.lens for r in self.reads if r.flagged)

    def read(self, lens: Lens) -> LensRead:
        return self.by_lens[lens]


# --- per-lens readers -------------------------------------------------------------------


def _wyckoff(result: EngineResult) -> LensRead:
    phase = result.phase
    if phase.bars_used == 0:
        return LensRead(Lens.WYCKOFF, LensState.UNAVAILABLE, "NO BARS",
                        "no complete bars visible at this decision moment — "
                        "structure unread, not absent")
    events = ", ".join(f"{e.kind} {e.date}" for e in phase.events)
    detail = phase.reason + (f" · events: {events}" if events else "")
    if phase.phase in (WyckoffPhase.C, WyckoffPhase.D):
        state = LensState.FLAGGED
    elif phase.phase in (WyckoffPhase.DISTRIBUTION, WyckoffPhase.E, WyckoffPhase.DOWNTREND):
        state = LensState.CONTRARY
    else:
        state = LensState.NEUTRAL
    return LensRead(Lens.WYCKOFF, state, _PHASE_TAG.get(phase.phase, phase.phase.value), detail)


def _wyckoff_2(result: EngineResult) -> LensRead:
    profile = result.vp
    if profile is None or not profile.available:
        note = profile.note if profile is not None else "no profile built"
        return LensRead(Lens.WYCKOFF_2, LensState.UNAVAILABLE, "NO PROFILE",
                        f"volume-at-price unread — {note} (missing ≠ zero)")
    confs = vp_mod.confluences(profile, result.phase.events)
    demand = tuple(c for c in confs if c.kind in ("SPRING", "LPS"))
    supply = tuple(c for c in confs if c.kind == "UTAD")
    if demand:
        return LensRead(
            Lens.WYCKOFF_2, LensState.FLAGGED,
            " · ".join(f"{c.kind}@{c.level}" for c in demand),
            " · ".join(c.note for c in demand), profile.annotation,
        )
    if supply:
        return LensRead(
            Lens.WYCKOFF_2, LensState.CONTRARY,
            " · ".join(f"{c.kind}@{c.level}" for c in supply),
            " · ".join(c.note for c in supply), profile.annotation,
        )
    return LensRead(
        Lens.WYCKOFF_2, LensState.NEUTRAL, "NO CONFLUENCE",
        "profile built, but no structure event landed on the point of control or a "
        "value-area edge", profile.annotation,
    )


def _vpa(result: EngineResult) -> LensRead:
    reading = result.vpa
    if reading is None or not reading.available:
        return LensRead(Lens.VPA, LensState.UNAVAILABLE, "UNREADABLE",
                        "no bar in the window carried a readable spread and volume context "
                        "(a locked ARA/ARB print has no spread to close inside)")
    latest = reading.latest
    latest_str = f"latest bar: {latest.character.value}" if latest is not None else "no latest bar"
    if reading.confirms_absorption:
        detail = f"absorption / stopping / no-supply prints in the window · {latest_str}"
        if reading.shows_weakness:
            detail += " · the window also carries supply-side prints"
        return LensRead(Lens.VPA, LensState.FLAGGED, "DEMAND SIDE", detail)
    if reading.shows_weakness:
        return LensRead(Lens.VPA, LensState.CONTRARY, "SUPPLY SIDE",
                        f"no-demand / supply-present / churn prints in the window · {latest_str}")
    return LensRead(Lens.VPA, LensState.NEUTRAL, "NO CHARACTER",
                    f"bars read, none carried a character the framework names · {latest_str}")


def _bandarmology(result: EngineResult) -> LensRead:
    comp = next((c for c in result.sms.components if c.key == "broker_concentration"), None)
    own = result.ownership
    fired = [v for v in result.veto.vetoes if v.reason in _BANDAR_VETOES]
    if fired:
        return LensRead(
            Lens.BANDARMOLOGY, LensState.CONTRARY, fired[0].reason.value.replace("_", " "),
            " · ".join(v.detail for v in fired),
            "the framework's own disqualifier — the §5 veto's engine effect is unchanged",
        )
    if comp is None or not comp.available:
        return LensRead(Lens.BANDARMOLOGY, LensState.UNAVAILABLE, "NO BROKER ROWS",
                        "no broker-summary rows visible at this decision moment — footprint "
                        "unread, not flat")

    obs = comp.observation
    days = obs.get("persistence_days") or 0
    flat_or_down = bool(obs.get("flat_or_down"))
    own_line = "" if own is None or not own.available else f" · KSEI: {own.detail}"
    if days >= config.SMS_BROKER_PERSIST_DAYS and flat_or_down:
        return LensRead(
            Lens.BANDARMOLOGY, LensState.FLAGGED, "PERSISTENT ACCUMULATOR",
            f"the same top-2 buyers net-bought {days} consecutive days on a flat-to-down "
            f"tape — quiet accumulation, not a chase{own_line}",
        )
    # KSEI is a *corroborator* by design (monthly cadence, far too coarse to call a name on
    # its own — §4.1 (a)), so it colours the lens only when the broker leg named nothing.
    if own is not None and own.corroborates_distribution:
        return LensRead(Lens.BANDARMOLOGY, LensState.CONTRARY, "OWNERSHIP FALLING",
                        f"broker footprint names nothing · {own.detail}",
                        "KSEI composition is monthly — a corroborator, never a call on its own")
    if days:
        return LensRead(
            Lens.BANDARMOLOGY, LensState.NEUTRAL, "CONCENTRATION ON A RISING TAPE",
            f"top-2 buyers persisted {days} consecutive days, but the tape rose with them — "
            f"a chase, not the quiet mark-down absorption the framework reads{own_line}",
        )
    return LensRead(Lens.BANDARMOLOGY, LensState.NEUTRAL, "NO PERSISTENCE",
                    f"no top-2 buyer sustained a net-buy streak in the window{own_line}")


def _magic_formula(
    result: EngineResult, *, fundamentals: Scr4Row | None, sector: str | None,
) -> LensRead:
    if tilt_mod.is_flow_only(sector):
        return LensRead(
            Lens.MAGIC_FORMULA, LensState.NOT_APPLICABLE, "MF SKIPPED",
            f"§7 FLOW_ONLY ({sector}) — ROE/PE/PB distort on leverage, so Magic Formula is "
            "deliberately not run on financials or utilities (LD-7); the sector proxy sizes "
            "this name instead",
        )
    if fundamentals is None or (fundamentals.mf_rank_pct is None and fundamentals.ev_ebit is None):
        return LensRead(
            Lens.MAGIC_FORMULA, LensState.UNAVAILABLE, "NO MF DATA",
            "no Magic Formula rank visible at this decision moment (SCR-4 not pulled, or the "
            "name is absent from it) — un-ranked, never assumed mid-tercile",
        )
    tilt = tilt_mod.classify_tilt(
        result.symbol, sector=sector, mf_rank_pct=fundamentals.mf_rank_pct,
        ev_ebit=fundamentals.ev_ebit, roe=fundamentals.roe,
    )
    state = {
        TiltKind.COMPOUNDER: LensState.FLAGGED,
        TiltKind.SPECULATIVE: LensState.CONTRARY,
        TiltKind.NEUTRAL: LensState.NEUTRAL,
        TiltKind.FLOW_ONLY: LensState.NOT_APPLICABLE,
    }[tilt.kind]
    return LensRead(
        Lens.MAGIC_FORMULA, state, tilt.kind.value, tilt.reason,
        "conviction & hold horizon only — fundamentals never gate an entry (LD-6)",
    )


def read_symbol(
    result: EngineResult, *, fundamentals: Scr4Row | None = None, sector: str | None = None,
) -> SymbolRead:
    """All five framework lenses over one already-decided candidate.

    Pure over `result` — reading a name through the lenses cannot change what the engine
    decided about it (RULE A), and no lens emits a number (RULE B)."""
    reads = (
        _wyckoff(result),
        _wyckoff_2(result),
        _vpa(result),
        _bandarmology(result),
        _magic_formula(result, fundamentals=fundamentals, sector=sector),
    )
    return SymbolRead(
        symbol=result.symbol, track=result.track, reads=reads,
        engine_state=result.state.value, tradeable=result.phase.tradeable,
    )


# --- aggregation ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Confluence:
    """One symbol seen by more than one framework at once.

    `n_lenses` is the **cardinality of a set** — how many independent frameworks flagged
    this name — not a score, not a rank, and not a confidence. Nothing multiplies it,
    nothing feeds it back into §4, and it carries no buy/sell verb (RULE B)."""

    symbol: str
    track: str
    lenses: tuple[Lens, ...]      # in LENS_ORDER
    engine_state: str
    tradeable: bool

    @property
    def n_lenses(self) -> int:
        return len(self.lenses)


def confluences(reads: list[SymbolRead], *, minimum: int = 2) -> tuple[Confluence, ...]:
    """Symbols flagged by at least `minimum` frameworks, most agreement first.

    Ordering is by set size then ticker — a stable way to put the widest agreement at the
    top of a list, never an assertion that more lenses means a better trade. The engine
    verdict rides along precisely so a name that six frameworks like and RULE A still
    rejects reads as exactly that, instead of quietly looking armed."""
    out = [
        Confluence(
            symbol=r.symbol, track=r.track,
            lenses=tuple(lens for lens in LENS_ORDER if lens in set(r.flagged)),
            engine_state=r.engine_state, tradeable=r.tradeable,
        )
        for r in reads
    ]
    return tuple(sorted(
        (c for c in out if c.n_lenses >= minimum), key=lambda c: (-c.n_lenses, c.symbol),
    ))


def lens_tally(reads: list[SymbolRead]) -> dict[Lens, dict[LensState, int]]:
    """Per-lens count of symbols in each state — a census of what each framework could and
    could not read today. Counts of set membership, never a score (RULE B)."""
    tally: dict[Lens, dict[LensState, int]] = {
        lens: {state: 0 for state in LensState} for lens in LENS_ORDER
    }
    for read in reads:
        for r in read.reads:
            tally[r.lens][r.state] += 1
    return tally
