"""Collector: speeches harvested directly from NCB websites (+ ECB blog).

Closes the governor-speech gap that the BIS hub leaves (47 speeches by our
members post-redesign). Discovery is **sitemap-first**: sites with a usable
sitemap are walked via their sitemap index (gzipped children and CDATA-wrapped
locs supported), URL-filtered per site, then each page is parsed for
structured data (JSON-LD, then meta tags, then an h1 + slug-date fallback)
and its text extracted generically (<main>/<article>/body minus chrome).

The speaker is matched against the Wayback membership tenures: the author
field when it names a member, else the first member (tenure overlapping the
page date ±180d) whose full name — or surname with matching initial —
appears at the top of the page. This keeps false positives (surnames
mentioned in passing) rare and disambiguates same-surname successors
(e.g. Weidmann → Nagel at the Bundesbank).

Working sites (2026-09): DE (bundesbank.de/en/press/speeches), FR
(banque-france.fr/en/governors-interventions), ES (bde.es
intervenciones-publicas), FI (suomenpankki.fi), SK (nbs.sk/en/news),
EE (eestipank.ee/en/press). The remaining NCBs are documented gaps —
JavaScript-rendered listings with no sitemap (BE, IE, SI, LV, BG, LU, HR,
AT SPA, IT event pages without text), or bot protection (NL, PT, GR, LT),
or unreachable (CY, MT). See docs/data-sources/ncb-websites.md.

Output:
- data/raw/speeches/ncb_web_speeches.parquet (matched speeches; schema
  matches bis_speeches.parquet: date, speaker, author_text, title, url,
  contents; ``source`` column = site code)
- data/raw/speeches/ncb_web_fetched.parquet (every fetched URL + parse
  outcome — the resume index)
"""

from __future__ import annotations

import gzip
import json
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
import polars as pl
from bs4 import BeautifulSoup

from synthetic_council.collectors.persons import canonicalise
from synthetic_council.config import RAW_DIR, USER_AGENT

OUT_PATH = RAW_DIR / "speeches" / "ncb_web_speeches.parquet"
FETCHED_PATH = RAW_DIR / "speeches" / "ncb_web_fetched.parquet"

DATE_FROM = date(2015, 1, 1)
TENURE_GRACE_DAYS = 180
MAX_URLS_PER_SITE = 1500
SLEEP_S = 0.25

# site code -> config. ``include`` is matched against full URLs discovered
# from the sitemaps; keep it tight so we only fetch speech pages.
NCB_SITES: list[dict] = [
    {
        "code": "de",
        "country": "DE",
        "sitemaps": [
            "https://www.bundesbank.de/service-sitemap-34041d60a21884ad31dcd7976ab01d50-sitemap_index.xml",
            "https://www.bundesbank.de/service-sitemap-01a59a5d5e114f25a9a55e26583069ec-sitemap_index.xml",
        ],
        "include": r"bundesbank\.de/en/press/speeches/",
    },
    {
        "code": "fr",
        "country": "FR",
        "sitemaps": ["https://www.banque-france.fr/sitemap.xml"],
        "include": r"banque-france\.fr/en/governors-interventions/",
    },
    {
        "code": "es",
        "country": "ES",
        "sitemaps": ["https://www.bde.es/f/webbe/INF/assets/xml/sitemap.xml"],
        "include": r"bde\.es/wbe/en/noticias-eventos/actualidad-banco-espana/"
        r"intervenciones-publicas/",
    },
    {
        "code": "fi",
        "country": "FI",
        "sitemaps": ["https://www.suomenpankki.fi/bof-sitemap.xml"],
        "include": r"suomenpankki\.fi/en/.*speech",
    },
    {
        "code": "sk",
        "country": "SK",
        "sitemaps": ["https://nbs.sk/wp-sitemap.xml"],
        "include": r"nbs\.sk/en/news/.*speech",
    },
    {
        "code": "ee",
        "country": "EE",
        "sitemaps": ["https://www.eestipank.ee/sitemap.xml"],
        "include": r"eestipank\.ee/en/press/speech",
    },
]

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}
_TEXT_ARTIFACTS_RE = re.compile(
    r"^(cookie|accept|skip to|jump to|search|subscribe|follow us|share|print|back to)",
    re.IGNORECASE,
)


@dataclass
class SiteStats:
    code: str
    n_urls_discovered: int = 0
    n_fetched: int = 0
    n_matched: int = 0
    errors: list[str] = field(default_factory=list)


def _norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", n.lower()).strip()


def _client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=45,
        follow_redirects=True,
    )


# --- sitemap discovery ------------------------------------------------------

_LOC_RE = re.compile(r"<loc>(?:<!\[CDATA\[)?\s*([^<\s|\]]+)")


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """(child sitemap urls, page urls) from a sitemap or sitemap index."""
    children = re.findall(r"<sitemap>\s*<loc>(?:<!\[CDATA\[)?\s*([^<\s|\]]+)", xml_text)
    if children:
        return children, []
    urls = _LOC_RE.findall(xml_text)
    return [], [u for u in urls if not u.endswith(".xml") and not u.endswith(".gz")]


def _fetch_sitemap(client: httpx.Client, url: str) -> str | None:
    try:
        r = client.get(url)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    if url.endswith(".gz"):
        return gzip.decompress(r.content).decode("utf-8", errors="replace")
    return r.text


def discover_urls(site: dict, client: httpx.Client) -> list[str]:
    """All speech URLs for a site: sitemap walk (indexes followed 3 levels)."""
    include = re.compile(site["include"], re.IGNORECASE)
    found: set[str] = set()
    queue = deque((u, 0) for u in site["sitemaps"])
    seen: set[str] = set()
    while queue and len(found) < MAX_URLS_PER_SITE:
        sm, depth = queue.popleft()
        if sm in seen or depth > 3:
            continue
        seen.add(sm)
        text = _fetch_sitemap(client, sm)
        if not text:
            continue
        children, urls = parse_sitemap(text)
        for child in children:
            queue.append((child, depth + 1))
        found.update(u for u in urls if include.search(u))
        time.sleep(0.1)
    return sorted(found)


# --- page parsing ------------------------------------------------------------


def _parse_jsonld(soup: BeautifulSoup) -> dict:
    best: dict = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_type = item.get("@type", "")
            if isinstance(raw_type, list):
                raw_type = " ".join(str(t) for t in raw_type)
            types = str(raw_type).lower()
            if any(k in types for k in ("article", "speech", "document")):
                best = item
    return best


def _clean_author(a) -> str:
    if isinstance(a, dict):
        a = a.get("name", "")
    if isinstance(a, list):
        a = ", ".join(x.get("name", "") if isinstance(x, dict) else str(x) for x in a)
    return (a or "").strip()


def parse_speech_page(html: str, url: str) -> dict | None:
    """(title, iso date, author, text) from one speech page, or None."""
    soup = BeautifulSoup(html, "html.parser")
    # structured data FIRST: JSON-LD lives in <script> tags that would
    # otherwise be decomposed with the page chrome
    ld = _parse_jsonld(soup)
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    title = (ld.get("headline") or ld.get("name") or "").strip()
    date_str = (ld.get("datePublished") or ld.get("dateCreated") or "")[:10]
    author = _clean_author(ld.get("author"))

    if not title:
        og = soup.find("meta", property="og:title")
        title = (og.get("content", "").strip() if og else "") or (
            soup.h1.get_text(" ", strip=True) if soup.h1 else ""
        )
    if not date_str:
        for meta in soup.find_all("meta"):
            key = meta.get("property") or meta.get("name") or ""
            if key in ("article:published_time", "og:updated_time", "date", "DC.date"):
                m = _ISO_RE.search(meta.get("content", ""))
                if m:
                    date_str = m.group(0)
                    break
    if not date_str and soup.time:
        m = _ISO_RE.search(soup.time.get("datetime", "") or soup.time.get_text())
        if m:
            date_str = m.group(0)
    if not date_str:  # visible byline dates: "29.01.2018" (DE) or
        # "14 March 2025" / "March 14, 2025" — first match wins (the byline
        # sits near the top of the page)
        visible = soup.get_text(" ", strip=True)[:4000]
        m = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", visible)
        if m:
            date_str = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        else:
            m_day_first = re.search(
                r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b",
                visible,
                re.IGNORECASE,
            )  # "14 March 2025"
            m_month_first = re.search(
                r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b",
                visible,
                re.IGNORECASE,
            )  # "March 14, 2025"
            if m_day_first:
                day, month, year = m_day_first.groups()
                date_str = f"{year}-{_MONTHS[month.lower()]:02d}-{int(day):02d}"
            elif m_month_first:
                month, day, year = m_month_first.groups()
                date_str = f"{year}-{_MONTHS[month.lower()]:02d}-{int(day):02d}"
    if not date_str:  # slug fallback: /2024/05/12/ or 2024-05-12_
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url) or re.search(r"(\d{4}-\d{2}-\d{2})_", url)
        if m:
            date_str = "-".join(m.groups()) if m.lastindex == 3 else m.group(1)

    m = _ISO_RE.search(date_str or "")
    if not m:
        return None
    try:
        day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None

    container = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )
    text = container.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not _TEXT_ARTIFACTS_RE.match(ln)]
    text = "\n".join(lines)
    if not author:
        author = ""
    return {"url": url, "title": title[:300], "date": str(day), "author": author, "text": text}


# --- ECB blog (via the foedb publications DB, type 226) ---------------------


def ecb_blog_urls() -> list[str]:
    """English ECB-blog URLs from the foedb corpus (blogs by staff and
    Executive Board members; non-member authors are filtered out by the
    speaker matching, exactly like NCB pages)."""
    foedb = RAW_DIR / "foedb_publications.parquet"
    if not foedb.exists():
        return []
    df = pl.read_parquet(foedb).filter(pl.col("type") == 226)
    urls = []
    for r in df.iter_rows(named=True):
        en = [u for u in (r["document_urls"] or []) if u.endswith(".en.html")]
        if en:
            urls.append(f"https://www.ecb.europa.eu{en[0]}")
    return sorted(urls)


# --- speaker matching --------------------------------------------------------


def _person_variants() -> list[tuple[str, str, str, date, date]]:
    """(canonical person, normalized full name, normalized surname, start, end)."""
    memberships = pl.read_parquet(RAW_DIR / "gc_memberships_wayback.parquet")
    out = []
    for r in memberships.iter_rows(named=True):
        full = canonicalise(r["person"])
        n = _norm(full)
        out.append((full, n, n.split()[-1], r["start"], r["end"]))
    return out


def match_speaker(
    page: dict,
    variants: list[tuple[str, str, str, str, str]],
) -> str | None:
    """Member whose tenure covers the page date and who is named on it."""
    d = page["date"]
    head = _norm(page["author"] + " " + page["title"] + " " + page["text"][:3000])
    for person, full_n, _s, start, end in variants:
        if not (start <= d <= str(date.fromisoformat(end) + timedelta(days=TENURE_GRACE_DAYS))):
            continue
        if full_n in head:
            return person
    for person, full_n, surname, start, end in variants:
        if not (start <= d <= str(date.fromisoformat(end) + timedelta(days=TENURE_GRACE_DAYS))):
            continue
        # surname as a word near the head of the page (first-name initial check
        # when the initial is present)
        if re.search(rf"\b{re.escape(surname)}\b", head):
            first_initial = full_n[0]
            m = re.search(rf"\b([a-z])\s*{re.escape(surname)}\b", head)
            if not m or m.group(1) == first_initial:
                return person
    return None


# --- collection ----------------------------------------------------------------


def collect(
    sites: list[dict] | None = None, sleep_s: float = SLEEP_S, include_blog: bool = True
) -> pl.DataFrame:
    """Harvest all configured sites (+ the ECB blog); resumable."""
    variants = _person_variants()
    speeches = pl.read_parquet(OUT_PATH) if OUT_PATH.exists() else pl.DataFrame()
    fetched = pl.read_parquet(FETCHED_PATH) if FETCHED_PATH.exists() else None
    fetched_urls: dict[str, str] = (
        {r["url"]: r["outcome"] for r in fetched.iter_rows(named=True)}
        if fetched is not None
        else {}
    )
    rows: list[dict] = speeches.to_dicts() if speeches.height else []
    stats: list[SiteStats] = []

    def _harvest(code: str, urls: list[str]) -> SiteStats:
        st = SiteStats(code=code)
        st.n_urls_discovered = len(urls)
        for u in urls:
            if u in fetched_urls:
                continue
            try:
                r = client.get(u)
                r.raise_for_status()
                page = parse_speech_page(r.text, u)
            except httpx.HTTPError as exc:
                fetched_urls[u] = f"http-error:{type(exc).__name__}"
                st.n_fetched += 1
                if len(st.errors) < 3:
                    st.errors.append(f"{u}: {exc}")
                continue
            if page is None or len(page["text"]) < 400:
                fetched_urls[u] = "no-date-or-too-short"
                st.n_fetched += 1
                continue
            if date.fromisoformat(page["date"]) < DATE_FROM:
                fetched_urls[u] = "before-2015"
                st.n_fetched += 1
                continue
            person = match_speaker(page, variants)
            fetched_urls[u] = f"matched:{person}" if person else "no-member-match"
            if person:
                rows.append(
                    {
                        "date": page["date"],
                        "speaker": person,
                        "author_text": page["author"],
                        "title": page["title"],
                        "url": u,
                        "contents": page["text"],
                        "source": code,
                    }
                )
                st.n_matched += 1
            st.n_fetched += 1
            time.sleep(sleep_s)
            if st.n_fetched % 100 == 0:  # crash-resilient resume
                _flush(rows, fetched_urls)
        return st

    def _flush(rows: list[dict], fetched_urls: dict[str, str]) -> None:
        df = pl.DataFrame(
            rows,
            schema={
                "date": pl.String,
                "speaker": pl.String,
                "author_text": pl.String,
                "title": pl.String,
                "url": pl.String,
                "contents": pl.String,
                "source": pl.String,
            },
        ).unique(subset=["url"])
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(OUT_PATH)
        pl.DataFrame(
            {"url": list(fetched_urls), "outcome": list(fetched_urls.values())}
        ).write_parquet(FETCHED_PATH)

    with _client() as client:
        for site in sites or NCB_SITES:
            urls = discover_urls(site, client)
            stats.append(_harvest(site["code"], urls))
        if include_blog:
            stats.append(_harvest("ecb_blog", ecb_blog_urls()))

    for st in stats:
        print(
            f"{st.code}: discovered={st.n_urls_discovered} fetched={st.n_fetched} "
            f"matched={st.n_matched}" + (f" errors={st.errors}" if st.errors else "")
        )

    _flush(rows, fetched_urls)
    return pl.read_parquet(OUT_PATH).unique(subset=["url"]).sort("date")


if __name__ == "__main__":
    import sys

    codes = sys.argv[1:]
    sites = [s for s in NCB_SITES if not codes or s["code"] in codes]
    df = collect(sites=sites)
    print(
        f"NCB web speeches: {df.height} | persons={df['speaker'].n_unique()} | "
        f"span={df['date'].min()}..{df['date'].max()}"
    )
    print(df.group_by("source").len().sort("len", descending=True))
