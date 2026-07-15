"""Slice 21 §18.3 — catalog view RULE B / P1–P4 acceptance tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from currentflow import config
from currentflow.patterns.accrual import seed_catalog
from currentflow.store.schema import PatternCatalogRow
from currentflow.ui import catalog_view

NOW = datetime(2026, 7, 1, 9, 0)

_ROOT = Path(__file__).resolve().parent.parent / "currentflow"


def _results(horizons: dict) -> str:
    return json.dumps({"horizons": horizons, "confounds": {}, "stability": "x"})


def test_small_n_renders_interval_only(store):
    store.upsert_pattern_catalog(PatternCatalogRow(
        "p", 1, "B", "ESTIMATED", json.dumps({"folk_claim": "", "outcome_spec": {}}),
        _results({
            "10": {"n": 5, "rate": 0.4, "ci90": [0.1, 0.8], "rate_uncond": 0.3},   # small n
            "20": {"n": 40, "rate": 0.55, "ci90": [0.4, 0.7], "rate_uncond": 0.3},  # normal
            "60": {"n": 0, "rate": None, "ci90": [0.0, 1.0], "rate_uncond": 0.3},   # none
        }),
        NOW,
    ))
    card = catalog_view.catalog_cards(store)[0]
    rows = {h["horizon_days"]: h for h in card["horizons"]}

    assert rows[10]["rate"]["small_n"] is True and rows[10]["rate"]["point"] is None
    assert rows[20]["rate"]["small_n"] is False and rows[20]["rate"]["point"] is not None
    assert rows[60]["rate"]["display"] == catalog_view.NO_INSTANCES     # never 0%
    # the null ALWAYS travels with the rate (§5.4) — shown on every horizon
    assert all(h["null"] != "—" for h in card["horizons"])
    assert card["stability"] == "UNKNOWN (current regime only)"


def test_seeded_cards_carry_no_advice_verb(store):
    seed_catalog(store, now=NOW)
    cards = catalog_view.catalog_cards(store)
    assert len(cards) == 6
    # P2 bans buy/sell VERBS as advice, not the word inside a flow descriptor ("NBSA buy
    # streak", "net seller"). Ban the advice framings a base rate must never carry.
    banned = ("buy signal", "sell signal", "go long", "go short", "probability",
              "recommend", "you should", "target price")
    for card in cards:
        blob = " ".join(str(card[k]) for k in ("folk_claim", "definition", "outcome_label")).lower()
        for token in banned:
            assert token not in blob, f"{card['pattern_id']} leaked advice token {token!r}: {blob!r}"
        assert card["stability"] == catalog_view.STABILITY


def test_p1_base_rates_absent_from_live_view_modules():
    """P1: catalog stats live only in the catalog view — no live surface imports it or
    references its identifiers (grep-style firewall test, test_rule_b family)."""
    catalog_tokens = ("catalog_view", "pattern_catalog", "rate_uncond", "historical frequency")
    for name in ("pipeline_view.py", "watchlist_view.py"):
        src = (_ROOT / "ui" / name).read_text().lower()
        for tok in catalog_tokens:
            assert tok not in src, f"{name} references catalog token {tok!r} (P1 violation)"


def test_summary_has_no_composite_score(store):
    seed_catalog(store, now=NOW)
    summary = catalog_view.catalog_summary(store)
    assert summary["count"] == 6
    # P2: no cross-pattern composite score anywhere in the summary.
    assert "score" not in summary and "sms" not in summary
