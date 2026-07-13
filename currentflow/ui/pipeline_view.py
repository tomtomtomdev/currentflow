"""Signal Pipeline view-model (design v2 handoff — the sole top-level screen).

Pure data shaping over `engine.evaluate()` results, no Streamlit. Builds the
Track A / Track B lanes of candidate rows; each row surfaces all four locked
pipeline stages (§2) left → right and a verdict:

    [1] UNIVERSE GATE      §3 hard liquidity floor · track assignment
    [2] PHASE CLASSIFIER   RULE A · only Wyckoff Phase C/D passes
    [3] SIGNAL COMPONENTS  §4 track-weighted · SMS internal (RULE B)
    [4] VETO FILTERS       §5 hard rejects — any one kills the signal

A candidate STOPS at the first stage it fails — downstream stages render as
`skip` (NOT EVALUATED), never re-derived. The decisive stage drives the verdict.

RULE B (LD-9): the Signal-Components stage shows a categorical `pass` / `low`
(below the internal ARMED bar) plus its component *observations* — NEVER the
composite SMS number, a probability, or a buy/sell verb. The internal SMS is
consulted only to order rows and to split pass↔low; its value is never emitted.

RULE A (LD-2): the phase gate runs before the signal stage; a non-C/D name is
`REJECTED` at the phase cell and the signal/veto cells are `skip`.

Scope note — the Gate cell currently covers the §3 **liquidity-floor + track**
leg (ADV20 vs `config.ADV_FLOOR_IDR`), the leg the live app path already derives.
The remaining §3 checks (history/IPO, data-gap, corp-action window, ARA/ARB bands
via `universe.gate.evaluate_gate`) and the **EXITED** lane (a position that entered
then exited on a broken thesis + realized P&L, from the portfolio paper-trader)
are NOT produced here — deferred to a follow-up slice. See PLAN.md "Phase 2 —
pipeline plumbing to resolve" and PROGRESS.md.
"""

from __future__ import annotations

from currentflow import config
from currentflow.signals.engine import EngineResult, EngineState
from currentflow.signals.phase import WyckoffPhase

# --- stage / verdict enums (semantic only — shell.py maps these to colors/marks) ---

# stage-cell states, matching the design's `s` field
PASS, FAIL, LOW, REV, SKIP = "pass", "fail", "low", "rev", "skip"
# verdicts (EXITED deferred — see module docstring / PLAN.md Phase 2)
ARMED, WATCH, REJECTED = "ARMED", "WATCH", "REJECTED"

STAGES = ("gate", "phase", "sig", "veto")

_LANE_DESC = {
    "A": "large-cap · LQ45/IDX80 · foreign-flow reliable — NBSA co-lead (wt 25)",
    "B": "lapis-2 · passes hard floor only · broker-concentration reliable (wt 35)",
}

_VERDICT_ORDER = {ARMED: 0, WATCH: 1, REJECTED: 2}

# component key → operator-facing label for the signal-cell reason (observation only)
_COMP_LABEL = {
    "divergence": "divergence",
    "broker_concentration": "broker concentration",
    "foreign_flow": "foreign flow",
    "rvol": "relative volume",
    "block_trade": "block trades",
}

# veto reason → short cell tag (the categorical reason, never a number)
_VETO_TAG = {
    "SINGLE_BANDAR_MONOPOLY": "SINGLE BANDAR",
    "DISTRIBUTION_DRESSED": "DISTRIBUTION",
    "MARKUP_ON_THIN_VOLUME": "THIN VOLUME",
    "WASH_CHURN": "WASH / CHURN",
    "BROKER_ROTATION": "BROKER ROTATION",
    "RETAIL_FOMO": "RETAIL FOMO",
    "EVENT_DRIVEN": "EVENT WINDOW",
    "PHASE_MISMATCH": "PHASE MISMATCH",
}


def _bn(value: float | None) -> str:
    """IDR value → compact 'bn' string (no fabricated precision)."""
    if value is None:
        return "—"
    bn = value / 1e9
    return f"{bn:.0f}" if bn >= 100 else f"{bn:.1f}".rstrip("0").rstrip(".")


def _cell(stage: str, state: str, tag: str, reason: str) -> dict:
    return {"stage": stage, "state": state, "tag": tag, "reason": reason}


def _skip(stage: str) -> dict:
    return _cell(stage, SKIP, "NOT EVALUATED", "stopped upstream — never reached this stage")


def _gate_state(adv20: float | None) -> str:
    if adv20 is None:
        return LOW  # missing ≠ zero — floor unconfirmed, not a reject
    return PASS if adv20 >= config.ADV_FLOOR_IDR else FAIL


def _phase_tag(phase: WyckoffPhase) -> str:
    return {
        WyckoffPhase.C: "PHASE C",
        WyckoffPhase.D: "PHASE D",
        WyckoffPhase.A: "PHASE A",
        WyckoffPhase.B: "PHASE B",
        WyckoffPhase.E: "PHASE E · LATE",
        WyckoffPhase.DISTRIBUTION: "DISTRIBUTION",
        WyckoffPhase.DOWNTREND: "DOWNTREND",
        WyckoffPhase.UNKNOWN: "NO STRUCTURE",
    }.get(phase, phase.value)


def _sig_reason(result: EngineResult, *, armed: bool) -> str:
    """RULE-B-safe component summary — names the strongest available components as
    observation; never the composite number."""
    ranked = sorted(
        (c for c in result.sms.components if c.available and c.key in _COMP_LABEL),
        key=lambda c: c.subscore,
        reverse=True,
    )
    lead = ", ".join(_COMP_LABEL[c.key] for c in ranked[:2]) or "no component data"
    if armed:
        return f"track-weighted §4 components aligned — lead: {lead} · SMS internal (RULE B)"
    return (
        f"components below the internal ARMED bar — lead: {lead} · "
        "number withheld until paper-validated (RULE B)"
    )


def _row(candidate: dict) -> dict:
    """One pipeline candidate row: four stage cells + verdict. `candidate` carries
    the `EngineResult` plus display meta:
        {result, name, price, chg, adv20, sector}
    """
    r: EngineResult = candidate["result"]
    phase = r.phase
    veto = r.veto
    adv20 = candidate.get("adv20")

    gate_s = _gate_state(adv20)
    # decisive (first failing) stage → drives the verdict and the downstream skips
    if gate_s == FAIL:
        decisive = "gate"
    elif not phase.tradeable:
        decisive = "phase"
    elif veto.rejected:
        decisive = "veto"
    elif r.state is not EngineState.ARMED:
        decisive = "sig"   # WATCH — flow present, below the internal bar
    else:
        decisive = None    # ARMED — cleared every stage

    armed = decisive is None

    # -- gate cell --
    if gate_s == PASS:
        gate = _cell("gate", PASS, f"TRACK {r.track}",
                     f"ADV IDR {_bn(adv20)} bn ≥ 10 bn floor · Track-{r.track} assignment")
    elif gate_s == FAIL:
        gate = _cell("gate", FAIL, "BELOW FLOOR",
                     f"20-day ADV IDR {_bn(adv20)} bn < IDR 10 bn hard floor")
    else:  # LOW — adv unavailable
        gate = _cell("gate", LOW, "ADV UNAVAILABLE",
                     "20-day ADV unavailable — hard floor unconfirmed (missing ≠ zero)")

    # -- phase cell (RULE A) --
    if decisive == "gate":
        ph = _skip("phase")
    elif phase.tradeable:
        ph = _cell("phase", PASS, _phase_tag(phase.phase), phase.reason)
    else:
        ph = _cell("phase", FAIL, _phase_tag(phase.phase), phase.reason)

    # -- signal-components cell (RULE B) --
    if decisive in ("gate", "phase"):
        sig = _skip("sig")
    else:
        sig_state = PASS if r.state is EngineState.ARMED or r.sms.internal_score >= config.SMS_ARMED_THRESHOLD else LOW
        sig = _cell("sig", sig_state,
                    "COMPONENTS ALIGNED" if sig_state == PASS else "BELOW THRESHOLD",
                    _sig_reason(r, armed=sig_state == PASS))

    # -- veto cell (§5) --
    if decisive in ("gate", "phase", "sig"):
        ve = _skip("veto")
    elif veto.rejected:
        first = veto.vetoes[0]
        ve = _cell("veto", FAIL, _VETO_TAG.get(first.reason.value, first.reason.value),
                   " · ".join(v.detail for v in veto.vetoes))
    else:
        ve = _cell("veto", PASS, "ALL CLEAR", "no §5 breach — all filters clear")

    # -- verdict + note --
    if decisive == "gate":
        verdict, note = REJECTED, "hard liquidity floor — not negotiable (§3)"
    elif decisive == "phase":
        verdict, note = REJECTED, "RULE A: only Wyckoff Phase C/D is tradeable"
    elif decisive == "veto":
        verdict, note = REJECTED, "§5 hard reject — " + veto.vetoes[0].detail
    elif decisive == "sig":
        verdict, note = WATCH, "re-scored nightly on the new broker summary"
    else:
        verdict, note = ARMED, "on watchlist — flow + phase aligned; awaiting trigger (LD-3)"

    return {
        "ticker": r.symbol,
        "name": candidate.get("name", r.symbol),
        "track": r.track,
        "price": candidate.get("price"),
        "chg": candidate.get("chg"),
        "sector": candidate.get("sector"),
        "adv20": adv20,
        "cells": [gate, ph, sig, ve],
        "result": verdict,
        "note": note,
        # RULE-B-safe ordering key: internal SMS orders rows but is never emitted
        "_order": (_VERDICT_ORDER[verdict], -r.sms.internal_score),
    }


def _count_str(rows: list[dict]) -> str:
    counts = {ARMED: 0, WATCH: 0, REJECTED: 0}
    for row in rows:
        counts[row["result"]] += 1
    # EXITED omitted until the portfolio paper-trader feeds it (Phase 2)
    return f"{counts[ARMED]} armed · {counts[WATCH]} watch · {counts[REJECTED]} rejected"


def build_row(candidate: dict) -> dict:
    """A single candidate row (no ordering key) — used for the evidence-detail header."""
    row = _row(candidate)
    row.pop("_order", None)
    return row


_VERDICT_PHRASE = {ARMED: "is ARMED", WATCH: "is on WATCH", REJECTED: "was REJECTED"}


def detail_header(row: dict) -> dict:
    """Contextual evidence-view header derived from a candidate row (design v2):
    'Why {TICKER} is ARMED / is on WATCH / was REJECTED' + a subtitle naming the
    decisive stage (or, when every stage passed, the aligned signal)."""
    cells = {c["stage"]: c for c in row["cells"]}
    decisive = next((c for c in row["cells"] if c["state"] in (FAIL, LOW)), None)
    if decisive is not None:
        sub = f"Decisive stage: {decisive['reason']} — the tabs below show the underlying evidence."
    else:
        sub = f"Passed every stage — {cells['sig']['reason']}. The tabs below show the underlying evidence."
    return {"title": f"Why {row['ticker']} {_VERDICT_PHRASE[row['result']]}", "subtitle": sub}


def build_lanes(candidates: list[dict]) -> list[dict]:
    """Two lanes (Track A, then Track B) of candidate rows, ARMED → WATCH → REJECTED
    within each (strongest internal flow first — ordering only, RULE B). `candidates`
    is a list of `{result: EngineResult, name, price, chg, adv20, sector}` dicts.
    A lane with no candidates is still returned (empty rows) so the shell renders both
    tracks."""
    lanes = []
    for track in ("A", "B"):
        rows = [_row(c) for c in candidates if c["result"].track == track]
        rows.sort(key=lambda row: row["_order"])
        for row in rows:
            del row["_order"]
        lanes.append({
            "track": track,
            "label": f"TRACK {track}",
            "desc": _LANE_DESC[track],
            "rows": rows,
            "count_str": _count_str(rows),
        })
    return lanes
