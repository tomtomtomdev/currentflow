"""`python -m currentflow.scheduler` — the standalone ingestion daemon (slice 12).

Opens the SAME DuckDB the terminal reads, then runs the tick loop against the operator's own
live session (built by the runner; 401 fails loud). Meant to run under launchd
(`deploy/com.currentflow.scheduler.plist`) but works as a bare foreground daemon too.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from currentflow.ingest.__main__ import DEFAULT_DB
from currentflow.logging_setup import configure_logging
from currentflow.scheduler.runner import run_loop
from currentflow.store.db import Store


def main(argv: list[str] | None = None, **overrides) -> int:
    """CLI entry. `overrides` (store/client/transport/clock/tick_seconds/max_ticks) are for tests."""
    configure_logging()  # persist dal `net-error` lines to logs/net.log
    parser = argparse.ArgumentParser(
        prog="currentflow.scheduler",
        description="Automated per-feed ingestion daemon (writes cache only; slice 12).",
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB,
        help=f"DuckDB path — must match the terminal (default {DEFAULT_DB})",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="run a single tick and exit (smoke check / cron-style invocation)",
    )
    args = parser.parse_args(argv)

    store = overrides.pop("store", None) or Store(args.db)
    loop_kwargs = {"max_ticks": 1} if args.once else {}
    loop_kwargs.update(overrides)
    try:
        return asyncio.run(run_loop(store, **loop_kwargs))
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
