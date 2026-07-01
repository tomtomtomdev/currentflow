"""CurrentFlow — private single-operator IDX smart-money screener & flow terminal.

See LOCKED_SPEC.md (v1.1, LOCKED) for behavior. Slice 1 implements the data layer:
DAL (Stockbit `exodus`) + DuckDB store + integrity/gap checks + look-ahead-safe reads.
"""

__version__ = "0.1.0"
