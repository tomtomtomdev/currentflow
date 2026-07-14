"""Fast Mode book view-model (spec §9, LD-11) — pure data shaping, no Streamlit.

Surfaces the auto paper-trader's state for the operator:
  * the **open book** — positions with entry / stop / target and a mark-to-latest-close
    *unrealized* P&L (a factual mark, RULE-B-safe once an entry price exists — the §9
    Risk-Monitor precedent);
  * **closed trades** — each with net-of-fee realized P&L (a fact — what the paper engine
    actually did);
  * **RULE B accrual** — months / `PAPER_VALIDATION_MONTHS` and trade count toward the
    `fast_mode` lane's validation. The strategy's **aggregate** hit-rate / expectancy is the
    promotable claim and is **withheld** (`•••`) until the lane is VALIDATED (via `gated_display`,
    server-authoritative — never a client toggle).

Every number here is either a stored fact (realized/marked P&L) or a gated claim (aggregate
stats). No predictive score, probability, or buy/sell verb leaks pre-validation.
"""

from __future__ import annotations

from datetime import datetime

from currentflow import config
from currentflow.validation.fast_mode import FAST_MODE_MODULE, _months_since
from currentflow.validation.state import ModuleState, gated_display

_FAR_FUTURE = datetime(2100, 1, 1)


def _latest_close(store, symbol: str) -> float | None:
    """The most recent traded close for `symbol` (the honest mark; None if unknown)."""
    bars = [b for b in store.read_daily_bars(symbol, _FAR_FUTURE) if b.close is not None]
    return bars[-1].close if bars else None


def open_rows(store) -> list[dict]:
    """The open Fast-Mode book with a mark-to-latest-close unrealized P&L (fact, not forecast).
    `unrealized_pnl` is None when no mark is available (missing ≠ zero)."""
    out: list[dict] = []
    for p in store.read_fast_positions():
        mark = _latest_close(store, p.symbol)
        unreal = (mark - p.entry_price) * p.qty if mark is not None else None
        out.append({
            "symbol": p.symbol, "track": p.track, "sector": p.sector, "qty": p.qty,
            "entry_date": p.entry_date, "entry_price": p.entry_price, "stop": p.stop,
            "target": p.target, "mark": mark, "unrealized_pnl": unreal,
        })
    return out


def closed_rows(store) -> list[dict]:
    """Closed Fast-Mode trades, newest exit first — each with net-of-fee realized P&L (fact)."""
    rows = store.read_fast_trades()
    out = [
        {
            "symbol": t.symbol, "track": t.track, "entry_date": t.entry_date,
            "exit_date": t.exit_date, "qty": t.qty, "entry_price": t.entry_price,
            "exit_price": t.exit_price, "exit_reason": t.exit_reason,
            "net_pnl": (t.exit_price - t.entry_price) * t.qty - (t.entry_fee + t.exit_fee),
            "won": ((t.exit_price - t.entry_price) * t.qty - (t.entry_fee + t.exit_fee)) > 0,
        }
        for t in rows
    ]
    out.sort(key=lambda r: (r["exit_date"], r["symbol"]), reverse=True)
    return out


def build_view(store, ledger, *, now: datetime) -> dict:
    """The whole Fast-Mode panel model: arm state, open book, closed trades, RULE B accrual.

    The aggregate hit-rate / expectancy is a *claim* — routed through `gated_display` on the
    `fast_mode` lane, so it reads `•••` until the ledger promotes the module."""
    state = store.read_fast_mode_state()
    opens = open_rows(store)
    closes = closed_rows(store)

    realized = sum(c["net_pnl"] for c in closes)          # fact — sum of what happened
    unreal = sum(c["unrealized_pnl"] or 0.0 for c in opens)
    months = _months_since(state.since_date if state else None, now)
    module_state = ledger.state(FAST_MODE_MODULE) if ledger is not None else ModuleState.OBSERVATION_ONLY
    registry = ledger.states() if ledger is not None else None

    n = len(closes)
    wins = sum(1 for c in closes if c["won"])
    hit_rate = (wins / n) if n else None                  # claim — gated until validated
    expectancy = (realized / n) if n else None            # claim — gated until validated

    return {
        "enabled": bool(state.enabled) if state else False,
        "since_date": state.since_date if state else None,
        "last_run_day": state.last_run_day if state else None,
        "open_positions": opens,
        "closed_trades": closes,
        "n_open": len(opens),
        "n_closed": n,
        # facts (observations) — realized/marked P&L is what the engine did:
        "realized_pnl": realized,
        "unrealized_pnl": unreal,
        # RULE B accrual toward the fast_mode lane:
        "months_accrued": months,
        "required_months": config.PAPER_VALIDATION_MONTHS,
        "module_state": module_state.value,
        "validated": module_state is ModuleState.VALIDATED,
        # claims — withheld (`•••`) until the fast_mode lane is VALIDATED:
        "hit_rate_display": gated_display(
            FAST_MODE_MODULE, hit_rate, registry=registry, fmt="{:.0%}"
        ),
        "expectancy_display": gated_display(
            FAST_MODE_MODULE, expectancy, registry=registry, fmt="IDR {:,.0f}"
        ),
        "framing": (
            "Auto paper-trade record — observations only. Aggregate hit-rate / expectancy is "
            "withheld until this lane clears forward-paper validation (RULE B)."
        ),
    }
