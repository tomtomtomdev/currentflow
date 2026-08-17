"""Framework Lenses view-model — the five frameworks as five switchable sections.

Pure data shaping over `signals.frameworks`, no Streamlit. The Signal Pipeline shows the
frameworks **fused** into the locked §2 gate chain (a name stops at the first stage it
fails). This surface shows them **apart**: one section per framework, each listing every
symbol that framework read — including names the pipeline rejected upstream — plus a
CONFLUENCE section for the symbols more than one framework flagged.

RULE A: this is a read-only surface over `engine.evaluate()` results. A lens section never
arms, re-gates, or re-orders the decision path; every row carries the engine's own verdict
so agreement between frameworks can never be mistaken for tradeability.

RULE B: rows show a category and a sentence. The only digits on this surface are counts of
symbols (set cardinality — the same species of fact as the pipeline's "3 armed · 2 watch"),
never a score, probability, SMS value, or buy/sell verb.
"""

from __future__ import annotations

from currentflow.signals.frameworks import (
    LENS_META,
    LENS_ORDER,
    Confluence,
    Lens,
    LensState,
    SymbolRead,
    confluences,
    lens_tally,
)

CONFLUENCE = "confluence"

# switcher key → lens (the confluence section has no single lens)
SECTION_LENS: dict[str, Lens] = {
    "wyckoff": Lens.WYCKOFF,
    "wyckoff2": Lens.WYCKOFF_2,
    "vpa": Lens.VPA,
    "bandar": Lens.BANDARMOLOGY,
    "mf": Lens.MAGIC_FORMULA,
}
LENS_SECTION: dict[Lens, str] = {v: k for k, v in SECTION_LENS.items()}

# switcher order: the five frameworks, then the aggregation across them
SECTIONS: tuple[tuple[str, str], ...] = tuple(
    [(LENS_SECTION[lens], LENS_META[lens].label) for lens in LENS_ORDER]
    + [(CONFLUENCE, "Confluence")]
)

# short chip labels — used when one row names the *other* lenses that flagged the name
LENS_CHIP = {
    Lens.WYCKOFF: "WYCKOFF",
    Lens.WYCKOFF_2: "PROFILE",
    Lens.VPA: "VPA",
    Lens.BANDARMOLOGY: "BANDAR",
    Lens.MAGIC_FORMULA: "MAGIC F.",
}

# The five fixed slots of the cross-lens strip. Codes are categorical labels at constant
# positions — a name flagged by one lens and a name flagged by four produce the same
# geometry, only different slots. Never sorted on, never widened by count (RULE B).
LENS_CODE = {
    Lens.WYCKOFF: "WYK",
    Lens.WYCKOFF_2: "VP",
    Lens.VPA: "VPA",
    Lens.BANDARMOLOGY: "BND",
    Lens.MAGIC_FORMULA: "MF",
}
CONFLUENCE_CODE = "∪"

FRAMING = (
    "Each framework read on its own terms — a name rejected by the locked pipeline still "
    "appears here under whichever lens does see something. Observation, not a "
    "recommendation: agreement between frameworks is not a score and does not arm anything."
)

CONFLUENCE_FRAMING = (
    "Symbols more than one framework flagged at the same decision moment. The count is how "
    "many independent lenses agree — a set size, not a score, not a ranking of quality, and "
    "never multiplied into the Smart Money Score (RULE B). The engine verdict rides on every "
    "row: a name four frameworks like that RULE A still rejects is exactly that, and is not "
    "tradeable."
)

# state → display order within a section (what the framework saw, strongest read first),
# then ticker. Ordering only — it asserts nothing about which name is the better trade.
_STATE_ORDER = {
    LensState.FLAGGED: 0,
    LensState.CONTRARY: 1,
    LensState.NEUTRAL: 2,
    LensState.NOT_APPLICABLE: 3,
    LensState.UNAVAILABLE: 4,
}

_STATE_LABEL = {
    LensState.FLAGGED: "FLAGGED",
    LensState.CONTRARY: "CONTRARY",
    LensState.NEUTRAL: "NEUTRAL",
    LensState.NOT_APPLICABLE: "N/A",
    LensState.UNAVAILABLE: "UNREAD",
}

# What each read state *means*, in the framework's terms. Printed once in the persistent
# key and once per band, so "unread" can never be misread as "found nothing".
_STATE_MEANS = {
    LensState.FLAGGED: "this framework's accumulation read is present",
    LensState.CONTRARY: "it reads the other side",
    LensState.NEUTRAL: "read it, named nothing",
    LensState.NOT_APPLICABLE: "deliberately abstains",
    LensState.UNAVAILABLE: "could not read it (missing ≠ zero)",
}

# Bands render in this fixed order whether or not they hold rows — an empty state says so
# rather than disappearing. Same sort the rows already used, made visible.
BAND_ORDER = (
    LensState.FLAGGED,
    LensState.CONTRARY,
    LensState.NEUTRAL,
    LensState.NOT_APPLICABLE,
    LensState.UNAVAILABLE,
)

EMPTY_BAND_NOTE = "no name in this state today — stated, not omitted"

# de-emphasised without collapsing — the five states stay five
_DIM_STATES = frozenset({LensState.NOT_APPLICABLE, LensState.UNAVAILABLE})

# column captions — the four columns, named. The confluence section reads different
# columns over the same grid.
LENS_COLUMNS = ("SYMBOL", "THIS LENS READS", "IN ITS OWN VOCABULARY", "ALSO FLAGGED BY · ENGINE")
CONFLUENCE_COLUMNS = ("SYMBOL", "SET SIZE", "WHICH FRAMEWORKS", "ENGINE · RULE A")


def section_keys() -> tuple[str, ...]:
    return tuple(key for key, _ in SECTIONS)


def is_lens_section(key: str) -> bool:
    return key in SECTION_LENS


def section_header(key: str) -> dict:
    """Title + framing for one section of the switcher."""
    if key == CONFLUENCE:
        return {
            "key": CONFLUENCE,
            "code": CONFLUENCE_CODE,
            "title": "Confluence — where the frameworks agree",
            "framework": "aggregation across all five lenses",
            "scope": "symbols flagged by two or more frameworks at this decision moment",
            "source": "signals/frameworks.py · set membership only, no composite (RULE B)",
            "framing": CONFLUENCE_FRAMING,
        }
    meta = LENS_META[SECTION_LENS[key]]
    return {
        "key": key,
        "code": LENS_CODE[SECTION_LENS[key]],
        "title": meta.label,
        "framework": meta.framework,
        "scope": meta.scope,
        "source": meta.source,
        "framing": FRAMING,
    }


def _also(read: SymbolRead, exclude: Lens | None) -> tuple[str, ...]:
    """The *other* frameworks that flagged this same name — the aggregation, carried on
    every row so a section is never read in isolation."""
    return tuple(LENS_CHIP[lens] for lens in read.flagged if lens is not exclude)


def _strip(flagged, self_lens: Lens | None) -> tuple[dict, ...]:
    """The fixed five-slot cross-lens strip: one slot per framework, always all five, at
    constant positions. `self` is the section you are reading; `on` means that lens
    flagged this name; `off` means it did not. Set membership, never a proportion."""
    flagged = set(flagged)
    return tuple(
        {
            "code": LENS_CODE[lens],
            "slot": "self" if lens is self_lens else ("on" if lens in flagged else "off"),
        }
        for lens in LENS_ORDER
    )


def lens_rows(reads: list[SymbolRead], key: str) -> list[dict]:
    """Every symbol as one framework read it, strongest read first then ticker."""
    lens = SECTION_LENS[key]
    rows = []
    for read in reads:
        r = read.read(lens)
        rows.append({
            "ticker": read.symbol,
            "track": read.track,
            "state": r.state.value,
            "state_label": _STATE_LABEL[r.state],
            "tag": r.tag,
            "detail": r.detail,
            "note": r.note,
            "engine_state": read.engine_state,
            "tradeable": read.tradeable,
            "also": _also(read, lens),
            "strip": _strip(read.flagged, lens),
            "dim": r.state in _DIM_STATES,
            "_order": (_STATE_ORDER[r.state], read.symbol),
        })
    rows.sort(key=lambda row: row["_order"])
    for row in rows:
        del row["_order"]
    return rows


def lens_bands(rows: list[dict]) -> list[dict]:
    """The section's rows grouped under their read state — all five bands, in the fixed
    order, whether or not they hold rows. Grouping is the existing sort order made
    visible; it asserts nothing about which name is the better trade."""
    by_state: dict[str, list[dict]] = {state.value: [] for state in BAND_ORDER}
    for row in rows:
        by_state[row["state"]].append(row)
    bands = []
    for state in BAND_ORDER:
        group = by_state[state.value]
        n = len(group)
        bands.append({
            "state": state.value,
            "label": _STATE_LABEL[state],
            "means": _STATE_MEANS[state],
            "count": n,
            "count_str": f"{n} name" if n == 1 else f"{n} names",
            "empty_note": EMPTY_BAND_NOTE,
            "rows": group,
        })
    return bands


def read_state_key() -> tuple[dict, ...]:
    """The persistent read-state legend above every section. Printed always, so "unread"
    is never read as "found nothing"."""
    return tuple(
        {"state": state.value, "label": _STATE_LABEL[state], "means": _STATE_MEANS[state]}
        for state in BAND_ORDER
    )


def columns(key: str) -> tuple[str, ...]:
    """Captions for the four columns of this section's grid."""
    return CONFLUENCE_COLUMNS if key == CONFLUENCE else LENS_COLUMNS


def section_tabs(reads: list[SymbolRead]) -> list[dict]:
    """The switcher, as a census of the day: one card per section carrying its lens code,
    name, and a tally. Counts of symbols only (RULE B) — no ranking of the sections."""
    tally = lens_tally(reads)
    tabs = []
    for lens in LENS_ORDER:
        t = tally[lens]
        tabs.append({
            "key": LENS_SECTION[lens],
            "code": LENS_CODE[lens],
            "label": LENS_META[lens].label,
            # flagged and unread — the two facts that must never be inferred from silence
            "tally": f"{t[LensState.FLAGGED]} flagged · {t[LensState.UNAVAILABLE]} unread",
        })
    rows = confluences(reads)
    tabs.append({
        "key": CONFLUENCE,
        "code": CONFLUENCE_CODE,
        "label": "Confluence",
        "tally": f"{len(rows)} symbols · widest {rows[0].n_lenses if rows else 0}",
    })
    return tabs


def section_count(reads: list[SymbolRead], key: str) -> str:
    """The section's census line: how many symbols each state, in words + counts."""
    if key == CONFLUENCE:
        rows = confluences(reads)
        if not rows:
            return "no symbol flagged by two or more frameworks"
        widest = rows[0].n_lenses
        return f"{len(rows)} symbols · widest agreement: {widest} of {len(LENS_ORDER)} lenses"
    tally = lens_tally(reads)[SECTION_LENS[key]]
    parts = [
        f"{tally[LensState.FLAGGED]} flagged",
        f"{tally[LensState.CONTRARY]} contrary",
        f"{tally[LensState.NEUTRAL]} neutral",
    ]
    if tally[LensState.NOT_APPLICABLE]:
        parts.append(f"{tally[LensState.NOT_APPLICABLE]} not applicable")
    # UNREAD is always named, including at zero — a framework that could read every name
    # today is itself a fact worth stating (missing ≠ zero cuts both ways).
    parts.append(f"{tally[LensState.UNAVAILABLE]} unread")
    return " · ".join(parts)


def confluence_rows(reads: list[SymbolRead], *, minimum: int = 2) -> list[dict]:
    """The aggregation rows: one per symbol flagged by ≥ `minimum` frameworks."""
    return [_confluence_row(c) for c in confluences(reads, minimum=minimum)]


def _confluence_row(c: Confluence) -> dict:
    return {
        "ticker": c.symbol,
        "track": c.track,
        "lenses": tuple(LENS_CHIP[lens] for lens in c.lenses),
        "strip": _strip(c.lenses, None),
        "n_lenses": c.n_lenses,
        "n_total": len(LENS_ORDER),
        "engine_state": c.engine_state,
        "tradeable": c.tradeable,
        # the loudest line in the row when it reads NOT TRADEABLE: agreement between
        # frameworks must never out-shout the RULE A gate that rejected the name
        "rule_a": "RULE A · TRADEABLE PHASE" if c.tradeable else "RULE A · NOT TRADEABLE",
        "note": _confluence_note(c),
    }


def _confluence_note(c: Confluence) -> str:
    """Why the agreement and the engine can disagree — stated on the row, never hidden."""
    if c.tradeable:
        return (
            f"{c.n_lenses} frameworks flagged this name; the pipeline verdict is "
            f"{c.engine_state} — see the Signal Pipeline for the stage that decided it"
        )
    return (
        f"{c.n_lenses} frameworks flagged this name, but RULE A rejected it "
        f"({c.engine_state}) — only Wyckoff Phase C/D is tradeable, and framework "
        "agreement never overrides that gate"
    )


def summary(reads: list[SymbolRead]) -> dict:
    """Top-of-view census across every lens — counts of symbols, nothing derived."""
    rows = confluences(reads)
    tally = lens_tally(reads)
    return {
        "n_symbols": len(reads),
        "n_confluence": len(rows),
        "per_lens": {
            LENS_SECTION[lens]: tally[lens][LensState.FLAGGED] for lens in LENS_ORDER
        },
        "framing": FRAMING,
    }
