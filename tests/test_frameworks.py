"""Framework lenses — the five source frameworks read APART (observation layer).

The pipeline fuses them into the locked §2 gate chain; this layer reads each one on its
own terms and aggregates the overlap. The tests that matter here are the boundaries:

  RULE A — a lens is a pure read over an already-decided `EngineResult`. It cannot arm a
  name, cannot reopen the C/D gate, and a name RULE A rejected still appears under every
  other framework that saw something (that is the whole point of the split).
  RULE B — categories and sentences only; the sole digits are counts of symbols. The
  confluence count is a SET SIZE, never a score, and never feeds §4.
  missing ≠ zero — UNAVAILABLE (could not read) is distinct from NEUTRAL (read, named
  nothing) and from NOT_APPLICABLE (deliberately abstains).
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from html import unescape

from builders import (
    Chart,
    brow,
    distribution_bars,
    phase_b_bars,
    strong_phase_c_bars,
    two_buyer_rows,
)

from currentflow.dal.models import InvestorType, Scr4Row, Side
from currentflow.signals import engine, frameworks
from currentflow.signals.engine import EngineState
from currentflow.signals.frameworks import Lens, LensState
from currentflow.ui import lens_view, shell

TS = datetime(2026, 7, 1, 9, 0)
BDAYS = [Date(2026, 6, 24), Date(2026, 6, 25), Date(2026, 6, 26)]


def _scr4(symbol: str, *, mf: float | None = None, ev_ebit: float | None = 8.0,
          roe: float | None = None) -> Scr4Row:
    return Scr4Row(
        symbol=symbol, date=Date(2026, 6, 26), as_of=datetime(2026, 6, 27, 9, 0),
        mf_rank_pct=mf, roc_greenblatt=None, ev_ebit=ev_ebit, rank_roic=None,
        roe=roe, market_cap=None,
    )


def _armed(store, sym="STRONG", track="B"):
    store.write_daily_bars(strong_phase_c_bars(sym))
    store.write_broker_net(two_buyer_rows(sym, BDAYS))
    return engine.evaluate(store, sym, TS, track=track)


def _phase_rejected(store, sym="PHB"):
    store.write_daily_bars(phase_b_bars(sym))
    store.write_broker_net(two_buyer_rows(sym, BDAYS))
    return engine.evaluate(store, sym, TS, track="B")


def _distribution(store, sym="DIST"):
    store.write_daily_bars(distribution_bars(sym))
    store.write_broker_net(two_buyer_rows(sym, BDAYS))
    return engine.evaluate(store, sym, TS, track="B")


def _monopoly(store, sym="MONO"):
    """One broker takes > 60% of the net buying — the §5 single-bandar veto, which is the
    Bandarmology framework's own disqualifier."""
    store.write_daily_bars(strong_phase_c_bars(sym))
    rows = []
    for d in BDAYS:
        rows += [
            brow("DX", Side.BUY, 9e9, d, symbol=sym, investor=InvestorType.FOREIGN, avg_price=105),
            brow("KI", Side.BUY, 0.4e9, d, symbol=sym),
            brow("YP", Side.SELL, 8e9, d, symbol=sym),
        ]
    store.write_broker_net(rows)
    return engine.evaluate(store, sym, TS, track="B")


# --- shape ------------------------------------------------------------------------------


def test_every_symbol_gets_one_read_per_framework(store):
    read = frameworks.read_symbol(_armed(store))
    assert tuple(r.lens for r in read.reads) == frameworks.LENS_ORDER
    assert len(read.reads) == 5
    assert set(read.by_lens) == set(frameworks.LENS_ORDER)


def test_lens_meta_names_the_framework_and_its_source(store):
    for lens in frameworks.LENS_ORDER:
        meta = frameworks.LENS_META[lens]
        assert meta.framework and meta.scope and meta.source


# --- RULE A: the lens layer decides nothing ----------------------------------------------


def test_lens_read_is_pure_over_the_engine_result(store):
    result = _armed(store)
    before = (result.state, result.phase.phase, result.phase.tradeable, result.sms.internal_score)
    frameworks.read_symbol(result)
    frameworks.read_symbol(result, fundamentals=_scr4("STRONG", mf=90.0), sector="Energy")
    after = (result.state, result.phase.phase, result.phase.tradeable, result.sms.internal_score)
    assert before == after


def test_a_rule_a_rejected_name_is_still_read_by_the_other_frameworks(store):
    """The point of the split: the pipeline stops a non-C/D name at stage [2], but the
    broker footprint and the tape are still observable facts about it."""
    result = _phase_rejected(store)
    assert result.state is EngineState.GATE_REJECTED
    read = frameworks.read_symbol(result)

    assert read.tradeable is False
    assert read.engine_state == "GATE_REJECTED"
    assert read.read(Lens.WYCKOFF).state is not LensState.FLAGGED
    # the other four lenses ran regardless — none of them is skipped upstream
    for lens in (Lens.WYCKOFF_2, Lens.VPA, Lens.BANDARMOLOGY, Lens.MAGIC_FORMULA):
        assert read.read(lens).state in set(LensState)
    assert read.read(Lens.BANDARMOLOGY).state is not LensState.UNAVAILABLE


def test_framework_agreement_never_implies_tradeable(store):
    result = _phase_rejected(store)
    read = frameworks.read_symbol(result, fundamentals=_scr4("PHB", mf=95.0), sector="Energy")
    rows = lens_view.confluence_rows([read], minimum=1)
    assert rows and rows[0]["tradeable"] is False
    assert "RULE A" in rows[0]["note"]
    assert rows[0]["engine_state"] == "GATE_REJECTED"


# --- Wyckoff lens -------------------------------------------------------------------------


def test_wyckoff_lens_flags_phase_c_and_reads_distribution_contrary(store):
    flagged = frameworks.read_symbol(_armed(store)).read(Lens.WYCKOFF)
    assert flagged.state is LensState.FLAGGED
    assert flagged.tag.startswith("PHASE ")

    contrary = frameworks.read_symbol(_distribution(store)).read(Lens.WYCKOFF)
    assert contrary.state is LensState.CONTRARY


def test_wyckoff_lens_is_neutral_on_a_range_with_no_test(store):
    read = frameworks.read_symbol(_phase_rejected(store)).read(Lens.WYCKOFF)
    assert read.state is LensState.NEUTRAL
    assert read.tag == "PHASE B"


def test_wyckoff_lens_unread_with_no_bars(store):
    store.write_broker_net(two_buyer_rows("EMPTY", BDAYS))
    read = frameworks.read_symbol(engine.evaluate(store, "EMPTY", TS, track="B")).read(Lens.WYCKOFF)
    assert read.state is LensState.UNAVAILABLE
    assert "not absent" in read.detail


# --- Wyckoff 2.0 / VPA lenses -------------------------------------------------------------


def test_volume_profile_lens_carries_the_fidelity_caveat(store):
    read = frameworks.read_symbol(_armed(store)).read(Lens.WYCKOFF_2)
    assert read.state in (LensState.FLAGGED, LensState.CONTRARY, LensState.NEUTRAL)
    assert read.note  # the daily-bar approximation annotation travels with every read


def _absorption_bars(symbol: str):
    """An oscillating range that ends on heavy volume closing near its high but below the
    prior close, and NOT on a new low — Coulling's absorption."""
    ch = Chart(symbol).oscillate(40)
    ch.add(110, 112, 104, 111.5, 3000)
    return ch.bars


def test_vpa_lens_flags_the_absorption_archetype(store):
    store.write_daily_bars(_absorption_bars("ABS"))
    store.write_broker_net(two_buyer_rows("ABS", BDAYS))
    read = frameworks.read_symbol(engine.evaluate(store, "ABS", TS, track="B")).read(Lens.VPA)
    assert read.state is LensState.FLAGGED
    assert read.tag == "DEMAND SIDE"


def test_vpa_lens_reads_the_supply_side_as_contrary(store):
    """The ARMED archetype's flat high-volume cluster is churn — effort without result.
    The VPA lens must say so even while the pipeline arms the name: the frameworks are
    read apart precisely so they are allowed to disagree."""
    result = _armed(store)
    read = frameworks.read_symbol(result).read(Lens.VPA)
    assert read.state is LensState.CONTRARY
    assert result.state is EngineState.ARMED


def test_unreadable_tape_is_unavailable_not_neutral(store):
    """A lens that could not read the name must never look like a lens that read it and
    named nothing (missing ≠ zero)."""
    store.write_broker_net(two_buyer_rows("EMPTY2", BDAYS))
    read = frameworks.read_symbol(engine.evaluate(store, "EMPTY2", TS, track="B"))
    assert read.read(Lens.VPA).state is LensState.UNAVAILABLE
    assert read.read(Lens.WYCKOFF_2).state is LensState.UNAVAILABLE


# --- Bandarmology lens ---------------------------------------------------------------------


def test_bandarmology_lens_flags_a_persistent_accumulator(store):
    read = frameworks.read_symbol(_armed(store)).read(Lens.BANDARMOLOGY)
    assert read.state is LensState.FLAGGED
    assert read.tag == "PERSISTENT ACCUMULATOR"


def test_bandarmology_lens_reads_its_own_veto_as_contrary(store):
    result = _monopoly(store)
    assert result.state is EngineState.VETOED
    read = frameworks.read_symbol(result).read(Lens.BANDARMOLOGY)
    assert read.state is LensState.CONTRARY
    assert "SINGLE BANDAR" in read.tag


def test_bandarmology_lens_unread_with_no_broker_rows(store):
    store.write_daily_bars(strong_phase_c_bars("NOBROKER"))
    read = frameworks.read_symbol(
        engine.evaluate(store, "NOBROKER", TS, track="B")
    ).read(Lens.BANDARMOLOGY)
    assert read.state is LensState.UNAVAILABLE
    assert "not flat" in read.detail


# --- Magic Formula lens ---------------------------------------------------------------------


def test_magic_formula_lens_terciles(store):
    result = _armed(store)
    top = frameworks.read_symbol(result, fundamentals=_scr4("STRONG", mf=80.0), sector="Energy")
    mid = frameworks.read_symbol(result, fundamentals=_scr4("STRONG", mf=50.0), sector="Energy")
    low = frameworks.read_symbol(result, fundamentals=_scr4("STRONG", mf=10.0), sector="Energy")
    assert top.read(Lens.MAGIC_FORMULA).state is LensState.FLAGGED
    assert mid.read(Lens.MAGIC_FORMULA).state is LensState.NEUTRAL
    assert low.read(Lens.MAGIC_FORMULA).state is LensState.CONTRARY


def test_magic_formula_lens_is_unavailable_not_neutral_without_fundamentals(store):
    read = frameworks.read_symbol(_armed(store), sector="Energy").read(Lens.MAGIC_FORMULA)
    assert read.state is LensState.UNAVAILABLE
    assert "never assumed mid-tercile" in read.detail


def test_magic_formula_lens_abstains_on_flow_only_sectors(store):
    read = frameworks.read_symbol(
        _armed(store), fundamentals=_scr4("STRONG", mf=90.0), sector="FINANCIALS",
    ).read(Lens.MAGIC_FORMULA)
    assert read.state is LensState.NOT_APPLICABLE
    assert read.state is not LensState.UNAVAILABLE
    assert "LD-7" in read.detail


def test_magic_formula_lens_never_gates(store):
    """LD-6: fundamentals size conviction, they never block. A CONTRARY tilt must leave
    the engine verdict exactly as it was."""
    result = _armed(store)
    read = frameworks.read_symbol(result, fundamentals=_scr4("STRONG", mf=1.0), sector="Energy")
    assert read.read(Lens.MAGIC_FORMULA).state is LensState.CONTRARY
    assert result.state is EngineState.ARMED
    assert read.engine_state == "ARMED"


# --- aggregation ------------------------------------------------------------------------------


def test_confluence_counts_set_membership(store):
    read = frameworks.read_symbol(
        _armed(store), fundamentals=_scr4("STRONG", mf=90.0), sector="Energy",
    )
    rows = frameworks.confluences([read], minimum=2)
    assert len(rows) == 1
    c = rows[0]
    assert c.n_lenses == len(c.lenses) == len(set(c.lenses))
    assert set(c.lenses) == set(read.flagged)
    assert c.n_lenses <= len(frameworks.LENS_ORDER)


def test_confluence_honours_the_minimum_and_orders_by_agreement(store):
    wide = frameworks.read_symbol(
        _armed(store, "WIDE"), fundamentals=_scr4("WIDE", mf=90.0), sector="Energy",
    )
    narrow = frameworks.read_symbol(_phase_rejected(store, "NARROW"))
    rows = frameworks.confluences([narrow, wide], minimum=2)
    assert [r.symbol for r in rows] == sorted(
        [r.symbol for r in rows], key=lambda s: -next(x.n_lenses for x in rows if x.symbol == s),
    )
    assert rows[0].symbol == "WIDE"
    assert all(r.n_lenses >= 2 for r in rows)
    # raising the bar can only shrink the set — never reorder it into something new
    stricter = frameworks.confluences([narrow, wide], minimum=4)
    assert {r.symbol for r in stricter} <= {r.symbol for r in rows}


def test_confluence_lenses_follow_the_canonical_order(store):
    read = frameworks.read_symbol(
        _armed(store), fundamentals=_scr4("STRONG", mf=90.0), sector="Energy",
    )
    c = frameworks.confluences([read], minimum=1)[0]
    order = [frameworks.LENS_ORDER.index(lens) for lens in c.lenses]
    assert order == sorted(order)


def test_lens_tally_is_a_census_of_every_symbol(store):
    reads = [
        frameworks.read_symbol(_armed(store, "A1")),
        frameworks.read_symbol(_phase_rejected(store, "B1")),
        frameworks.read_symbol(_distribution(store, "C1")),
    ]
    tally = frameworks.lens_tally(reads)
    for lens in frameworks.LENS_ORDER:
        assert sum(tally[lens].values()) == len(reads)


# --- RULE B ------------------------------------------------------------------------------------


_BANNED = ("probability", "confidence", "smart money score", "sms", "buy this", "sell this")


def test_no_lens_emits_a_score_or_a_buy_sell_claim(store):
    reads = [
        frameworks.read_symbol(_armed(store, "A2"), fundamentals=_scr4("A2", mf=90.0), sector="Energy"),
        frameworks.read_symbol(_phase_rejected(store, "B2")),
        frameworks.read_symbol(_monopoly(store, "C2")),
    ]
    for read in reads:
        for r in read.reads:
            blob = f"{r.tag} {r.detail} {r.note}".lower()
            for banned in _BANNED:
                assert banned not in blob, f"{r.lens} leaked {banned!r}: {blob!r}"


def test_the_internal_sms_never_reaches_a_lens_row(store):
    result = _armed(store, "A3")
    read = frameworks.read_symbol(result)
    for r in read.reads:
        assert f"{result.sms.internal_score:.0f}" not in r.tag
        assert "internal_score" not in r.detail


def test_confluence_count_is_never_weighted_into_anything(store):
    """The count is a set size. Nothing multiplies it, and §4's weights are untouched by
    the existence of this surface (the optimizer stays their only writer)."""
    result = _armed(store, "A4")
    before = tuple((c.key, c.weight) for c in result.sms.components)
    read = frameworks.read_symbol(result, fundamentals=_scr4("A4", mf=90.0), sector="Energy")
    frameworks.confluences([read], minimum=1)
    after = tuple((c.key, c.weight) for c in result.sms.components)
    assert before == after


# --- view-model ---------------------------------------------------------------------------------


def test_sections_are_the_five_frameworks_plus_confluence():
    keys = lens_view.section_keys()
    assert len(keys) == 6
    assert keys[-1] == lens_view.CONFLUENCE
    assert set(keys[:-1]) == set(lens_view.SECTION_LENS)
    for key in keys:
        header = lens_view.section_header(key)
        assert header["title"] and header["framing"]


def test_lens_rows_put_the_strongest_read_first(store):
    reads = [
        frameworks.read_symbol(_phase_rejected(store, "B3")),   # NEUTRAL under Wyckoff
        frameworks.read_symbol(_armed(store, "A5")),            # FLAGGED under Wyckoff
        frameworks.read_symbol(_distribution(store, "C3")),     # CONTRARY under Wyckoff
    ]
    rows = lens_view.lens_rows(reads, "wyckoff")
    assert [r["ticker"] for r in rows] == ["A5", "C3", "B3"]
    assert [r["state"] for r in rows] == ["FLAGGED", "CONTRARY", "NEUTRAL"]


def test_a_row_names_the_other_lenses_that_flagged_it_but_never_itself(store):
    read = frameworks.read_symbol(
        _armed(store, "A6"), fundamentals=_scr4("A6", mf=90.0), sector="Energy",
    )
    row = lens_view.lens_rows([read], "wyckoff")[0]
    assert lens_view.LENS_CHIP[Lens.WYCKOFF] not in row["also"]
    assert lens_view.LENS_CHIP[Lens.BANDARMOLOGY] in row["also"]


def test_section_count_always_names_the_unread(store):
    reads = [frameworks.read_symbol(_armed(store, "A7"))]
    line = lens_view.section_count(reads, "mf")
    assert "unread" in line
    assert lens_view.section_count(reads, lens_view.CONFLUENCE)


def test_summary_counts_symbols_only(store):
    reads = [
        frameworks.read_symbol(_armed(store, "A8"), fundamentals=_scr4("A8", mf=90.0), sector="Energy"),
        frameworks.read_symbol(_phase_rejected(store, "B4")),
    ]
    s = lens_view.summary(reads)
    assert s["n_symbols"] == 2
    assert s["n_confluence"] <= s["n_symbols"]
    assert set(s["per_lens"]) == set(lens_view.SECTION_LENS)


# --- shell rendering ------------------------------------------------------------------------------


def test_shell_renders_a_lens_row_and_a_confluence_row(store):
    read = frameworks.read_symbol(
        _armed(store, "A9"), fundamentals=_scr4("A9", mf=90.0), sector="Energy",
    )
    row = lens_view.lens_rows([read], "bandar")[0]
    html = shell.lens_row_html(row)
    assert "A9" in html and "PERSISTENT ACCUMULATOR" in html
    assert "cf-lensrow" in html

    crow = lens_view.confluence_rows([read], minimum=1)[0]
    chtml = shell.confluence_row_html(crow)
    assert f'>{crow["n_lenses"]}<' in chtml
    assert "of 5 lenses" in chtml

    header = shell.lens_section_header_html(
        lens_view.section_header("bandar"), lens_view.section_count([read], "bandar"),
    )
    assert "Bandarmology" in header
    assert "cf-lenshead" in header
    assert "RULE B" in shell.lens_footer_html()


def test_every_state_gets_a_band_even_at_zero(store):
    """Grouping is the existing sort order made visible. All five bands render whether or
    not they hold rows — an empty state is stated, never omitted, and UNREAD never merges
    into NEUTRAL."""
    rows = lens_view.lens_rows([frameworks.read_symbol(_armed(store, "A11"))], "wyckoff")
    bands = lens_view.lens_bands(rows)
    assert [b["state"] for b in bands] == [s.value for s in lens_view.BAND_ORDER]
    assert sum(b["count"] for b in bands) == len(rows)
    empty = [b for b in bands if not b["count"]]
    assert empty, "this fixture should leave at least one state empty"
    for band in empty:
        assert band["rows"] == []
        assert lens_view.EMPTY_BAND_NOTE in shell.lens_band_html(band)
    flagged = next(b for b in bands if b["state"] == LensState.FLAGGED.value)
    assert flagged["count_str"] == "1 name"      # singular, not "1 names"
    assert "FLAGGED" in shell.lens_band_html(flagged)


def test_the_strip_is_five_fixed_slots_never_a_proportion(store):
    """Set membership is column-scannable, not a meter: always five slots, always in the
    canonical lens order, whatever the count (RULE B)."""
    reads = [
        frameworks.read_symbol(_armed(store, "A12"), fundamentals=_scr4("A12", mf=90.0), sector="Energy"),
        frameworks.read_symbol(_phase_rejected(store, "B12")),
    ]
    for row in lens_view.lens_rows(reads, "wyckoff"):
        strip = row["strip"]
        assert [s["code"] for s in strip] == [
            lens_view.LENS_CODE[lens] for lens in frameworks.LENS_ORDER
        ]
        # the section you are reading marks itself; it never counts as agreement
        assert [s["slot"] for s in strip].count("self") == 1
        assert strip[0]["slot"] == "self"       # WYK is the section under test
        html = shell.lens_row_html(row)
        assert html.count("cf-lensslot") == len(frameworks.LENS_ORDER)
        for banned in ("width:", "flex-grow", "%;"):
            assert banned not in html, f"strip leaked a proportion: {banned}"


def test_a_lens_row_drops_the_state_word_but_keeps_the_mark_and_tag(store):
    row = lens_view.lens_rows([frameworks.read_symbol(_armed(store, "A13"))], "wyckoff")[0]
    html = shell.lens_row_html(row)
    assert "cf-lensstatelab" not in html          # the band above the group owns it
    assert "◉" in html and row["tag"] in html
    for glyph in ("✓", "✕", "▽", "⤶"):            # never the pipeline's stage glyphs
        assert glyph not in html


def test_unread_and_na_rows_are_dimmed_not_collapsed(store):
    """De-emphasis without collapsing — the five states stay five."""
    read = frameworks.read_symbol(_armed(store, "A14"))   # no fundamentals → MF UNAVAILABLE
    row = lens_view.lens_rows([read], "mf")[0]
    assert row["state"] == LensState.UNAVAILABLE.value
    assert row["dim"] is True
    html = shell.lens_row_html(row)
    assert "is-dim" in html
    assert row["detail"] in html                  # dimmed, still printed in full


def test_the_switcher_is_a_census_naming_flagged_and_unread(store):
    reads = [
        frameworks.read_symbol(_armed(store, "A15"), fundamentals=_scr4("A15", mf=90.0), sector="Energy"),
        frameworks.read_symbol(_phase_rejected(store, "B15")),
    ]
    tabs = lens_view.section_tabs(reads)
    assert [t["key"] for t in tabs] == list(lens_view.section_keys())
    for tab in tabs[:-1]:
        assert "flagged" in tab["tally"] and "unread" in tab["tally"]
    assert "symbols" in tabs[-1]["tally"]         # confluence: a set size, nothing derived
    html = shell.lens_tab_html(tabs[0], active=True)
    assert "is-active" in html and tabs[0]["code"] in html and tabs[0]["tally"] in html
    assert "is-active" not in shell.lens_tab_html(tabs[1], active=False)


def test_the_read_state_key_always_prints_all_five_states():
    items = lens_view.read_state_key()
    assert [i["state"] for i in items] == [s.value for s in lens_view.BAND_ORDER]
    html = unescape(shell.lens_key_html(items))
    for item in items:
        assert item["label"] in html and item["means"] in html
    assert "missing ≠ zero" in html               # unread is never "found nothing"


def test_rule_a_outshouts_the_agreement_count_on_an_untradeable_row(store):
    """The `PTRO` case: three frameworks like a name RULE A rejected. The gate line must be
    the loudest element in the row, and the count must be muted — agreement never
    overrides the phase gate."""
    read = frameworks.read_symbol(
        _phase_rejected(store, "B16"), fundamentals=_scr4("B16", mf=90.0), sector="Energy",
    )
    row = lens_view.confluence_rows([read], minimum=1)[0]
    assert row["tradeable"] is False
    assert row["rule_a"] == "RULE A · NOT TRADEABLE"
    html = shell.confluence_row_html(row)
    assert "cf-rulea is-not" in html              # 11px/600 #f85149 per the sheet
    assert "cf-conflcount is-muted" in html
    assert html.count("is-untradeable") >= 2      # row border + side cell tint
    assert row["engine_state"] in html

    tradeable = lens_view.confluence_rows(
        [frameworks.read_symbol(
            _armed(store, "A16"), fundamentals=_scr4("A16", mf=90.0), sector="Energy")],
        minimum=1,
    )[0]
    assert tradeable["rule_a"] == "RULE A · TRADEABLE PHASE"
    thtml = shell.confluence_row_html(tradeable)
    assert "is-not" not in thtml and "is-untradeable" not in thtml
    assert "is-muted" not in thtml


def test_every_row_carries_the_engine_verdict(store):
    """RULE A structural: a lens can never be read without the pipeline's own verdict."""
    reads = [
        frameworks.read_symbol(_armed(store, "A17"), fundamentals=_scr4("A17", mf=90.0), sector="Energy"),
        frameworks.read_symbol(_distribution(store, "C17")),
    ]
    for key in lens_view.section_keys():
        if key == lens_view.CONFLUENCE:
            rows = lens_view.confluence_rows(reads, minimum=1)
            render = shell.confluence_row_html
        else:
            rows = lens_view.lens_rows(reads, key)
            render = shell.lens_row_html
        assert rows
        for row in rows:
            html = render(row)
            assert "pipeline:" in html
            assert row["engine_state"] in html


def test_column_captions_name_all_four_columns():
    assert len(lens_view.columns("wyckoff")) == 4
    assert len(lens_view.columns(lens_view.CONFLUENCE)) == 4
    assert lens_view.columns("wyckoff") != lens_view.columns(lens_view.CONFLUENCE)
    html = shell.lens_columns_html(lens_view.columns("wyckoff"))
    assert "SYMBOL" in html and "cf-lenscols" in html


def test_the_redesigned_surface_carries_no_digit_but_counts(store):
    """The only numerals on the surface are counts of symbols: band counts, the census
    line, the switcher tally, and the confluence set size (RULE B)."""
    reads = [frameworks.read_symbol(_armed(store, "A18"))]
    rows = lens_view.lens_rows(reads, "bandar")
    for band in lens_view.lens_bands(rows):
        digits = "".join(c for c in unescape(shell.lens_band_html(band)) if c.isdigit())
        # only the band's own count of names (the class names carry no digit at all)
        assert digits == str(band["count"])


def test_shell_escapes_lens_text(store):
    row = lens_view.lens_rows([frameworks.read_symbol(_armed(store, "A10"))], "wyckoff")[0]
    row["detail"] = "<script>x</script>"
    row["ticker"] = "<b>X</b>"
    html = shell.lens_row_html(row)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
