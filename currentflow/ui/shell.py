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
    "smart": "#e3b341",
}

# Quadrant → state color (design: Sector Rotation Map + sector cards).
QUADRANT_COLORS = {
    "LEADERS": TOKENS["buy"],
    "EARLY_RECOVERY": TOKENS["accent"],
    "DISTRIBUTION_WARN": TOKENS["sell"],
    "AVOID": TOKENS["text_faint"],
}
QUADRANT_LABELS = {
    "LEADERS": "LEADERS",
    "EARLY_RECOVERY": "EARLY RECOVERY",
    "DISTRIBUTION_WARN": "DISTRIBUTION WARN",
    "AVOID": "AVOID",
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

# Broker-DNA chip palette + labels — design/SCREENS_terminal.md § Design Tokens.
DNA_COLORS = {
    "FOREIGN_INST": TOKENS["foreign"],
    "LOCAL_INST": "#a371f7",
    "SMART_MONEY": "#e3b341",
    "RETAIL": "#6e7681",
    "PROP": "#56d4bd",
    "UNKNOWN": TOKENS["text_faint"],
}
DNA_LABELS = {
    "FOREIGN_INST": "Foreign Inst",
    "LOCAL_INST": "Local Inst",
    "SMART_MONEY": "Smart Money",
    "RETAIL": "Retail",
    "PROP": "Prop",
    "UNKNOWN": "Unknown",
}

_MONO = "'Geist Mono', ui-monospace, 'SF Mono', Menlo, monospace"

# Spark-bar order + colors: FF blue when positive else faded red; others take the
# row-state color (amber armed / cyan watch) — design/SCREENS_terminal.md § Watchlist.
SPARK_ORDER = ("DIV", "BRK", "FF", "RVOL", "BLK")
_STATE_COLOR = {"ARMED": TOKENS["armed"], "WATCH": TOKENS["accent"]}
_STATE_DOT = {"ARMED": TOKENS["armed"], "WATCH": TOKENS["accent"]}


def shell_css() -> str:
    """One-shot CSS: hairline-border/layered-background depth model (no shadows),
    mono numerics, the shell's keyframe animations, and the Streamlit-chrome
    overrides that pull the host app toward the design shell (hide the Streamlit
    header/toolbar, kill the default page gutters, restyle the sidebar module
    radio into the design's nav-rail items)."""
    return f"""<style>
/* --- Streamlit chrome → design shell -------------------------------------
   Keep the header element in the DOM. When the sidebar is collapsed Streamlit
   renders the ">>" expand control INSIDE the header toolbar, so the old blanket
   `display:none` on the header left no way to reopen the nav rail. Instead
   neutralize the header visually, hide only the chrome noise (menu, deploy,
   status, decoration), and surface the expand button as a styled floating
   control that's always reachable. */
#MainMenu, footer,
div[data-testid="stMainMenu"], div[data-testid="stDecoration"],
div[data-testid="stStatusWidget"], div[data-testid="stToolbarActions"],
div[data-testid="stAppDeployButton"],
div[data-testid="stHeaderActionElements"] {{ display:none !important; }}
header[data-testid="stHeader"] {{
  background:transparent !important; height:0 !important; min-height:0 !important;
  box-shadow:none !important;
}}
div[data-testid="stToolbar"] {{ background:transparent !important; right:auto; }}
button[data-testid="stExpandSidebarButton"] {{
  display:inline-flex !important; position:fixed !important; top:9px; left:9px;
  z-index:1000; width:30px; height:30px; align-items:center; justify-content:center;
  background:{TOKENS["bg_panel"]} !important; border:1px solid {TOKENS["border_panel"]} !important;
  border-radius:8px; color:{TOKENS["text"]} !important;
}}
button[data-testid="stExpandSidebarButton"]:hover {{
  border-color:rgba(88,196,221,0.40) !important; background:rgba(88,196,221,0.08) !important;
}}
.stApp {{ background:{TOKENS["bg_app"]}; }}
.stMainBlockContainer, .block-container {{
  padding:14px 18px 8px !important; max-width:100% !important;
}}
/* Nav rail — design width 82px (SCREENS_terminal §shell); pin it so Streamlit's
   ~244px default collapses to the design's icon-over-label rail. */
section[data-testid="stSidebar"] {{
  background:{TOKENS["bg_rail"]}; border-right:1px solid {TOKENS["border_panel"]};
  width:96px !important; min-width:96px !important; max-width:96px !important;
}}
section[data-testid="stSidebar"] > div {{ width:96px !important; min-width:96px !important; }}
section[data-testid="stSidebar"] div[data-testid="stSidebarUserContent"] {{ padding:8px 6px; }}
/* nav-rail items stack vertically — icon glyph over title, centered (design:
   the leftmost rail is icon-on-top-of-label, not a horizontal row). st.radio's
   `captions=` renders the title directly beneath the icon label; the flex-column
   label just centers the two and hides the radio circle. */
section[data-testid="stSidebar"] div[data-testid="stRadio"] label {{
  display:flex; flex-direction:column; align-items:center; text-align:center;
  gap:1px; border-radius:9px; padding:9px 4px; line-height:1.2;
  margin:2px 0; border:1px solid transparent; color:{TOKENS["text_muted"]};
  width:100%; cursor:pointer;
}}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label div[data-testid="stMarkdownContainer"] p {{
  margin:0; font-size:17px; line-height:1.4;  /* the icon label */
}}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label div[data-testid="stCaptionContainer"] {{
  text-align:center;
}}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label div[data-testid="stCaptionContainer"] p {{
  margin:0; font-size:10px; letter-spacing:0.01em; line-height:1.2;  /* the title */
}}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label:hover {{
  color:{TOKENS["text"]};
}}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label:hover div[data-testid="stCaptionContainer"] p {{
  color:{TOKENS["text_secondary"]};
}}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label:has(input:checked) {{
  background:rgba(88,196,221,0.10); border-color:rgba(88,196,221,0.25);
  color:{TOKENS["text"]};
}}
section[data-testid="stSidebar"] div[data-testid="stRadio"] label[data-baseweb="radio"] > div:first-child {{
  display:none;  /* the radio circle — the design nav has none */
}}
/* --- operator head + sign-out (top of the ARMED watchlist rail) ------------ */
.cf-ophead {{
  display:flex; align-items:center; gap:7px; flex-wrap:wrap;
  padding:2px 0 8px; font-size:11px; color:{TOKENS["text_secondary"]};
}}
.cf-ophead .cf-opname {{ color:{TOKENS["text"]}; font-weight:600; }}
.cf-ophead .cf-optoken {{ color:{TOKENS["text_muted"]}; font-size:10.5px; }}
.cf-ophead .cf-opsrc {{
  margin-left:auto; font-size:9px; letter-spacing:0.06em; color:{TOKENS["text_faint"]};
  border:1px solid {TOKENS["border_panel"]}; border-radius:4px; padding:1px 5px;
}}
div[class*="st-key-cfsignout"] div[data-testid="stButton"] {{ margin:-4px 0 12px; }}
div[class*="st-key-cfsignout"] button {{
  width:100%; background:transparent; border:1px solid {TOKENS["border_panel"]};
  border-radius:8px; color:{TOKENS["text_muted"]}; font-size:11px; padding:4px 0;
}}
div[class*="st-key-cfsignout"] button:hover {{
  border-color:rgba(248,81,73,0.40); color:{TOKENS["sell"]};
  background:rgba(248,81,73,0.06);
}}
.cf-scrollbody {{ max-height:520px; overflow-y:auto; }}
/* --- clickable watchlist cards (design: rail card IS the symbol selector) --
   Each card is a keyed st.container holding the card HTML plus an invisible
   st.button stretched over it (label hidden) — full-card click, no JS layer.
   Streamlit 1.58 sizes a button's element container to its content (explicit
   width beats `inset:0`) and, with `help=`, nests the button two auto-height
   wrappers deep — so every layer of the chain is pinned absolute/full-size,
   with !important against the inline/emotion sizing. Streamlit also puts
   margin-bottom:-16px on stMarkdownContainer, which shrinks the keyed wrap
   below the card's height — zeroed here so the overlay covers the whole card. */
div[class*="st-key-cfwatch-"] {{ position:relative; margin-bottom:-6px; }}
div[class*="st-key-cfwatch-"] .cf-card {{ margin-bottom:0; }}
div[class*="st-key-cfwatch-"] div[data-testid="stMarkdownContainer"] {{
  margin:0 !important;
}}
div[class*="st-key-cfsel-"] {{
  position:absolute; inset:0; z-index:2;
  width:100% !important; height:100% !important;
}}
div[class*="st-key-cfsel-"] div[data-testid="stButton"],
div[class*="st-key-cfsel-"] div[data-testid="stButton"] > div {{
  position:absolute; inset:0; width:100% !important; height:100% !important;
  min-height:0; margin:0; padding:0;
}}
div[class*="st-key-cfsel-"] button {{
  position:absolute; inset:0; width:100% !important; height:100% !important;
  min-height:0; padding:0; background:transparent;
  border:1px solid transparent; border-radius:9px; color:transparent;
}}
div[class*="st-key-cfsel-"] button:hover,
div[class*="st-key-cfsel-"] button:focus-visible {{
  border-color:rgba(88,196,221,0.40); background:rgba(88,196,221,0.04);
  color:transparent;
}}
div[class*="st-key-cfsel-"] button:active {{ color:transparent; }}
div[class*="st-key-cfsel-"] button p {{ display:none; }}
/* --- keyed panel containers (native charts inside design panels) ---------- */
div[class*="st-key-cfpanel"] {{
  background:{TOKENS["bg_panel"]}; border:1px solid {TOKENS["border_panel"]};
  border-radius:10px; padding:14px 14px 8px; margin-bottom:12px;
}}
div[class*="st-key-cfpanel"] div[data-testid="stVerticalBlock"] {{ gap:0.35rem; }}
div[class*="st-key-cfpanel"] canvas, div[class*="st-key-cfpanel"] svg {{ border-radius:6px; }}
/* --- kv stat rows / callouts / split bar ---------------------------------- */
.cf-kvrow {{
  display:flex; justify-content:space-between; align-items:baseline; gap:12px;
  padding:5px 0; font-size:11px; color:{TOKENS["text_secondary"]};
  border-bottom:1px solid rgba(255,255,255,0.035);
}}
.cf-kvrow:last-child {{ border-bottom:none; }}
.cf-kvrow .cf-kvval {{ font-family:{_MONO}; font-size:11.5px; color:{TOKENS["text"]}; white-space:nowrap; }}
.cf-callout {{
  display:flex; gap:10px; align-items:baseline; background:{TOKENS["bg_panel"]};
  border:1px solid {TOKENS["border_panel"]}; border-radius:10px;
  padding:11px 14px; margin-bottom:12px; font-size:12px; color:{TOKENS["text_secondary"]};
}}
.cf-callout .cf-calldot {{ flex:none; width:7px; height:7px; border-radius:50%; align-self:center; }}
.cf-callout .cf-calllabel {{
  font-family:{_MONO}; font-size:9.5px; letter-spacing:0.08em; color:{TOKENS["text_faint"]};
  display:block; margin-bottom:2px;
}}
.cf-splitbar {{ display:flex; height:18px; border-radius:5px; overflow:hidden; margin-top:6px; }}
.cf-splitbar span {{
  display:flex; align-items:center; font-family:{_MONO}; font-size:9.5px;
  color:#04121a; padding:0 8px; white-space:nowrap;
}}
/* --- replay WYCKOFF PHASE box ---------------------------------------------- */
.cf-phasebox {{ border-radius:7px; padding:9px 11px; margin-top:12px; }}
.cf-phasebox .cf-phaselabel {{
  font-family:{_MONO}; font-size:9.5px; letter-spacing:0.05em; color:{TOKENS["text_muted"]};
}}
.cf-phasebox .cf-phasetitle {{ font-size:15px; font-weight:600; margin-top:2px; }}
.cf-phasebox .cf-phasenote {{ font-size:10.5px; color:{TOKENS["text_muted"]}; margin-top:3px; line-height:1.45; }}
/* --- replay transport bar: circular accent play button + scrubber ---------- */
div[class*="st-key-cfreplayplay"] div[data-testid="stButton"] {{ display:flex; }}
div[class*="st-key-cfreplayplay"] button {{
  width:38px; height:38px; min-height:38px; padding:0; border-radius:50%;
  background:{TOKENS["accent"]}; border:none; color:#04121a;
  display:flex; align-items:center; justify-content:center;
}}
div[class*="st-key-cfreplayplay"] button:hover {{ background:#6fd0e6; color:#04121a; }}
div[class*="st-key-cfreplayplay"] button:active {{ background:{TOKENS["accent"]}; }}
div[class*="st-key-cfreplayplay"] button p {{
  font-family:{_MONO}; font-size:14px; font-weight:600; color:#04121a;
}}
.cf-replayscale {{
  display:flex; justify-content:space-between; font-family:{_MONO}; font-size:9.5px;
  color:{TOKENS["text_faint"]}; margin-top:2px;
}}
.cf-replayscale .cf-mid {{ color:{TOKENS["text_muted"]}; }}
/* --- heatmap tile grid ----------------------------------------------------- */
.cf-heatrow {{ display:grid; grid-template-columns:110px repeat(6, 1fr); gap:6px; margin-bottom:6px; }}
.cf-heatrow .cf-heatsector {{
  font-size:11px; color:{TOKENS["text_secondary"]}; align-self:center; padding-right:6px;
}}
.cf-tile {{
  border-radius:7px; padding:9px 6px; text-align:center; position:relative;
  border:1px solid rgba(255,255,255,0.05);
}}
.cf-tile .cf-tilesym {{ font-family:{_MONO}; font-size:11px; font-weight:700; color:{TOKENS["text"]}; }}
.cf-tile .cf-tileval {{ font-family:{_MONO}; font-size:9.5px; color:{TOKENS["text_secondary"]}; margin-top:2px; }}
.cf-tile.cf-div {{ outline:1.5px solid {TOKENS["divergence"]}99; }}
.cf-tile.cf-div::after {{
  content:"◆"; position:absolute; top:2px; right:5px; font-size:7px; color:{TOKENS["divergence"]};
}}
.cf-legend {{ display:flex; gap:14px; align-items:center; font-size:10px; color:{TOKENS["text_muted"]}; margin-bottom:10px; }}
.cf-legend .cf-swatch {{ display:inline-block; width:18px; height:9px; border-radius:2px; margin-right:5px; vertical-align:-1px; }}
/* --- sector cards / stat cards --------------------------------------------- */
.cf-seccard {{
  background:{TOKENS["bg_panel"]}; border:1px solid {TOKENS["border_panel"]};
  border-radius:9px; padding:11px 13px; margin-bottom:9px;
}}
.cf-seccard .cf-secname {{ font-size:12.5px; font-weight:600; color:{TOKENS["text"]}; }}
.cf-seccard .cf-secnote {{ font-size:10.5px; color:{TOKENS["text_muted"]}; margin:3px 0 6px; }}
.cf-seccard .cf-secstats {{ font-family:{_MONO}; font-size:10px; color:{TOKENS["text_muted"]}; }}
.cf-statcards {{ display:flex; gap:10px; margin-bottom:12px; }}
.cf-statcards .cf-statcard {{
  flex:1; background:{TOKENS["bg_panel"]}; border:1px solid {TOKENS["border_panel"]};
  border-radius:10px; padding:12px 14px;
}}
.cf-statcards .cf-cardlabel {{ font-size:9.5px; color:{TOKENS["text_faint"]}; font-family:{_MONO}; }}
.cf-statcards .cf-bigstat {{ margin-top:4px; }}
/* --- login hero + floating card -------------------------------------------- */
.cf-hero {{ max-width:56ch; padding:9vh 0 0 2vw; }}
.cf-hero .cf-herolabel {{
  font-family:{_MONO}; font-size:10px; letter-spacing:0.22em; color:{TOKENS["accent"]};
  margin-bottom:14px;
}}
.cf-hero h1 {{ font-size:40px; line-height:1.15; font-weight:700; color:{TOKENS["text"]}; margin:0 0 16px; }}
.cf-hero .cf-herosub {{ font-size:13px; color:{TOKENS["text_muted"]}; line-height:1.6; margin-bottom:22px; }}
.cf-checkrow {{ display:flex; gap:11px; margin-bottom:13px; font-size:12px; color:{TOKENS["text_muted"]}; line-height:1.5; }}
.cf-checkrow .cf-checkbox {{
  flex:none; width:17px; height:17px; border-radius:4px; margin-top:1px;
  border:1px solid rgba(63,185,80,0.4); background:rgba(63,185,80,0.10);
  color:{TOKENS["buy"]}; font-size:10px; display:flex; align-items:center; justify-content:center;
}}
.cf-checkrow b {{ color:{TOKENS["text_secondary"]}; }}
div[class*="st-key-cflogincard"] {{
  background:{TOKENS["bg_panel"]}; border:1px solid rgba(255,255,255,0.09);
  border-radius:13px; padding:20px 22px; margin-top:9vh;
  box-shadow:0 18px 48px rgba(0,0,0,0.5);
}}
div[class*="st-key-cflogincard"] div[data-testid="stForm"] {{ border:none; padding:0; }}
/* --- shell fragments ------------------------------------------------------ */
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
/* --- module panels (design: bg #0d121b, hairline border, radius 10) ------ */
.cf-panel {{
  background:{TOKENS["bg_panel"]}; border:1px solid {TOKENS["border_panel"]};
  border-radius:10px; padding:14px; margin-bottom:12px;
}}
.cf-panelhead {{
  font-size:11px; font-weight:600; letter-spacing:0.06em;
  color:{TOKENS["text"]}; margin-bottom:10px;
}}
.cf-panelhead small {{ color:{TOKENS["text_faint"]}; font-weight:400; letter-spacing:0; }}
.cf-stockhead {{ display:flex; align-items:center; gap:10px; margin:8px 0 12px; flex-wrap:wrap; }}
.cf-stockhead .cf-sym {{ font-size:26px; font-weight:700; color:{TOKENS["text"]}; line-height:1; }}
.cf-chip {{
  display:inline-block; border-radius:5px; padding:2px 7px; font-size:9.5px;
  font-weight:600; letter-spacing:0.05em; white-space:nowrap;
}}
.cf-stockhead .cf-price {{ font-family:{_MONO}; font-size:20px; color:{TOKENS["text"]}; }}
.cf-stockhead .cf-adv {{ font-size:10px; color:{TOKENS["text_muted"]}; text-align:right; }}
.cf-table {{ width:100%; border-collapse:collapse; font-size:11.5px; }}
.cf-table td, .cf-table th {{
  padding:7px 10px; border-bottom:1px solid rgba(255,255,255,0.04);
  text-align:left; vertical-align:middle;
}}
.cf-table th {{
  font-size:9px; font-weight:600; letter-spacing:0.08em;
  color:{TOKENS["text_faint"]}; font-family:{_MONO};
}}
.cf-table tr:last-child td {{ border-bottom:none; }}
.cf-table .cf-rank {{ color:{TOKENS["text_faint"]}; font-family:{_MONO}; font-size:10px; }}
.cf-table .cf-code {{ font-family:{_MONO}; font-weight:600; color:{TOKENS["text"]}; }}
.cf-table .cf-num {{ font-family:{_MONO}; text-align:right; }}
.cf-netbar {{ height:3px; border-radius:2px; margin-top:3px; margin-left:auto; }}
.cf-dots {{ font-size:9px; letter-spacing:2px; white-space:nowrap; }}
.cf-bigstat {{ font-family:{_MONO}; font-size:24px; font-weight:600; line-height:1.1; }}
.cf-statlabel {{ font-size:10px; color:{TOKENS["text_muted"]}; }}
.cf-bartrack {{ height:5px; border-radius:3px; background:rgba(255,255,255,0.07); margin-top:8px; }}
.cf-bartrack span {{ display:block; height:5px; border-radius:3px; }}
.cf-vetorow {{
  display:flex; align-items:center; gap:9px; padding:6px 0; font-size:11px;
  color:{TOKENS["text_secondary"]}; border-bottom:1px solid rgba(255,255,255,0.035);
}}
.cf-vetorow:last-child {{ border-bottom:none; }}
.cf-vetorow .cf-vmark {{ font-family:{_MONO}; font-size:11px; flex:none; }}
.cf-vetorow .cf-vval {{
  margin-left:auto; font-family:{_MONO}; font-size:10px; color:{TOKENS["text_muted"]};
  text-align:right; max-width:46%;
}}
.cf-matrix {{ width:100%; border-collapse:separate; border-spacing:4px 4px; font-size:11px; }}
.cf-matrix th {{
  font-family:{_MONO}; font-size:10px; font-weight:600; color:{TOKENS["text_muted"]};
  padding:2px 6px; text-align:center;
}}
.cf-matrix th:first-child {{ text-align:left; }}
.cf-matrix .cf-cell {{
  font-family:{_MONO}; text-align:center; padding:7px 6px; border-radius:6px;
  border:1px solid rgba(255,255,255,0.05); color:{TOKENS["text"]};
}}
.cf-matrix .cf-empty {{ border:1px dashed rgba(255,255,255,0.05); color:{TOKENS["text_faint"]}; }}
@keyframes cf-livedot {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.25; }} }}
@keyframes cf-armedpulse {{
  0%,100% {{ box-shadow:0 0 0 0 rgba(210,153,34,0.5); }}
  50% {{ box-shadow:0 0 0 5px rgba(210,153,34,0); }}
}}
@keyframes cf-tickscroll {{ 0% {{ transform:translateX(0); }} 100% {{ transform:translateX(-100%); }} }}
</style>"""


def ihsg_html(value: float | None, change_pct: float | None) -> str:
    """The top-bar IHSG (Jakarta Composite) quote: label + mono index level + signed
    %-change (green/red). The level is display-only chrome — signals never benchmark
    to IHSG (§8, LQ45/sector only). Not ingested → the slot renders an em-dash;
    a missing datum is shown as absent, never faked (§10)."""
    if value is None:
        return (
            f'IHSG <span class="cf-mono" style="color:{TOKENS["text_faint"]}">—</span>'
        )
    chg = ""
    if change_pct is not None:
        color = TOKENS["buy"] if change_pct >= 0 else TOKENS["sell"]
        chg = f' <span class="cf-mono" style="color:{color}">{change_pct:+.2f}%</span>'
    return (
        f'IHSG <span class="cf-mono" style="color:{TOKENS["text"]}">{value:,.1f}</span>{chg}'
    )


def top_bar_html(
    *,
    as_of: str | None,
    track: str = "B",
    ihsg: float | None = None,
    ihsg_change_pct: float | None = None,
    published: str = "Broker summary published · T+1",
) -> str:
    """The 52px top status bar: logo/wordmark, live publish note, as-of stamp,
    RULE-B pill, IHSG quote, track chip. The masked operator + sign-out live at the
    top of the ARMED watchlist rail (`operator_head_html`), not here."""
    stamp = escape(as_of) if as_of else "—"
    return (
        '<div class="cf-topbar">'
        '<div class="cf-logo">V</div>'
        '<div><div class="cf-word">VECTOR·LAB</div>'
        '<div class="cf-sub">IDX SMART-MONEY FLOW TERMINAL</div></div>'
        f'<div><span class="cf-livedot"></span>&nbsp; {escape(published)}</div>'
        f'<div>as-of <span class="cf-mono">{stamp}</span> WIB</div>'
        f'<div style="flex:1"></div><div class="cf-ruleb">{RULE_B_PILL}</div>'
        f'<div>{ihsg_html(ihsg, ihsg_change_pct)}</div>'
        f'<div>Track <span class="cf-mono">{escape(track)}</span></div>'
        "</div>"
    )


def operator_head_html(who: str, preview: str, source: str) -> str:
    """Masked session identity at the top of the ARMED watchlist rail: live dot +
    operator name + masked token preview + capture source. Confirms which session
    is live without leaking it; the sign-out control renders beneath it in the app."""
    return (
        '<div class="cf-ophead">'
        '<span class="cf-livedot"></span>'
        f'<span class="cf-opname">{escape(who)}</span>'
        f'<span class="cf-mono cf-optoken">{escape(preview)}</span>'
        f'<span class="cf-opsrc">{escape(source)}</span>'
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


def dna_chip_html(dna: str) -> str:
    """A broker-DNA chip (Foreign Inst / Local Inst / Smart Money / Retail / Prop)."""
    color = DNA_COLORS.get(dna, TOKENS["text_faint"])
    label = DNA_LABELS.get(dna, dna)
    return (
        f'<span class="cf-chip" style="border:1px solid {color}44; '
        f'background:{color}14; color:{color}">{escape(label)}</span>'
    )


def stock_header_html(
    *,
    symbol: str,
    track: str,
    sector: str | None = None,
    price: float | None = None,
    change_pct: float | None = None,
    adv_bn: float | None = None,
) -> str:
    """The design's stock-header row: ticker (26px), track chip, sector chip,
    price (mono 20px) + signed % change, 20-day ADV. Missing data renders as
    absent — never faked (§10)."""
    chips = (
        f'<span class="cf-chip" style="border:1px solid {TOKENS["accent"]}44; '
        f'background:{TOKENS["accent"]}14; color:{TOKENS["accent"]}">'
        f"TRACK {escape(track)}</span>"
    )
    if sector and sector != "UNKNOWN":
        chips += (
            f'<span class="cf-chip" style="border:1px solid rgba(255,255,255,0.10); '
            f'color:{TOKENS["text_muted"]}">{escape(sector)}</span>'
        )
    right = ""
    if price is not None:
        chg = ""
        if change_pct is not None:
            color = TOKENS["buy"] if change_pct >= 0 else TOKENS["sell"]
            chg = (
                f' <span class="cf-mono" style="font-size:12px; color:{color}">'
                f"{change_pct:+.2f}%</span>"
            )
        right = f'<span class="cf-price">{price:,.0f}</span>{chg}'
    adv = (
        f'<div class="cf-adv">20d ADV<br><span class="cf-mono">IDR {adv_bn:,.0f} bn</span></div>'
        if adv_bn is not None
        else ""
    )
    return (
        '<div class="cf-stockhead">'
        f'<span class="cf-sym">{escape(symbol)}</span>{chips}'
        f'<span style="flex:1"></span>{right}{adv}'
        "</div>"
    )


def panel_html(
    title: str, body: str, *, note: str | None = None, right: str | None = None
) -> str:
    """A design module panel: 11px 600 header (muted trailing note, optional
    right-aligned tag HTML) + body HTML."""
    head_note = f" <small>· {escape(note)}</small>" if note else ""
    tag = f'<span style="float:right">{right}</span>' if right else ""
    return (
        '<div class="cf-panel">'
        f'<div class="cf-panelhead">{escape(title)}{head_note}{tag}</div>{body}</div>'
    )


def broker_table_html(rows: list[dict]) -> str:
    """The Broker Net Flow table (design module 1): rank · broker code + DNA chip ·
    signed NET in IDR bn (green/red, proportional under-bar) · 7-dot persistence
    strip. Buy/sell/VWAP detail rides the row tooltip — raw observation only."""
    max_abs = max((abs(r["net_idr_bn"]) for r in rows), default=0.0)
    body = []
    for r in rows:
        net = r["net_idr_bn"]
        color = TOKENS["buy"] if net >= 0 else TOKENS["sell"]
        width = 0 if not max_abs else max(4, round(abs(net) / max_abs * 100))
        filled = r["persist"].count("●")
        dots = (
            f'<span style="color:{color}">{"●" * filled}</span>'
            f'<span style="color:rgba(255,255,255,0.12)">{"○" * (len(r["persist"]) - filled)}</span>'
        )
        vwap = f" · accum VWAP {r['accum_vwap']:,.0f}" if r.get("accum_vwap") else ""
        tip = (
            f"gross {r['buy_idr_bn']:+,.2f} / {r['sell_idr_bn']:+,.2f} IDR bn{vwap}"
        )
        body.append(
            f'<tr title="{escape(tip)}">'
            f'<td class="cf-rank">{r["#"]}</td>'
            f'<td><span class="cf-code">{escape(r["broker"])}</span> '
            f"{dna_chip_html(r['dna'])}</td>"
            f'<td class="cf-num" style="color:{color}">{net:+,.2f}'
            f'<div class="cf-netbar" style="width:{width}%; background:{color}66"></div></td>'
            f'<td class="cf-dots">{dots}</td></tr>'
        )
    return (
        '<table class="cf-table"><thead><tr>'
        "<th>#</th><th>BROKER · DNA</th>"
        '<th style="text-align:right">NET</th><th>PERSIST</th>'
        f'</tr></thead><tbody>{"".join(body)}</tbody></table>'
    )


def concentration_html(panel: dict) -> str:
    """The Concentration panel: Top-2 net-buy share (big cyan %, progress bar) +
    Herfindahl (2 dp + dispersed/concentrated label) + top-2 buyer note.
    A missing measurement renders as an em-dash — absent, never zero."""
    share = panel["top2_share_pct"]
    hhi = panel["hhi"]
    share_stat = (
        f'<div class="cf-bigstat" style="color:{TOKENS["accent"]}">{share:.0f}%</div>'
        f'<div class="cf-bartrack"><span style="width:{min(share, 100):.0f}%; '
        f'background:{TOKENS["accent"]}"></span></div>'
        if share is not None
        else f'<div class="cf-bigstat" style="color:{TOKENS["text_faint"]}">—</div>'
    )
    hhi_stat = (
        f'<div class="cf-bigstat">{hhi:.2f}</div>'
        f'<div class="cf-statlabel">{escape(panel["hhi_label"] or "")}</div>'
        if hhi is not None
        else f'<div class="cf-bigstat" style="color:{TOKENS["text_faint"]}">—</div>'
    )
    note = (
        f'<div class="cf-statlabel" style="margin-top:10px">Top-2 buyers are '
        f'<span class="cf-mono" style="color:{TOKENS["text"]}">{escape(panel["top2_names"])}</span>.</div>'
        if panel["top2_names"]
        else ""
    )
    body = (
        '<div style="display:flex; gap:18px">'
        f'<div style="flex:1.4"><div class="cf-statlabel">Top-2 net-buy share</div>{share_stat}</div>'
        f'<div style="flex:1"><div class="cf-statlabel">Herfindahl (HHI)</div>{hhi_stat}</div>'
        f"</div>{note}"
    )
    return panel_html("CONCENTRATION", body)


def veto_panel_html(rows: list[dict]) -> str:
    """The Veto Checks panel (§5 hard rejects): one row per filter, ✓ (clear,
    green) or ✕ (fired, red) + the observation that tripped it. Categorical
    reasons only — a veto is never a number (RULE B)."""
    body = []
    for r in rows:
        mark, color = ("✕", TOKENS["sell"]) if r["fired"] else ("✓", TOKENS["buy"])
        detail = escape(r["detail"]) if r["detail"] else "clear"
        body.append(
            '<div class="cf-vetorow">'
            f'<span class="cf-vmark" style="color:{color}">{mark}</span>'
            f'<span>{escape(r["label"])}</span>'
            f'<span class="cf-vval">{detail}</span></div>'
        )
    return panel_html("VETO CHECKS", "".join(body), note="§5 hard rejects")


def matrix_html(rows: list[dict], symbols: list[str], *, selected: str | None = None) -> str:
    """The Broker × Stock matrix: cells tinted green/red with intensity = |net|
    share of the largest cell; a name where the broker was not a top participant
    is an empty slot (missing ≠ zero). The selected symbol's column is highlighted."""
    cells = [
        abs(r[s]) for r in rows for s in symbols if isinstance(r.get(s), (int, float))
    ]
    max_abs = max(cells, default=0.0)
    head_cells = []
    for s in symbols:
        style = f' style="color:{TOKENS["accent"]}"' if s == selected else ""
        head_cells.append(f"<th{style}>{escape(s)}</th>")
    head = "<th>BROKER</th>" + "".join(head_cells)
    body = []
    for r in rows:
        chip = f"&nbsp;{dna_chip_html(r['dna'])}" if r.get("dna") else ""
        tds = [
            '<td style="padding:2px 6px; white-space:nowrap">'
            f'<span class="cf-code">{escape(r["broker"])}</span>{chip}</td>'
        ]
        for s in symbols:
            v = r.get(s)
            if not isinstance(v, (int, float)):
                tds.append('<td class="cf-cell cf-empty" title="not a top participant">·</td>')
                continue
            rgb = "63,185,80" if v >= 0 else "248,81,73"
            alpha = 0.10 + (0.50 * abs(v) / max_abs if max_abs else 0)
            ring = f"; outline:1px solid {TOKENS['accent']}55" if s == selected else ""
            tds.append(
                f'<td class="cf-cell" style="background:rgba({rgb},{alpha:.2f}){ring}" '
                f'title="{escape(r["broker"])} → {escape(s)}: {v:+,.2f} IDR bn">{v:+,.1f}</td>'
            )
        body.append(f"<tr>{''.join(tds)}</tr>")
    return (
        f'<table class="cf-matrix"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
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


def watchlist_card_html(row: dict, *, selected: bool = False) -> str:
    """One ARMED-watchlist card from `watchlist_view.rows()`: status dot, ticker,
    track chip, state WORD, five component spark-bars. No composite number, no rank,
    no verb (RULE B) — exact component values live only in the bar tooltips.

    The card doubles as the terminal's symbol selector (design: no sidebar dropdown);
    `selected` draws the design's brightened border around the active name."""
    state = row["state"]
    dot = _STATE_DOT.get(state, TOKENS["text_faint"])
    pulse = " armed" if state == "ARMED" else ""
    comps = row["components"]
    bars = "".join(_spark_bar(k, comps.get(k), state) for k in SPARK_ORDER)
    labels = "".join(f"<span>{k}</span>" for k in SPARK_ORDER)
    ring = (
        ' style="border-color:rgba(255,255,255,0.30); background:#10161f"'
        if selected
        else ""
    )
    return (
        f'<div class="cf-card"{ring}>'
        f'<span class="cf-dot{pulse}" style="background:{dot}"></span>'
        f'<span class="cf-tick">{escape(row["symbol"])}</span>'
        f'<span class="cf-track">{escape(row["track"])}</span>'
        f'<span class="cf-state" style="color:{_STATE_COLOR.get(state, TOKENS["text_faint"])}">{escape(state)}</span>'
        f'<div class="cf-sparks">{bars}</div>'
        f'<div class="cf-sparklabels">{labels}</div>'
        "</div>"
    )


def rail_head_html(framing: str) -> str:
    """Rail header + framing note (rendered once, above the clickable cards)."""
    return (
        '<div class="cf-railhead">ARMED WATCHLIST</div>'
        f'<div class="cf-railnote">{escape(framing.capitalize())}. '
        "Internal ARMED state; score withheld (RULE B).</div>"
    )


def rail_foot_html(data: dict) -> str:
    """Rail footer: the never-silent cap note + the RULE-B framing reminder."""
    dropped = (
        f'<div class="cf-railnote">…and {data["dropped"]} more '
        f'(top {len(data["rows"])} shown of {data["total"]})</div>'
        if data["dropped"]
        else ""
    )
    return (
        f"{dropped}"
        '<div class="cf-railnote">Components are raw observation — no probability '
        "or verb until validated.</div>"
    )


def watchlist_rail_html(data: dict, *, selected: str | None = None) -> str:
    """The full right rail as one static blob: header, framing, cards, cap note.
    The app renders the cards individually (clickable containers); this composition
    keeps the whole-rail contract in one place for tests and static rendering."""
    body = "".join(
        watchlist_card_html(r, selected=r["symbol"] == selected) for r in data["rows"]
    ) or '<div class="cf-railnote">— nothing ARMED or watching today</div>'
    return f"{rail_head_html(data['framing'])}{body}{rail_foot_html(data)}"


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


def kv_rows_html(rows: list[dict]) -> str:
    """Label-left / mono-value-right stat rows (design right-column panels).
    A missing value renders as an em-dash — absent, never zero."""
    out = []
    for r in rows:
        color = r.get("color") or TOKENS["text"]
        value = r["value"] if r["value"] is not None else "—"
        if r["value"] is None:
            color = TOKENS["text_faint"]
        out.append(
            '<div class="cf-kvrow">'
            f'<span>{escape(r["label"])}</span>'
            f'<span class="cf-kvval" style="color:{color}">{escape(str(value))}</span></div>'
        )
    return "".join(out)


def bigstat_bar_html(
    value: str, note: str, frac_pct: float | None, color: str
) -> str:
    """Big mono stat + sub-note + proportional bar (design: FOREIGN OWN vs FREE-FLOAT)."""
    bar = (
        f'<div class="cf-bartrack"><span style="width:{min(max(frac_pct, 0), 100):.0f}%; '
        f'background:{color}"></span></div>'
        if frac_pct is not None
        else ""
    )
    return (
        f'<div class="cf-bigstat" style="color:{color}">{escape(value)}</div>'
        f'<div class="cf-statlabel">{escape(note)}</div>{bar}'
    )


def sparkline_svg(
    values: list[float], *, width: int = 220, height: int = 46, color: str | None = None
) -> str:
    """Inline-SVG sparkline (design: KSEI OWNERSHIP · 6mo). Needs ≥2 points;
    otherwise the caller renders the missing-data note instead."""
    color = color or TOKENS["foreign"]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 4
    pts = " ".join(
        f"{pad + i * (width - 2 * pad) / (len(values) - 1):.1f},"
        f"{height - pad - (v - lo) / span * (height - 2 * pad):.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg width="100%" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'style="display:block; height:{height}px">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def split_bar_html(fgn_bn: float, dom_bn: float) -> str:
    """FOREIGN vs DOMESTIC split bar: blue foreign segment / purple domestic segment,
    widths by share of |flow|, signed mono labels at the ends (design bottom strip)."""
    total = abs(fgn_bn) + abs(dom_bn)
    f_pct = 50.0 if total == 0 else abs(fgn_bn) / total * 100
    return (
        '<div class="cf-splitbar">'
        f'<span style="flex:0 0 {f_pct:.1f}%; '
        f'background:linear-gradient(90deg,{TOKENS["accent"]},{TOKENS["foreign"]}); '
        f'justify-content:flex-start">FGN{fgn_bn:+.1f}</span>'
        f'<span style="flex:1; '
        f'background:linear-gradient(90deg,#8957e5,{TOKENS["divergence"]}); '
        f'justify-content:flex-end">DOM{dom_bn:+.1f}</span></div>'
    )


def callout_html(label: str, text: str, *, color: str | None = None) -> str:
    """A detection callout strip: colored dot + small-caps label + observation text
    (design: FLOW-REVERSAL DETECTION / the accumulation bottom note)."""
    color = color or TOKENS["accent"]
    return (
        '<div class="cf-callout">'
        f'<span class="cf-calldot" style="background:{color}"></span>'
        f'<span><span class="cf-calllabel">{escape(label)}</span>{escape(text)}</span></div>'
    )


def phase_box_html(title: str, note: str, color_key: str) -> str:
    """WYCKOFF PHASE box (design 06 replay): caps label + colored phase title + note,
    on a tint of the phase's semantic color. A label, never a number (RULE A/B)."""
    color = TOKENS.get(color_key, TOKENS["text_faint"])
    # tint the fill/border from the phase color's channels (matches design intensity).
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return (
        f'<div class="cf-phasebox" style="background:rgba({r},{g},{b},0.08); '
        f'border:1px solid rgba({r},{g},{b},0.32)">'
        '<div class="cf-phaselabel">WYCKOFF PHASE</div>'
        f'<div class="cf-phasetitle" style="color:{color}">{escape(title)}</div>'
        f'<div class="cf-phasenote">{escape(note)}</div></div>'
    )


def heatmap_legend_html() -> str:
    """Heatmap legend strip: net-sell/net-buy swatches, intensity note, divergence key."""
    return (
        '<div class="cf-legend">'
        f'<span><span class="cf-swatch" style="background:{TOKENS["sell"]}"></span>net sell</span>'
        f'<span><span class="cf-swatch" style="background:{TOKENS["buy"]}"></span>net buy</span>'
        "<span>intensity = flow as % of cap</span>"
        f'<span style="flex:1"></span><span style="color:{TOKENS["divergence"]}">'
        "◆ divergence: local buy + foreign sell</span></div>"
    )


def _tile_html(cell: dict) -> str:
    """One heatmap tile. Direction colors the tile, intensity sets its alpha against
    the grid max; a missing intensity renders the tile faint with an em-dash."""
    sym = escape(cell["symbol"])
    intensity = cell["intensity_pct_of_cap"]
    direction = cell["direction"]
    div_cls = " cf-div" if cell["divergence"] else ""
    fgn = cell["foreign_net_bn"]
    smart = cell["local_smart_net_bn"]
    tip = escape(
        f"{cell['symbol']}: foreign net "
        + (f"{fgn:+,.2f} bn" if fgn is not None else "—")
        + (f" · local smart {smart:+,.2f} bn" if smart is not None else "")
    )
    if intensity is None or direction == "NEUTRAL":
        return (
            f'<div class="cf-tile{div_cls}" title="{tip}" '
            'style="background:rgba(255,255,255,0.02)">'
            f'<div class="cf-tilesym">{sym}</div>'
            f'<div class="cf-tileval" style="color:{TOKENS["text_faint"]}">'
            f'{"—" if intensity is None else "·"}</div></div>'
        )
    rgb = "63,185,80" if direction == "BUY" else "248,81,73"
    alpha = 0.12 + 0.5 * min(intensity / cell["_max_intensity"], 1.0)
    sign = "+" if direction == "BUY" else "−"
    return (
        f'<div class="cf-tile{div_cls}" title="{tip}" '
        f'style="background:rgba({rgb},{alpha:.2f})">'
        f'<div class="cf-tilesym">{sym}</div>'
        f'<div class="cf-tileval">{sign}{intensity:.2f}%</div></div>'
    )


def heatmap_grid_html(sectors: list[dict]) -> str:
    """The sector → stock tile grid (design module 5): one row per sector, tiles
    colored by direction with intensity = flow-as-%-of-cap alpha; divergence tiles
    carry the purple ring + ◆. Pure rendering — no score, no rank (RULE B)."""
    max_i = max(
        (c["intensity_pct_of_cap"] or 0.0 for s in sectors for c in s["tiles"]),
        default=0.0,
    ) or 1.0
    rows = []
    for s in sectors:
        tiles = "".join(_tile_html({**c, "_max_intensity": max_i}) for c in s["tiles"])
        rows.append(
            '<div class="cf-heatrow">'
            f'<div class="cf-heatsector">{escape(s["sector"])}</div>{tiles}</div>'
        )
    return heatmap_legend_html() + "".join(rows)


def divergence_panel_html(rows: list[dict]) -> str:
    """DIVERGENCE ALERTS panel: mono ticker + categorical observation per row."""
    if not rows:
        body = (
            f'<div class="cf-statlabel" style="color:{TOKENS["buy"]}">'
            "✓ no divergence alerts across the grid</div>"
        )
    else:
        body = "".join(
            '<div class="cf-kvrow" style="justify-content:flex-start; gap:16px">'
            f'<span class="cf-mono" style="color:{TOKENS["text"]}; flex:0 0 52px; '
            f'font-weight:600">{escape(r["symbol"])}</span>'
            f'<span>{escape(r["note"])}</span></div>'
            for r in rows
        )
    return panel_html(
        "◆ DIVERGENCE ALERTS", body, note="observation, not a recommendation"
    )


def quadrant_chip_html(quadrant: str) -> str:
    """A sector-card state chip (LEADERS / EARLY RECOVERY / …), quadrant-colored."""
    color = QUADRANT_COLORS.get(quadrant, TOKENS["text_faint"])
    label = QUADRANT_LABELS.get(quadrant, quadrant)
    return (
        f'<span class="cf-chip" style="border:1px solid {color}44; '
        f'background:{color}14; color:{color}">{escape(label)}</span>'
    )


def sector_card_html(row: dict) -> str:
    """One Sector-Rotate right-column card: name + quadrant chip, observation note,
    mono flow/RS/tide stats. Missing measurements render as em-dashes."""
    flow = row["net_foreign_flow_bn"]
    rs = row["relative_strength_pct"]
    flow_s = (
        f'<span style="color:{TOKENS["buy"] if flow >= 0 else TOKENS["sell"]}">{flow:+.2f}</span>'
        if flow is not None
        else "—"
    )
    rs_s = f"{rs:+.2f}" if rs is not None else "—"
    tide = f" &nbsp;{escape(row['tide'])}" if row.get("tide") else ""
    return (
        '<div class="cf-seccard">'
        f'<span class="cf-secname">{escape(row["sector"])}</span>'
        f'<span style="float:right">{quadrant_chip_html(row["quadrant"])}</span>'
        f'<div class="cf-secnote">{escape(row["note"].capitalize())}.</div>'
        f'<div class="cf-secstats">flow {flow_s} &nbsp;RS {rs_s}{tide}</div></div>'
    )


def stat_cards_html(cards: list[dict]) -> str:
    """The Risk-Monitor top strip: label / big mono value / sub-label cards.
    A missing measurement is an em-dash, never a zero."""
    out = []
    for c in cards:
        color = c.get("color") or TOKENS["text"]
        value = c["value"] if c["value"] is not None else "—"
        if c["value"] is None:
            color = TOKENS["text_faint"]
        sub = f'<div class="cf-statlabel">{escape(c["sub"])}</div>' if c.get("sub") else ""
        out.append(
            '<div class="cf-statcard">'
            f'<div class="cf-cardlabel">{escape(c["label"])}</div>'
            f'<div class="cf-bigstat" style="color:{color}">{escape(str(value))}</div>'
            f"{sub}</div>"
        )
    return f'<div class="cf-statcards">{"".join(out)}</div>'


_CAP_COLORS = {"OK": TOKENS["buy"], "WARN": TOKENS["armed"], "OVER CAP": TOKENS["sell"]}


def cap_bars_html(rows: list[dict]) -> str:
    """Exposure-cap bars (§6): weight-vs-cap fill, colored OK/WARN/OVER CAP."""
    out = []
    for r in rows:
        w, cap = r["weight_pct"], r["cap_pct"]
        frac = 0.0 if not cap else min(w / cap * 100, 100)
        color = _CAP_COLORS.get(r["status"], TOKENS["text_faint"])
        out.append(
            '<div style="margin-bottom:9px">'
            f'<span style="font-size:11px; color:{TOKENS["text_secondary"]}">{escape(r["key"])}</span>'
            f'<span class="cf-mono" style="float:right; font-size:11px; color:{color}">'
            f"{w:.1f}%</span>"
            f'<div class="cf-bartrack"><span style="width:{frac:.0f}%; background:{color}"></span></div>'
            "</div>"
        )
    return "".join(out)


def positions_table_html(rows: list[dict]) -> str:
    """OPEN PAPER POSITIONS table: name · sector · %-equity (bar vs the §6 cap) ·
    days-to-exit. P&L stays withheld until real paper fills exist — an em-dash with
    the reason in the tooltip, never a fabricated number."""
    body = []
    for r in rows:
        w, cap = r["weight_pct"], r["cap_pct"]
        frac = 0.0 if not cap else min((w or 0) / cap * 100, 100)
        color = _CAP_COLORS.get(r["status"], TOKENS["text_faint"])
        dte = f'{r["days_to_exit"]:.1f}d' if r.get("days_to_exit") is not None else "—"
        pnl = (
            f'<span style="color:{TOKENS["text_faint"]}" '
            'title="withheld — no paper fills yet (preview book, no entry price)">—</span>'
        )
        body.append(
            "<tr>"
            f'<td class="cf-code">{escape(r["symbol"])}</td>'
            f'<td style="color:{TOKENS["text_muted"]}">{escape(r["sector"])}</td>'
            f'<td class="cf-num" style="color:{color}">{w:.1f}%'
            f'<div class="cf-netbar" style="width:{frac:.0f}%; background:{color}66"></div></td>'
            f'<td class="cf-num">{dte}</td>'
            f'<td class="cf-num">{pnl}</td></tr>'
        )
    return (
        '<table class="cf-table"><thead><tr>'
        "<th>NAME</th><th>SECTOR</th>"
        '<th style="text-align:right">% EQUITY</th>'
        '<th style="text-align:right">DTE</th>'
        '<th style="text-align:right">P&amp;L</th>'
        f'</tr></thead><tbody>{"".join(body)}</tbody></table>'
    )


def crowding_matrix_html(matrix: dict[str, dict[str, float | None]], *, threshold: float) -> str:
    """CROWDING MATRIX (same-bandar ρ): red alpha rises with ρ; pairs at/over the §6
    threshold get the ring. None (no broker flow) is an empty slot — missing ≠ zero."""
    symbols = list(matrix)
    head = "<th></th>" + "".join(f"<th>{escape(s)}</th>" for s in symbols)
    body = []
    for a in symbols:
        tds = [f'<td class="cf-code" style="padding:2px 6px">{escape(a)}</td>']
        for b in symbols:
            rho = matrix[a].get(b)
            if rho is None:
                tds.append('<td class="cf-cell cf-empty" title="no broker flow">·</td>')
                continue
            if a == b:
                tds.append(
                    '<td class="cf-cell" style="background:rgba(255,255,255,0.04); '
                    f'color:{TOKENS["text_faint"]}">1.00</td>'
                )
                continue
            alpha = 0.06 + 0.5 * max(rho, 0.0)
            ring = f"; outline:1px solid {TOKENS['sell']}88" if rho >= threshold else ""
            tds.append(
                f'<td class="cf-cell" style="background:rgba(248,81,73,{alpha:.2f}){ring}" '
                f'title="{escape(a)} × {escape(b)}: ρ = {rho:.2f}">{rho:.2f}</td>'
            )
        body.append(f"<tr>{''.join(tds)}</tr>")
    return (
        f'<table class="cf-matrix"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>'
    )


def scenario_rows_html(rows: list[dict]) -> str:
    """SCENARIO STRESS rows: shock name + detail, signed impact right (red when
    negative). Hypothetical what-ifs, not forecasts."""
    out = []
    for r in rows:
        pct = r["impact_pct_of_equity"]
        if pct is None:
            val, color = "—", TOKENS["text_faint"]
        else:
            val = f"{pct:+.1f}%"
            color = TOKENS["sell"] if pct < 0 else TOKENS["buy"]
        bn = f' title="{r["impact_bn"]:+,.2f} IDR bn"' if r["impact_bn"] is not None else ""
        out.append(
            '<div class="cf-kvrow">'
            f'<span>{escape(r["scenario"])}'
            f'<span style="color:{TOKENS["text_faint"]}"> · {escape(r["detail"])}</span></span>'
            f'<span class="cf-kvval" style="color:{color}"{bn}>{val}</span></div>'
        )
    return "".join(out)


def login_hero_html() -> str:
    """The session-gate hero (design/SCREENS_login.md): headline, framing, the three
    checkmark rows, and the RULE-B pill. Copy matches the pixel target."""
    checks = (
        ("Credentialed sign-in.", "Your own Stockbit login drives the verified "
         "login/v6 flow — no hand-pasted token."),
        ("Multi-factor by OTP.", "A one-time code by email, WhatsApp, or SMS; the "
         "challenge can loop across channels before it clears."),
        ("Keychain-backed session.", "Access + refresh tokens held in the OS "
         "Keychain, read fresh, never written in plaintext (§10). Auth only — it "
         "gates no signal or RULE A/B behaviour."),
    )
    rows = "".join(
        '<div class="cf-checkrow"><span class="cf-checkbox">✓</span>'
        f"<span><b>{escape(title)}</b> {escape(text)}</span></div>"
        for title, text in checks
    )
    return (
        '<div class="cf-hero">'
        '<div class="cf-herolabel">SESSION GATE</div>'
        "<h1>Sign in to open the terminal.</h1>"
        '<div class="cf-herosub">Establish <b>your own authenticated Stockbit '
        "session</b> — username, password, and a one-time code. The resulting "
        "session lives on this machine only; nothing is republished.</div>"
        f"{rows}"
        f'<div class="cf-ruleb" style="display:inline-block; margin-top:10px">'
        "RULE B · Observation-only — scores stay gated until paper-validated</div>"
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
