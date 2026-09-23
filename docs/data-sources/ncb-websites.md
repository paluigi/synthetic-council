# NCB websites + ECB blog speech harvest

`collectors/ncb_websites.py` supplements the ECB all-speeches CSV (Executive
Board only) and the thin BIS hub with speeches harvested directly from NCB
websites, plus the ECB Blog.

## Method

1. **Discovery (sitemap-first)** — each configured site's sitemap index is
   walked (up to 3 levels; gzipped children and CDATA-wrapped `<loc>`s
   supported, both seen in the wild), URL-filtered per site so only speech
   pages are fetched.
2. **Page parsing** — structured data in priority order: JSON-LD
   (`Article`/`Speech`/`*Document`: headline, datePublished, author), then
   `og:title`/`article:published_time` meta tags + `<h1>`, then a slug-date
   fallback (`/2024/05/12/` or `2024-05-12_`). Text: `<main>`/`<article>`/
   body minus nav/footer/aside/script chrome.
3. **Speaker matching** — against the Wayback membership tenures: pages whose
   author field names a member (tenure overlapping the page date ±180d), else
   the first member whose full name — or surname with matching first initial —
   appears in the author/title/first 3,000 characters. This disambiguates
   same-surname successors (Weidmann → Nagel) and rejects surnames mentioned
   in passing with a different initial.
4. **Filters** — date ≥ 2015-01-01, text ≥ 400 chars; resume index at
   `data/raw/speeches/ncb_web_fetched.parquet` (every URL + outcome:
   `matched:<person>`, `no-member-match`, `before-2015`,
   `no-date-or-too-short`, `http-error:*`).

ECB blog: English URLs enumerated from the foedb publications DB (type 226,
252 posts 2020-03 →), parsed/matched identically; non-member authors (ECB
staff) fall out via the speaker matching.

## Working sites (2026-09)

| code | site | speech URLs discovered | matched (2015→) | notes |
|---|---|---|---|---|
| de | bundesbank.de | 696 | 47 | `/en/press/speeches/…`, gz sitemap indexes; many speeches are by non-GC Bundesbank board members (only the President sits on the GC) |
| fr | banque-france.fr | 191 | 99 | `/en/governors-interventions/…` |
| es | bde.es | 430 | 0 | sitemap URLs are stale (redirect to `app.bde.es` → 404) |
| fi | suomenpankki.fi | 193 | 132 | `/en/news-and-topical/speeches-and-interviews2/…` (Rehn 117) |
| sk | nbs.sk | 5 | 0 | Kazimír slugs exist but pages parse without dates |
| ee | eestipank.ee | 8 | 5 | `/en/press/speech…` |
| ecb_blog | ecb.europa.eu | 252 | 88 | foedb-enumerated, member-filtered |
| it | bancaditalia.it | 230 | 220 | governor speeches per year page (`pubblicazioni/interventi-governatore`). 2015–2024 items link the speech PDF directly (text via pypdf; dates from the listing's `bdi-result-date`, never the filename; English files preferred per date). Since 2025 items link an HTML entry page that carries the full text — fetched with the English-language dotCMS param, no PDF involved. For those, the `/YYYYMMDD-surname/` slug is authoritative for both the speech date (page chrome carries unrelated batch-updated "published" stamps) and the speaker (the body names the governor only in the footnotes and can greet another member by full name near the top) |

Final corpus: 591 speeches by 28 members, 2015-01 → 2026-09. Combined effect
on the strict-window join: 647 links over 92 meetings (was 453 before NCB
harvesting), governor links 83 → 266, mean members-with-speech per meeting
4.9 → 7.0.

## Documented gaps (2026-09) — why each site is skipped

- **JavaScript-rendered listings, no usable sitemap**: BE (nbb.be), IE
  (centralbank.ie listing pages load via JS; sitemap holds only section
  pages), SI (bsi.si), LV (bank.lv sitemap has no EN speech pages), BG, LU,
  HR (robots points at a `localhost` sitemap), AT (oenb.at is an SPA — every
  path returns a soft-404 shell).
- **Bot protection (HTTP 403)**: NL (dnb.nl), PT (bportugal.pt), GR
  (bankofgreece.gr), LT (lb.lt).
- **Unreachable from this host**: CY, MT.

Future work: per-site JS rendering or the sites' internal JSON APIs. PDF
extraction for IT survives only for the 2015–2024 listings. Coverage note:
the ECB CSV already covers Executive Board
members fully, so the gap is governors of the skipped countries only; the
strict-window join simply omits the speech section for meetings without a
qualifying speech.

## Politeness

0.25 s sleep between page fetches, descriptive User-Agent
(`synthetic-council/0.1`), ≤1,500 URLs per site, resumable.
