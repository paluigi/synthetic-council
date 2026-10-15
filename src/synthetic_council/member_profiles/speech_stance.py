"""Speech-grounded policy orientation for member profiles (point 2d).

Instead of a standalone numeric hawkishness prior, each governor's revealed
policy orientation is distilled from their OWN speeches in the corpus
(union of ECB/BIS/NCB/CBS sources; press coverage excluded — it is not
their verbatim voice) and embedded as one section of the member profile,
which the memo already carries via ``profile_text``. Hansen–Kazinnik-style
hawkishness, reflected qualitatively in the persona rather than estimated
as a number.

One generator-role LLM call per member over an evenly-spread sample of
their speeches (most recent + earliest + spread; each truncated). The
section is written in the second person, grounded ONLY in the excerpts,
and refuses to invent an orientation the excerpts don't support. The
call is archived under ``data/processed/llm_calls/speech_stance.jsonl``.

Profile application is idempotent and review-safe: the section is
inserted before the first trait heading (early, inside the memo's
2,500-char profile cap) or replaces its own previous block; everything
else in the profile file is preserved verbatim.

Output:
- data/processed/profiles/speech_stance/{Person}.md   (the raw section)
- the section embedded in data/processed/member_profiles/{Person}.md
"""

from __future__ import annotations

import polars as pl

from synthetic_council.config import PROCESSED_DIR
from synthetic_council.mpa_extract.llm import chat

PROFILES_DIR = PROCESSED_DIR / "member_profiles"
SECTIONS_DIR = PROCESSED_DIR / "profiles" / "speech_stance"

HEADING = "## Policy orientation — from your own speeches"
MAX_SPEECHES = 8
MAX_SPEECH_CHARS = 4_000

_STANCE_SYSTEM = """You write one section of a central banker's persona profile, to be read by \
that central banker before an ECB Governing Council meeting. You are given excerpts of the \
member's OWN speeches and public remarks, spread over time.

Characterise the member's revealed monetary policy orientation: hawkish, dovish or centrist \
leanings as actually expressed, the themes and arguments they lean on (inflation persistence, \
growth, financial stability, ...), and how their positioning evolved across the period covered. \
Write in the second person ("You have consistently ..."), grounded ONLY in the excerpts — quote \
one or two key phrases in quotation marks. Do not give a score or a rating; this is qualitative \
orientation evidence, not a measurement. If the excerpts are too thin or generic to characterise \
an orientation, say so plainly in one or two sentences instead of inventing one. 2-4 sentences \
of substance, plain prose, no headings, no bullet points."""


def _load_corpus() -> pl.DataFrame:
    """All verbatim speech sources; press coverage excluded (not the
    member's own voice)."""
    from synthetic_council.speech_union import union_corpus

    return union_corpus().filter(pl.col("source") != "press")


def select_speeches(corpus: pl.DataFrame, person: str, k: int = MAX_SPEECHES) -> list[dict]:
    """Evenly-spread sample of one speaker's speeches (earliest → latest)."""
    sub = corpus.filter(pl.col("speaker") == person).sort("date")
    rows = sub.select("date", "title", "contents").to_dicts()
    if not rows:
        return []
    if len(rows) <= k:
        return rows
    step = (len(rows) - 1) / (k - 1)
    idx = sorted({round(i * step) for i in range(k)})
    return [rows[i] for i in idx]


def _section_text(person: str, speeches: list[dict]) -> str:
    excerpts = []
    for s in speeches:
        body = (s["contents"] or "")[:MAX_SPEECH_CHARS]
        excerpts.append(f"--- {s['date']} | {s['title'] or '(untitled)'} ---\n{body}")
    user = (
        f"MEMBER: {person}\n\nSPEECH EXCERPTS ({len(speeches)}, chronological):\n\n"
        + "\n\n".join(excerpts)
    )
    return chat(_STANCE_SYSTEM, user, temperature=0.2, context="speech_stance").strip()


def apply_to_profile(person: str, section: str) -> bool:
    """Insert/refresh the section in the profile markdown; returns True if
    the file changed. Everything outside the section block is preserved."""
    path = PROFILES_DIR / f"{person}.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = f"{HEADING}\n\n{section}\n"
    if HEADING in text:  # replace the previous block only
        start = text.index(HEADING)
        nxt = text.find("\n## ", start + len(HEADING))
        end = text.find("\n---\n", start + len(HEADING))
        stops = [p for p in (nxt, end) if p != -1]
        stop = min(stops) + 1 if stops else len(text)
        new = text[:start] + block + text[stop:]
    else:
        # insert before the first trait heading; failing that (sparse
        # profiles with no trait sections), before the provenance footer —
        # profile_text only reads up to the footer, so the section must
        # stay ahead of it
        first_trait = text.find("\n## ")
        footer = text.find("\n---\n")
        stops = [p for p in (first_trait, footer) if p != -1]
        insert_at = (min(stops) + 1) if stops else len(text.rstrip("\n"))
        if stops:
            new = text[:insert_at] + block + "\n" + text[insert_at:]
        else:
            new = text.rstrip("\n") + "\n\n" + block
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def build_speech_stance(
    person: str, corpus: pl.DataFrame | None = None, k: int = MAX_SPEECHES
) -> str | None:
    """Generate (or reuse) the section for one member; returns the text."""
    corpus = corpus if corpus is not None else _load_corpus()
    out = SECTIONS_DIR / f"{person}.md"
    speeches = select_speeches(corpus, person, k)
    if not speeches:
        return None
    if out.exists():  # review-safe: regenerate only by deleting the file
        return out.read_text(encoding="utf-8").strip()
    section = _section_text(person, speeches)
    SECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(section + "\n", encoding="utf-8")
    return section


def run(persons: list[str] | None = None, k: int = MAX_SPEECHES) -> dict[str, int]:
    """Build + embed speech-stance sections for profiled members."""
    corpus = _load_corpus()
    targets = persons or [p.stem for p in sorted(PROFILES_DIR.glob("*.md"))]
    n_new = n_embedded = n_no_speeches = 0
    for person in targets:
        section = build_speech_stance(person, corpus, k)
        if section is None:
            n_no_speeches += 1
            continue
        n_new += 1
        if apply_to_profile(person, section):
            n_embedded += 1
    return {
        "members": len(targets),
        "sections": n_new,
        "embedded": n_embedded,
        "no_speeches": n_no_speeches,
    }


if __name__ == "__main__":
    import sys

    stats = run(persons=sys.argv[1:] or None)
    print(stats)
