"""Decision-mode extraction from the accounts (point 1b): unanimity /
consensus / majority, with probabilities.

Two stages per meeting (the deterministic keyword harvest alone proved too
brittle — the agreement language is scattered across the whole §2 discussion):

1. **Evidence curation (chat LLM)** — reads §2 "Governing Council's
   discussion and monetary policy decisions" and extracts VERBATIM every
   passage characterising how the decision was reached ("all members
   supported the proposal", "there was broad consensus", "a majority of
   members", "some members preferred", counterproposals, …). Quotes are
   stored to ``data/processed/decision_mode_evidence/{date}.json`` so they
   remain auditable against the archived account markdown; a deterministic
   keyword harvest over §2 is kept as fallback when the LLM output cannot be
   parsed.
2. **Classification (Jev)** — one ChoiceQuestion over the three categories,
   with the Bruegel/Trichet definitions in the criteria; the full probability
   distribution is saved.

Output: data/processed/account_decision_mode.parquet + .csv
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import polars as pl

from synthetic_council.config import PROCESSED_DIR, RAW_DIR
from synthetic_council.jev import DEFAULT_JEV_MODEL, jev_choice
from synthetic_council.mpa_extract.llm import chat

ACCOUNTS_DIR = RAW_DIR / "accounts"
EVIDENCE_DIR = PROCESSED_DIR / "decision_mode_evidence"
OUT_PATH = PROCESSED_DIR / "account_decision_mode.parquet"

PROMPT_VERSION = "dm-v1"

SECTION2_RE = re.compile(r"^#{1,3}\s+2\.\s+Governing Council", re.MULTILINE)
SECTION_END_RE = re.compile(r"^#{1,3}\s+Meeting of ")

KEYWORDS = re.compile(
    r"unanim\w*|consensus|broad(?:ly)? (?:agree|support)|majority|widely (?:concurre|share)"
    r"|all members (?:support|agree|endorse)|some members|a few members|many members|most members"
    r"|minority|oppos\w*|prefer\w*|counterproposal|reserv\w*|prevailed|dissent",
    re.IGNORECASE,
)

EVIDENCE_SYSTEM = """You read section 2 ("Governing Council's discussion and monetary policy \
decisions") of an ECB monetary policy account and extract, VERBATIM, every passage that \
characterises HOW the rate decision was reached — the degree and distribution of agreement \
or disagreement among members.

Include:
- passages describing support for or opposition to the proposal (e.g. "all members supported \
the proposal", "there was broad consensus", "a majority of members", "some members expressed \
a preference for ...", "widely concurred", "a few members argued");
- any counterproposal and its fate;
- the closing passage describing what was decided and how ("upon a proposal by the \
President, the Governing Council took the monetary policy decisions ...").

Exclude passages about substance only (inflation outlook, market developments) unless they \
frame agreement/disagreement.

Rules: copy exactly, no paraphrase; keep each quote to at most two consecutive sentences; \
order the quotes as they appear in the text. Reply with a JSON array of strings and nothing \
else."""

DECISION_MODE_CRITERIA = {
    "unanimity": (
        "all members fully endorsed the decision — the account reports unanimous support "
        "with no member expressing a reservation or a preference for another option"
    ),
    "consensus": (
        "broad agreement without a vote — members could 'agree or not object' "
        "(e.g. 'broad consensus', 'widely concurred', 'most members supported while "
        "others did not object'); disagreement, if any, is peripheral and does not "
        "amount to open opposition"
    ),
    "majority": (
        "a decision taken by a majority with explicit dissent — 'a (large) majority of "
        "members', open opposition, rejected counterproposals, or a sizeable minority "
        "preferring a different option"
    ),
}

DECISION_MODE_INSTRUCTIONS = (
    "You are given verbatim evidence quotes from an ECB Governing Council monetary "
    "policy account, describing how the rate decision was reached. Choose the category "
    "that best characterises the decision process. Treat 'large majority' as majority."
)


@dataclass
class DecisionMode:
    meeting_date: str
    label: str
    probabilities: str  # JSON dict outcome -> probability
    margin: float | None
    evidence_method: str
    evidence_file: str
    n_quotes: int
    model: str
    prompt_version: str
    notes: str


def section2(markdown: str) -> str:
    m = SECTION2_RE.search(markdown)
    if not m:
        return ""
    end = SECTION_END_RE.search(markdown, m.end())
    return markdown[m.end() : end.start() if end else len(markdown)].strip()


def _parse_quotes(answer: str) -> list[str]:
    answer = answer.strip()
    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer, flags=re.DOTALL)
    try:
        quotes = json.loads(answer)
    except json.JSONDecodeError:
        return []
    if not isinstance(quotes, list):
        return []
    return [q.strip() for q in quotes if isinstance(q, str) and len(q.strip()) > 15]


def keyword_harvest(sec: str) -> list[str]:
    """Deterministic fallback: sentences in §2 containing agreement keywords."""
    quotes = []
    for sent in re.split(r"(?<=[.!?])\s+", sec):
        if KEYWORDS.search(sent):
            quotes.append(sent.strip())
    return quotes


def build_evidence(meeting_date: str, markdown: str, use_llm: bool = True) -> tuple[list[str], str]:
    """Curated verbatim quotes + method ('llm' | 'keyword_fallback')."""
    sec = section2(markdown)
    if not sec:
        return [], "no_section2"
    out_file = EVIDENCE_DIR / f"{meeting_date}.json"
    if out_file.exists():  # resumable: reuse curated evidence
        data = json.loads(out_file.read_text(encoding="utf-8"))
        return data["quotes"], data["method"]
    quotes: list[str] = []
    method = "keyword_fallback"
    if use_llm:
        answer = chat(EVIDENCE_SYSTEM, sec[:60_000])
        quotes = _parse_quotes(answer)
        if quotes:
            method = "llm"
    if not quotes:
        quotes = keyword_harvest(sec)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(
            {"meeting_date": meeting_date, "method": method, "quotes": quotes},
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return quotes, method


async def classify(
    meeting_date: str,
    quotes: list[str],
    method: str,
    model: str = DEFAULT_JEV_MODEL,
) -> DecisionMode:
    res = await jev_choice(
        state={
            "meeting_date": meeting_date,
            "evidence": "\n\n".join(f"[{i + 1}] {q}" for i, q in enumerate(quotes)),
        },
        criteria=DECISION_MODE_CRITERIA,
        instructions=DECISION_MODE_INSTRUCTIONS,
        model=model,
    )
    return DecisionMode(
        meeting_date=meeting_date,
        label=res.outcome,
        probabilities=json.dumps(res.probabilities),
        margin=res.margin,
        evidence_method=method,
        evidence_file=f"decision_mode_evidence/{meeting_date}.json",
        n_quotes=len(quotes),
        model=model,
        prompt_version=PROMPT_VERSION,
        notes="",
    )


def run(
    meetings: list[str] | None = None,
    use_llm: bool = True,
    model: str = DEFAULT_JEV_MODEL,
) -> int:
    """Full pipeline over all accounts (or selected meetings)."""
    import asyncio

    files = (
        sorted(ACCOUNTS_DIR.glob("*_account.md"))
        if meetings is None
        else [ACCOUNTS_DIR / f"{d}_account.md" for d in meetings]
    )
    rows: list[dict] = []
    for path in files:
        meeting_date = path.name[:10]
        markdown = path.read_text(encoding="utf-8")
        if markdown.startswith("---"):
            markdown = markdown.split("---", 2)[2]
        quotes, method = build_evidence(meeting_date, markdown, use_llm=use_llm)
        if not quotes:
            print(f"WARNING: no evidence for {meeting_date} ({method})")
            continue
        rows.append(asyncio.run(classify(meeting_date, quotes, method, model)).__dict__)
    df = pl.DataFrame(rows)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    df.write_csv(OUT_PATH.with_suffix(".csv"))
    print(
        f"decision modes: {df.height} meetings; labels="
        + str(df.group_by("label").len().sort("len", descending=True).rows())
    )
    return df.height


if __name__ == "__main__":
    import sys

    meetings = sys.argv[1:] or None
    run(meetings)
