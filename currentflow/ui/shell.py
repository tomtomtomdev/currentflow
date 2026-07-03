"""Design-shell view helpers (design/SCREENS_terminal.md + design/screens/) — pure
string builders, no Streamlit imports.

The pixel targets in design/screens/ specify a shell Streamlit does not provide
natively: a 52px top status bar, badge pills on module headers, a right-hand ARMED
watchlist rail with five component spark-bars per card, an amber validation-state
bar, and a scrolling §15 disclaimer ticker. These helpers emit that shell as
self-contained HTML/CSS (design tokens inlined), to be injected via
`st.markdown(..., unsafe_allow_html=True)` — approximate fidelity within Streamlit,
no React/JS layer (2026-07-01 stack decision).

RULE B holds by construction: nothing here renders a composite score, probability,
rank number, or buy/sell verb. Watchlist cards carry a state WORD + component bars
(exact component values only in hover tooltips — raw observation, same surface as
`watchlist_view.spark_line`); the validation bar says "number withheld" until the
server-authoritative ledger says VALIDATED. Missing components render as an empty
slot with a "not available" tooltip — missing is never drawn as zero.
"""

from __future__ import annotations

from html import escape

# Design tokens — design/SCREENS_terminal.md § Design Tokens.
TOKENS = {
    "bg_app": "#070a10",
    "bg_rail": "#0a0e14",
    "bg_panel": "#0d121b",
    "border_panel": "rgba(255,255,255,0.06)",
    "border_bar": "rgba(255,255,255,0.07)",
    "text": "#e6edf3",
    "text_secondary": "#c2ccd8",
    "text_muted": "#8b98a9",
    "text_faint": "#5a6675",
    "buy": "#3fb950",
    "sell": "#f85149",
    "armed": "#d29922",
    "armed_text": "#e8c168",
    "accent": "#58c4dd",
    "divergence": "#bc8cff",
    "foreign": "#58a6ff",
}

# §15 disclaimers, cycled by the bottom ticker (LOCKED_SPEC.md §15).
DISCLAIMERS = (
    "Private personal-use analytics tool. Not a product, not a service, not for redistribution.",
    "Not investment advice. All outputs are observations for the operator's own decisions.",
    "Data consumed from the operator's own session; used at own risk; not republished.",
    "No live execution. Paper trading only. Paper results do not guarantee live performance.",
    "Credentials/OTP are entered locally, never persisted or logged; only session tokens are stored (§9.1).",
)

RULE_B_PILL = "RULE B · OBSERVATION ONLY — scores gated until paper-validated"

# Badge kinds → (border/base color, text color). design: green OBSERVATION,
# amber GATED, cyan DERIVED VIEW.
_BADGE = {
    "observation": (TOKENS["buy"], TOKENS["buy"]),
    "gated": (TOKENS["armed"], TOKENS["armed_text"]),
    "derived": (TOKENS["accent"], TOKENS["accent"]),
}

_MONO = "'Geist Mono', ui-monospace, 'SF Mono', Menlo, monospace"

# Spark-bar order + colors: FF blue when positive else faded red; others take the
# row-state color (amber armed / cyan watch) — design/SCREENS_terminal.md § Watchlist.
SPARK_ORDER = ("DIV", "BRK", "FF", "RVOL", "BLK")
_STATE_COLOR = {"ARMED": TOKENS["armed"], "WATCH": TOKENS["accent"]}
_STATE_DOT = {"ARMED": TOKENS["armed"], "WATCH": TOKENS["accent"]}


def shell_css() -> str:
    """One-shot CSS: hairline-border/layered-background depth model (no shadows),
    mono numerics, and the shell's keyframe animations."""
    return f"""<style>
.cf-topbar {{
  display:flex; align-items:center; gap:14px; height:52px; padding:0 14px;
  background:linear-gradient(180deg,#0d121b,#0a0e14);
  border:1px solid {TOKENS["border_bar"]}; border-radius:10px;
  font-size:11px; color:{TOKENS["text_muted"]}; overflow:hidden; white-space:nowrap;
}}
.cf-topbar .cf-logo {{
  width:26px; height:26px; border-radius:6px; flex:none;
  background:linear-gradient(135deg,#58c4dd,#3a8fb0); color:#04121a;
  font-weight:700; display:flex; align-items:center; justify-content:center;
}}
.cf-topbar .cf-word {{ color:{TOKENS["text"]}; font-weight:600; font-size:15px; }}
.cf-topbar .cf-sub {{ color:{TOKENS["text_faint"]}; font-size:10px; letter-spacing:0.14em; }}
.cf-topbar .cf-mono, .cf-mono {{ font-family:{_MONO}; }}
.cf-livedot {{
  display:inline-block; width:7px; height:7px; border-radius:50%;
  background:{TOKENS["buy"]}; animation:cf-livedot 1.8s ease-in-out infinite;
}}
.cf-ruleb {{
  border:1px solid rgba(210,153,34,0.32); background:rgba(210,153,34,0.08);
  color:{TOKENS["armed_text"]}; border-radius:6px; padding:3px 9px; font-size:10px;
}}
.cf-badge {{
  display:inline-block; border-radius:6px; padding:3px 10px; font-size:10.5px;
  font-weight:600; letter-spacing:0.04em; white-space:nowrap;
}}
.cf-modhead h2 {{ margin:0 0 2px; font-size:22px; font-weight:700; color:{TOKENS["text"]}; }}
.cf-modhead .cf-modsub {{ color:{TOKENS["text_muted"]}; font-size:12px; max-width:64ch; }}
.cf-card {{
  background:{TOKENS["bg_panel"]}; border:1px solid {TOKENS["border_panel"]};
  border-radius:9px; padding:11px; margin-bottom:9px;
}}
.cf-card .cf-tick {{ font-family:{_MONO}; font-weight:600; font-size:13px; color:{TOKENS["text"]}; }}
.cf-card .cf-track {{
  font-size:9px; color:{TOKENS["text_muted"]}; border:1px solid {TOKENS["border_panel"]};
  border-radius:4px; padding:0 4px; margin-left:6px;
}}
.cf-card .cf-state {{ float:right; font-family:{_MONO}; font-size:10.5px; font-weight:600; }}
.cf-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px; }}
.cf-dot.armed {{ animation:cf-armedpulse 1.8s ease-in-out infinite; }}
.cf-sparks {{ display:flex; gap:3px; height:26px; align-items:flex-end; margin-top:9px; }}
.cf-sparks span {{ flex:1; border-radius:2px 2px 0 0; min-height:2px; }}
.cf-sparklabels {{ display:flex; gap:3px; margin-top:3px; }}
.cf-sparklabels span {{
  flex:1; text-align:center; font-size:8px; letter-spacing:0.06em;
  color:{TOKENS["text_faint"]}; font-family:{_MONO};
}}
.cf-railhead {{ font-size:11px; font-weight:600; letter-spacing:0.08em; color:{TOKENS["text"]}; }}
.cf-railnote {{ font-size:10px; color:{TOKENS["text_muted"]}; margin:4px 0 10px; }}
.cf-valbar {{
  border:1px solid rgba(210,153,34,0.32); background:rgba(210,153,34,0.06);
  border-radius:9px; padding:11px 14px; margin-bottom:12px;
}}
.cf-valbar.validated {{ border-color:rgba(63,185,80,0.4); background:rgba(63,185,80,0.06); }}
.cf-valbar .cf-vallabel {{
  font-size:10px; letter-spacing:0.08em; color:{TOKENS["text_faint"]}; font-family:{_MONO};
}}
.cf-valbar .cf-valstate {{ font-family:{_MONO}; font-size:11.5px; }}
.cf-valtrack {{ height:5px; border-radius:3px; background:rgba(255,255,255,0.07); margin-top:8px; }}
.cf-valtrack span {{ display:block; height:5px; border-radius:3px; }}
.cf-ticker {{
  display:flex; align-items:center; gap:12px; height:26px; margin-top:6px;
  background:{TOKENS["bg_rail"]}; border-top:1px solid {TOKENS["border_bar"]};
  font-size:9.5px; overflow:hidden; white-space:nowrap;
}}
.cf-ticker .cf-chip {{ color:{TOKENS["text_faint"]}; font-family:{_MONO}; flex:none; padding-left:10px; }}
.cf-ticker .cf-scroll {{ display:inline-block; padding-left:100%; animation:cf-tickscroll 42s linear infinite; color:{TOKENS["text_muted"]}; }}
@keyframes cf-livedot {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.25; }} }}
@keyframes cf-armedpulse {{
  0%,100% {{ box-shadow:0 0 0 0 rgba(210,153,34,0.5); }}
  50% {{ box-shadow:0 0 0 5px rgba(210,153,34,0); }}
}}
@keyframes cf-tickscroll {{ 0% {{ transform:translateX(0); }} 100% {{ transform:translateX(-100%); }} }}
</style>"""


def top_bar_html(
    *,
    as_of: str | None,
    track: str = "B",
    operator: str | None = None,
    published: str = "Broker summary published · T+1",
) -> str:
    """The 52px top status bar: logo/wordmark, live publish note, as-of stamp,
    RULE-B pill, track chip, masked operator. No IHSG quote — the feed is not
    ingested, and a missing datum is shown as absent, never faked."""
    who = escape(operator) if operator else "operator"
    stamp = escape(as_of) if as_of else "—"
    return (
        '<div class="cf-topbar">'
        '<div class="cf-logo">V</div>'
        '<div><div class="cf-word">VECTOR·LAB</div>'
        '<div class="cf-sub">IDX SMART-MONEY FLOW TERMINAL</div></div>'
        f'<div><span class="cf-livedot"></span>&nbsp; {escape(published)}</div>'
        f'<div>as-of <span class="cf-mono">{stamp}</span> WIB</div>'
        f'<div style="flex:1"></div><div class="cf-ruleb">{RULE_B_PILL}</div>'
        f'<div>Track <span class="cf-mono">{escape(track)}</span></div>'
        f'<div><span class="cf-livedot"></span>&nbsp; <span class="cf-mono">{who}</span></div>'
        "</div>"
    )


def badge_html(kind: str, text: str) -> str:
    """A module-header status pill. `kind`: observation | gated | derived."""
    base, color = _BADGE[kind]
    return (
        f'<span class="cf-badge" style="border:1px solid {base}55; '
        f'background:{base}14; color:{color}">● {escape(text)}</span>'
    )


def module_header_html(title: str, subtitle: str, kind: str, badge: str) -> str:
    """Module header ribbon: title + framing subtitle + status badge."""
    return (
        '<div class="cf-modhead">'
        f"<h2>{escape(title)}</h2>"
        f'<div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap">'
        f'<span class="cf-modsub">{escape(subtitle)}</span>{badge_html(kind, badge)}'
        "</div></div>"
    )


def _spark_bar(label: str, value: int | None, state: str) -> str:
    """One spark-bar slot. Height = component strength %; a missing component is an
    empty slot (hairline only) with a 'not available' tooltip — never a zero bar."""
    if value is None:
        return (
            f'<span title="{label}: not available (missing ≠ zero)" '
            'style="height:2px; background:rgba(255,255,255,0.08)"></span>'
        )
    if label == "FF":
        color = TOKENS["foreign"] if value >= 50 else f"{TOKENS['sell']}88"
    else:
        color = _STATE_COLOR.get(state, TOKENS["text_faint"])
    h = max(2, round(26 * min(value, 100) / 100))
    return f'<span title="{label} {value}" style="height:{h}px; background:{color}"></span>'


def watchlist_card_html(row: dict) -> str:
    """One ARMED-watchlist card from `watchlist_view.rows()`: status dot, ticker,
    track chip, state WORD, five component spark-bars. No composite number, no rank,
    no verb (RULE B) — exact component values live only in the bar tooltips."""
    state = row["state"]
    dot = _STATE_DOT.get(state, TOKENS["text_faint"])
    pulse = " armed" if state == "ARMED" else ""
    comps = row["components"]
    bars = "".join(_spark_bar(k, comps.get(k), state) for k in SPARK_ORDER)
    labels = "".join(f"<span>{k}</span>" for k in SPARK_ORDER)
    return (
        '<div class="cf-card">'
        f'<span class="cf-dot{pulse}" style="background:{dot}"></span>'
        f'<span class="cf-tick">{escape(row["symbol"])}</span>'
        f'<span class="cf-track">{escape(row["track"])}</span>'
        f'<span class="cf-state" style="color:{_STATE_COLOR.get(state, TOKENS["text_faint"])}">{escape(state)}</span>'
        f'<div class="cf-sparks">{bars}</div>'
        f'<div class="cf-sparklabels">{labels}</div>'
        "</div>"
    )


def watchlist_rail_html(data: dict) -> str:
    """The full right rail: header, framing, cards, and the never-silent cap note."""
    body = "".join(watchlist_card_html(r) for r in data["rows"]) or (
        '<div class="cf-railnote">— nothing ARMED or watching today</div>'
    )
    dropped = (
        f'<div class="cf-railnote">…and {data["dropped"]} more '
        f'(top {len(data["rows"])} shown of {data["total"]})</div>'
        if data["dropped"]
        else ""
    )
    return (
        '<div class="cf-railhead">ARMED WATCHLIST</div>'
        f'<div class="cf-railnote">{escape(data["framing"].capitalize())}. '
        "Internal ARMED state; score withheld (RULE B).</div>"
        f"{body}{dropped}"
        '<div class="cf-railnote">Components are raw observation — no probability '
        "or verb until validated.</div>"
    )


def validation_bar_html(months_accrued: float, required_months: int, validated: bool) -> str:
    """The SMS/Rank per-module validation-state bar. Amber + 'number withheld' until
    the server-authoritative ledger resolves VALIDATED; green 'CLAIM ENABLED' after."""
    frac = min(months_accrued / required_months, 1.0) if required_months else 0.0
    if validated:
        cls, color = " validated", TOKENS["buy"]
        state = f"{months_accrued:.1f} / {required_months} — CLAIM ENABLED"
    else:
        cls, color = "", TOKENS["armed"]
        state = f"{months_accrued:.1f} / {required_months} months forward-paper — number withheld"
    return (
        f'<div class="cf-valbar{cls}">'
        f'<span class="cf-vallabel">PER-MODULE VALIDATION STATE · PAPER_VALIDATION_MONTHS = {required_months}</span>'
        f'<span class="cf-valstate" style="float:right; color:{color}">{escape(state)}</span>'
        f'<div class="cf-valtrack"><span style="width:{frac * 100:.0f}%; background:{color}"></span></div>'
        "</div>"
    )


def ticker_html(disclaimers: tuple[str, ...] = DISCLAIMERS) -> str:
    """Bottom status bar: LOCAL·SINGLE-USER·PAPER chip + scrolling §15 disclaimers."""
    line = "   ·   ".join(escape(d) for d in disclaimers)
    return (
        '<div class="cf-ticker">'
        '<span class="cf-chip">LOCAL · SINGLE-USER · PAPER</span>'
        f'<span style="overflow:hidden; flex:1"><span class="cf-scroll">{line}</span></span>'
        "</div>"
    )
