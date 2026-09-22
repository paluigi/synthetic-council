"""Speech-stance memo sections (point 2c): one per (member, meeting).

For every member whose latest speech falls in the strict inter-meeting
window (see ``speech_union``), a single LLM call distils the speech into a
short second-person section — "In your most recent speech ('title', date)
you advocated …" — saved as markdown and reused verbatim by every memo
rebuild. Each speech qualifies for at most one meeting, so sections are
stable. Members with no qualifying speech simply get no section.

Output:
- data/processed/speech_sections/{meeting_date}_{person}.md
- data/processed/speech_sections_index.parquet (+ .csv)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl

from synthetic_council.config import PROCESSED_DIR
from synthetic_council.mpa_extract.llm import chat

SECTIONS_DIR = PROCESSED_DIR / "speech_sections"
INDEX_PATH = SECTIONS_DIR / "speech_sections_index.parquet"
JOIN_PATH = PROCESSED_DIR / "latest_speech_per_meeting.parquet"

PROMPT_VERSION = "ss-v1"
MAX_SPEECH_CHARS = 14_000

_SECTION_SYSTEM = """You write one short briefing section for a central banker's internal meeting \
memo. You are given a speech (title, date, full text) delivered shortly before an ECB Governing \
Council monetary policy meeting.

Extract the monetary policy stance the member demonstrated in that speech. Write 2-4 sentences \
in the second person ("In your most recent speech you ..."), stating what the member advocated, \
emphasised or warned about — grounded ONLY in the speech text, quoting one or two key phrases in \
quotation marks where effective. Do not mention the Governing Council, this memo or any \
briefing context. Plain prose, no headings, no bullet points."""


@dataclass
class SpeechSectionsResult:
    n_sections: int
    n_llm_calls: int
    n_skipped_existing: int


def _slug(person: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", person.lower()).strip("-")
    return s


def _section_text(speech: dict) -> str:
    body = (speech["contents"] or "")[:MAX_SPEECH_CHARS]
    user = f"SPEECH TITLE: {speech['title']}\nDATE: {speech['speech_date']}\n\n{body}"
    return chat(_SECTION_SYSTEM, user, temperature=0.2).strip()


def build_speech_sections(
    meetings: list[str] | None = None,
    use_llm: bool = True,
) -> SpeechSectionsResult:
    join = pl.read_parquet(JOIN_PATH)
    if meetings:
        join = join.filter(pl.col("meeting_date").is_in(meetings))
    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    n_calls = 0
    n_existing = 0
    for r in join.iter_rows(named=True):
        fname = f"{r['meeting_date']}_{_slug(r['person'])}.md"
        out = SECTIONS_DIR / fname
        if out.exists():
            text = out.read_text(encoding="utf-8").strip()
            n_existing += 1
        elif use_llm:
            text = _section_text(r)
            n_calls += 1
            out.write_text(text + "\n", encoding="utf-8")
        else:
            continue
        rows.append(
            {
                "meeting_date": r["meeting_date"],
                "person": r["person"],
                "speech_date": r["speech_date"],
                "title": r["title"],
                "source": r["source"],
                "file": fname,
                "prompt_version": PROMPT_VERSION,
            }
        )
    df = pl.DataFrame(rows)
    df.write_parquet(INDEX_PATH)
    df.write_csv(INDEX_PATH.with_suffix(".csv"))
    return SpeechSectionsResult(
        n_sections=df.height, n_llm_calls=n_calls, n_skipped_existing=n_existing
    )


if __name__ == "__main__":
    import sys

    res = build_speech_sections(meetings=sys.argv[1:] or None)
    print(res)
