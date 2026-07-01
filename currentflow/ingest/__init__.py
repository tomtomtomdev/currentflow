"""Ingest orchestration — fetch-if-missing (ingest-once) → store → coverage check."""

from currentflow.ingest.pipeline import IngestResult, ingest_symbol

__all__ = ["IngestResult", "ingest_symbol"]
