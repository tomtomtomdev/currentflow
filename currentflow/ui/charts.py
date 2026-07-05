"""Altair chart builders for the design screens (design/screens/) — pure functions
from view-model rows to `alt.Chart`, no Streamlit imports.

Geometry and palette follow the pixel targets: foreign-flow blue area/bars, cyan
price lane over an amber accumulation lane with the shaded stealth zone, and the
sector quadrant map. All inputs are observation rows shaped by the ui/*_view
modules — nothing here computes a signal, score, or claim (RULE B).

Missing data stays missing: rows carrying None simply do not plot (Altair drops
null-encoded marks); no series is coerced to zero.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from currentflow.ui.shell import TOKENS

_MONO = "Geist Mono, ui-monospace, SF Mono, Menlo, monospace"
_GRID = "rgba(255,255,255,0.05)"
_LANE_TITLE_KW = dict(
    fontSize=12, font=_MONO, color="rgba(139,152,169,0.5)", fontWeight=400,
    anchor="start", dy=-4,
)


def themed(chart: alt.Chart | alt.LayerChart) -> alt.Chart | alt.LayerChart:
    """The design's chart chrome: transparent canvas, hairline grid, muted mono
    axis labels, no view border."""
    return (
        chart.configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(
            grid=True, gridColor=_GRID, domainOpacity=0, tickOpacity=0,
            labelColor=TOKENS["text_muted"], labelFontSize=9.5, labelFont=_MONO,
            titleColor=TOKENS["text_faint"], titleFontSize=9.5, titleFont=_MONO,
            titleFontWeight=400,
        )
        .configure_title(**_LANE_TITLE_KW)
    )


def _date_x() -> alt.X:
    return alt.X("date:T", axis=alt.Axis(title=None, format="%d %b"))


def foreign_cumulative(rows: list[dict], *, height: int = 230) -> alt.LayerChart:
    """CUMULATIVE FOREIGN NET lane: blue line over a fading blue area."""
    df = pd.DataFrame(rows)
    base = alt.Chart(df, height=height, title="CUMULATIVE FOREIGN NET")
    area = base.mark_area(
        color=alt.Gradient(
            gradient="linear",
            stops=[
                alt.GradientStop(color="rgba(88,166,255,0.28)", offset=1),
                alt.GradientStop(color="rgba(88,166,255,0.02)", offset=0),
            ],
            x1=1, x2=1, y1=1, y2=0,
        ),
    ).encode(x=_date_x(), y=alt.Y("cumulative_bn:Q", axis=alt.Axis(title="IDR bn"),
                                  scale=alt.Scale(zero=False)))
    line = base.mark_line(color=TOKENS["foreign"], strokeWidth=2).encode(
        x=_date_x(), y=alt.Y("cumulative_bn:Q", scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("cumulative_bn:Q", title="cum IDR bn")],
    )
    return alt.layer(area, line)


def foreign_daily(rows: list[dict], *, height: int = 150) -> alt.Chart:
    """DAILY FOREIGN NET lane: bars around zero, blue net-buy / red net-sell."""
    df = pd.DataFrame(rows)
    return (
        alt.Chart(df, height=height, title="DAILY FOREIGN NET (IDR bn)")
        .mark_bar(size=6, cornerRadiusTopLeft=1, cornerRadiusTopRight=1)
        .encode(
            x=_date_x(),
            y=alt.Y("net_foreign_bn:Q", axis=alt.Axis(title=None)),
            color=alt.condition(
                alt.datum.net_foreign_bn >= 0,
                alt.value(TOKENS["foreign"]),
                alt.value(TOKENS["sell"]),
            ),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("net_foreign_bn:Q", title="net IDR bn")],
        )
    )


def accumulation_combined(
    rows: list[dict],
    *,
    vwap: float | None,
    stealth_zone: tuple | None = None,
    height: int = 330,
) -> alt.LayerChart:
    """The Accum-Detect canvas: cyan price lane + amber cumulative-accumulation lane
    (independent y scales), dashed accumulator-VWAP reference with right label, and
    the shaded STEALTH ZONE band over the divergence window (when detected)."""
    df = pd.DataFrame(rows)
    price_layers = []

    if stealth_zone is not None:
        zone = pd.DataFrame([{"start": stealth_zone[0], "end": stealth_zone[1]}])
        price_layers.append(
            alt.Chart(zone).mark_rect(color=TOKENS["armed"], opacity=0.07)
            .encode(x="start:T", x2="end:T")
        )
        price_layers.append(
            alt.Chart(zone).mark_text(
                text="STEALTH ZONE", color=TOKENS["armed_text"], font=_MONO,
                fontSize=11, baseline="top", dy=6,
            ).encode(x="start:T", y=alt.value(0))
        )

    price_layers.append(
        alt.Chart(df).mark_line(color=TOKENS["accent"], strokeWidth=2).encode(
            x=_date_x(),
            y=alt.Y("close:Q", scale=alt.Scale(zero=False),
                    axis=alt.Axis(title="price", orient="left")),
            tooltip=[alt.Tooltip("date:T"), alt.Tooltip("close:Q")],
        )
    )
    if vwap is not None:
        ref = pd.DataFrame([{"vwap": vwap, "date": df["date"].max()}])
        price_layers.append(
            alt.Chart(ref).mark_rule(
                color="rgba(230,237,243,0.45)", strokeDash=[5, 4], strokeWidth=1
            ).encode(y="vwap:Q")
        )
        price_layers.append(
            alt.Chart(ref).mark_text(
                text=f"VWAP {vwap:,.0f}", color=TOKENS["text_secondary"], font=_MONO,
                fontSize=11, align="right", baseline="bottom", dx=-4, dy=-4,
            ).encode(x="date:T", y="vwap:Q")
        )

    accum = alt.Chart(df).mark_line(
        color=TOKENS["smart"], strokeWidth=2, interpolate="monotone"
    ).encode(
        x=_date_x(),
        y=alt.Y("cum_accumulation_bn:Q",
                axis=alt.Axis(title="cumulative accumulation (IDR bn)", orient="right")),
        tooltip=[alt.Tooltip("date:T"),
                 alt.Tooltip("cum_accumulation_bn:Q", title="cum IDR bn")],
    )
    combined = alt.layer(alt.layer(*price_layers), accum).resolve_scale(y="independent")
    return combined.properties(height=height)


def sector_quadrant(points: list[dict], *, height: int = 420) -> alt.LayerChart:
    """The RS × flow quadrant map: tinted LEADERS / DISTRIBUTION WARN halves, zero
    axes, one labeled bubble per sector (radius = |flow|)."""
    df = pd.DataFrame(points)
    df["code"] = df["sector"].str.replace(r"[^A-Za-z]", "", regex=True).str.upper().str[:4]
    xmax = max(df["x_relative_strength_pct"].abs().max(), 1.0) * 1.25
    ymax = max(df["y_net_flow_bn"].abs().max(), 1.0) * 1.25
    xscale = alt.Scale(domain=[-xmax, xmax])
    yscale = alt.Scale(domain=[-ymax, ymax])

    quads = pd.DataFrame([
        {"x": 0, "x2": xmax, "y": 0, "y2": ymax, "color": "rgba(63,185,80,0.05)"},
        {"x": 0, "x2": xmax, "y": -ymax, "y2": 0, "color": "rgba(248,81,73,0.05)"},
    ])
    tint = alt.Chart(quads).mark_rect().encode(
        x=alt.X("x:Q", scale=xscale, axis=alt.Axis(title="RS →")),
        x2="x2:Q",
        y=alt.Y("y:Q", scale=yscale, axis=alt.Axis(title="FLOW ↑")),
        y2="y2:Q",
        color=alt.Color("color:N", scale=None),
    )
    labels = pd.DataFrame([
        {"x": -xmax * 0.97, "y": ymax * 0.94, "t": "EARLY RECOVERY",
         "c": TOKENS["accent"], "a": "left"},
        {"x": xmax * 0.97, "y": ymax * 0.94, "t": "LEADERS", "c": TOKENS["buy"], "a": "right"},
        {"x": -xmax * 0.97, "y": -ymax * 0.94, "t": "AVOID",
         "c": TOKENS["text_faint"], "a": "left"},
        {"x": xmax * 0.97, "y": -ymax * 0.94, "t": "DISTRIBUTION WARN",
         "c": TOKENS["sell"], "a": "right"},
    ])
    corner = [
        alt.Chart(labels[labels["a"] == a]).mark_text(
            font=_MONO, fontSize=11, fontWeight=600, align=a
        ).encode(x=alt.X("x:Q", scale=xscale), y=alt.Y("y:Q", scale=yscale),
                 text="t:N", color=alt.Color("c:N", scale=None))
        for a in ("left", "right")
    ]
    axes = alt.Chart(pd.DataFrame([{"v": 0}]))
    zero_x = axes.mark_rule(color="rgba(255,255,255,0.14)").encode(
        x=alt.X("v:Q", scale=xscale))
    zero_y = axes.mark_rule(color="rgba(255,255,255,0.14)").encode(
        y=alt.Y("v:Q", scale=yscale))

    quad_color = alt.Color(
        "quadrant:N", legend=None,
        scale=alt.Scale(
            domain=["LEADERS", "EARLY_RECOVERY", "DISTRIBUTION_WARN", "AVOID"],
            range=[TOKENS["buy"], TOKENS["accent"], TOKENS["sell"], TOKENS["text_faint"]],
        ),
    )
    pos = dict(
        x=alt.X("x_relative_strength_pct:Q", scale=xscale),
        y=alt.Y("y_net_flow_bn:Q", scale=yscale),
    )
    bubbles = alt.Chart(df).mark_circle(opacity=0.18).encode(
        **pos, color=quad_color,
        size=alt.Size("radius_flow_bn:Q", legend=None, scale=alt.Scale(range=[700, 3600])),
        tooltip=[alt.Tooltip("sector:N"),
                 alt.Tooltip("x_relative_strength_pct:Q", title="RS %"),
                 alt.Tooltip("y_net_flow_bn:Q", title="net flow IDR bn")],
    )
    rings = alt.Chart(df).mark_point(filled=False, strokeWidth=1.5, opacity=0.9).encode(
        **pos, color=quad_color,
        size=alt.Size("radius_flow_bn:Q", legend=None, scale=alt.Scale(range=[700, 3600])),
    )
    text = alt.Chart(df).mark_text(font=_MONO, fontSize=10, fontWeight=700).encode(
        **pos, text="code:N", color=quad_color)

    return alt.layer(tint, zero_x, zero_y, *corner, bubbles, rings, text).properties(
        height=height)
