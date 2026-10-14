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
EE (eestipank.ee/en/press), IT (bancaditalia.it per-year governor listing:
HTML entry pages 2025→, PDFs 2015–24). The remaining NCBs are documented
gaps — JavaScript-rendered listings with no sitemap (BE, IE, SI, LV, BG,
LU, HR, AT SPA), or bot protection (NL, PT, GR, LT), or unreachable
(CY, MT). See docs/data-sources/ncb-websites.md.

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
    if not date_str:  # slug fallback: /2024/05/12/, 2024-05-12_ or /20240512-name/
        m = (
            re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
            or re.search(r"(\d{4}-\d{2}-\d{2})_", url)
            or re.search(r"/(\d{4})(\d{2})(\d{2})-[a-z]", url)
        )
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


# --- Banca d'Italia: governor speeches per-year listing ----------------------

IT_YEAR_URL = (
    "https://www.bancaditalia.it/pubblicazioni/interventi-governatore/"
    "integov{year}/index.html?com.dotmarketing.htmlpage.language=1&dotcache=refresh"
)
IT_ENTRY_LANG = "?com.dotmarketing.htmlpage.language=1&dotcache=refresh"


def _slug_speaker(surname: str, day: str, variants: list[tuple]) -> str | None:
    """Member whose surname matches the entry-page slug and covers ``day``.

    Entry-page bodies name the governor only in the footnotes and can cite
    other members by full name near the top (one 2025 speech greets Makhlouf
    before any Panetta mention), so the slug is the only trustworthy signal.
    """
    s = _norm(surname)
    for person, _full, last, start, end in variants:
        if last == s and start <= day <= str(
            date.fromisoformat(end) + timedelta(days=TENURE_GRACE_DAYS)
        ):
            return person
    return None


def it_speech_entries(
    client: httpx.Client, year_from: int = 2015, year_to: int | None = None
) -> dict[str, dict]:
    """{url: {title, date, kind, speaker}} for governor speeches from the
    per-year pages.

    Two listing layouts. 2015–2024 items link the speech PDF directly
    (kind="pdf"; text via pypdf). Since 2025 items link an HTML entry page
    that itself carries the full text (kind="html") — fetched with the
    English-language dotCMS param, no PDF needed. The entry-page slug
    (``/YYYYMMDD-surname/``) gives both the speech date and the speaker
    surname: the page body names the governor only in the footnotes, and
    the listing's ``bdi-result-date`` is the *publication* date, which can
    lag the speech date. PDF filenames come in a dozen ad-hoc formats, so
    PDF dates come from the listing, never the filename. When a day exists
    in both languages the English file wins; an HTML entry beats any PDF
    of the same day.
    """
    year_to = year_to or date.today().year
    entries: dict[str, dict] = {}

    def _listing_date(item) -> str | None:
        d = item.find(class_="bdi-result-date")
        if not d:
            return None
        # months appear both abbreviated ("10 Dec 2019") and in full
        prefixes = "|".join(m[:3] for m in _MONTHS)
        m = re.search(
            rf"(\d{{1,2}})\s+({prefixes})\w*\s+(\d{{4}})",
            d.get_text(" ", strip=True),
            re.IGNORECASE,
        )
        if not m:
            return None
        month_no = {mm[:3].lower(): i for mm, i in _MONTHS.items()}[m.group(2).lower()]
        try:
            return str(date(int(m.group(3)), month_no, int(m.group(1))))
        except ValueError:
            return None

    for year in range(year_from, year_to + 1):
        try:
            r = client.get(IT_YEAR_URL.format(year=year))
            r.raise_for_status()
        except httpx.HTTPError:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.find_all("li"):
            anchors = [
                a
                for a in item.find_all("a", href=True)
                if ".pdf" in a["href"] and ".epub" not in a["href"]
            ]
            if not anchors:
                continue
            a = anchors[0]
            href = a["href"].strip()
            full = href if href.startswith("http") else f"https://www.bancaditalia.it{href}"
            title = a.get_text(" ", strip=True) or item.get_text(" ", strip=True)[:200]
            entries.setdefault(
                full, {"title": title, "date": _listing_date(item), "kind": "pdf", "speaker": ""}
            )
        # 2025 ->: entry pages with the full text in HTML
        for a in soup.find_all("a", href=True):
            m = re.search(r"/(\d{4})(\d{2})(\d{2})-([a-z-]+)/index\.html", a["href"])
            if not m:
                continue
            href = a["href"].strip()
            base = href if href.startswith("http") else f"https://www.bancaditalia.it{href}"
            entries[base + IT_ENTRY_LANG] = {
                "title": a.get_text(" ", strip=True),
                "date": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
                "kind": "html",
                "speaker": m.group(4),
            }
        time.sleep(0.2)

    # drop undated; one entry per day: html > English pdf > other pdf
    def _is_en(url: str) -> bool:
        name = url.lower().split("/")[-1]
        return name.startswith("en") or name.endswith("_en.pdf")

    def _rank(url: str) -> int:
        if entries[url]["kind"] == "html":
            return 0
        return 1 if _is_en(url) else 2

    by_date: dict[str, str] = {}
    for url, meta in entries.items():
        day = meta.get("date")
        if not day:
            continue
        cur = by_date.get(day)
        if cur is None or _rank(url) < _rank(cur):
            by_date[day] = url
    return {u: entries[u] for u in by_date.values()}


def _prefer_it_html(rows: list[dict], fetched_urls: dict[str, str]) -> list[dict]:
    """Drop IT PDF rows whose day now has an HTML row (2025-> relaunch).

    The corpus was first harvested via PDFs, including the entry-page years;
    once those days are covered by parsed HTML the PDF rows are redundant
    and are retired (marked ``superseded-by-html`` in the resume index so
    they are never re-fetched).
    """
    html_days = {r["date"] for r in rows if r["source"] == "it" and ".html" in r["url"]}
    kept: list[dict] = []
    for r in rows:
        if r["source"] == "it" and ".pdf" in r["url"] and r["date"] in html_days:
            fetched_urls.setdefault(r["url"], "superseded-by-html")
            continue
        kept.append(r)
    return kept


def _it_pdf_date(url: str) -> str | None:
    """ISO date from a Banca d'Italia PDF filename.

    Two layouts: ``en-VISCO_10.12.2019.pdf`` and
    ``en-panetta-21-09-2026.pdf`` — both DD MM YYYY.
    """
    name = url.split("/")[-1]
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", name) or re.search(
        r"(\d{2})-(\d{2})-(\d{4})\.pdf", name
    )
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    try:
        return str(date(int(yyyy), int(mm), int(dd)))
    except ValueError:
        return None


def parse_speech_pdf(content: bytes, url: str, title_hint: str = "", day: str = "") -> dict | None:
    """(date, title, text) from a speech PDF (pypdf).

    ``day`` (ISO) comes from the listing item or entry-page slug — the PDF
    filenames themselves come in a dozen ad-hoc formats and are not trusted.
    """
    import io

    from pypdf import PdfReader

    if not day:
        day = _it_pdf_date(url) or ""
    try:
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # corrupt / scanned PDFs
        return None
    text = text.strip()
    if len(text) < 400 or not day:
        return None
    return {"url": url, "title": title_hint[:300], "date": day, "author": "", "text": text}


# --- collection ----------------------------------------------------------------


def collect(
    sites: list[dict] | None = None,
    sleep_s: float = SLEEP_S,
    include_blog: bool = True,
    include_it: bool = True,
) -> pl.DataFrame:
    """Harvest all configured sites (+ the ECB blog + Banca d'Italia); resumable."""
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
        for site in sites if sites is not None else NCB_SITES:
            urls = discover_urls(site, client)
            stats.append(_harvest(site["code"], urls))
        if include_blog:
            stats.append(_harvest("ecb_blog", ecb_blog_urls()))
        if include_it:
            entries = it_speech_entries(client)
            st = SiteStats(code="it")
            st.n_urls_discovered = len(entries)
            for u, meta in sorted(entries.items()):
                if u in fetched_urls:
                    continue
                try:
                    r = client.get(u)
                    r.raise_for_status()
                    if meta["kind"] == "html":
                        page = parse_speech_page(r.text, u)
                        if page:
                            # the slug is the speech date; page chrome can
                            # carry an unrelated visible date (batch-updated
                            # "published" stamps)
                            page["date"] = meta["date"]
                            # entry pages name the governor only in the
                            # footnotes — the surname comes from the slug
                            if meta.get("speaker") and not page["author"]:
                                page["author"] = meta["speaker"]
                            page["title"] = re.sub(r"^Banca d.Italia\s*[-–—]\s*", "", page["title"])
                    else:
                        page = parse_speech_pdf(r.content, u, meta["title"], meta["date"])
                except httpx.HTTPError as exc:
                    fetched_urls[u] = f"http-error:{type(exc).__name__}"
                    st.n_fetched += 1
                    if len(st.errors) < 3:
                        st.errors.append(f"{u}: {exc}")
                    continue
                if page is None or len(page["text"]) < 400:
                    fetched_urls[u] = (
                        "no-date-or-too-short" if meta["kind"] == "html" else "unparsable-pdf"
                    )
                    st.n_fetched += 1
                    continue
                if date.fromisoformat(page["date"]) < DATE_FROM:
                    fetched_urls[u] = "before-2015"
                    st.n_fetched += 1
                    continue
                if meta["kind"] == "html" and meta.get("speaker"):
                    person = _slug_speaker(
                        meta["speaker"], page["date"], variants
                    ) or match_speaker(page, variants)
                else:
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
                            "source": "it",
                        }
                    )
                    st.n_matched += 1
                st.n_fetched += 1
                time.sleep(sleep_s)
                if st.n_fetched % 100 == 0:
                    _flush(rows, fetched_urls)
            stats.append(st)
            rows = _prefer_it_html(rows, fetched_urls)

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
    sites = [s for s in NCB_SITES if not codes or s["code"] in codes] or None
    include_it = not codes or "it" in codes
    df = collect(sites=sites, include_it=include_it)
    print(
        f"NCB web speeches: {df.height} | persons={df['speaker'].n_unique()} | "
        f"span={df['date'].min()}..{df['date'].max()}"
    )
    print(df.group_by("source").len().sort("len", descending=True))
