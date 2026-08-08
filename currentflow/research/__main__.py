"""Research CLI — falsify SMS components before funding them with weight.

Two steps, both against a THROWAWAY store (never `currentflow.duckdb`):

    # 1. cheap strided pull: full bars, broker only on sampled days
    python -m currentflow.research backfill --symbols BBCA,BBRI,BMRI,TLKM --stride 10

    # 2. the univariate premise tests
    python -m currentflow.research test --horizon 10 --buckets 5

`backfill` prints its call budget before spending anything. `test` reads only the local
store — no API calls, no paywall counter. Nothing here writes a signal: outputs are
measurements (see `research/__init__.py`).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date as Date
from datetime import datetime
from pathlib import Path

from currentflow import config
from currentflow.dal.errors import AuthError, ExodusError
from currentflow.dal.session import build_live_client
from currentflow.logging_setup import configure_logging
from currentflow.research.backfill import (
    ResearchBackfillError,
    guard_target_db,
    plan_budget,
    run_research_backfill,
)
from currentflow.research.premise import FEATURES, run_premise
from currentflow.store.db import Store

DEFAULT_DB = "research.duckdb"
PRODUCTION_DB = "currentflow.duckdb"


def _resolve_symbols(raw: str | None, seed_from: str | None) -> list[str]:
    """Explicit list, a file of tickers, or the symbol set of an existing store."""
    if raw:
        text = Path(raw).read_text() if Path(raw).exists() else raw
        return sorted({s.strip().upper() for s in text.replace("\n", ",").split(",") if s.strip()})
    if seed_from:
        store = Store(seed_from)
        try:
            return sorted(store.symbols())
        finally:
            store.close()
    return []


def _cmd_backfill(args) -> int:
    try:
        guard_target_db(args.db)
    except ValueError as exc:
        print(f"REFUSED: {exc}")
        return 3
    symbols = _resolve_symbols(args.symbols, args.seed_from)
    if not symbols:
        print("no symbols — pass --symbols BBCA,BBRI (or a file), or --seed-from <db>")
        return 1

    now = datetime.now()
    start = args.start or config.REGIME_START_TRACK_A
    end = args.end or now.date()
    approx_days = round((end - start).days * 5 / 7)
    budget = plan_budget(
        n_symbols=len(symbols), n_trading_days=approx_days, stride_days=args.stride
    )
    print(budget.line())
    if not args.yes:
        print("\nre-run with --yes to spend it")
        return 0

    async def _go() -> int:
        transport = store = None
        try:
            client, transport = build_live_client()
            store = Store(args.db)
            report = await run_research_backfill(
                client, store, symbols, start=start, end=end,
                stride_days=args.stride, now=now, log_fn=print,
            )
        except AuthError as exc:
            print(f"AUTH FAILED — run `./run.sh login` first: {exc}")
            return 1
        except (ExodusError, ResearchBackfillError) as exc:
            print(f"backfill error: {exc}")
            return 2
        finally:
            if transport is not None:
                await transport.aclose()
            if store is not None:
                store.close()

        print(
            f"\ndone — {report.bars_inserted} bars, {report.broker_rows_inserted} broker rows "
            f"over {len(report.broker_days)} sampled day(s) into {args.db}"
        )
        if report.broker_days_skipped_cached:
            print(f"  {report.broker_days_skipped_cached} broker day(s) already cached")
        if report.failed_symbol:
            print(f"stopped at {report.failed_symbol} — re-run to resume")
            return 2
        return 0

    return asyncio.run(_go())


def _cmd_test(args) -> int:
    now = datetime.now()
    start = args.start or config.REGIME_START_TRACK_A
    end = args.end or now.date()
    names = args.feature or sorted(FEATURES)

    store = Store(args.db)
    try:
        if not store.symbols():
            print(f"{args.db} is empty — run `python -m currentflow.research backfill` first")
            return 1
        reports = [
            run_premise(store, FEATURES[n], name=n, horizon_days=args.horizon,
                        buckets=args.buckets, start=start, end=end, now=now,
                        stride_days=args.stride)
            for n in names
        ]
    finally:
        store.close()

    print(f"\npremise tests · {start} → {end} · horizon {args.horizon}d · {args.buckets} buckets\n")
    for r in reports:
        print(f"  {r.verdict_line()}")
        if r.mean_spread is not None:
            per_bucket = "  ".join(f"Q{i + 1} {m * 100:+.2f}%" for i, m in enumerate(r.bucket_means))
            print(f"      buckets (low→high feature): {per_bucket}")
        if r.skipped_days or r.dropped_missing_feature or r.dropped_missing_return:
            print(f"      dropped: {r.skipped_days} thin day(s), "
                  f"{r.dropped_missing_feature} no-feature, {r.dropped_missing_return} no-return")

    print("\ncaveats (apply to every line above):")
    for c in reports[0].caveats if reports else ():
        print(f"  - {c}")
    print(
        "\nA spread indistinguishable from zero does not prove the component is dead — it "
        "proves this test could not detect an edge at this horizon. Either way it is not "
        "evidence FOR the weight it currently carries."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="currentflow.research",
        description="Univariate premise tests for SMS components.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _common(p):
        p.add_argument("--db", default=DEFAULT_DB,
                       help=f"throwaway research store (default {DEFAULT_DB}; "
                            f"{PRODUCTION_DB} is refused)")
        p.add_argument("--start", type=Date.fromisoformat, default=None,
                       help="default = REGIME_START_TRACK_A (REGIME.md)")
        p.add_argument("--end", type=Date.fromisoformat, default=None)

    b = sub.add_parser("backfill", help="strided pull: full bars, broker on sampled days only")
    _common(b)
    b.add_argument("--symbols", help="comma list or a file of tickers")
    b.add_argument("--seed-from", help="take the symbol list from an existing store")
    b.add_argument("--stride", type=int, default=10, help="sampling stride in trading days")
    b.add_argument("--yes", action="store_true", help="spend the printed budget")
    b.set_defaults(fn=_cmd_backfill)

    t = sub.add_parser(
        "test", help="run the univariate premise tests",
        epilog="Keep the effective stride (--stride, else --horizon) EQUAL TO or a "
               "MULTIPLE OF the backfill --stride. A smaller stride samples days with no "
               "broker rows, silently shrinking the broker_top2_share sample — the "
               "'no-feature' drop count in the output is where that shows up.",
    )
    _common(t)
    t.add_argument("--feature", choices=sorted(FEATURES), action="append",
                   help="repeatable; default = all three")
    t.add_argument("--horizon", type=int, default=10, help="forward horizon in trading days")
    t.add_argument("--buckets", type=int, default=5, help="cross-sectional buckets")
    t.add_argument("--stride", type=int, default=None,
                   help="day sampling stride; default = horizon (non-overlapping)")
    t.set_defaults(fn=_cmd_test)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
