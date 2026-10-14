# CBS dataset (Campiglio–Deyris–Romelli–Scalisi)

`collectors/cbs_speeches.py` ingests the CBS dataset — 35,487 speeches by
131 central banks, 1986–2023, compiled for Campiglio, Deyris, Romelli &
Scalisi (2025), "Warning words in a warming world: Central bank
communication and climate change", *European Economic Review* 178
(https://cbspeeches.com/). It combines the BIS speech hub (18,045 rows),
scraped NCB websites (15,435) and archives (2,007); non-English speeches
carry a Microsoft-translation English `text` alongside `text_original`.

## Why it is in the corpus

It closes the governor-speech gap at NCBs we cannot harvest directly
(JS-rendered, bot-blocked or unreachable sites — see ncb-websites.md),
**for 2015–2023 only**: the dataset ends 2023-12. 2024→ is covered by the
BIS sitemap recrawl (`ncb_speeches.collect_from_sitemap`) where the hub
carries the speech — most gap governors' full texts, incl. new-era
governors (Escrivá, Sleijpen, Patsalides, Kocher) — with news-layer
search (TinyFish, planned) left for what BIS lacks: PT interviews,
Luxembourg, and one-off speeches. CBS also adds speeches at harvestable
NCBs that our own crawlers miss (BIS-era back-catalogue: FR +180, ES +165,
DE +187 across Weidmann/Nagel, IT +42).

## Pipeline

1. `download()` streams the 686 MB CSV from the authors' Dropbox link to
   `data/raw/CBS_dataset_v1.0.csv` (gitignored; never committed).
2. Filter: `Role == "Governor"`, CBS `Country` in the euro-area ISO-3 set
   (+ BGR, whose governor is on our roster). ECB-institution rows are
   excluded: they are Executive Board speeches already covered — with
   correct speech dates — by the official ECB CSV; the CBS copies carry
   bis.org/review *publication* dates. Date ≥ 2015, text ≥ 400 chars.
3. Speaker resolution against the Wayback membership tenures
   (`resolve_speaker`). The CSV deletes diacritic letters outright
   ("Mrio Centeno", "Boris Vuji" = Vujčić, "Gediminas imkus" = Šimkus,
   "Madis Mller" = Müller), so matching runs in passes, always under
   tenure (±180d grace) + country checks:
   full-skeleton exact → surname exact with compatible first name →
   CBS surname a subsequence of the roster surname (≤2 letters shorter).
   First-name compatibility allows deletions ("Mrio"/"Mario") and
   transliteration variants ("Giannis"/"Yannis", bounded edit distance).
4. Dedup: one row per (person, day) — same-person spelling variants
   double-count speeches in CBS ("Giannis"/"Yannis Stournaras");
   original-English preferred, then the longer text. Rows already covered
   by a primary source (ECB CSV, BIS hub, NCB websites) are dropped on
   (speaker, date): CBS never overrides a primary source.

Output: `data/raw/speeches/cbs_speeches.parquet` —
(date, speaker, author_text, title, url, contents, source='cbs',
language, translated). The last two columns are ignored by the union.

## Result (2026-09 build)

1,482 speeches by 37 members, 2015-01 → 2023-12; 332 machine-translated.
Largest: Villeroy de Galhau 180, de Cos 165, Weidmann 132, Stournaras
115, Knot 111, da Silva Costa 96, Makhlouf 64. Strict-window join:
1,187 links over 92 meetings (was 647), mean members-with-speech per
meeting 7.0 → 12.9.

## Caveats

- **2024→ falls to the BIS recrawl** — after `collect_from_sitemap`, the
  remaining CBS-side holes are Luxembourg (3 rows ever) and thin PT/BG/CY
  interview coverage. The authors plan periodic updates; re-run
  `python -m synthetic_council.collectors.cbs_speeches` when a new
  version appears (update `CBS_CSV_URL`).
- Luxembourg is nearly absent (3 rows); Belgium thin (13 Wunsch + 5
  Smets).
- ~22% of rows are machine translations (mostly PT, BG, GR, HR, LT);
  the `translated` flag lets downstream steps treat them separately.
- Genres mix speeches with interviews and testimony (BIS-review rows).
- License: free for academic research and personal non-commercial use;
  redistribution requires the authors' consent. Cite the paper above.
