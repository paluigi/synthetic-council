"""Profile synthesis: deterministic facts + Tavily + Wikipedia -> one markdown
profile card per GC member (for manual review and as agent-persona input).

The synthesised body is LLM-written but strictly grounded: it may only use
the provided evidence, must quote key phrases, and tags every section with
its sources. Raw evidence stays archived under data/raw/profiles/. In
``no_llm`` mode the card is assembled deterministically from the verbatim
Tavily answers instead (verbose but offline-safe).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from synthetic_council.config import PROCESSED_DIR
from synthetic_council.member_profiles import tavily
from synthetic_council.member_profiles.wikipedia import fetch_page
from synthetic_council.mpa_extract.ce_tenure import CHIEF_ECONOMISTS
from synthetic_council.mpa_extract.llm import chat

PROFILES_DIR = PROCESSED_DIR / "member_profiles"
PROMPT_VERSION = "prof-v1"

TRAIT_TITLES = {
    "stance": "Monetary policy stance",
    "career": "Career and professional background",
    "thinking": "Economic thinking and intellectual formation",
    "mandate": "Approach to the price stability mandate",
}

_SYNTH_SYSTEM = """You write the profile card of one member of the ECB Governing Council, for use \
as the persona of an agent in a research simulation of council deliberations.

You receive: (a) verified facts (roles, tenures), (b) per-trait evidence from a web-search \
assistant (each with its raw answer and sources), (c) a Wikipedia extract.

Rules:
1. Use ONLY the provided evidence. Never invent facts, dates or quotes.
2. Write one section per trait heading given, 2-5 sentences each, third person.
3. Quote distinctive phrases from the evidence in quotation marks.
4. Tag the end of each section with its evidence, e.g. "[tavily-stance, wikipedia]"; use "[]" \
only if a section has no evidence at all — then write "(no evidence collected)".
5. Where evidence streams disagree, prefer the Wikipedia/verified facts and note the conflict.
6. Output markdown only: the trait sections (## headings), nothing before or after them."""


def _role_desc(role: str) -> str:
    r = role.lower()
    if "president" in r and "vice" not in r:
        return "ECB President"
    if "vice" in r:
        return "ECB Vice-President"
    if "executive board" in r:
        return "ECB Executive Board member"
    if "governor" in r:
        return f"{role.split(', ', 1)[-1]} governor"
    return role


def member_facts(memberships: pl.DataFrame) -> dict[str, dict]:
    """Verified per-person facts from the Wayback tenures + CE tenure table."""
    out: dict[str, dict] = {}
    for person in memberships["person"].unique().to_list():
        tenures = memberships.filter(pl.col("person") == person).sort("start")
        roles = []
        country = None
        for r in tenures.iter_rows(named=True):
            span = f"{r['start']} → {r['end']}"
            label = r["role"]
            if any(person == ce and r["start"] >= cs for ce, cs, _ in CHIEF_ECONOMISTS):
                label += " (Chief Economist)"
            roles.append(f"{label}, {span}")
            country = country or r["country"]
        out[person] = {"roles": roles, "country": country}
    return out


def _evidence_bundle(person: str, role_desc: str) -> tuple[dict, dict]:
    tav = tavily.fetch_all_traits(person, role_desc)
    wiki = fetch_page(person, role_desc)
    return tav, wiki


def _llm_body(tav: dict, wiki: dict) -> str:
    trait_headings = "\n".join(f"- ## {TRAIT_TITLES[k]}" for k in tavily.TRAITS)
    parts = [
        f"(a) Verified facts are in the header above. Trait sections to write:\n{trait_headings}"
    ]
    for trait, payload in tav.items():
        sources = "; ".join(r["url"] for r in payload.get("results", [])[:3])
        parts.append(
            f"(b) Tavily evidence for trait '{trait}' (query: {payload['query']}):\n"
            f"ANSWER: {payload['answer']}\nSOURCES: {sources}"
        )
    parts.append(f"(c) Wikipedia extract:\n{(wiki.get('extract') or '')[:8000]}")
    return chat(_SYNTH_SYSTEM, "\n\n".join(parts), temperature=0.2)


def _verbatim_body(tav: dict, wiki: dict) -> str:
    sections = []
    for trait in tavily.TRAITS:
        title = TRAIT_TITLES[trait]
        if trait in tav and tav[trait].get("answer"):
            srcs = "\n".join(
                f"  - {r['title']}: {r['url']}" for r in tav[trait].get("results", [])[:3]
            )
            sections.append(f"## {title}\n\n{tav[trait]['answer']}\n\nSources:\n{srcs}")
        else:
            sections.append(f"## {title}\n\n(no evidence collected)")
    if wiki.get("extract"):
        sections.append(f"## Wikipedia extract ({wiki['title']})\n\n{wiki['extract'][:4000]}")
    return "\n\n".join(sections)


def build_profile(
    person: str,
    facts: dict,
    use_llm: bool = True,
) -> Path:
    role_desc = _role_desc(facts["roles"][-1].split(",")[0]) if facts["roles"] else "central banker"
    tav, wiki = _evidence_bundle(person, role_desc)
    if use_llm and any(tav.get(k, {}).get("answer") for k in tavily.TRAITS):
        body = _llm_body(tav, wiki)
        method = f"llm-synthesised (prompt {PROMPT_VERSION})"
    else:
        body = _verbatim_body(tav, wiki)
        method = "verbatim evidence"
    country = facts["country"] or "— (Executive Board)"
    header = (
        f"# Member profile — {person}\n\n"
        f"**Country:** {country}\n\n"
        f"**Roles:** " + "; ".join(facts["roles"]) + "\n\n"
    )
    footer = (
        f"\n\n---\nProvenance: {method} · generated {date.today().isoformat()} · "
        f"raw evidence: data/raw/profiles/tavily/{tavily.slug(person)}/, "
        f"data/raw/profiles/wikipedia/{tavily.slug(person)}.json\n"
    )
    out = PROFILES_DIR / f"{person}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + body.strip() + "\n" + footer, encoding="utf-8")
    return out
