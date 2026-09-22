"""Extraction of the Chief Economist's proposal from each account (point 1c).

Deterministic first: section 1 of every account ("…monetary policy
considerations and policy options") closes with the Chief Economist's
proposal, phrased e.g.

    Mr Lane therefore proposed that the three key ECB interest rates be
    lowered by 25 basis points.

We locate the paragraph(s) in which the tenure-table Chief Economist
(Mr/Ms <surname>) "proposes/proposed", capture them verbatim, and parse the
proposed direction (hike/cut/hold) and size (basis points). If the regex
fails, an LLM fallback extracts the proposal (flagged in
``extraction_method``). The observed proposal speaker is recorded so the
tenure table itself is continuously verified (``ce_mismatch``).

Output: data/processed/account_ce_proposal.parquet + .csv
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl

from synthetic_council.config import PROCESSED_DIR, RAW_DIR
from synthetic_council.mpa_extract.ce_tenure import chief_economist_on
from synthetic_council.mpa_extract.llm import chat

ACCOUNTS_DIR = RAW_DIR / "accounts"
OUT_PATH = PROCESSED_DIR / "account_ce_proposal.parquet"

_SPEAKER_RE = re.compile(r"(?:Mr|Ms|Mrs|Dr)\.?\s+(?:[A-ZÀ-Ž][\w.'-]*\s+)?([A-ZÀ-Ž][\w.'-]*)\b")
_BP_RE = re.compile(r"(\d{1,3})\s+basis points", re.IGNORECASE)
_HIKE_RE = re.compile(r"\b(?:rais|increas|hik)(?:e|es|ed|ing)\b", re.IGNORECASE)
_CUT_RE = re.compile(
    r"\b(?:lower(?:s|ed|ing)?|cut(?:s|ting)?|reduced?|reducing|reductions?)\b", re.IGNORECASE
)
# explicit hold constructions, checked first so counterfactuals ("a further
# rate cut would…", "inflation set to remain above target") cannot flip a hold
_HOLD_RATE_RE = re.compile(
    r"(?:maintain\w*|keep\w*|leav\w*)[^.]{0,120}?"
    r"(?:interest rates?|policy rates?|key (?:ECB )?rates)"
    r"|(?:interest|policy|key (?:ECB )?)\s*rates?[^.]{0,80}?(?:unchanged|"
    r"at (?:their )?(?:current|present(?: or lower)?) levels)",
    re.IGNORECASE,
)
_RATE_SENT_RE = re.compile(
    r"interest rates?|policy rates?|key (?:ECB )?rates|deposit facility"
    r"|main refinancing|marginal lending",
    re.IGNORECASE,
)

_FALLBACK_SYSTEM = (
    "You extract the Chief Economist's proposal from an ECB monetary policy "
    "account. The Chief Economist is {ce}. Find the passage where this person "
    "proposes the monetary policy decision (usually: '{ce_short} therefore "
    "proposed ...'). Reply with the verbatim proposal text only, no commentary. "
    "If no such proposal exists, reply exactly: NOT FOUND."
)


@dataclass
class CeProposal:
    meeting_date: str
    chief_economist: str
    proposal_speaker: str | None
    proposal_text: str
    proposed_direction: str | None
    proposed_bp: int | None
    ce_mismatch: bool
    extraction_method: str
    source_span: str


def _paragraphs(markdown: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", markdown) if p.strip()]


def _speaker_of(paragraph: str) -> str | None:
    m = re.search(_SPEAKER_RE, paragraph)
    return m.group(0) if m else None


def _surname(name: str) -> str:
    return name.split()[-1]


def _direction(text: str) -> tuple[str | None, int | None]:
    """(direction, bp) from the proposal sentence itself.

    The sentence containing "propos…" is authoritative: direction keywords
    elsewhere in the paragraph ("lower policy rates would…", "reduce the pace
    of purchases") routinely describe counterfactuals or asset-purchase
    calibration. If the proposal sentence does not mention rates, later
    sentences of the same paragraph are tried (colon-lists like
    "proposed: (a) … ; (b) keeping the key rates unchanged" run on); if none
    mention rates, the proposal is a package (APP/TLTRO/guidance) proposal
    and (None, None) is returned.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    start = next(
        (i for i, s in enumerate(sentences) if re.search(r"\bpropos", s, re.IGNORECASE)), 0
    )
    for s in sentences[start : start + 2]:
        if not _RATE_SENT_RE.search(s):
            continue
        bp_m = _BP_RE.search(s)
        bp = int(bp_m.group(1)) if bp_m else None
        if _HOLD_RATE_RE.search(s):
            return "hold", None
        if _HIKE_RE.search(s):
            return "hike", bp
        if _CUT_RE.search(s):
            return "cut", bp
        return None, bp
    return None, None


def extract_one(meeting_date: str, markdown: str, use_llm_fallback: bool = True) -> CeProposal:
    ce = chief_economist_on(meeting_date)
    surname = _surname(ce) if ce else ""
    # paragraphs where the CE "proposes" — the last such paragraph of §1 is the
    # canonical proposal; capture every matching paragraph in order
    pat = re.compile(
        rf"(?:Mr|Ms|Mrs|Dr)\.?\s+[\w.'-]*\s*{re.escape(surname)}\b[^.{{}}]*?\b"
        rf"propos(?:e|ed|al|ing)\b[^.{{}}]*?\.",
        re.IGNORECASE,
    )
    hits = [p for p in _paragraphs(markdown) if pat.search(p)]
    method = "deterministic"
    text = ""
    speaker = None
    if hits:
        text = "\n\n".join(hits)
        speaker = _speaker_of(hits[-1])
    elif use_llm_fallback:
        answer = chat(
            _FALLBACK_SYSTEM.format(ce=ce, ce_short=f"Mr {surname}"),
            markdown,
        ).strip()
        if answer and answer.upper() != "NOT FOUND":
            text = answer
            speaker = _speaker_of(answer)
            method = "llm"
        else:
            method = "llm_not_found"
    else:
        method = "failed"
    direction, bp = _direction(text) if text else (None, None)
    mismatch = bool(speaker and surname.lower() not in speaker.lower())
    return CeProposal(
        meeting_date=meeting_date,
        chief_economist=ce or "",
        proposal_speaker=speaker,
        proposal_text=text,
        proposed_direction=direction,
        proposed_bp=bp,
        ce_mismatch=mismatch,
        extraction_method=method,
        source_span=text[:160],
    )


def extract(meetings: list[str] | None = None, use_llm_fallback: bool = True) -> int:
    files = (
        sorted(ACCOUNTS_DIR.glob("*_account.md"))
        if meetings is None
        else [ACCOUNTS_DIR / f"{d}_account.md" for d in meetings]
    )
    rows = []
    for path in files:
        md = path.read_text(encoding="utf-8")
        if md.startswith("---"):
            md = md.split("---", 2)[2]
        rows.append(extract_one(path.name[:10], md, use_llm_fallback).__dict__)
    df = pl.DataFrame(rows)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    df.write_csv(OUT_PATH.with_suffix(".csv"))
    n_det = int((df["extraction_method"] == "deterministic").sum())
    n_mis = int(df["ce_mismatch"].sum())
    print(
        f"CE proposals: {df.height} meetings, deterministic={n_det}, "
        f"llm={df.height - n_det}, tenure mismatches={n_mis}"
    )
    return df.height


if __name__ == "__main__":
    extract()
