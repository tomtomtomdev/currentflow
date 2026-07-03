# Handoff: In-app Login / Session Gate (VectorLab · IDX Flow Terminal)

## Overview
This is the **login / session gate** for the VectorLab *IDX Smart-Money Flow Terminal* — the screen that stands between `./run.sh` → browser and the terminal modules. It implements **Slice 11 of `PLAN.md`** and **§9.1 of `LOCKED_SPEC.md` (v1.2)**: an **in-app username/password + MFA (OTP) login flow** that establishes the operator's *own* authenticated Stockbit session, replacing the earlier hand-pasted Bearer as the primary auth surface (Bearer-paste is kept only as an honest fallback).

On load the shell reads session status (Keychain, no network). No valid session → render the **login flow instead of the modules** (fail loud, never a blank/stale terminal). Valid session → render the terminal, with a masked account status + sign-out in the top bar. A mid-session `401` returns the operator to the login flow.

**Scope guardrail (important):** this is **auth plumbing only**. It establishes the operator's own session and gates *nothing* about the analytics — no signal, number, or RULE A / RULE B behavior changes. Gated modules stay server-authoritative via the validation ledger regardless of login.

## About the Design Files
The file in this bundle (`IDX Flow Terminal.dc.html`) is a **design reference created in HTML** — a working prototype that shows the intended look and behavior, **not production code to copy directly**. It is authored as a "Design Component" (`.dc.html`: an inline `<x-dc>` template + a `class Component extends DCLogic` logic block) specific to the design tool; that format is **not** a production framework.

The task is to **recreate this login flow in the target codebase's environment**. Per `LOCKED_SPEC.md §10` the stack is **local-first Python: Streamlit UI**, so the production target is `ui/login_view.py` (a Streamlit view driven by a pure view-model state machine) over the existing `dal/auth.py` + `dal/token_store.py` + `dal/session.py` from slices 10–11. If you are instead building a React/other front-end over a local API, treat this HTML as the visual/interaction target and rebuild with your stack's components. **Do not ship the HTML.**

All auth in the prototype is **simulated** (seeded/mock, `setTimeout` in place of network calls). Wire the real endpoints per `DATA_SOURCES.md §4.1`.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, states, and interaction logic are specified. Recreate pixel-accurately using the target stack. It shares the terminal's design tokens exactly (see Design Tokens) — reuse them, do not introduce new ones.

Design canvas: the login fills the viewport (`100vh`, `min-height:760px`), desktop-first (matches the terminal shell, 1280×800 reference). Usable down to ~900px; not a mobile design.

---

## The verified wire contract (what the UI drives)
Source: `DATA_SOURCES.md §4.1` (HAR `login-stockbit.har`, 2026-07-03). All `POST … application/json` to `https://exodus.stockbit.com`. The UI is a thin driver over this 5-step flow:

1. `POST /login/v6/username` — req `{ user, password, recaptcha_token, recaptcha_version:"RECAPTCHA_VERSION_3", player_id }`. New-device / MFA branch → `data.new_device.multi_factor.{ login_token, verification_token }` (both 36-char). *(Trusted-device path returning `access`/`refresh` directly was NOT captured — guard for it, don't assume.)*
2. `POST /mfa/verification/v1/challenge/start` — req `{ verification_token }` → `data.next_challenge` (e.g. `CHALLENGE_OTP`), `data.supporting_data.otp.{ channels:[{channel,target}], default_channel }`. Channels seen: `CHANNEL_EMAIL`, `CHANNEL_WHATSAPP`, `CHANNEL_SMS`; `target` is masked (`tom****@gmail.com`).
3. `POST /mfa/verification/v1/challenge/otp/send` — req `{ verification_token, channel }` → `data.{ channel, target, next_attempt_in:60 }` (resend cooldown, seconds).
4. `POST /mfa/verification/v1/challenge/otp/verify` — req `{ verification_token, otp }` (6-digit) → `data.next_challenge`. **This LOOPS**: a verify can return another `CHALLENGE_OTP` with a *new* channel set before finally returning `CHALLENGE_FINISH`. Drive: repeat send→verify until `next_challenge == CHALLENGE_FINISH`.
5. `POST /login/v6/new-device/verify` — req `{ multi_factor:{ login_token } }` → `data.access.{ token, expired_at }`, `data.refresh.{ token, expired_at }`, `data.user.{ id, username, email, … }`. Lifetimes ≈ access 24h / refresh 7d.

**Open items — do NOT guess in code (probe first, per PLAN Slice 11):**
- **`recaptcha_token`** is reCAPTCHA **v3 (invisible)** — silently browser-minted, no user challenge shown. The question is **server enforcement**, not UX. Probe `login/v6/username` with the token omitted/empty/junk. Not enforced → pure-Python login works. Enforced → pick (a) headless browser `grecaptcha.execute` (heavy dep), (b) operator-assisted token paste, or (c) fall back to Bearer-paste (this prototype keeps that path). Pin the decision into §9.1 before coding `dal/auth.login`.
- **`player_id`** (OneSignal UUID) — required / arbitrary-UUID / omittable is unconfirmed; probe alongside recaptcha.
- **Refresh endpoint** route/shape unconfirmed (not exercised in the HAR) — leave `dal/auth.refresh` raising until captured; until then a 401 falls loud back to the login form.

The prototype models steps 1–5 as a client state machine: **CREDENTIALS → OTP (looping) → FINISH**. The OTP loop is deliberately shown (round 1 = email; round 2 = WhatsApp/SMS) to mirror step 4.

---

## Screens / Views
There is one route (the session gate) with **three mutually-exclusive card states** inside one shared shell. The shell persists across states.

### Shell (all states)
- **Root:** `display:flex; flex-direction:column; height:100vh; min-height:760px; background:#070a10; color:#e6edf3; font-family:'Geist'`.
- **Top bar** — height **52px**, `linear-gradient(180deg,#0d121b,#0a0e14)`, bottom border `1px solid rgba(255,255,255,0.07)`. Left→right: 26×26 logo mark (rounded 6px, `linear-gradient(135deg,#58c4dd,#3a8fb0)`, "V" Geist 700 `#04121a`), wordmark **VECTOR·LAB** (Geist 600 15px; the `·` is `#58c4dd`) + sub-label "IDX SMART-MONEY FLOW TERMINAL" (10px `#5a6675` letter-spacing 0.14em); spacer; a status line "Session gate — sign-in required" with a pulsing amber dot (`#d29922`, `livedot` 1.8s); a `LOCAL · SINGLE-USER · PAPER` chip (Geist Mono 9.5px `#5a6675`, left hairline divider).
- **Body** — `flex:1; display:flex`, a two-column split:
  - **Left (brand/posture)** — `flex:1`, vertically centered, padding `0 clamp(30px,5vw,76px)`, right border `1px solid rgba(255,255,255,0.06)`, subtle `radial-gradient(130% 100% at 10% 6%, rgba(88,196,221,0.07), transparent 58%)`.
  - **Right (auth card)** — `flex:0 0 clamp(380px,34%,468px)`, vertically centered, padding `0 clamp(26px,3vw,46px)`, background `#0a0e14`.
- **Disclaimer ticker** — height **26px**, `#0a0e14`, top border. Left `LOCAL · SINGLE-USER · PAPER` chip; right a horizontally-scrolling marquee (`tickscroll` 42s linear infinite) cycling the four §15 disclaimers.

### Left column content (all states)
- Eyebrow "SESSION GATE" — Geist Mono 11px, letter-spacing 0.22em, `#58c4dd`, margin-bottom 18px.
- Headline "Sign in to open the terminal." — Geist 600, `clamp(28px,3.4vw,42px)`, line-height 1.1, letter-spacing −0.01em, `max-width:15ch`.
- Sub-paragraph (13.5px `#8b98a9`, line-height 1.6, `max-width:52ch`, margin-top 18px): "Establish **your own authenticated Stockbit session** (`#c2ccd8` on the emphasized clause) — username, password, and a one-time code. The resulting session lives on this machine only; nothing is republished."
- Three posture rows (gap 14px, margin-top 34px, `max-width:56ch`). Each: an 18×18 rounded-5px cyan check chip (`bg rgba(88,196,221,0.12)`, border `rgba(88,196,221,0.3)`, `#58c4dd`, "✓") + text (12.5px `#c2ccd8`, bold lead-in in `#e6edf3`):
  1. **Credentialed sign-in.** Your own Stockbit login drives the verified `login/v6` flow — no hand-pasted token.
  2. **Multi-factor by OTP.** A one-time code via email, WhatsApp, or SMS; the challenge can loop across channels before it clears.
  3. **Keychain-backed session.** Access + refresh tokens held in the OS Keychain, read fresh, never written in plaintext (§10). Auth only — it gates no signal or RULE A/B behaviour.
- **RULE B pill** (margin-top 34px, inline-flex, padding 6px 12px, border `1px solid rgba(210,153,34,0.32)`, bg `rgba(210,153,34,0.08)`, `#e8c168`, letter-spacing 0.03em): mono-bold "RULE B" · "Observation-only — scores stay gated until paper-validated".

### Auth card container (all states)
`background:#0d121b; border:1px solid rgba(255,255,255,0.07); border-radius:12px; padding:26px 24px;` followed by a footer note (10px `#5a6675`, line-height 1.6, margin-top 16px): "On success, access + refresh tokens are written to the Keychain and the terminal reruns. Credentials and the one-time code are held only for this attempt — never persisted, rendered back, or logged."

**Shared input style** (username, password, bearer): `width:100%; box-sizing:border-box; background:#070a10; border:1px solid rgba(255,255,255,0.12); border-radius:8px; padding:12px 13px; color:#e6edf3; font-family:'Geist Mono'; font-size:12.5px; letter-spacing:0.04em; outline:none;` — **focus:** `border-color:rgba(88,196,221,0.6); box-shadow:0 0 0 1px rgba(88,196,221,0.3);`.

**Shared primary button** (`btn`): `width:100%; margin-top:18px; padding:12px 14px; border-radius:8px; border:none; text-align:center; font-family:'Geist'; font-weight:600; font-size:13px; letter-spacing:0.02em; color:#04121a; background:#58c4dd;` — **busy/disabled:** `background:#3a8fb0; opacity:0.8; cursor:default;`.

**Section labels** (USERNAME / PASSWORD / SEND CODE VIA / 6-DIGIT CODE / SESSION BEARER): Geist Mono 10px, letter-spacing 0.1em, `#5a6675`.

Field error box (credentials + OTP + bearer): margin-top 14px, padding 10px 12px, radius 8px, bg `rgba(248,81,73,0.10)`, border `rgba(248,81,73,0.32)`; a `#f85149` "✕" + message text 11.5px `#f6a9a4`.

### State A — CREDENTIALS (default)
- Header row: title "Operator sign-in" (Geist 600 16px) + right badge "STEP 1 · 2" (Geist Mono 9px `#5a6675`). Sub: "Use your own Stockbit credentials." (11.5px `#8b98a9`).
- **USERNAME** label → text input (placeholder "username or email").
- **PASSWORD** label (margin-top 14px) → password input (placeholder "••••••••").
- Error box (conditional).
- Primary button — label "Sign in" / busy "Signing in…".
- reCAPTCHA note (margin-top 14px, 10px `#5a6675`): "⛨ Protected by reCAPTCHA — runs invisibly (v3). No challenge is shown."
- Divider + fallback link (margin-top 15px, top hairline): "Prefer a token? **Paste a session Bearer instead →**" (`#58c4dd`, pointer) → switches card to State C.

### State B — OTP CHALLENGE
- Header row: a back arrow "←" (`#8b98a9`, 17px, pointer → returns to State A / "different account"), title "Verify it's you" (Geist 600 16px) + sub "One-time code · multi-factor"; right badge "STEP 2 · 2".
- **Loop note** (conditional, only on round ≥ 2): amber box (bg `rgba(210,153,34,0.09)`, border `rgba(210,153,34,0.3)`) with "↻" + "Additional verification required — a fresh code was sent via {channel}." (`#e8c168`).
- **SEND CODE VIA** — a row of channel chips (`display:flex; gap:8px`), one per available channel for the current round. Chip: column flex, padding 10px 6px, radius 8px, a 15px glyph + 10px label. **Selected:** bg `rgba(88,196,221,0.10)`, border `rgba(88,196,221,0.5)`, `#58c4dd`. **Unselected:** bg `#070a10`, border `rgba(255,255,255,0.10)`, `#8b98a9`. Clicking a chip (re)sends to that channel and restarts the cooldown. Glyphs: Email `✉`, WhatsApp `✆`, SMS `▤`.
  - Round 1 channels: Email (default) · WhatsApp · SMS. Round 2 channels: WhatsApp (default) · SMS.
- Sent note (margin-top 11px, 11px `#8b98a9`): "Code sent to {maskedTarget} via {channel}." Targets are masked: email `tom****@gmail.com`, phone `+62 ***-***-4821`.
- **6-DIGIT CODE** → numeric input: same base input style but `font-size:22px; font-weight:600; letter-spacing:0.5em; text-align:center;`, placeholder "••••••". Input is sanitized to digits, max length 6 (`inputmode="numeric"`, `autocomplete="one-time-code"`).
- Error box (conditional).
- Primary button — "Verify code" / busy "Verifying…".
- Footer row (margin-top 15px, space-between): **Resend** control (11px; enabled `#58c4dd` pointer showing "Resend code"; during cooldown `#5a6675` non-interactive showing "Resend in {n}s", counting 60→0) and a right caption "code expires shortly" (10px `#5a6675`).

### State C — SESSION BEARER (fallback)
- Header: back arrow "←" (→ State A), title "Paste a session Bearer" + sub "Fallback — advanced (§10)".
- **SESSION BEARER** label row with right caption "password-type · masked" → password input (placeholder "Bearer eyJhbGciOi… — paste session token"). Helper (10px `#5a6675`): "A leading `Bearer ` prefix is stripped automatically."
- Error box (conditional).
- Primary button — "Verify & open terminal" / busy "Verifying with live ping…".
- Ping status row (margin-top 14px): a 7px status dot + message. Dot/message: idle → green `#3fb950` "The Bearer is verified with a live ping before it is accepted."; busy → amber `#d29922` "Pinging session endpoint — one short authenticated request…"; error → red `#f85149` "Rejected in-browser — token was not written to the Keychain."

### Authenticated top-bar control (terminal, post-login)
When authed, the terminal's top bar gains (right side, after a hairline divider): a status group — green dot + account username (`#c2ccd8`) + "·" + masked token (`····a1f9`, Geist Mono `#8b98a9`) — and a **Sign out** button (11px `#e6edf3`, padding 5px 11px, border `1px solid rgba(255,255,255,0.12)`, radius 6px; hover: bg `rgba(255,255,255,0.06)`, border `rgba(255,255,255,0.22)`). Sign out clears the Keychain session and returns to State A.

---

## Interactions & Behavior
- **CREDENTIALS submit** (button or Enter in either field): empty username/password → inline error, no request. Otherwise POST `login/v6/username`. On the new-device branch → advance to OTP (call `challenge/start`, auto-send to the default channel, start the 60s cooldown). On bad creds → `AuthError`, inline "Sign-in failed — check your username and password.", **store nothing**. *(Prototype simulation: rejects if password < 6 chars or the input contains "wrong"/"invalid"/"fail"; ~1.1s fake latency.)*
- **Channel select:** clicking a chip calls `otp/send` for that channel and restarts the cooldown from `next_attempt_in` (60s). No-op if already selected or while verifying.
- **Resend:** disabled during cooldown (shows countdown); when enabled, re-sends and restarts cooldown.
- **OTP submit** (button or Enter): must be exactly 6 digits (else inline "Enter the 6-digit code."). POST `otp/verify`. If response `next_challenge == CHALLENGE_OTP` → **loop**: swap to the new channel set, show the amber loop note, clear the code, restart cooldown. If `CHALLENGE_FINISH` → POST `new-device/verify`, store `{access, refresh}` in the Keychain, rerun into the terminal. Wrong code → `AuthError`, inline "Incorrect code — try again or resend." *(Prototype simulation: rejects `000000`; ~1.2s fake latency; two rounds hard-coded to demonstrate the loop.)*
- **Back arrow (OTP → CREDENTIALS):** clears the code + cooldown, returns to State A.
- **Fallback link / back (CREDENTIALS ↔ BEARER):** swaps card state; clears transient errors.
- **BEARER submit:** empty → inline prompt. Otherwise strip a leading `Bearer ` (case-insensitive), verify with a live ping; invalid/expired → inline 401 error, store nothing; valid → store + open terminal. *(Prototype simulation: rejects tokens < 24 chars or containing "expired"/"invalid"/"401".)*
- **Sign out:** clears the stored session and all transient auth state, returns to State A.
- **Mid-session 401 (production):** DAL surfaces 401 → attempt refresh (once the refresh route is confirmed); on refresh failure, return to State A. Never a silent stale/empty fallback.
- **Animations:** `livedot` (1.8s opacity pulse on the top-bar status dot), `tickscroll` (42s linear marquee), `armedpulse` (terminal only). Card state changes are instant swaps (no cross-fade in the prototype).
- **Security posture:** password, OTP, recaptcha, and token bodies are **never** persisted to component state, rendered back, or logged. Inputs are read transiently (held outside React state in the prototype; hold them only for the in-flight attempt in production). Password + Bearer fields are `type="password"`; the masked token preview shows only the last 4 chars.

## State Management
Production view-model (mirror the prototype's state machine — keep it pure so it's unit-testable without the Streamlit runtime):
- `authed: bool` — false → gate, true → terminal (seeded from Keychain session status on load).
- `authMode: 'login' | 'bearer'` — primary flow vs. fallback.
- `authStep: 'credentials' | 'otp'` — step within the login flow.
- `authRound: int` — OTP challenge index; drives channel set + loop note (mirrors repeated `CHALLENGE_OTP`).
- `selChannel: channel id` — currently selected OTP channel.
- `resendIn: int` — cooldown seconds counting to 0 (from `next_attempt_in`).
- `verifying: bool` — in-flight request → busy button + disabled controls.
- `credError | otpError | authError: string | null` — per-surface inline errors.
- `username / tokenPreview` — for the authed top-bar status (masked; last-4 only).
- **Transient, non-state:** raw username, password, OTP, and Bearer are held only for the in-flight attempt — never in durable state, never logged.
- **Data:** the real feeds are the §4.1 endpoints (injected transport, like the slice-10 tests). `access`/`refresh` (+ expiries) persist as one JSON blob in the OS Keychain via the extended `dal/token_store.py`; read fresh per request.

## Design Tokens
**Colors** — Background `#070a10` (app), `#0a0e14` (rails/auth column + ticker), `#0d121b` (cards). Borders `rgba(255,255,255,0.06)`/`0.07` (panels/bars), `rgba(255,255,255,0.10)`/`0.12` (inputs/chips). Text `#e6edf3` (primary), `#c2ccd8` (secondary), `#8b98a9` (muted), `#5a6675` (faint). Accent/brand `#58c4dd` (focus, links, selected chip; hover fill `#3a8fb0`, deep `#04121a` for on-accent text). Caution/RULE-B `#d29922` (accent text `#e8c168`, tint `rgba(210,153,34,0.08–0.32)`). Error `#f85149` (text `#f6a9a4`, tint `rgba(248,81,73,0.10)`, border `rgba(248,81,73,0.32)`). Success `#3fb950`.
**Typography** — UI **Geist** (400/500/600/700); numerics/codes/labels/inputs **Geist Mono** (400/500/600) from Google Fonts. Scale: headline `clamp(28,3.4vw,42)`/600; card title 16/600; body 11.5–13.5; labels 9–10 with letter-spacing 0.08–0.1em; OTP input 22/600 letter-spacing 0.5em.
**Spacing / shape** — card radius 12px; inputs/buttons/chips/error boxes radius 8px; pill/RULE-B radius 6px; card padding 26px 24px; input padding 12–13px; button padding 12px 14px. Right column width `clamp(380px,34%,468px)`.
**Shadows** — none; depth is layered backgrounds + hairline borders. Focus ring is a 1px `box-shadow` in accent.
**Animations** — `livedot` 1.8s, `tickscroll` 42s, `armedpulse` 1.8s (see the `<style>` in `<helmet>`).

## Assets
No external images or icon fonts. All glyphs are Unicode: `✓ ✉ ✆ ▤ ⛨ ↻ ← ✕ ·`. Fonts load from Google Fonts (Geist, Geist Mono). In production, swap Unicode glyphs for the codebase's icon set.

## Files
- `IDX Flow Terminal.dc.html` — the full prototype. The login/session gate lives in the `<sc-if value="{{ showLogin }}">` block near the top of the `<x-dc>` template (the three card states are nested `<sc-if>` on `showCredentials` / `showOtp` / `showBearer`); the terminal is the sibling `<sc-if value="{{ authed }}">`. The auth logic is the `// session gate — in-app credential + MFA login` section of the `class Component extends DCLogic` block (`OTP_ROUNDS`, `submitCredentials`, `beginOtpRound`, `selectChannel`, `startCooldown`, `resendOtp`, `verifyOtp`, `submitToken`, `signOut`) plus the `authVals` object in `renderVals()`. Read for layout, styling, copy, state shape, and interaction logic only — do not ship it.

## Reference (authoritative — governs this UI)
- `LOCKED_SPEC.md §9.1` (session gate), `§10`/`§15` (posture/disclaimers) — the spec the UI serves (v1.2 bump is auth-posture only; no LD/weight/gate change).
- `DATA_SOURCES.md §4.1` (verified `login/v6` + `mfa/verification/v1` wire contract), `§4` (auth token lifecycle, operational constraints).
- `PLAN.md` Slice 11 — the DAL/session/UI/test checklist this implements, including the reCAPTCHA-enforcement probe that decides the login approach (do this **first**).
