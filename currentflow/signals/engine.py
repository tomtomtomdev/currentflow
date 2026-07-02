"""Decision engine — pipeline steps [3]→[5] + the ARMED state (spec §2).

    [3] PHASE CLASSIFIER (RULE A hard gate)  → only Phase C/D proceeds
    [4] SMART MONEY SCORE (§4, INTERNAL)     → components observed; number gated (RULE B)
    [5] VETO FILTERS (§5)                     → any hit kills the candidate
        SMS ≥ 70  AND  phase ∈ {C,D}  AND  no veto  →  state = ARMED (watchlist)

RULE A: the phase gate runs first; a non-C/D name can never be ARMED regardless of its
(internal) score. RULE B: `armed` is a server-authoritative *state*, not a number — the
watchlist is driven by it, but the SMS value stays hidden until the module is VALIDATED.
The SMS *components* are always available as observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from enum import Enum

from currentflow import config
from currentflow.dal.models import RowStatus
from currentflow.signals import broker_flow, foreign_flow, phase as phase_mod
from currentflow.signals.broker_flow import BrokerDNA
from currentflow.signals.phase import PhaseClassification
from currentflow.signals.sms import SmsResult, compute_sms
from currentflow.signals.veto import VetoResult, evaluate_vetoes
from currentflow.store.db import Store


class EngineState(str, Enum):
    GATE_REJECTED = "GATE_REJECTED"   # RULE A: phase not C/D — never scored for entry
    VETOED = "VETOED"                 # phase C/D but a §5 veto fired
    WATCH = "WATCH"                   # phase C/D, no veto, internal SMS < threshold
    ARMED = "ARMED"                   # phase C/D, no veto, internal SMS ≥ threshold


@dataclass(frozen=True, slots=True)
class EngineResult:
    symbol: str
    decision_ts: datetime
    track: str
    phase: PhaseClassification
    sms: SmsResult                    # components = observation; internal_score = GATED (RULE B)
    veto: VetoResult
    state: EngineState

    @property
    def armed(self) -> bool:
        return self.state is EngineState.ARMED


def _adv20(bars) -> float | None:
    window = [b for b in bars if b.status is RowStatus.TRADED][-config.ADV_WINDOW_DAYS:]
    vals = [b.value for b in window if b.value is not None]
    return sum(vals) / len(vals) if vals else None


def evaluate(
    store: Store,
    symbol: str,
    decision_ts: datetime,
    *,
    track: str,
    start: Date | None = None,
    end: Date | None = None,
    rebalance_multiplier: float = 1.0,
    has_material_news: bool = False,
    registry: dict[str, BrokerDNA] | None = None,
) -> EngineResult:
    """Run the gate → score → veto → ARMED decision for one look-ahead-safe candidate."""
    bars = store.read_daily_bars(symbol, decision_ts, start=start, end=end)
    broker = broker_flow.analyze(store, symbol, decision_ts, start=start, end=end, registry=registry)
    foreign = foreign_flow.analyze(store, symbol, decision_ts, start=start, end=end) if track == "A" else None
    phase_cls = phase_mod.classify(symbol, bars, decision_ts)

    sms = compute_sms(
        symbol, track=track, bars=bars, broker=broker, foreign=foreign,
        phase_cls=phase_cls, decision_ts=decision_ts, adv20=_adv20(bars),
        rebalance_multiplier=rebalance_multiplier,
    )
    veto = evaluate_vetoes(
        symbol, broker=broker, bars=bars, phase_cls=phase_cls,
        decision_ts=decision_ts, has_material_news=has_material_news,
    )

    state = _decide(phase_cls, sms, veto)
    return EngineResult(
        symbol=symbol, decision_ts=decision_ts, track=track, phase=phase_cls,
        sms=sms, veto=veto, state=state,
    )


def _decide(phase_cls: PhaseClassification, sms: SmsResult, veto: VetoResult) -> EngineState:
    # RULE A: phase gate first — a non-C/D name is rejected before entry consideration.
    if not phase_cls.tradeable:
        return EngineState.GATE_REJECTED
    if veto.rejected:
        return EngineState.VETOED
    if sms.internal_score >= config.SMS_ARMED_THRESHOLD:
        return EngineState.ARMED
    return EngineState.WATCH
