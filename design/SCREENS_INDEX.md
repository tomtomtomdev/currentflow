# Screenshots — visual fidelity targets

High-quality reference captures of every screen, rendered at the prototype's native 1280×800. **These are the pixel targets** — match layout, spacing, color, and type against them. Open `IDX Flow Terminal.dc.html` in a browser for the live, interactive version.

## Session gate (§9.1 · `SCREENS_login.md`)
- `screens/01-login-credentials.png` — Card state 1: username + password, reCAPTCHA-invisible note, "Paste a session Bearer instead" fallback link.
- `screens/02-login-otp.png` — Card state 2: OTP challenge, channel picker (Email / WhatsApp / SMS), resend + loop-to-next-challenge behavior.

## Terminal — shell + 8 modules (`SCREENS_terminal.md`)
Every terminal shot shows the full shell: top status bar (as-of stamp, RULE-B banner, IHSG/Track chip, authed operator control), left module nav rail, main module pane, right ARMED watchlist, bottom disclaimer ticker.

- `screens/03-broker-flow.png` — Broker Flow Analyzer. Net buy/sell, DNA classification, concentration (HHI), veto checks. OBSERVATION badge.
- `screens/04-foreign-flow.png` — Foreign Flow.
- `screens/05-accumulation-detector.png` — Wyckoff phase classifier (RULE A gate — only Phase C/D tradeable).
- `screens/06-money-replay.png` — as-of audit replay with scrubber (look-ahead control).
- `screens/07-smart-heatmap.png` — Smart Heatmap.
- `screens/08-sector-rotation.png` — Sector Rotation (Track B / SMC index — never IHSG headline).
- `screens/09-risk-monitor.png` — Risk Monitor.
- `screens/10-sms-rank.png` — SMS / Rank. **RULE B in force:** "GATED · number withheld", per-module validation bar (1.4 / 3 months), flow-ranked ordering framed as observation, no score number rendered.

## What to reproduce exactly
- The **RULE-B gating states** — badges ("OBSERVATION · ships now" vs "GATED · number withheld"), the amber validation progress bar, and the fact that no score/probability number appears on gated modules.
- The **no-shadow, layered-background + hairline-border** depth model.
- **Geist / Geist Mono** split: all numerics, tickers, dates, and codes are monospace.
- The ARMED watchlist mini-bars (DIV / BRK / FF / RVOL / BLK) and WATCH/ARMED row states.
