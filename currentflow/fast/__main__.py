"""`python -m currentflow.fast` — operator control for the LD-11 auto paper-trader (slice 15).

Arms/disarms Fast Mode (the ARMED-only cohort) and can step it once against the already-
ingested local store. The implementation is shared with Haste Mode (LD-12) in
`currentflow.autotrader_cli`; only the mode differs. **Paper only — never a live order (§15).**
"""

from __future__ import annotations

from datetime import datetime

from currentflow.autotrader_cli import main as _main
from currentflow.store.db import Store
from currentflow.store.schema import MODE_FAST


def main(argv: list[str] | None = None, *, store: Store | None = None,
         now: datetime | None = None) -> int:
    """CLI entry. `store`/`now` are injectable for tests."""
    return _main(argv, mode=MODE_FAST, store=store, now=now)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
