"""Store — local DuckDB feature store keyed `(symbol, date, as_of)`.

Ingest-once (never re-pull a stored datum), look-ahead-safe reads (`as_of < decision_ts`),
and integrity/gap checks that keep 'empty ≠ zero'.
"""

from currentflow.store.db import Store
from currentflow.store.integrity import CoverageReport, classify_coverage

__all__ = ["Store", "CoverageReport", "classify_coverage"]
