"""Wikipedia page extraction for GC member profiles.

Search Wikipedia for the member's biography page (role context in the query
to survive disambiguation), then store the plaintext extract verbatim for the
profile synthesis. Cached per person; failures degrade to no-Wikipedia.
"""

from __future__ import annotations

import json
import time

import httpx

from synthetic_council.config import RAW_DIR, USER_AGENT
from synthetic_council.member_profiles.tavily import slug

API = "https://en.wikipedia.org/w/api.php"
EVIDENCE_DIR = RAW_DIR / "profiles" / "wikipedia"


def fetch_page(person: str, role_desc: str) -> dict:
    """Search + extract; returns {title, url, revision, extract} (cached)."""
    out = EVIDENCE_DIR / f"{slug(person)}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as c:
        sr = c.get(
            API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": f"{person} {role_desc}",
                "srlimit": 5,
                "format": "json",
            },
        )
        sr.raise_for_status()
        hits = sr.json().get("query", {}).get("search", [])
        best = None
        person_l = person.lower()
        for h in hits:
            t = h.get("title", "")
            surname = person.split()[-1].lower()
            if surname in t.lower():
                best = t
                if person_l in t.lower():
                    break
        if best is None:
            payload = {
                "person": person,
                "title": None,
                "url": None,
                "revision": None,
                "extract": "",
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return payload
        time.sleep(0.2)
        ex = c.get(
            API,
            params={
                "action": "query",
                "prop": "extracts|revisions",
                "explaintext": 1,
                "titles": best,
                "format": "json",
            },
        )
        ex.raise_for_status()
        pages = ex.json().get("query", {}).get("pages", {})
        page = next(iter(pages.values()))
        payload = {
            "person": person,
            "title": best,
            "url": f"https://en.wikipedia.org/wiki/{best.replace(' ', '_')}",
            "revision": (page.get("revisions") or [{}])[0].get("revid"),
            "extract": page.get("extract", ""),
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.2)
    return payload
