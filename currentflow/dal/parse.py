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
    BoardType,
    BrokerNet,
    CorpAction,
    DailyBar,
    InvestorType,
    OwnershipSlice,
    RowStatus,
    Side,
    SymbolInfo,
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


def _raw_rows(payload: Any, *keys: str) -> list:
    """The list the feed put in this page, UNFILTERED (may contain non-dicts). This is
    the pagination-fullness signal: how many rows the server sent, independent of how
    many parse cleanly — so a malformed row can't masquerade as a short (final) page."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        node: Any = payload
        # descend common wrappers first
        for wrap in ("data", "result", "results"):
            if isinstance(node, dict) and wrap in node:
                node = node[wrap]
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            for k in keys:
                if isinstance(node.get(k), list):
                    return node[k]
    return []


def _rows(payload: Any, *keys: str) -> list[dict]:
    """Pull the list of records out of whatever envelope the feed used, dropping any
    non-dict entry (tolerant parse). For the raw page size use `_raw_rows`."""
    return [r for r in _raw_rows(payload, *keys) if isinstance(r, dict)]


def ohlcv_page_rowcount(payload: Any) -> int:
    """Raw row count for one OHLCV page — counted BEFORE per-row validity filtering so a
    single malformed row can't shrink the count below the page limit and truncate a
    backfill early (no silent caps). Client pagination terminates on this, not on the
    number of rows that happened to parse."""
    return len(_raw_rows(payload, "chart", "data"))


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
    # Feed convention (marketdetectors NET): a net-seller's `sval`/`slot` arrive
    # SIGNED NEGATIVE. We store a MAGNITUDE with `side` carrying direction, so the
    # aggregation layer's `buy - sell` nets correctly instead of double-flipping.
    raw_val, raw_lot = _num(val), _int(lot)
    return BrokerNet(
        symbol=symbol,
        date=day,
        as_of=as_of,
        broker_code=str(r.get("netbs_broker_code") or r.get("broker_code") or "").strip(),
        side=side,
        investor_type=_investor_type(r.get("type")),
        avg_price=_num(r.get("netbs_buy_avg_price") or r.get("netbs_sell_avg_price")),
        value=abs(raw_val) if raw_val is not None else None,
        lot=abs(raw_lot) if raw_lot is not None else None,
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


# --- symbol info (universe-gate flags + index membership) ---------------------------


def _unwrap(payload: Any) -> Any:
    node: Any = payload
    for wrap in ("data", "result", "results"):
        if isinstance(node, dict) and wrap in node:
            node = node[wrap]
    return node


def _truthy_flag(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("true", "1", "yes", "suspend", "suspended", "active")


def parse_symbol_info(symbol: str, payload: Any, *, fetched_at: datetime) -> SymbolInfo:
    """emitten/{sym}/info → SymbolInfo (status, suspend, notation[], indexes[])."""
    node = _unwrap(payload)
    if not isinstance(node, dict):
        node = {}

    status = str(node.get("status") or "").strip().lower()
    suspend_info = (node.get("market_hour") or {}).get("suspend_info") if isinstance(
        node.get("market_hour"), dict
    ) else None
    suspended = status == "suspended" or bool(suspend_info)

    tradeable_raw = node.get("tradeable")
    tradeable = None if tradeable_raw is None else _truthy_flag(tradeable_raw)

    notations = tuple(
        str(n.get("code") if isinstance(n, dict) else n).strip()
        for n in node.get("notation") or []
    )
    uma = bool(node.get("uma")) or "UMA" in notations

    indexes = tuple(
        str(i.get("name") if isinstance(i, dict) else i).strip()
        for i in node.get("indexes") or []
    )

    return SymbolInfo(
        symbol=symbol,
        as_of=fetched_at,
        suspended=suspended,
        tradeable=tradeable,
        uma=uma,
        notations=notations,
        indexes=indexes,
    )


# --- corporate actions ---------------------------------------------------------------


def parse_corp_actions(symbol: str, payload: Any, *, fetched_at: datetime) -> list[CorpAction]:
    """corpaction/{sym} → list[CorpAction]. Tolerates flat lists or per-type dicts."""
    node = _unwrap(payload)
    rows: list[tuple[str, dict]] = []
    if isinstance(node, list):
        rows = [(str(r.get("type") or r.get("action_type") or ""), r) for r in node if isinstance(r, dict)]
    elif isinstance(node, dict):
        for action_type, v in node.items():
            for r in v if isinstance(v, list) else [v]:
                if isinstance(r, dict):
                    rows.append((str(r.get("type") or action_type), r))

    out: list[CorpAction] = []
    for action_type, r in rows:
        ex = r.get("ex_date") or r.get("exDate") or r.get("date")
        rec = r.get("recording_date") or r.get("recordingDate")
        out.append(
            CorpAction(
                symbol=symbol,
                as_of=fetched_at,
                action_type=action_type.strip().lower(),
                ex_date=_parse_date(ex) if ex else None,
                recording_date=_parse_date(rec) if rec else None,
            )
        )
    return out


# --- special / development board membership ------------------------------------------


def parse_special_board(payload: Any) -> dict[str, BoardType]:
    """emitten/indexes/special-board → {symbol: BoardType.DEVELOPMENT}.

    Symbols absent from the mapping are MAIN by default (the feed lists only the
    special/development boards).
    """
    node = _unwrap(payload)
    out: dict[str, BoardType] = {}
    rows: list[Any] = []
    if isinstance(node, list):
        rows = node
    elif isinstance(node, dict):
        for v in node.values():
            rows.extend(v if isinstance(v, list) else [v])
    for r in rows:
        sym = str(r.get("symbol") or r.get("code") or "").strip() if isinstance(r, dict) else str(r).strip()
        if sym:
            out[sym] = BoardType.DEVELOPMENT
    return out


# --- KSEI ownership (shareholders chart) ----------------------------------------------

_FOREIGN_KEYS = ("foreign", "foreign_percentage", "asing")
_LOCAL_KEYS = ("local", "local_percentage", "lokal", "domestic")


def _pct_from(r: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in r:
            v = r[k]
            return _num(v.get("value") if isinstance(v, dict) else v)
    return None


def parse_ksei_ownership(
    symbol: str, payload: Any, *, fetched_at: datetime
) -> list[OwnershipSlice]:
    """emitten-metadata/shareholders/{sym}/chart → monthly Local vs Foreign % series.

    Tolerates two envelope shapes: a flat list of per-month rows carrying both
    percentages, or parallel `foreign`/`local` series of {date, value} merged by date.
    """
    node = _unwrap(payload)

    merged: dict[Date, dict[str, float | None]] = {}
    if isinstance(node, dict) and any(
        isinstance(node.get(k), list) for k in (*_FOREIGN_KEYS, *_LOCAL_KEYS)
    ):
        for field, keys in (("foreign_pct", _FOREIGN_KEYS), ("local_pct", _LOCAL_KEYS)):
            for k in keys:
                for r in node.get(k) or []:
                    if not isinstance(r, dict):
                        continue
                    day = _parse_date(r.get("date") or r.get("period") or r.get("month"))
                    merged.setdefault(day, {})[field] = _num(r.get("value") or r.get("percentage"))
    else:
        for r in _rows(payload, "chart", "data"):
            raw_day = r.get("date") or r.get("period") or r.get("month")
            if raw_day is None:
                continue
            merged.setdefault(_parse_date(raw_day), {}).update(
                foreign_pct=_pct_from(r, _FOREIGN_KEYS),
                local_pct=_pct_from(r, _LOCAL_KEYS),
            )

    return [
        OwnershipSlice(
            symbol=symbol,
            date=day,
            as_of=fetched_at,
            foreign_pct=vals.get("foreign_pct"),
            local_pct=vals.get("local_pct"),
        )
        for day, vals in sorted(merged.items())
    ]


# --- screener results ------------------------------------------------------------------


def parse_screener_results(payload: Any) -> list[dict[str, Any]]:
    """screener/templates response → [{symbol, values: {fitem_id: raw}}].

    Results arrive as `calcs[].results[]` per company: {id, item, raw, display}.
    Tolerant to the company list living under `calcs` or a flat `companies` key.
    """
    node = _unwrap(payload)
    companies: list[dict] = []
    if isinstance(node, dict):
        for key in ("calcs", "companies", "stocks"):
            if isinstance(node.get(key), list):
                companies = [c for c in node[key] if isinstance(c, dict)]
                break
    elif isinstance(node, list):
        companies = [c for c in node if isinstance(c, dict)]

    out: list[dict[str, Any]] = []
    for c in companies:
        sym = str(c.get("symbol") or c.get("code") or c.get("company", {}).get("symbol") or "").strip()
        if not sym:
            continue
        values: dict[int, float | None] = {}
        for res in c.get("results") or []:
            if not isinstance(res, dict):
                continue
            fid = _int(res.get("item") or res.get("id"))
            if fid is not None:
                values[fid] = _num(res.get("raw"))
        out.append({"symbol": sym, "values": values})
    return out


def parse_screener_totalrows(payload: Any) -> int | None:
    """Total survivor count the server claims for a screener run (`data.totalrows`),
    or None when absent — the client pages until it has them all (no silent caps)."""
    node = _unwrap(payload)
    if isinstance(node, dict):
        return _int(node.get("totalrows"))
    return None
