"""Collector: national central bank governor speeches from the BIS hub.

The ECB all-speeches CSV (``collectors/speeches.py``) covers Executive Board
speakers only — NCB governors publish on their own banks' sites. This
collector fills that gap from the BIS speeches hub (``bis.org/speeches``,
which absorbed the old "central bankers' speeches" database in the site
redesign).

Method: for every GC member in the Wayback memberships, query the hub's
keyword search (exact full name, then bare surname), keep result cards whose
author matches the member, and fetch each speech page for the full text.

Coverage caveat (verified 2026-09): the redesigned hub migrated only a subset
of the historical database — e.g. it lists only a handful of Jens Weidmann's
2014-21 speeches and just one by Ignazio Visco. Per-person results are
therefore best-effort; the downstream strict-window join simply omits the
memo speech section when no speech qualifies. Harvesting NCB websites
directly remains future work (docs/TODO.md).

Output:
- data/raw/speeches/bis_speeches.parquet  (date, speaker, title, url, contents)
- data/raw/speeches/bis_query_log.csv     (person, query, n_cards, n_matched)
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date

import httpx
import polars as pl

from synthetic_council.collectors.persons import canonicalise
from synthetic_council.config import RAW_DIR, USER_AGENT

HUB = "https://www.bis.org/speeches"
OUT_PATH = RAW_DIR / "speeches" / "bis_speeches.parquet"
LOG_PATH = RAW_DIR / "speeches" / "bis_query_log.csv"

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})")
_CARD_SPLIT_RE = re.compile(r'class="card-wrapper"')
_CARD_FIELDS_RE = re.compile(
    r'href="(?P<href>/speeches/[^"]+)"[^>]*class="card-link".*?'
    r'class="card-date[^"]*">(?P<date>[^<]+)</span>.*?'
    r'card-heading">(?P<title>.*?)</h5>.*?'
    r'card-author">.*?>(?P<author>[^<]+)</a>',
    re.DOTALL,
)
_TOTAL_RE = re.compile(r"Displaying \d+-\d+ of (\d+)")


@dataclass
class BisSpeechesResult:
    n_speeches: int
    n_persons_with_speeches: int
    n_pages_fetched: int


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)


def _norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", n.lower()).strip()


def _surname(full: str) -> str:
    return _norm(full).split()[-1]


def _author_matches(author: str, person: str) -> bool:
    """Author card matches the member: same surname, same first initial."""
    a, p = _norm(author), _norm(person)
    a_parts, p_parts = a.split(), p.split()
    if not a_parts or not p_parts:
        return False
    return a_parts[-1] == p_parts[-1] and a_parts[0][0] == p_parts[0][0]


def parse_cards(html: str) -> list[dict]:
    cards = []
    for chunk in _CARD_SPLIT_RE.split(html)[1:]:
        m = _CARD_FIELDS_RE.search(chunk)
        if not m:
            continue
        dm = _DATE_RE.search(m.group("date"))
        if not dm:
            continue
        try:
            day = date(int(dm.group(3)), _MONTHS[dm.group(2)], int(dm.group(1)))
        except (KeyError, ValueError):
            continue
        cards.append(
            {
                "url": m.group("href"),
                "date": str(day),
                "title": re.sub(r"\s+", " ", m.group("title")).strip(),
                "author": re.sub(r"\s+", " ", m.group("author")).strip(),
            }
        )
    return cards


def _walk_listing(
    client: httpx.Client, persons: list[str], sleep_s: float, max_pages: int = 120
) -> dict[str, dict]:
    """Walk the default listing (all speeches, newest first, ~75 pages as of
    2026-09) and keep cards authored by our members. The hub caps pagination
    well below its advertised total, so the per-person keyword queries in
    ``collect`` supplement this walk."""
    by_person = {p: {} for p in persons}
    for page in range(max_pages):
        r = client.get(HUB, params={"page": page})
        r.raise_for_status()
        cards = parse_cards(r.text)
        if not cards:
            break
        for c in cards:
            for person in persons:
                if _author_matches(c["author"], person):
                    by_person[person][c["url"]] = {**c, "person": canonicalise(person)}
                    break
        time.sleep(sleep_s)
    return {u: card for cards in by_person.values() for u, card in cards.items()}


def _search(client: httpx.Client, query: str, max_pages: int, sleep_s: float) -> list[dict]:
    """All result cards for a keyword query (paginated)."""
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(max_pages):
        r = client.get(HUB, params={"keywords": query, "page": page})
        r.raise_for_status()
        cards = parse_cards(r.text)
        for c in cards:
            if c["url"] not in seen:
                seen.add(c["url"])
                out.append(c)
        total = _TOTAL_RE.search(r.text)
        if not cards or (total and len(seen) >= int(total.group(1))):
            break
        time.sleep(sleep_s)
    return out


def _speech_text(client: httpx.Client, url: str) -> str:
    from bs4 import BeautifulSoup

    r = client.get(f"https://www.bis.org{url}")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    parts = [d.get_text("\n", strip=True) for d in soup.find_all(class_="text__component")]
    text = "\n\n".join(p for p in parts if len(p) > 40)
    if not text:  # fallback: the <article> block
        art = soup.find("article")
        text = art.get_text("\n", strip=True) if art else ""
    return text


def _membership_persons() -> list[str]:
    path = RAW_DIR / "gc_memberships_wayback.parquet"
    df = pl.read_parquet(path)
    return sorted({canonicalise(p) for p in df["person"].to_list()})


def collect(
    max_pages: int = 10,
    sleep_s: float = 0.25,
    fetch_texts: bool = True,
) -> BisSpeechesResult:
    """Query the hub per member; write the deduplicated speech corpus."""
    persons = _membership_persons()
    existing = pl.read_parquet(OUT_PATH) if OUT_PATH.exists() else None
    rows: dict[str, dict] = {}
    if existing is not None:
        for r in existing.iter_rows(named=True):
            # normalise saved rows to the in-run card shape
            rows[r["url"]] = {
                "date": r["date"],
                "author": r.get("author_text", ""),
                "title": r["title"],
                "person": r["speaker"],
                "contents": r.get("contents", ""),
            }
    log: list[dict] = []
    n_pages = 0
    with _client() as client:
        walked = _walk_listing(client, persons, sleep_s)
        for url, card in walked.items():
            rows.setdefault(url, {**card})
        n_pages += len(walked)
        log.append(
            {
                "person": "(listing-walk)",
                "query": "-",
                "n_cards": len(walked),
                "n_matched": len(walked),
            }
        )
        for person in persons:
            matched: dict[str, dict] = {}
            for query in (f'"{person}"', person.split()[-1]):
                cards = _search(client, query, max_pages, sleep_s)
                n_pages += 1
                n_match = 0
                for c in cards:
                    if _author_matches(c["author"], person):
                        matched[c["url"]] = {**c, "person": canonicalise(person)}
                        n_match += 1
                log.append(
                    {"person": person, "query": query, "n_cards": len(cards), "n_matched": n_match}
                )
                if matched and query.startswith('"'):
                    break  # exact-name query already found this person's cards
            for url, card in matched.items():
                if url in rows and rows[url].get("contents"):
                    rows[url] = {**card, "contents": rows[url]["contents"]}
                else:
                    rows[url] = {**card}
            time.sleep(sleep_s)

    if fetch_texts:
        with _client() as client:
            todo = [u for u, v in rows.items() if not v.get("contents")]
            for url in todo:
                rows[url]["contents"] = _speech_text(client, url)
                time.sleep(sleep_s)

    df = pl.DataFrame(
        [
            {
                "date": v["date"],
                "speaker": v["person"],
                "author_text": v["author"],
                "title": v["title"],
                "url": u,
                "contents": v.get("contents", ""),
            }
            for u, v in rows.items()
        ],
        schema={
            "date": pl.String,
            "speaker": pl.String,
            "author_text": pl.String,
            "title": pl.String,
            "url": pl.String,
            "contents": pl.String,
        },
    ).sort("date")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    pl.DataFrame(log).write_csv(LOG_PATH)
    return BisSpeechesResult(
        n_speeches=df.height,
        n_persons_with_speeches=df["speaker"].n_unique() if df.height else 0,
        n_pages_fetched=n_pages,
    )


if __name__ == "__main__":
    print(collect())
