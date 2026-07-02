"""Parsers for the slice-2 endpoints: symbol info, corp actions, special board."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime

from currentflow.dal.models import BoardType
from currentflow.dal.parse import (
    parse_corp_actions,
    parse_special_board,
    parse_symbol_info,
)

FETCHED = datetime(2026, 7, 1, 10, 0)


# --- symbol info -------------------------------------------------------------------


def test_symbol_info_normal():
    payload = {
        "data": {
            "status": "active",
            "tradeable": True,
            "market_hour": {"suspend_info": None},
            "notation": [{"code": "X"}],
            "indexes": [{"name": "LQ45"}, {"name": "IDX80"}],
        }
    }
    info = parse_symbol_info("BBRI", payload, fetched_at=FETCHED)
    assert not info.suspended
    assert info.tradeable is True
    assert info.indexes == ("LQ45", "IDX80")
    assert info.notations == ("X",)
    assert info.as_of == FETCHED


def test_symbol_info_suspended_via_status_or_suspend_info():
    by_status = parse_symbol_info(
        "ZZZZ", {"data": {"status": "suspended"}}, fetched_at=FETCHED
    )
    assert by_status.suspended
    by_info = parse_symbol_info(
        "ZZZZ",
        {"data": {"status": "active", "market_hour": {"suspend_info": "suspended since …"}}},
        fetched_at=FETCHED,
    )
    assert by_info.suspended


def test_symbol_info_tolerates_string_lists_and_missing_fields():
    info = parse_symbol_info("ABCD", {"data": {"indexes": ["IDXSMC-LIQ"]}}, fetched_at=FETCHED)
    assert info.indexes == ("IDXSMC-LIQ",)
    assert info.tradeable is None
    assert not info.suspended and not info.uma


# --- corp actions ---------------------------------------------------------------------


def test_corp_actions_flat_list():
    payload = {"data": [{"type": "Dividend", "ex_date": "2026-07-10"}]}
    (ca,) = parse_corp_actions("BBRI", payload, fetched_at=FETCHED)
    assert ca.action_type == "dividend"
    assert ca.ex_date == Date(2026, 7, 10)


def test_corp_actions_per_type_dict():
    payload = {"data": {"rightissue": [{"exDate": "2026-08-01", "recordingDate": "2026-08-03"}]}}
    (ca,) = parse_corp_actions("BRMS", payload, fetched_at=FETCHED)
    assert ca.action_type == "rightissue"
    assert ca.ex_date == Date(2026, 8, 1)
    assert ca.recording_date == Date(2026, 8, 3)


def test_corp_actions_empty():
    assert parse_corp_actions("BBRI", {"data": []}, fetched_at=FETCHED) == []


# --- special board ------------------------------------------------------------------------


def test_special_board_maps_symbols_to_development():
    payload = {"data": [{"symbol": "DEWA"}, {"code": "RAJA"}]}
    boards = parse_special_board(payload)
    assert boards == {
        "DEWA": BoardType.DEVELOPMENT,
        "RAJA": BoardType.DEVELOPMENT,
    }
    assert boards.get("BBRI", BoardType.MAIN) is BoardType.MAIN
