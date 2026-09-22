"""Tavily per-trait evidence for GC member profiles.

One simple, single-purpose query per stable, time-invariant trait per member
(the profile is built once per person — no meeting- or era-specific questions
such as dissents or views on negative rates). The raw answer AND the returned
sources are archived verbatim per (member, trait) for audit and re-querying.

Requires TAVILY_API_KEY (in .env.local or the environment).
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path

import httpx

from synthetic_council.config import RAW_DIR, USER_AGENT

TAVILY_URL = "https://api.tavily.com/search"
EVIDENCE_DIR = RAW_DIR / "profiles" / "tavily"

# trait key -> query template ({name}, {role_desc} substituted)
TRAITS: dict[str, str] = {
    "stance": "{name} ({role_desc}) monetary policy stance: hawkish or dovish?",
    "career": "{name} economist career background",
    "thinking": "{name} economic thinking and intellectual formation",
    "mandate": "{name} approach to the ECB price stability mandate",
}


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _api_key() -> str:
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        env_local = Path(".env.local")
        if env_local.exists():
            for line in env_local.read_text().splitlines():
                if line.startswith("TAVILY_API_KEY="):
                    key = line.partition("=")[2].strip().strip("'\"")
    return key


def fetch_trait(
    person: str,
    role_desc: str,
    trait: str,
    client: httpx.Client | None = None,
    max_results: int = 5,
) -> dict:
    """One Tavily call; the raw response is archived and returned (cached)."""
    out = EVIDENCE_DIR / slug(person) / f"{trait}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    query = TRAITS[trait].format(name=person, role_desc=role_desc)
    own = client is None
    c = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60)
    try:
        r = c.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={
                "query": query,
                "search_depth": "advanced",
                "include_answer": True,
                "max_results": max_results,
            },
        )
        r.raise_for_status()
        data = r.json()
    finally:
        if own:
            c.close()
    payload = {
        "person": person,
        "trait": trait,
        "query": query,
        "answer": data.get("answer", ""),
        "results": [
            {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")}
            for x in data.get("results", [])
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.3)
    return payload


def fetch_all_traits(
    person: str, role_desc: str, traits: list[str] | None = None
) -> dict[str, dict]:
    """All trait evidence for one member; failures degrade to missing traits."""
    out: dict[str, dict] = {}
    for trait in traits or list(TRAITS):
        try:
            out[trait] = fetch_trait(person, role_desc, trait)
        except Exception as exc:  # missing key, network, quota — degrade gracefully
            print(f"WARNING tavily {person}/{trait}: {exc}")
            continue
    return out
