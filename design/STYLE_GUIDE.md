# STYLE GUIDE — pixel-fidelity implementation reference

Every value here is **lifted verbatim from `IDX Flow Terminal.dc.html`** — not approximated. Match these exactly. Pair this with `screens/` (the visual targets) and `SCREENS_terminal.md` / `SCREENS_login.md` (per-screen composition).

> The prototype uses **no CSS framework and no shadows** — depth is built purely from layered background shades + 1px hairline borders. Reproduce that model; do not add drop shadows, glassmorphism, or rounded "card with accent left-border" tropes.

---

## 1. Color palette (exact hex)
Defined in the logic class as `C = {…}` plus inline literals. Use these as your design tokens.

### Backgrounds (darkest → lightest, this is the depth ladder)
| Token | Hex | Use |
|---|---|---|
| bg-0 (app) | `#070a10` | app root, main content pane |
| bg-1 (chrome) | `#0a0e14` | nav rail, right watchlist rail, ticker bar, auth card column |
| bg-2 (panel) | `#0d121b` | cards, panels, tables, auth card, scrollbar-adjacent surfaces |
| bg-3 (control) | `#1e2632` | scrollbar thumb, range-slider track, inset controls |

Top bar is a gradient: `linear-gradient(180deg,#0d121b,#0a0e14)`.

### Text
| Token | Hex | Use |
|---|---|---|
| text | `#e6edf3` | primary text |
| text-strong-alt | `#c2ccd8` | secondary emphasis body |
| muted | `#8b98a9` | labels, secondary |
| faint | `#5a6675` | captions, monospace meta, table `#` column, placeholders |

### Semantic / accent
| Token | Hex | Use |
|---|---|---|
| accent (brand) | `#58c4dd` | brand, links, active nav, focus, selection, slider thumb |
| accent-deep | `#3a8fb0` | brand logo gradient end (`135deg,#58c4dd,#3a8fb0`) |
| buy / positive | `#3fb950` | net buy, positive change, safe states |
| sell / negative | `#f85149` | net sell, negative, errors |
| armed / caution (RULE B) | `#d29922` | ARMED state, RULE-B gating, stealth zone, VWAP marker |
| armed-text | `#e8c168` | RULE-B caution text on dark |
| divergence | `#bc8cff` | divergence signals |
| foreign lens | `#58a6ff` | foreign-institutional accents |
| error-text | `#f6a9a4` | error message body |

### Border & fill conventions (rgba on dark)
- Hairline border (default): `1px solid rgba(255,255,255,0.06)`
- Hairline border (chrome/top): `rgba(255,255,255,0.07)`
- Table row divider (faint): `rgba(255,255,255,0.04)` / `0.035`
- Accent tint fill: `rgba(88,196,221,0.12)`, border `rgba(88,196,221,0.25–0.3)`
- Caution tint fill: `rgba(210,153,34,0.08)`, border `rgba(210,153,34,0.32)`
- Error tint fill: `rgba(248,81,73,0.10)`, border `rgba(248,81,73,0.32)`
- Selection: `rgba(88,196,221,0.3)`

---

## 2. Typography
Two families only, loaded from Google Fonts:
```
Geist:      400 500 600 700   — all UI text, headings, body
Geist Mono: 400 500 600       — ALL numerics, tickers, prices, dates, codes, meta labels
```
The mono/sans split is a hard rule: **any number, ticker symbol, price, percentage, date, token, or ALL-CAPS meta label is Geist Mono.** Prose and titles are Geist.

### Type scale (px, from source)
| Role | Size / weight | Notes |
|---|---|---|
| Module title | 19 / 600 | main pane header ribbon |
| Stock ticker (hero) | 26 / 700 | broker flow stock header |
| Stock ticker (module) | 22 / 700 | other modules |
| Price (hero) | 20 / 600 mono | |
| Auth headline | clamp(28,3.4vw,42) / 600 | login left panel |
| Card title | 16 / 600 | auth card |
| Section/table header | 12 / 600, letter-spacing 0.03em | |
| Body | 12.5–13.5 | |
| Secondary body | 11.5 / muted | |
| Meta label (caps) | 9–11 mono, letter-spacing 0.08–0.22em, faint | e.g. `USERNAME`, `SESSION GATE`, `STEP 1 · 2` |
| Nav rail label | 9, letter-spacing 0.02em | |
| Table cell | 11–12.5 | mono for numerics |
| Ticker bar | 9.5 mono, letter-spacing 0.08em | |

Heading letter-spacing: titles `-0.01em` to `0.01em`; caps labels `0.08–0.22em`.

---

## 3. Shell layout (exact dimensions)
Full-height flex column, `background:#070a10; color:#e6edf3`, `min-height:760px`. Native canvas **1280×800**.

```
┌─ TOP BAR ── height 52px, flex 0 0 52px ─────────────────────────┐
│  gradient(180deg,#0d121b,#0a0e14), border-bottom rgba(255,255,255,.07)
│  padding 0 18px, gap 18px
│  [logo 26×26 rounded-6 gradient(135deg,#58c4dd,#3a8fb0)] VECTOR·LAB
│  … as-of stamp · RULE-B banner · IHSG/Track chip · operator control
├─ BODY (flex:1, display:flex, min-height:0) ─────────────────────┤
│ NAV RAIL │        MAIN (flex:1, bg #070a10)         │ WATCHLIST  │
│ 82px     │  ┌ header ribbon: pad 14/20/12 ────────┐ │ 296px      │
│ flex 0 0 │  │ title 19/600 + subtitle 11.5 muted   │ │ flex 0 0   │
│ bg #0a0e14│  │ + module badge (dot + text)          │ │ bg #0a0e14 │
│ pad 10/0 │  ├ content: flex 1, overflow auto,      │ │ border-left│
│ gap 2px  │  │ padding 18px 20px                     │ │ rgba .06   │
│ 8 items  │  └──────────────────────────────────────┘ │            │
├─ TICKER / DISCLAIMER BAR ── height 26px, flex 0 0 26px ─────────┤
│  bg #0a0e14, border-top rgba .06, marquee 9.5px mono faint       │
└─────────────────────────────────────────────────────────────────┘
```

**Nav rail item:** 82px wide column, each item = glyph (Unicode) + 9px label, centered, `gap 2px`; active item uses accent tint; gated modules show a `🔒` at `top:6px; right:9px`. Glyphs used: Broker `▦`-style, Foreign, Accum, Replay, Heatmap, Sector, Risk, SMS `∑` (see source `modDefs`).

**Right watchlist rail (296px):** header "ARMED WATCHLIST" (12/600) + observation caveat (armed-text). Each row: status dot (armed → `armedpulse` animation) + ticker (13 mono 600) + track chip + mini component bars **DIV / BRK / FF / RVOL / BLK**, and WATCH/ARMED state.

**Cards/panels:** `background:#0d121b; border:1px solid rgba(255,255,255,0.06); border-radius:10px; overflow:hidden`. Panel header row: `padding:11px 14px; border-bottom:1px solid rgba(255,255,255,0.06)`. Border radius: cards 10px, auth card 12px, chips/badges 5px, small tags 3–4px, buttons ~8px.

---

## 4. Component patterns

### Buttons
Primary (submit): accent fill, dark text, radius ~8px, full-width in auth card. Disabled/loading state swaps label + dims (`credBtnStyle`/`credBtnLabel` computed in logic).

### Inputs
`background:#0d121b`-family, `1px solid rgba(255,255,255,0.06)` hairline, radius ~8px, Geist body; caps mono label above at `margin:22px 0 8px` (first) / `14px 0 8px` (subsequent). Focus → accent border. Placeholders faint.

### Badges / chips
- Track chip (LQ45/SMC): accent tint fill + accent text, radius 5px, 10px, letter-spacing 0.06em.
- Sector chip: `rgba(255,255,255,0.05)` fill, muted text.
- Module badge (observation vs gated): dot + text; **OBSERVATION** uses buy/accent dot, **GATED** uses armed dot.

### Tables
CSS grid rows with explicit `grid-template-columns` (e.g. broker flow `34px 1fr 96px 70px`). Header row: 10px caps faint labels, letter-spacing 0.05em. Row divider `rgba(255,255,255,0.035–0.05)`. Numerics right-aligned, mono. DNA/state tags inline.

### Charts (SVG, hand-built)
Rendered via `React.createElement('svg'…)` in logic — recreate with the target stack's charting lib. Conventions: stealth-zone shade `rgba(227,179,65,0.07)` + "STEALTH ZONE" mono label; VWAP dashed line in armed color `strokeDasharray:'4 4'`; markers dashed armed lines `'3 3'`; flow lines colored per DNA/series; legends bottom-right. Grid/axis text 9–10px mono faint.

---

## 5. Motion (keyframes — reproduce exactly)
```css
@keyframes armedpulse { 0%,100%{box-shadow:0 0 0 0 rgba(210,153,34,.55)} 50%{box-shadow:0 0 0 5px rgba(210,153,34,0)} }  /* ARMED status dots */
@keyframes tickscroll { from{transform:translateX(0)} to{transform:translateX(-50%)} }                                     /* bottom disclaimer marquee */
@keyframes livedot    { 0%,100%{opacity:1} 50%{opacity:.25} }                                                              /* live/session status dot, 1.8s */
```
`armedpulse` and `livedot` run at `1.8s infinite`. **`armedpulse` only plays when a module is validated** (RULE B) — gated dots are static.

Scrollbars: 9px, thumb `#1e2632` radius 6px, transparent track.

---

## 6. RULE B rendering states (the most important visual contract)
Every scored module renders one of two visual states, driven by a **server-authoritative** validation flag (`validated`), never a client toggle:

**GATED (default until PAPER_VALIDATION_MONTHS met):**
- Score shown as `•••` in faint color, never a number.
- Badge: armed dot + "GATED · number withheld".
- Amber progress bar: e.g. `1.4 / 3 months forward-paper — number withheld`, width 47%, color armed.
- Ranking still shown but framed as "flow-derived ordering — observation, not a recommendation (RULE B)."

**VALIDATED (after ≥3 months):**
- Real numeric score, armed color if ARMED (≥70) else text color.
- Badge → observation/enabled; progress bar green (buy), `3.0 / 3 months — CLAIM ENABLED`, width 100%.
- ARMED dots gain `armedpulse`.

SMS weights (Track B) surfaced in copy: `DIV 30 · Broker 35 · RVOL 15 · Block 10 · phase bonus 10` (§4). ARMED threshold SMS ≥ 70; WATCH ≥ 55.

---

## 7. Recreating in production (per LOCKED_SPEC §10)
Target is **local-first Python: Streamlit + DuckDB/SQLite + Pandas/Polars + TA-Lib.** To hit this fidelity in Streamlit, inject a global CSS block with the tokens above (custom theme + `st.markdown` component CSS), load Geist/Geist Mono via `@font-face`/Google Fonts, and build panels as styled containers. Charts → Plotly/Altair themed to the palette (dark template, mono tick fonts, the shade/dashed-line conventions in §4). If a React front-end is chosen instead, port the inline styles directly — they are already framework-agnostic literals.

Do **not** ship the `.dc.html` runtime or its `<x-dc>` / `DCLogic` scaffolding — it is design-tool-specific. Read it only for values, composition, and interaction logic.
