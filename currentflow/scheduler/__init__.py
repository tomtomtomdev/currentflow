"""Automated per-feed ingestion scheduler (slice 12).

Infra, not a spec §11 slice (the build order ends at 9 — this is the slice-10 posture).
Replaces the manual `run.sh ingest` / empty-store bootstrap with a daemon that fires each
already-implemented feed on its own cadence during Mon–Fri trading hours and writes to the
DuckDB cache. It writes cache ONLY — never scores, never touches RULE A/B, and `as_of`
stamping is unchanged, so look-ahead safety is untouched. The calc engine keeps reading only
from the cache. Ingest-once still holds: a restart, a holiday, or a double-tick is a cheap
no-op, never a re-pull.

Layout:
  * `schedule` — the declarative cadence surface (the only thing you edit to retune).
  * `calendar` — the trading-hours gate + pure due-math (injectable clock).
  * `runner`   — the tick loop that dispatches due feeds through the existing ingest surface.
"""
