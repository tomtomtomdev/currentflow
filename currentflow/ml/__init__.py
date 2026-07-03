"""Scale / ML layer (spec §11 step 9, LD-8) — GATED.

ML is deferred to Phase 4+ and gated: the rules system must FIRST demonstrate ≥3 months of
forward paper with a positive walk-forward Sharpe before the ML layer may run at all. When
admitted, ML is confined to a signal-weight **optimizer / ranker over engineered features**
under mandatory **purged + embargoed** cross-validation. Weights are never hand-edited live —
the optimizer is the sole writer of the weight surface.

The whole package is closed by `ml.admission` until the gate opens (the ML analogue of RULE B):
every entry point calls `require_admission` first, so nothing here can run ahead of the rules.
"""
