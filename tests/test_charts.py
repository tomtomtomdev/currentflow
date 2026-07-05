"""Altair chart builders (ui/charts.py) — spec-validity and design-contract tests.

Each builder must produce a vega-lite spec that serializes (`to_dict` validates the
schema), keep the design palette, and never coerce missing data to zero.
"""

from __future__ import annotations

from datetime import date

from currentflow.ui import charts
from currentflow.ui.shell import TOKENS

_DATES = [date(2026, 6, d) for d in range(1, 11)]


class TestForeignFlowCharts:
    def test_cumulative_serializes_with_blue_line(self):
        rows = [{"date": d, "cumulative_bn": i - 3.0} for i, d in enumerate(_DATES)]
        spec = charts.themed(charts.foreign_cumulative(rows)).to_dict()
        assert TOKENS["foreign"] in str(spec)
        assert spec["config"]["background"] == "transparent"

    def test_daily_bars_color_by_sign(self):
        rows = [{"date": d, "net_foreign_bn": (-1.0) ** i} for i, d in enumerate(_DATES)]
        spec = charts.foreign_daily(rows).to_dict()
        s = str(spec)
        assert TOKENS["foreign"] in s and TOKENS["sell"] in s


class TestAccumulationChart:
    _ROWS = [
        {"date": d, "close": 100.0 + i, "cum_accumulation_bn": i * 0.5 if i else None}
        for i, d in enumerate(_DATES)
    ]

    def test_combined_serializes_with_both_lanes_and_vwap(self):
        spec = charts.themed(
            charts.accumulation_combined(
                self._ROWS, vwap=104.0, stealth_zone=(_DATES[5], _DATES[-1])
            )
        ).to_dict()
        s = str(spec)
        assert TOKENS["accent"] in s          # price lane
        assert TOKENS["smart"] in s           # accumulation lane
        assert "VWAP 104" in s
        assert "STEALTH ZONE" in s
        assert spec["resolve"]["scale"]["y"] == "independent"

    def test_no_zone_no_vwap_still_serializes(self):
        spec = charts.accumulation_combined(self._ROWS, vwap=None).to_dict()
        assert "STEALTH ZONE" not in str(spec) and "VWAP" not in str(spec)


class TestSectorQuadrant:
    _PTS = [
        {"sector": "Energy", "x_relative_strength_pct": 3.0, "y_net_flow_bn": 5.0,
         "radius_flow_bn": 5.0, "quadrant": "LEADERS"},
        {"sector": "Technology", "x_relative_strength_pct": 2.0, "y_net_flow_bn": -4.0,
         "radius_flow_bn": 4.0, "quadrant": "DISTRIBUTION_WARN"},
    ]

    def test_quadrant_serializes_with_labels_and_colors(self):
        spec = charts.themed(charts.sector_quadrant(self._PTS)).to_dict()
        s = str(spec)
        for label in ("LEADERS", "EARLY RECOVERY", "DISTRIBUTION WARN", "AVOID"):
            assert label in s
        assert TOKENS["buy"] in s and TOKENS["sell"] in s
