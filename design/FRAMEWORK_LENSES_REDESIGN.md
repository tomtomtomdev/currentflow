# Framework Lenses — redesign spec (implementation reference)

Supersedes the *composition* of `HANDOFF_v2.md` §Screens 6. **Tokens, states, copy and rules are
unchanged** — every colour, font size and radius below is already in `STYLE_GUIDE.md` or the
current sheet. What changed is layout: the switcher, state banding, the cross-lens strip, and the
Confluence row's RULE A treatment.

Files:

| file | what it is |
|---|---|
| `handoff/framework-lenses-redesign.html` | all six sections + switcher, rendered with the final class names. The second `<style>` block is the sheet to paste. |
| `Framework Lenses.dc.html` | the interactive prototype (switcher works, three tweaks) |
| this file | the written spec |

Placement is unchanged: **full width, no ARMED watchlist rail**, reached by `▸ Framework Lenses`
under the pipeline, `‹ Pipeline` returns. Default section `wyckoff`.

---

## 1. What changed, and why

| # | change | reason |
|---|---|---|
| 1 | Six plain buttons → six **tab cards** (`.cf-lenstab`): lens code chip + name + a mono tally `2 flagged · 0 unread`. Active card gets accent fill + a 2px accent top rule. | Section identity was weak; the tally makes the switcher itself a census of the day. |
| 2 | Rows are grouped under **state bands** (`.cf-lensband`) — `◉ FLAGGED · 2 names`. The per-row state cell keeps the mark + categorical tag but drops the repeated state word. | Scanning 40+ rows. Grouping is the spec's existing sort order made visible; it asserts nothing about which name is better. |
| 3 | Bands render **at zero** with `no name in this state today — stated, not omitted`. | Same principle as the always-printed unread count. |
| 4 | `N/A` and `UNREAD` rows carry `opacity:0.72` (`.is-dim`). | De-emphasis without collapsing — the five states stay five. |
| 5 | Variable cross-lens chips → a **fixed five-slot strip** (`WYK · VP · VPA · BND · MF`) at constant x-positions. Filled = flagged, hollow = not flagged, dashed = the lens you are in. | Set membership becomes column-scannable. Slots are categorical and fixed-width — no proportion, no fill, no count implied (see §5). |
| 6 | Confluence: the RULE A line becomes the **loudest element** — its own tinted cell, `#f85149`, 11px mono, plus a red hairline on the symbol cell; the agreement count drops to `#8b98a9`. | The `PTRO` case. Agreement must never out-shout the gate. |
| 7 | A persistent **read-state key** (`.cf-lenskey`) sits above every section. | "unread" must never be read as "found nothing". |
| 8 | Column captions row (`.cf-lenscols`): `SYMBOL · THIS LENS READS · IN ITS OWN VOCABULARY · ALSO FLAGGED BY · ENGINE`. | The four columns were unlabelled. |

Removed: nothing. Every datum on the current surface is still present.

---

## 2. Grid & dimensions

```
.cf-lenscols / .cf-lensrow
grid-template-columns: 104px 138px 1fr 214px;   gap: 9px;   margin-bottom: 7px
```
(was `120px 132px 1fr 150px` — col 1 narrows because the state word left it; col 4 widens to hold
the five-slot strip.)

Cell padding `9px 11px`, radius 8, `display:flex; flex-direction:column; justify-content:center`.
Compact density (optional): padding `6px 10px`, row gap `4px`, band margin-top `8px`.

Switcher: `.cf-lensbar` flex, gap 7, each `.cf-lenstab { flex:1; padding:9px 11px; radius 8 }`.
Band: flex, gap 9, `margin:12px 0 6px`, mark chip `18×18` radius 4, then a `flex:1` hairline rule.

---

## 3. Tokens (all pre-existing)

States — `_LENS_STATE_STYLE`, unchanged, and still deliberately not the pipeline palette:

| state | class | mark | fg | bg | border |
|---|---|---|---|---|---|
| FLAGGED | `st-flagged` | `◉` | `#7ee08a` | `rgba(63,185,80,0.06)` | `rgba(63,185,80,0.26)` |
| CONTRARY | `st-contrary` | `◐` | `#f6a9a4` | `rgba(248,81,73,0.06)` | `rgba(248,81,73,0.28)` |
| NEUTRAL | `st-neutral` | `○` | `#8fdcec` | `rgba(88,196,221,0.05)` | `rgba(88,196,221,0.2)` |
| NOT_APPLICABLE | `st-na` | `—` | `#8b98a9` | `rgba(255,255,255,0.02)` | `rgba(255,255,255,0.07)` |
| UNAVAILABLE | `st-unread` | `·` | `#5a6675` | `rgba(255,255,255,0.015)` | `rgba(255,255,255,0.05)` |

Strip slots: on `bg rgba(88,196,221,0.12)` / `border rgba(88,196,221,0.3)` / `fg #58c4dd`;
self `bg rgba(255,255,255,0.05)` / `1px dashed rgba(255,255,255,0.16)` / `#8b98a9`;
off `transparent` / `1px solid rgba(255,255,255,0.05)` / `#2f3846`. Mono 8px, `0.04em`, radius 4.

Engine verdict colours: `ARMED #e8c168` · `WATCH #8fdcec` · `GATE_REJECTED` / `VETOED #8b98a9`
(muted on purpose — the verdict is stated, not celebrated).

RULE A line: tradeable `#8b98a9` 10px; **not tradeable `#f85149` 11px/600**, cell
`bg rgba(248,81,73,0.07)`, `border rgba(248,81,73,0.42)`, symbol-cell border `rgba(248,81,73,0.32)`.

Type: Geist for prose; **Geist Mono for every ticker, tag, count, state label, verdict and caps
label**. Fallback `ui-monospace` / system sans must still read — nothing depends on Geist metrics.

---

## 4. DOM per row

```html
<div class="cf-lensrow is-dim?">
  <div class="cf-lenscand">
    <div class="cf-candtick">BRMS</div><div class="cf-candmeta">Track B</div></div>
  <div class="cf-lensstate st-flagged">
    <div class="cf-cellhead"><span class="cf-lensmark">◉</span></div>
    <div class="cf-lenstag">PHASE C</div></div>
  <div class="cf-lensbody">
    <div class="cf-lensdetail">…the framework's own sentence…</div>
    <div class="cf-lensnote">…optional caveat that must travel with the read…</div></div>
  <div class="cf-lensside">
    <div class="cf-lensstrip">
      <span class="cf-lensslot is-self">WYK</span><span class="cf-lensslot is-on">VP</span>
      <span class="cf-lensslot is-off">VPA</span><span class="cf-lensslot is-on">BND</span>
      <span class="cf-lensslot is-off">MF</span></div>
    <div class="cf-lensverdict">pipeline: <span class="cf-vd-armed">ARMED</span></div></div>
</div>
```

Confluence row: same grid; col 2 `.cf-conflcount` (+ `.is-muted` when not tradeable) + `of 5 lenses`
+ tag `FRAMEWORKS AGREEING`; col 3 strip + `.cf-conflnote`; col 4 `.cf-rulea` (+ `.is-not`) then
`pipeline:`. Row gets `.is-untradeable`, side cell gets `.is-untradeable`.

Order within a section: `FLAGGED → CONTRARY → NEUTRAL → N/A → UNREAD`, then ticker A–Z. Bands
render in that fixed order whether or not they hold rows.

---

## 5. Rule compliance (check these before merging)

- **RULE B — digits.** The only numerals on the surface are: band counts (`2 names`), the census
  string, the switcher tally, and the Confluence set size. No score, rank, probability, percentage.
  (Percentages inside a framework's own sentence — `MF rank 88%`, `71% of net buying` — are the
  framework's verbatim read and were already shipping; they are not a system claim.)
- **The set size is not a meter.** No bar, ring, gauge, `3/5` dial. The five strip slots are
  fixed-width, fixed-position, *labelled* categories — a name flagged by 1 lens and a name flagged
  by 4 produce the same geometry, only different slots. Do not sort rows by slot count and do not
  make slot width proportional.
- **RULE A.** Every lens row prints `pipeline: {ENGINE_STATE}`; every Confluence row prints the
  explicit RULE A line, and `not tradeable` is the highest-contrast element in the row.
- **Five states, never two.** All five bands always render; empty ones say so; unread is never an
  empty cell and never merges into neutral.
- **No lens cell may look like a stage cell.** Glyph set `◉ ◐ ○ — ·`, no `✓ ✕ ▽ ⤶`, no pass/fail
  wording, tint alphas below the pipeline's.
- **No buy/sell verb** in any tag, sentence, note or band label.

---

## 6. Streamlit implementation notes

Everything is CSS grid/flex + static styles — no JS, no hover-reveal, no popovers.

1. Paste the second `<style>` block of `framework-lenses-redesign.html` into the existing global
   CSS injection in `currentflow/ui/shell.py`. It only adds `.cf-lens*` / `.st-*` rules.
2. `lens_row_html()` changes: drop the state-label span (bands own it), emit `st-{state}` on the
   state cell, emit `.cf-lensstrip` with one `<span>` per lens in `LENS_ORDER` — class `is-self`
   for the active section, `is-on` if the ticker is in that lens's flagged set, else `is-off`.
3. New `lens_band_html(state, rows)` — emit before each group; emit the empty line when `not rows`.
4. `confluence_row_html()` changes: `.is-untradeable` on the row + side cell when
   `engine_state != tradeable`, `.is-muted` on the count, `.cf-rulea.is-not` for the RULE A line.
5. Switcher: the six `st.columns(6)` buttons become six keyed containers, each holding
   `.cf-lenstab` markup plus a stretched invisible `st.button` — the same overlay pattern already
   used by `st-key-cfwatch-*` and `st-key-cfpipe-*`. Fallback if you'd rather not: keep native
   buttons and set the tally as the button's second line via `\n` in the label; the card styling is
   the only thing lost.
6. `.cf-lenskey` and `.cf-lenscols` are static strings — render once per page, above the section.

Interaction is unchanged: one state variable `cf_lens`, whole-page rerun per click, no other state
moves. A lens switch can never change a verdict.
