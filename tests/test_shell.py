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

    def test_selected_card_carries_the_brightened_border(self):
        # design: the rail card is the symbol selector; the active name's card
        # gets a brightened border (see design screens: PTRO / CUAN / NCKL).
        plain = shell.watchlist_card_html(_ROW)
        selected = shell.watchlist_card_html(_ROW, selected=True)
        assert "border-color:rgba(255,255,255,0.30)" in selected
        assert "border-color:rgba(255,255,255,0.30)" not in plain
        # selection styling never adds a number, rank, or verb (RULE B)
        assert not re.search(r"\d", _visible_text(selected))


class TestWatchlistRail:
    def test_cap_is_never_silent(self):
        data = {"rows": [_ROW], "total": 9, "dropped": 8, "framing": "observation framing"}
        html = shell.watchlist_rail_html(data)
        assert "…and 8 more" in html and "of 9" in html

    def test_empty_rail_says_so(self):
        data = {"rows": [], "total": 0, "dropped": 0, "framing": "observation framing"}
        assert "nothing ARMED or watching today" in shell.watchlist_rail_html(data)

    def test_rail_highlights_the_selected_symbol_only(self):
        other = dict(_ROW, symbol="PTRO")
        data = {"rows": [_ROW, other], "total": 2, "dropped": 0,
                "framing": "observation framing"}
        html = shell.watchlist_rail_html(data, selected="PTRO")
        assert html.count("border-color:rgba(255,255,255,0.30)") == 1

    def test_rail_head_and_foot_compose_the_static_rail(self):
        # the app renders head/cards/foot separately (clickable cards); the pieces
        # must reassemble into exactly the static whole-rail blob.
        data = {"rows": [_ROW], "total": 9, "dropped": 8, "framing": "observation framing"}
        composed = (
            shell.rail_head_html(data["framing"])
            + shell.watchlist_card_html(_ROW)
            + shell.rail_foot_html(data)
        )
        assert composed == shell.watchlist_rail_html(data)


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
        html = shell.top_bar_html(as_of="2026-07-03")
        assert shell.RULE_B_PILL in html
        assert "2026-07-03" in html and "WIB" in html

    def test_top_bar_missing_as_of_shows_absent(self):
        assert ">—</span> WIB" in shell.top_bar_html(as_of=None)

    def test_top_bar_renders_ihsg_when_ingested(self):
        html = shell.top_bar_html(as_of="2026-07-03", ihsg=7241.6, ihsg_change_pct=-0.42)
        assert "IHSG" in html and "7,241.6" in html and "-0.42%" in html

    def test_top_bar_ihsg_absent_when_not_ingested(self):
        # not benchmarked to IHSG (§8); a missing datum is shown absent, never faked.
        assert "IHSG <span" in shell.top_bar_html(as_of="2026-07-03")
        assert "%" not in shell.ihsg_html(None, None)

    def test_operator_head_masks_the_session(self):
        html = shell.operator_head_html("op", "····a1f9", "keychain")
        assert "op" in html and "····a1f9" in html and "keychain" in html

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


class TestBrokerFlowPanels:
    _ROWS = [
        {"#": 1, "broker": "BQ", "dna": "PROP", "net_idr_bn": 37.0,
         "buy_idr_bn": 40.0, "sell_idr_bn": 3.0, "persist": "●●●●●●○",
         "accum_vwap": 412.0},
        {"#": 2, "broker": "CP", "dna": "RETAIL", "net_idr_bn": -10.6,
         "buy_idr_bn": 1.0, "sell_idr_bn": 11.6, "persist": "○○○○○○○",
         "accum_vwap": None},
    ]

    def test_broker_table_signed_colored_with_dna_chips(self):
        html = shell.broker_table_html(self._ROWS)
        assert "+37.00" in html and "-10.60" in html
        assert shell.TOKENS["buy"] in html and shell.TOKENS["sell"] in html
        text = _visible_text(html)
        assert shell.DNA_LABELS["PROP"] in text and shell.DNA_LABELS["RETAIL"] in text
        assert "PERSIST" in text  # 7-dot strip labeled

    def test_concentration_missing_measurement_is_absent(self):
        html = shell.concentration_html(
            {"top2_share_pct": None, "hhi": None, "hhi_label": None, "top2_names": None}
        )
        assert "—" in _visible_text(html)
        assert "0.00" not in html and "0%" not in html  # never faked as zero

    def test_concentration_renders_share_bar_and_hhi_label(self):
        html = shell.concentration_html(
            {"top2_share_pct": 83.0, "hhi": 0.4, "hhi_label": "highly concentrated",
             "top2_names": "BQ, NI"}
        )
        text = _visible_text(html)
        assert "83%" in text and "0.40" in text
        assert "highly concentrated" in text and "BQ, NI" in text

    def test_veto_panel_marks_fired_vs_clear(self):
        html = shell.veto_panel_html([
            {"check": "RETAIL_FOMO", "label": "Retail-FOMO (buy ratio >60%)",
             "fired": True, "detail": "retail brokers are 70% of buying"},
            {"check": "WASH_CHURN", "label": "Wash / churn", "fired": False,
             "detail": None},
        ])
        text = _visible_text(html)
        assert "✕" in text and "✓" in text and "clear" in text
        assert "70% of buying" in text

    def test_matrix_missing_cell_is_absent_not_zero(self):
        html = shell.matrix_html(
            [{"broker": "KZ", "BRMS": 10.0, "PTRO": None}], ["BRMS", "PTRO"]
        )
        assert "+10.0" in html
        assert "not a top participant" in html
        assert "0.0" not in _visible_text(html).replace("+10.0", "")

    def test_matrix_highlights_selected_column(self):
        html = shell.matrix_html(
            [{"broker": "KZ", "BRMS": 10.0, "PTRO": -4.0}],
            ["BRMS", "PTRO"], selected="PTRO",
        )
        assert f'style="color:{shell.TOKENS["accent"]}">PTRO' in html

    def test_stock_header_missing_price_is_absent(self):
        html = shell.stock_header_html(symbol="BRMS", track="B")
        assert "BRMS" in html and "TRACK B" in html
        assert "cf-price" not in html and "ADV" not in html

    def test_stock_header_full(self):
        html = shell.stock_header_html(
            symbol="BRMS", track="B", sector="Basic Materials",
            price=412.0, change_pct=2.74, adv_bn=38.0,
        )
        text = _visible_text(html)
        assert "412" in text and "+2.74%" in text and "38" in text
        assert "Basic Materials" in text

    def test_matrix_rows_carry_dna_chips_when_known(self):
        html = shell.matrix_html(
            [{"broker": "KZ", "dna": "FOREIGN_INST", "BRMS": 10.0}], ["BRMS"]
        )
        assert shell.DNA_LABELS["FOREIGN_INST"] in _visible_text(html)
        # unknown DNA row renders without a chip, not with a fake one
        plain = shell.matrix_html([{"broker": "KZ", "BRMS": 10.0}], ["BRMS"])
        assert "cf-chip" not in plain


class TestDesignPanels:
    def test_kv_rows_missing_value_is_absent_not_zero(self):
        html = shell.kv_rows_html([{"label": "vs prior average", "value": None}])
        assert "—" in _visible_text(html)
        assert "None" not in html and "0" not in _visible_text(html)

    def test_split_bar_signed_labels(self):
        html = shell.split_bar_html(1.2, -0.8)
        text = _visible_text(html)
        assert "FGN+1.2" in text and "DOM-0.8" in text

    def test_sparkline_needs_two_points_and_scales(self):
        svg = shell.sparkline_svg([36.2, 36.8, 37.1])
        assert svg.startswith("<svg") and "polyline" in svg

    def test_callout_carries_label_and_text(self):
        html = shell.callout_html("FLOW-REVERSAL DETECTION", "reversed on 26 May.")
        text = _visible_text(html)
        assert "FLOW-REVERSAL DETECTION" in text and "reversed on 26 May." in text

    def test_heat_tile_missing_intensity_is_absent(self):
        grid = shell.heatmap_grid_html([{
            "sector": "Energy",
            "tiles": [{"symbol": "PTRO", "direction": "BUY",
                       "intensity_pct_of_cap": None, "divergence": False,
                       "foreign_net_bn": None, "local_smart_net_bn": 1.0}],
        }])
        assert "—" in _visible_text(grid)

    def test_heat_tile_divergence_ring_and_intensity(self):
        grid = shell.heatmap_grid_html([{
            "sector": "Energy",
            "tiles": [
                {"symbol": "PTRO", "direction": "BUY", "intensity_pct_of_cap": 0.8,
                 "divergence": True, "foreign_net_bn": 2.0, "local_smart_net_bn": 1.0},
                {"symbol": "RAJA", "direction": "SELL", "intensity_pct_of_cap": 0.4,
                 "divergence": False, "foreign_net_bn": -1.0, "local_smart_net_bn": 0.2},
            ],
        }])
        assert "cf-div" in grid
        assert "+0.80%" in grid and "−0.40%" in grid
        assert "net sell" in _visible_text(grid)  # legend rides the grid

    def test_divergence_panel_empty_says_clear(self):
        assert "no divergence alerts" in _visible_text(shell.divergence_panel_html([]))

    def test_sector_card_chip_and_stats(self):
        html = shell.sector_card_html({
            "sector": "Technology", "quadrant": "DISTRIBUTION_WARN",
            "note": "strength persisting while flow leaves (watch)",
            "net_foreign_flow_bn": -0.35, "relative_strength_pct": 0.6, "tide": None,
        })
        text = _visible_text(html)
        assert "DISTRIBUTION WARN" in text and "-0.35" in text
        assert shell.TOKENS["sell"] in html

    def test_stat_cards_missing_is_dash(self):
        html = shell.stat_cards_html([{"label": "Portfolio β", "value": None, "sub": None}])
        assert "—" in _visible_text(html)

    def test_positions_pnl_is_withheld_never_faked(self):
        html = shell.positions_table_html([{
            "symbol": "BRMS", "sector": "Basic Materials", "weight_pct": 8.2,
            "cap_pct": 10.0, "status": "WARN", "days_to_exit": 2.1,
        }])
        assert "no paper fills yet" in html
        assert "8.2%" in _visible_text(html) and "2.1d" in _visible_text(html)

    def test_crowding_matrix_missing_is_empty_slot(self):
        html = shell.crowding_matrix_html(
            {"A": {"A": 1.0, "B": None}, "B": {"A": None, "B": None}}, threshold=0.7
        )
        assert "no broker flow" in html

    def test_crowding_matrix_flags_threshold(self):
        html = shell.crowding_matrix_html(
            {"A": {"A": 1.0, "B": 0.72}, "B": {"A": 0.72, "B": 1.0}}, threshold=0.7
        )
        assert "outline" in html and "0.72" in _visible_text(html)

    def test_scenario_rows_signed_and_colored(self):
        html = shell.scenario_rows_html([{
            "scenario": "IHSG −5% gap", "detail": "β 1.24 × −5%",
            "impact_bn": -3.2, "impact_pct_of_equity": -6.4,
        }])
        assert "-6.4%" in _visible_text(html)
        assert shell.TOKENS["sell"] in html

    def test_login_hero_carries_rule_b_and_no_advice_verbs(self):
        text = _visible_text(shell.login_hero_html())
        assert "Sign in to open the terminal." in text
        assert "RULE B" in text
        assert not _FORBIDDEN_VERBS.search(text.replace("Sign in", ""))
