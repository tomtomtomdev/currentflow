"""Parse raw `exodus` JSON into typed records, stamping `as_of`.

Field names follow DATA_SOURCES.md §1. The HAR did not capture every envelope shape,
so parsers are deliberately tolerant (multiple envelope keys, string-or-number values).
Parser breakage on endpoint changes is expected maintenance (spec §10), so the mapping
is isolated here.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any

from currentflow.dal.models import (
    BrokerNet,
    DailyBar,
    InvestorType,
    RowStatus,
    Side,
)
from currentflow.dal.timing import broker_as_of, ohlcv_as_of

# --- primitive coercers -----------------------------------------------------------


def _num(v: Any) -> float | None:
    """Numeric or None. NEVER returns 0 for a missing value (missing ≠ zero)."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):  # guard: bool is an int subclass
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def _parse_date(v: Any) -> Date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, Date):
        return v
    s = str(v).strip()
    # tolerate "YYYY-MM-DD", ISO datetime, or "YYYY-MM-DDTHH:MM:SS…"
    return datetime.fromisoformat(s.replace("Z", "+00:00").split("T")[0]).date()


def _parse_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # store WIB-local, tz-naive (spec convention)
    return dt.replace(tzinfo=None)


_INVESTOR_MAP = {
    "asing": InvestorType.FOREIGN,
    "foreign": InvestorType.FOREIGN,
    "f": InvestorType.FOREIGN,
    "lokal": InvestorType.LOCAL,
    "local": InvestorType.LOCAL,
    "d": InvestorType.LOCAL,
    "pemerintah": InvestorType.GOVERNMENT,
    "government": InvestorType.GOVERNMENT,
    "g": InvestorType.GOVERNMENT,
}


def _investor_type(v: Any) -> InvestorType:
    return _INVESTOR_MAP.get(str(v).strip().lower(), InvestorType.UNKNOWN)


def _rows(payload: Any, *keys: str) -> list[dict]:
    """Pull the list of records out of whatever envelope the feed used."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        node: Any = payload
        # descend common wrappers first
        for wrap in ("data", "result", "results"):
            if isinstance(node, dict) and wrap in node:
                node = node[wrap]
        if isinstance(node, list):
            return [r for r in node if isinstance(r, dict)]
        if isinstance(node, dict):
            for k in keys:
                if isinstance(node.get(k), list):
                    return [r for r in node[k] if isinstance(r, dict)]
    return []


# --- OHLCV + foreign flow ---------------------------------------------------------


def parse_ohlcv(symbol: str, payload: Any) -> list[DailyBar]:
    """company-price-feed/historical/summary/{sym} → list[DailyBar]."""
    out: list[DailyBar] = []
    for r in _rows(payload, "chart", "data"):
        day = _parse_date(r.get("date") or r.get("Date") or r.get("time"))
        volume = _int(r.get("volume"))
        frequency = _int(r.get("frequency"))
        # empty ≠ zero: a present row with zero volume AND zero freq is a real
        # no-trade day; anything else with activity is TRADED.
        traded = bool((volume or 0) > 0 or (frequency or 0) > 0)
        status = RowStatus.TRADED if traded else RowStatus.NO_TRADES
        out.append(
            DailyBar(
                symbol=symbol,
                date=day,
                as_of=ohlcv_as_of(day),
                status=status,
                open=_num(r.get("open")),
                high=_num(r.get("high")),
                low=_num(r.get("low")),
                close=_num(r.get("close")),
                volume=volume,
                value=_num(r.get("value")),
                frequency=frequency,
                vwap=_num(r.get("average") or r.get("vwap")),
                foreign_buy=_num(r.get("foreign_buy")),
                foreign_sell=_num(r.get("foreign_sell")),
                net_foreign=_num(r.get("net_foreign")),
                change_percentage=_num(r.get("change_percentage")),
            )
        )
    return out


# --- broker summary ---------------------------------------------------------------


def _broker_row(symbol: str, day: Date, as_of: datetime, side: Side, r: dict) -> BrokerNet:
    val = r.get("bval") if side is Side.BUY else r.get("sval")
    lot = r.get("blot") if side is Side.BUY else r.get("slot")
    return BrokerNet(
        symbol=symbol,
        date=day,
        as_of=as_of,
        broker_code=str(r.get("netbs_broker_code") or r.get("broker_code") or "").strip(),
        side=side,
        investor_type=_investor_type(r.get("type")),
        avg_price=_num(r.get("netbs_buy_avg_price") or r.get("netbs_sell_avg_price")),
        value=_num(val),
        lot=_int(lot),
        frequency=_int(r.get("freq")),
    )


def parse_broker_summary(symbol: str, payload: Any) -> list[BrokerNet]:
    """marketdetectors/{sym} → list[BrokerNet] (buy + sell sides).

    `as_of` uses the feed's `data_last_updated` when present, else the conservative
    next-day fallback (LD-5) via timing.broker_as_of.
    """
    node: Any = payload
    for wrap in ("data", "result", "results"):
        if isinstance(node, dict) and wrap in node:
            node = node[wrap]
    bs = node.get("broker_summary", node) if isinstance(node, dict) else {}
    if not isinstance(bs, dict):
        return []

    data_last_updated = _parse_dt(
        (node.get("data_last_updated") if isinstance(node, dict) else None)
        or bs.get("data_last_updated")
    )

    buys = [r for r in bs.get("brokers_buy", []) if isinstance(r, dict)]
    sells = [r for r in bs.get("brokers_sell", []) if isinstance(r, dict)]

    out: list[BrokerNet] = []
    for side, rows in ((Side.BUY, buys), (Side.SELL, sells)):
        for r in rows:
            day = _parse_date(r.get("netbs_date") or bs.get("netbs_date"))
            out.append(_broker_row(symbol, day, broker_as_of(day, data_last_updated), side, r))
    return out
