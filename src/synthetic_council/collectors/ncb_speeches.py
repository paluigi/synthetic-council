"""Collector: national central bank governor speeches from the BIS hub.

The ECB all-speeches CSV (``collectors/speeches.py``) covers Executive Board
speakers only — NCB governors publish on their own banks' sites. This
collector fills that gap from the BIS speeches hub (``bis.org/speeches``,
which absorbed the old "central bankers' speeches" database in the site
redesign).

Two discovery paths:

1. **Sitemap recrawl** (``collect_from_sitemap``) — the hub's default
   listing is a curated subset (~750 speeches spanning 1999→) and its
   keyword search returns nothing, but ``bis.org/default/sitemap.xml``
   lists every speech page (~22,000; ~1,000 per year). Speech URLs carry
   the delivery date (``/speeches/YYYYMMDD-slug/``); pages expose the
   author after a "By" line and a "Country" field. Authors are matched to
   the roster with ``cbs_speeches.resolve_speaker`` (skeleton + surname
   subsequence, tenure + country checked) and euro-area countries not in
   the map are skipped. Default ``date_from=20240101``: BIS content up to
   2023 is already covered by the CBS dataset (pre-deduplicated there).
2. **Listing walk + per-person keyword queries** (``collect``) — the
   original method; kept for the curated listing, though the hub's
   keyword search currently returns no cards.

Historical caveat (verified 2026-09): the redesigned hub migrated only a
subset of the pre-2024 database — the deep history lives on in the CBS
dataset instead.

Output:
- data/raw/speeches/bis_speeches.parquet  (date, speaker, title, url, contents)
- data/raw/speeches/bis_query_log.csv     (person, query, n_cards, n_matched)
- data/raw/speeches/bis_sitemap_fetched.parquet (url, outcome — resume index)
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
SITEMAP_URL = "https://www.bis.org/default/sitemap.xml"
OUT_PATH = RAW_DIR / "speeches" / "bis_speeches.parquet"
LOG_PATH = RAW_DIR / "speeches" / "bis_query_log.csv"
FETCHED_PATH = RAW_DIR / "speeches" / "bis_sitemap_fetched.parquet"

# country names as printed on BIS speech pages -> ISO2; anything else
# ("Japan", "United Kingdom", ...) is out of scope for the roster
COUNTRY_ISO2 = {
    "Austria": "AT",
    "Belgium": "BE",
    "Bulgaria": "BG",
    "Croatia": "HR",
    "Cyprus": "CY",
    "Estonia": "EE",
    "Finland": "FI",
    "France": "FR",
    "Germany": "DE",
    "Greece": "GR",
    "Ireland": "IE",
    "Italy": "IT",
    "Latvia": "LV",
    "Lithuania": "LT",
    "Luxembourg": "LU",
    "Malta": "MT",
    "Netherlands": "NL",
    "Portugal": "PT",
    "Slovakia": "SK",
    "Slovenia": "SI",
    "Spain": "ES",
}

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
    ``collect`` supplement this walk. Matching here is name-only; the
    post-tenure purge in ``_drop_post_tenure`` removes ex-members (e.g. a
    governor turned BIS manager keeps speaking on the hub)."""
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


def _drop_post_tenure(df: pl.DataFrame) -> pl.DataFrame:
    """Remove speeches dated after the speaker's last GC tenure (+180d grace).

    Pre-roster-start speeches are kept (the Wayback memberships only reach
    back to 2004-07); only clearly post-membership rows go — e.g. a
    governor who moved to BIS management and keeps giving speeches the
    name-only listing match happily attributes to their old identity.
    """
    from datetime import timedelta

    memberships = pl.read_parquet(RAW_DIR / "gc_memberships_wayback.parquet")
    last_end = {
        canonicalise(p): date.fromisoformat(e) + timedelta(days=180)
        for p, e in zip(memberships["person"], memberships["end"], strict=True)
    }
    keep = [
        date.fromisoformat(r["date"]) <= last_end.get(r["speaker"], date.max)
        for r in df.iter_rows(named=True)
    ]
    return df.filter(pl.Series(keep))


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


# --- sitemap recrawl (2024 ->) ------------------------------------------------

_SPEECH_URL_RE = re.compile(r"/speeches/(\d{8})-")


def speech_urls_from_sitemap(client: httpx.Client, date_from: str = "20240101") -> list[str]:
    """Dated speech page paths from the hub sitemap, >= ``date_from``.

    The sitemap paginates at 10,000 locs per page (~4 pages as of 2026-09).
    Only English pages are kept: the sitemap also lists translated variants
    (``/es/speeches/...``) of the same speech.
    """
    found: set[str] = set()
    for page in range(1, 60):
        r = client.get(SITEMAP_URL, params={"page": page})
        r.raise_for_status()
        locs = re.findall(r"<loc>\s*([^<\s]+)", r.text)
        if not locs:
            break
        for u in locs:
            path = u[len("https://www.bis.org") :] if u.startswith("https://www.bis.org") else u
            m = re.match(r"^/speeches/(\d{8})-", path)
            if m and m.group(1) >= date_from:
                found.add(path)
        time.sleep(0.2)
    return sorted(found)


def parse_speech_page(html: str, url: str) -> dict | None:
    """(date, title, author, country, text) from a hub speech page.

    Date comes from the URL (``/speeches/YYYYMMDD-…``) — reliable by
    construction. Author is the line after the ``By`` marker, with the
    ``Speech by …`` attribution line as fallback; country the line after
    ``Country``.
    """
    from bs4 import BeautifulSoup

    m = _SPEECH_URL_RE.search(url)
    if not m:
        return None
    d = m.group(1)
    day = f"{d[:4]}-{d[4:6]}-{d[6:]}"

    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        t.decompose()
    title = soup.h1.get_text(" ", strip=True) if soup.h1 else ""

    container = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [ln.strip() for ln in container.get_text("\n", strip=True).splitlines() if ln.strip()]

    def _after(marker: str) -> str:
        for i, ln in enumerate(lines):
            if ln == marker and i + 1 < len(lines):
                return lines[i + 1]
        return ""

    author = _after("By")
    country = _after("Country")
    if not author:
        for ln in lines:
            if ln.startswith("Speech by"):
                # "Speech by Ms Junko Nakagawa, Member of ..." -> name
                am = re.search(r"Speech by\s+(?:Mr|Ms|Mrs|Dr|Prof\.?)\s+([A-Z][^,]+)", ln)
                if am:
                    author = am.group(1).strip()
                    break

    text_parts = [d.get_text("\n", strip=True) for d in soup.find_all(class_="text__component")]
    text = "\n\n".join(p for p in text_parts if len(p) > 40)
    if not text:
        art = soup.find("article")
        text = art.get_text("\n", strip=True) if art else ""
    return {
        "url": url,
        "date": day,
        "title": title[:300],
        "author": author[:200],
        "country": country[:60],
        "text": text,
    }


def collect_from_sitemap(date_from: str = "20240101", sleep_s: float = 0.35) -> pl.DataFrame:
    """Recrawl hub speech pages discovered via the sitemap (2024 ->) and
    merge roster-matched rows into ``bis_speeches.parquet``; resumable."""
    from synthetic_council.collectors.cbs_speeches import _variants, resolve_speaker

    variants = _variants()
    existing = pl.read_parquet(OUT_PATH) if OUT_PATH.exists() else pl.DataFrame()
    rows: dict[str, dict] = {}
    if existing.height:
        for r in existing.iter_rows(named=True):
            rows[r["url"]] = {
                "date": r["date"],
                "author": r.get("author_text", ""),
                "title": r["title"],
                "person": r["speaker"],
                "contents": r.get("contents", ""),
            }
    fetched_df = pl.read_parquet(FETCHED_PATH) if FETCHED_PATH.exists() else None
    fetched: dict[str, str] = (
        {r["url"]: r["outcome"] for r in fetched_df.iter_rows(named=True)}
        if fetched_df is not None
        else {}
    )

    def _flush() -> None:
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
        df = _drop_post_tenure(df)
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(OUT_PATH)
        pl.DataFrame({"url": list(fetched), "outcome": list(fetched.values())}).write_parquet(
            FETCHED_PATH
        )

    n_new = 0
    with _client() as client:
        urls = speech_urls_from_sitemap(client, date_from)
        print(f"bis sitemap: {len(urls)} speech pages >= {date_from}")
        for i, u in enumerate(urls):
            if u in fetched or u in rows:
                continue
            try:
                r = client.get(f"https://www.bis.org{u}")
                r.raise_for_status()
                page = parse_speech_page(r.text, u)
            except httpx.HTTPError:
                fetched[u] = "http-error"
                continue
            if page is None:
                fetched[u] = "unparsable"
                continue
            in_scope = page["country"] in COUNTRY_ISO2
            person = (
                resolve_speaker(
                    page["author"], page["date"], COUNTRY_ISO2[page["country"]], variants
                )
                if in_scope and page["author"]
                else None
            )
            fetched[u] = (
                f"matched:{person}"
                if person
                else ("in-scope-no-match" if in_scope else "out-of-scope-country")
            )
            if person and len(page["text"]) >= 400:
                rows[u] = {
                    "date": page["date"],
                    "author": page["author"],
                    "title": page["title"],
                    "person": person,
                    "contents": page["text"],
                }
                n_new += 1
            time.sleep(sleep_s)
            if (i + 1) % 200 == 0:
                _flush()
                print(f"  {i + 1}/{len(urls)} fetched, {n_new} new speeches")

    _flush()
    print(f"bis sitemap recrawl: {n_new} new speeches -> {OUT_PATH}")
    return pl.read_parquet(OUT_PATH)


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
    df = _drop_post_tenure(df)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    pl.DataFrame(log).write_csv(LOG_PATH)
    return BisSpeechesResult(
        n_speeches=df.height,
        n_persons_with_speeches=df["speaker"].n_unique() if df.height else 0,
        n_pages_fetched=n_pages,
    )


if __name__ == "__main__":
    import sys

    if "--sitemap" in sys.argv:
        df = collect_from_sitemap()
    else:
        print(collect())
        df = pl.read_parquet(OUT_PATH)
    print(
        f"BIS speeches: {df.height} | persons={df['speaker'].n_unique()} | "
        f"span={df['date'].min()}..{df['date'].max()}"
    )
