"""Streamlit terminal (spec §9/§10; design/ handoff). Observation modules only in
slice 2. RULE B: no module here may render an SMS, probability, or buy/sell verb —
gated modules show components/observations until the paper-trade engine promotes
them (server-authoritative, never a client toggle)."""
