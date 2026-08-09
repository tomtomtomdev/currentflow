"""`python -m currentflow.haste` — operator control for the LD-12 auto paper-trader (slice 16).

Arms/disarms Haste Mode (the wider `WATCH ∪ ARMED` cohort — the `ARMED@70` arming cut dropped)
and can step it once against the already-ingested local store. The implementation is shared
with Fast Mode (LD-11) in `currentflow.autotrader_cli`; only the mode differs, and only one of
the two may be armed at a time. **Paper only — never a live order (§15).**
"""

from __future__ import annotations

from datetime import datetime

from currentflow.autotrader_cli import main as _main
from currentflow.store.db import Store
from currentflow.store.schema import MODE_HASTE


def main(argv: list[str] | None = None, *, store: Store | None = None,
         now: datetime | None = None) -> int:
    """CLI entry. `store`/`now` are injectable for tests."""
    return _main(argv, mode=MODE_HASTE, store=store, now=now)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
