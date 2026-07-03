# Handoff: VectorLab — IDX Smart-Money Flow Terminal (+ Session Gate)

**This is the index. Read it first, then the referenced docs.** It is self-sufficient for orienting a developer who was not in the design conversation.

---

## What this is
A private, **single-operator flow terminal** for the Indonesia Stock Exchange (IDX). It surfaces broker/foreign "smart-money" flow as **observation — never advice** — and enforces two governing rules directly in the UI:

- **RULE A (Tradeability gate, LD-2):** only Wyckoff Accumulation Phase C/D is tradeable; the phase classifier gates *before* scoring.
- **RULE B (Presentation gate, LD-9):** no confidence number, probability, Smart Money Score, or ranked buy/sell claim may be **displayed** until that module has survived `PAPER_VALIDATION_MONTHS` (default 3) of fill-realistic forward paper trading. Until then the module shows raw observation (components, flows) with **no number attached**.

The app has two parts, both in the single prototype file:
1. **Session gate** — in-app username/password + MFA (OTP) login that establishes the operator's own Stockbit session (§9.1). Fails loud — no valid session renders the login flow instead of the terminal, never a blank/stale terminal.
2. **Terminal** — a dark, paned, keyboard-adjacent workbench: top status bar, left module nav rail, main module pane, right ARMED watchlist, bottom disclaimer ticker. Eight modules (Broker Flow, Foreign Flow, Accumulation Detector, Money Replay, Smart Heatmap, Sector Rotation, Risk Monitor, SMS / Rank).

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, chart layouts, states, and interaction logic are all specified in the screen docs. Recreate pixel-accurately using the target stack's libraries.

## About the design files — do NOT ship the HTML
`IDX Flow Terminal.dc.html` is a **design reference created in HTML** — a working prototype of the intended look and behavior, **not production code to copy**. It is authored as a "Design Component" (`.dc.html`: an inline `<x-dc>` template + a `class Component extends DCLogic` logic block) specific to the design tool; that format is **not** a production framework. Read it for layout, styling, copy, data shape, and interaction logic only.

Per `LOCKED_SPEC.md §10` the production target is a **local-first Python stack: Streamlit UI, DuckDB/SQLite store, Pandas/Polars + TA-Lib analytics.** Rebuild the prototype as Streamlit views (or a small React front-end over a local API) using the existing DAL from the earlier slices. If no front-end exists yet, pick the most appropriate framework for a single-user local tool. **All numbers/auth in the prototype are simulated** (seeded deterministic mock data; `setTimeout` in place of network) — wire real feeds per `DATA_SOURCES.md` and the spec.

---

## Files in this bundle
- **`IDX Flow Terminal.dc.html`** — the full, current prototype: session gate + all 8 terminal modules, RULE A/B enforcement, seeded mock data. The single source of visual/interaction truth.
- **`screens/` + `SCREENS_INDEX.md`** — high-res 1280×800 captures of every screen (2 login states + shell + 8 modules). **These are the pixel-fidelity targets** — build against them.
- **`LOCKED_SPEC.md`** (v1.2) — the **authoritative specification. This governs; the UI serves it.** Priority sections: §0 (RULE A/B), §4 (SMS weights, internal-until-validated), §5 (veto filters), §6 (entry/sizing/risk), §9 (terminal modules & gating tiers; §9.1 session gate), §10 (local-first stack), §13 (acceptance criteria), §15 (disclaimers).
- **`DATA_SOURCES.md`** — real feed contracts. §4.1 is the **verified `login/v6` + `mfa/verification/v1` wire contract** the session gate drives (from HAR capture). Also covers auth token lifecycle and feed constraints.
- **`PLAN.md`** — implementation slices. **Slice 11** is the session-gate DAL/UI/test checklist, including the reCAPTCHA-enforcement probe that decides the login approach — **do that probe first.**
- **`SCREENS_terminal.md`** — detailed per-screen spec for the shell + all 8 terminal modules (layout, components, exact tokens, interactions, state, ticker/broker reference data).
- **`SCREENS_login.md`** — detailed spec for the session gate: the 3 card states (Credentials → OTP loop → Bearer fallback), the wire contract mapping, states, and the authenticated top-bar control.

## Suggested reading order for implementation
1. This README → `LOCKED_SPEC.md` §0, §9, §10 (understand the rules and the stack).
2. `PLAN.md` — find the slice you're building.
3. **Auth work:** `SCREENS_login.md` + `DATA_SOURCES.md §4.1` + `PLAN.md` Slice 11 (run the reCAPTCHA/player_id probe before writing `dal/auth.login`).
4. **Terminal work:** `SCREENS_terminal.md` + the relevant `LOCKED_SPEC.md` sections per module.

## Non-negotiable implementation constraints
- **RULE B is a hard constraint, not decoration.** No module renders a score/probability/buy-sell verb until its per-module validation state clears `PAPER_VALIDATION_MONTHS`. Keep the observation↔claim state **server-authoritative** (promoted by the paper-trade engine / validation ledger), not a client toggle.
- **RULE A gates before scoring** — the phase classifier must reject non-C/D candidates before SMS is computed (§13 test).
- **Look-ahead control:** every datum is `as_of`-stamped; a signal may only use data with `availability_ts < decision_ts`. The Replay module is the audit tool that reconstructs any past signal from stored `as_of` data.
- **Session gate is auth plumbing only** — it establishes the operator's own session and gates **nothing** about the analytics (no signal / RULE A/B behavior changes with login).
- **Never IHSG as headline benchmark** — Track A → LQ45, Track B → sector/SMC index (§8).
- **Fee-aware, IDX-aware paper broker** (§12): 100-share lots, ARA/ARB reject bands, full fee stack incl. 0.1% sell tax, next-open fills with slippage.
- Embed the §15 disclaimers in-app (the prototype runs them in the bottom ticker).

## Design tokens (shared across both parts — reuse, don't reinvent)
See `SCREENS_terminal.md` and `SCREENS_login.md` for the full lists. In brief: backgrounds `#070a10` / `#0a0e14` / `#0d121b`; text `#e6edf3` / `#c2ccd8` / `#8b98a9` / `#5a6675`; brand accent `#58c4dd`; buy `#3fb950`, sell `#f85149`, caution/RULE-B `#d29922` (text `#e8c168`), divergence `#bc8cff`. Type: **Geist** (UI) + **Geist Mono** (all numerics/codes/dates). No shadows — depth is layered backgrounds + hairline borders. No external images/icons — all glyphs are Unicode; swap for the codebase's icon set in production.
