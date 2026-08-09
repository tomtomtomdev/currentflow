"""VPA bar-character view-model (LD-13, slice 18) — pure data shaping, no Streamlit.

Observation only (RULE B): a bar's character is a *word* (No Demand / No Supply /
Absorption / …) with a word-severity and digit-free copy. The close-position, spread and
volume ratios that produced it are audit measurements on the signal, not display values —
a magnitude rendered beside a live name is exactly what RULE B withholds until earned.
"""

from __future__ import annotations

from currentflow.signals.vpa import BarCharacter, VpaBar, VpaReading

# Per-character glyph + short label + semantic color key (shell.TOKENS keys) + the
# digit-free note. Demand-side tells read in the buy/accent colors, the weakness side in
# sell/armed — colour is salience, never a recommendation.
CHARACTER_COPY: dict[str, tuple[str, str, str, str]] = {
    "NO_DEMAND": ("▽", "No Demand", "sell",
                  "up bar, narrow spread, volume under the recent average, after a rally"),
    "NO_SUPPLY": ("△", "No Supply", "buy",
                  "down bar, narrow spread, volume under the recent average, after a decline"),
    "ABSORPTION": ("◍", "Absorption", "accent",
                   "heavy volume, lower close, bar finished near its high"),
    "STOPPING_VOLUME": ("◉", "Stopping Volume", "buy",
                        "heavy volume made a new low and the bar closed near its high"),
    "SUPPLY_PRESENT": ("▼", "Supply Present", "sell",
                       "heavy volume lifted the close, bar finished in its lower third"),
    "CHURN": ("≈", "Churn", "armed",
              "heavy volume, narrow spread — effort with no price result"),
    "DEMAND_CONFIRMED": ("▲", "Demand Confirmed", "buy",
                         "heavy volume, wide spread, close on the high"),
    "NEUTRAL": ("·", "Neutral", "text_faint",
                "spread, volume and close position unremarkable"),
    "UNREADABLE": ("×", "Unreadable", "text_faint",
                   "no spread or no calibration base — not read as a neutral bar"),
}

RIBBON_FRAMING = "close position within the spread · effort vs result · observation"
EMPTY_LABEL = "no readable bars in the window — nothing to characterise"


def _cell(bar: VpaBar) -> dict:
    glyph, label, color, note = CHARACTER_COPY[bar.character.value]
    return {
        "date": bar.date,
        "character": bar.character.value,
        "label": label,
        "glyph": glyph,
        "color_key": color,
        "severity": bar.severity.value,
        "note": note,
        "effort_without_result": bar.effort_without_result,
        "result_without_effort": bar.result_without_effort,
        "available": bar.available,
    }


def ribbon_cells(reading: VpaReading) -> list[dict]:
    """One cell per bar, chronological — the ribbon lane for the Replay / Accumulation
    evidence tabs. Every cell is categorical; no ratio reaches the view."""
    return [_cell(b) for b in reading.bars]


def character_panel(reading: VpaReading) -> dict:
    """The latest bar's character as a panel reading, plus how often the demand-side
    tells printed in the window. `bars_*` are data-availability counts, not measurements."""
    latest = reading.latest
    if latest is None or not reading.available:
        return {
            "character": "UNREADABLE",
            "label": CHARACTER_COPY["UNREADABLE"][1],
            "glyph": CHARACTER_COPY["UNREADABLE"][0],
            "color_key": "text_faint",
            "severity": "INFO",
            "headline": EMPTY_LABEL,
            "note": CHARACTER_COPY["UNREADABLE"][3],
            "available": False,
            "demand_side_bars": 0,
            "bars_read": len(reading.bars),
            "effort_without_result": False,
            "result_without_effort": False,
        }
    cell = _cell(latest)
    return cell | {
        "headline": latest.detail,
        "available": reading.available,
        "demand_side_bars": reading.demand_side_bars,
        "bars_read": len(reading.bars),
    }


def effort_note(reading: VpaReading) -> str | None:
    """The effort-vs-result flag as one neutral line, or None when the latest bar shows
    neither anomaly. Never a verb, never a magnitude."""
    latest = reading.latest
    if latest is None or not latest.available:
        return None
    if latest.effort_without_result:
        return ("Effort without result: the volume arrived and the close did not follow "
                "— the anomaly the divergence spine cannot see.")
    if latest.result_without_effort:
        return ("Result without effort: the range moved with no volume behind it "
                "— a move nobody paid for.")
    return None


def demand_side_characters() -> tuple[str, ...]:
    """The accumulation-side character names, for the ribbon legend."""
    return ("NO_SUPPLY", "ABSORPTION", "STOPPING_VOLUME")


def character_label(character: BarCharacter | str) -> str:
    key = character.value if isinstance(character, BarCharacter) else character
    return CHARACTER_COPY[key][1]
