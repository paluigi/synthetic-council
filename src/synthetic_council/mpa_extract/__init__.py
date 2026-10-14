"""Extraction pipeline over the ECB Monetary policy accounts.

Input: the markdown accounts written by ``collectors.accounts``
(``data/raw/accounts/{meeting_date}_account.md``).

Outputs (``data/processed/``, parquet + csv):

- ``account_participants``    — attendees + voting rights (deterministic)
- ``account_decision_mode``   — unanimity/consensus/majority probabilities (Jev
                                over LLM-curated verbatim evidence)
- ``account_ce_proposal``     — the Chief Economist's proposal text
                                (deterministic regex, LLM fallback)

Every output carries provenance: ``source_span`` (verbatim quote or offsets)
and ``extraction_method`` (deterministic | llm).
"""
