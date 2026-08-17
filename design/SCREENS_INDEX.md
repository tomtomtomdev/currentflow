# Screenshots — visual fidelity targets

High-quality reference captures of every screen, rendered at the prototype's native 1280×800. **These are the pixel targets** — match layout, spacing, color, and type against them. Open `IDX Flow Terminal.dc.html` in a browser for the live, interactive version.

## Session gate (§9.1 · `SCREENS_login.md`)
- `screens/01-login-credentials.png` — Card state 1: username + password, reCAPTCHA-invisible note, "Paste a session Bearer instead" fallback link.
- `screens/02-login-otp.png` — Card state 2: OTP challenge, channel picker (Email / WhatsApp / SMS), resend + loop-to-next-challenge behavior.

## Terminal — shell + 8 modules (`SCREENS_terminal.md`)
Every terminal shot shows the full shell: top status bar (as-of stamp, RULE-B banner, IHSG/Track chip, authed operator control), left module nav rail, main module pane, right ARMED watchlist, bottom disclaimer ticker.

- `screens/03-broker-flow.png` — Broker Flow Analyzer. Net buy/sell, DNA classification, concentration (HHI), veto checks. OBSERVATION badge.
- `screens/04-foreign-flow.png` — Foreign Flow.
- `screens/05-accumulation-detector.png` — Wyckoff phase classifier (RULE A gate — only Phase C/D tradeable). Full module, top→bottom: STEALTH ACCUMULATION DETECTED banner · price/cumulative-accumulation chart · verdict note · STEALTH METRICS · ACCUMULATOR VWAP · **ABSORPTION card** ("unavailable — needs L2 depth — degrades gracefully, never faked §10") · **STEALTH-DIVERGENCE DETECTION footer** (price move over window vs rising net accumulation, framed as observation). The Absorption card and Divergence footer sit **below the 800px fold** — the hero PNG shows the top; build all sections.
- `screens/06-money-replay.png` — as-of audit replay with scrubber (look-ahead control).
- `screens/07-smart-heatmap.png` — Smart Heatmap.
- `screens/08-sector-rotation.png` — Sector Rotation (Track B / SMC index — never IHSG headline).
- `screens/09-risk-monitor.png` — Risk Monitor.
- `screens/10-sms-rank.png` — SMS / Rank. **RULE B in force:** "GATED · number withheld", per-module validation bar (1.4 / 3 months), flow-ranked ordering framed as observation, no score number rendered.

## Post-v2 surfaces — **no capture, no `.dc.html` prototype** (spec'd from the implementation)

These two full-width surfaces were built after the prototype was drawn, directly in Streamlit. They
have **no `.dc.html` prototype and no PNG here** — so for them the direction reverses: the shipped
code is the source and the handoff is derived from it. Named rather than faked; do not read the
absence of a shot as "not built". (Framework Lenses is the partial exception: its *composition* was
later redesigned in a handoff — [`FRAMEWORK_LENSES_REDESIGN.md`](FRAMEWORK_LENSES_REDESIGN.md) +
the static reference render [`framework-lenses-redesign.html`](framework-lenses-redesign.html),
2026-08-17, shipped — which is a rendered reference, not an interactive prototype. Tokens, states,
copy and rules were unchanged by it; only layout moved.)

- **Framework Lenses** (PLAN.md Slice 23 · `design/HANDOFF_v2.md` §Screens **6**) — the five source
  frameworks (Wyckoff · Wyckoff 2.0 volume profile · VPA bar character · Bandarmology · Greenblatt
  Magic Formula) read **apart** instead of fused into the §2 gate chain, one switchable section each
  plus a **Confluence** section. Full width, **no ARMED rail**. Implemented in
  `currentflow/ui/lens_view.py` + `shell.py` (`.cf-lens*`, `lens_*_html`). Rows sit under **state
  bands** (all five, printed even at zero) and each carries a **fixed five-slot cross-lens strip**
  (`WYK · VP · VPA · BND · MF`). Its lens-state glyphs (`◉ ◐ ○ — ·`) and tints are deliberately
  **not** the pipeline's stage palette — a lens read is an observation, not a verdict. Only digits on
  the surface are counts of symbols; the confluence figure is a **set size**, never a score, and the
  strip is categorical, never a meter (RULE B). Every row carries the engine's own verdict, and a
  confluence row RULE A rejected makes `RULE A · NOT TRADEABLE` its loudest element, so agreement can
  never look like tradeability (RULE A).
- **Pattern Catalog** (PLAN.md Slice 21 · LD-14) — the dedicated base-rate research view under P1–P4.
  **Not yet spec'd in `HANDOFF_v2.md` at all** — an open gap, listed here so it stays visible.

## What to reproduce exactly
- The **RULE-B gating states** — badges ("OBSERVATION · ships now" vs "GATED · number withheld"), the amber validation progress bar, and the fact that no score/probability number appears on gated modules.
- The **no-shadow, layered-background + hairline-border** depth model.
- **Geist / Geist Mono** split: all numerics, tickers, dates, and codes are monospace.
- The ARMED watchlist mini-bars (DIV / BRK / FF / RVOL / BLK) and WATCH/ARMED row states.
