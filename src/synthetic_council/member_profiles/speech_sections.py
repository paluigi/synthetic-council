"""Speech-stance memo sections (point 2c): one per (member, meeting).

For every member whose latest speech falls in the strict inter-meeting
window (see ``speech_union``), a single LLM call distils the speech into a
short second-person section — "In your most recent speech ('title', date)
you advocated …" — saved as markdown and reused verbatim by every memo
rebuild. Each speech qualifies for at most one meeting, so sections are
stable. Members with no qualifying speech simply get no section.

Rows with source == 'press' (TinyFish news coverage filling harvest gaps,
see ``collectors/press_speeches.py``) get a press-framed prompt: the
section summarises the positions attributed to the member in quoted
remarks plus analyst framing, and says explicitly that this is press
coverage rather than a verbatim speech.

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
PRESS_PROMPT_VERSION = "ss-press-v1"
MAX_SPEECH_CHARS = 14_000

_SECTION_SYSTEM = """You write one short briefing section for a central banker's internal meeting \
memo. You are given a speech (title, date, full text) delivered shortly before an ECB Governing \
Council monetary policy meeting.

Extract the monetary policy stance the member demonstrated in that speech. Write 2-4 sentences \
in the second person ("In your most recent speech you ..."), stating what the member advocated, \
emphasised or warned about — grounded ONLY in the speech text, quoting one or two key phrases in \
quotation marks where effective. Do not mention the Governing Council, this memo or any \
briefing context. Plain prose, no headings, no bullet points."""

_PRESS_SECTION_SYSTEM = """You write one short briefing section for a central banker's internal \
meeting memo. You are given a news article (publisher, title, date, full text) that covers the \
member's recent public remarks (a speech, interview or testimony) shortly before an ECB \
Governing Council monetary policy meeting.

Extract the monetary policy stance the member expressed in the remarks quoted or reported in \
the article. Write 2-4 sentences in the second person ("In your recent public remarks you \
..."), stating the positions attributed to you — quoting the member's own words in quotation \
marks where the article carries them, and clearly distinguishing them from the journalist's or \
analysts' framing. Begin by noting that this section is based on press coverage of your \
remarks, not the full text. If the article contains no substantive monetary-policy remarks by \
the member (e.g. it is about legal or institutional matters), say so in a single sentence and \
do not invent a stance. Ground everything ONLY in the article. Do not mention the Governing \
Council, this memo or any briefing context. Plain prose, no headings, no bullet points."""


@dataclass
class SpeechSectionsResult:
    n_sections: int
    n_llm_calls: int
    n_skipped_existing: int


def _slug(person: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", person.lower()).strip("-")
    return s


def _system_prompt(source: str) -> str:
    """Press rows (TinyFish news coverage) get the press-framed prompt."""
    return _PRESS_SECTION_SYSTEM if source == "press" else _SECTION_SYSTEM


def _section_text(speech: dict) -> str:
    press = speech.get("source") == "press"
    body = (speech["contents"] or "")[:MAX_SPEECH_CHARS]
    if press:
        head = f"NEWS ARTICLE TITLE: {speech['title']}\nDATE: {speech['speech_date']}"
    else:
        head = f"SPEECH TITLE: {speech['title']}\nDATE: {speech['speech_date']}"
    user = f"{head}\n\n{body}"
    return chat(
        _system_prompt(speech.get("source", "")), user, temperature=0.2, context="speech_sections"
    ).strip()


def build_speech_sections(
    meetings: list[str] | None = None,
    use_llm: bool = True,
) -> SpeechSectionsResult:
    from synthetic_council.inference import load_config

    model_id = load_config().role("generator").model_id
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
        generated = False
        if out.exists():
            text = out.read_text(encoding="utf-8").strip()
            n_existing += 1
        elif use_llm:
            text = _section_text(r)
            n_calls += 1
            out.write_text(text + "\n", encoding="utf-8")
            generated = True
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
                "prompt_version": (
                    PRESS_PROMPT_VERSION if r["source"] == "press" else PROMPT_VERSION
                ),
                # only claim the current model for text generated in THIS
                # run — a reused file's true generator lives in older archives
                "model": model_id if generated else "reused",
            }
        )
    df = pl.DataFrame(rows)
    if meetings is not None and INDEX_PATH.exists():
        # subset run: upsert instead of truncating — meetings not in this
        # invocation keep their index rows (their .md files stay untouched)
        untouched = pl.read_parquet(INDEX_PATH).filter(
            ~pl.col("meeting_date").is_in(meetings)
        )
        df = pl.concat([untouched, df], how="vertical_relaxed").sort(
            "meeting_date", "person"
        )
    df.write_parquet(INDEX_PATH)
    df.write_csv(INDEX_PATH.with_suffix(".csv"))
    return SpeechSectionsResult(
        n_sections=df.height, n_llm_calls=n_calls, n_skipped_existing=n_existing
    )


if __name__ == "__main__":
    import sys

    res = build_speech_sections(meetings=sys.argv[1:] or None)
    print(res)
