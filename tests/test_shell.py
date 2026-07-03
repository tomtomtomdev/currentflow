"""Design-shell helpers (ui/shell.py) — RULE-B safety and design-contract tests.

The shell renders the design/screens/ pixel targets' chrome as HTML strings. The
load-bearing assertions: no composite score, probability, or buy/sell verb ever
appears in watchlist cards or the unvalidated validation bar; missing components
render as absent, never as zero; the §15 disclaimers all ride the ticker.
"""

from __future__ import annotations

import re

from currentflow.ui import shell

_ROW = {
    "symbol": "BRMS",
    "track": "B",
    "state": "ARMED",
    "components": {"DIV": 82, "BRK": 74, "FF": 31, "RVOL": 66, "BLK": 12},
}

_FORBIDDEN_VERBS = re.compile(r"\b(buy|sell|long|short)\b", re.IGNORECASE)


def _visible_text(html: str) -> str:
    """Strip tags AND attribute content — what the operator actually reads
    (tooltips carry component values by design; they are not visible text)."""
    return re.sub(r"<[^>]*>", " ", html)


class TestWatchlistCard:
    def test_state_word_and_labels_render(self):
        html = shell.watchlist_card_html(_ROW)
        text = _visible_text(html)
        assert "BRMS" in text and "ARMED" in text
        for label in shell.SPARK_ORDER:
            assert label in text

    def test_no_composite_number_rank_or_verb(self):
        # RULE B: visible card text carries no numerics (component values live only
        # in hover tooltips) and no buy/sell verb.
        text = _visible_text(shell.watchlist_card_html(_ROW))
        assert not re.search(r"\d", text)
        assert "SMS" not in text
        assert not _FORBIDDEN_VERBS.search(text)

    def test_missing_component_is_absent_not_zero(self):
        row = dict(_ROW, components=dict(_ROW["components"], FF=None))
        html = shell.watchlist_card_html(row)
        assert "FF: not available" in html
        # an available zero-strength bar still gets the minimum 2px stub with its value
        zero = dict(_ROW, components=dict(_ROW["components"], DIV=0))
        assert 'title="DIV 0"' in shell.watchlist_card_html(zero)

    def test_ff_bar_colored_by_direction_others_by_state(self):
        html = shell.watchlist_card_html(_ROW)  # FF=31 → faded red
        assert f"{shell.TOKENS['sell']}88" in html
        strong_ff = dict(_ROW, components=dict(_ROW["components"], FF=88))
        assert shell.TOKENS["foreign"] in shell.watchlist_card_html(strong_ff)
        assert shell.TOKENS["armed"] in html  # ARMED state colors the other bars

    def test_watch_state_uses_accent(self):
        row = dict(_ROW, state="WATCH")
        html = shell.watchlist_card_html(row)
        assert shell.TOKENS["accent"] in html
        assert "WATCH" in _visible_text(html)


class TestWatchlistRail:
    def test_cap_is_never_silent(self):
        data = {"rows": [_ROW], "total": 9, "dropped": 8, "framing": "observation framing"}
        html = shell.watchlist_rail_html(data)
        assert "…and 8 more" in html and "of 9" in html

    def test_empty_rail_says_so(self):
        data = {"rows": [], "total": 0, "dropped": 0, "framing": "observation framing"}
        assert "nothing ARMED or watching today" in shell.watchlist_rail_html(data)


class TestValidationBar:
    def test_unvalidated_withholds_the_number(self):
        html = shell.validation_bar_html(1.4, 3, False)
        assert "number withheld" in html
        assert "1.4 / 3" in html
        assert "CLAIM" not in html

    def test_validated_enables_the_claim(self):
        html = shell.validation_bar_html(3.0, 3, True)
        assert "CLAIM ENABLED" in html and "withheld" not in html

    def test_progress_is_clamped(self):
        assert "width:100%" in shell.validation_bar_html(7.5, 3, True)


class TestChrome:
    def test_top_bar_carries_rule_b_pill_and_as_of(self):
        html = shell.top_bar_html(as_of="2026-07-03", operator="op · ····a1f9")
        assert shell.RULE_B_PILL in html
        assert "2026-07-03" in html and "WIB" in html

    def test_top_bar_missing_as_of_shows_absent(self):
        assert ">—</span> WIB" in shell.top_bar_html(as_of=None)

    def test_ticker_cycles_every_section_15_disclaimer(self):
        html = shell.ticker_html()
        from html import escape

        for line in shell.DISCLAIMERS:
            assert escape(line) in html
        assert "LOCAL · SINGLE-USER · PAPER" in html

    def test_badge_kinds(self):
        assert "OBSERVATION" in shell.badge_html("observation", "OBSERVATION · ships now")
        gated = shell.badge_html("gated", "GATED · number withheld (RULE B)")
        assert shell.TOKENS["armed_text"] in gated

    def test_html_is_escaped(self):
        row = dict(_ROW, symbol="<script>")
        assert "<script>" not in shell.watchlist_card_html(row)
