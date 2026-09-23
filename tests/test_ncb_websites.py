"""Tests for the NCB-website / ECB-blog speech harvester (no network)."""

from __future__ import annotations

import gzip

import pytest

from synthetic_council.collectors import ncb_websites as nw


class TestSitemapParsing:
    def test_index_and_urlset(self):
        idx = "<sitemapindex><sitemap><loc> https://x/child.xml </loc></sitemap></sitemapindex>"
        children, urls = nw.parse_sitemap(idx)
        assert children == ["https://x/child.xml"]
        assert urls == []

        urlset = "<urlset><url><loc>https://x/a</loc></url><url><loc><![CDATA[https://x/b]]></loc></url></urlset>"
        children, urls = nw.parse_sitemap(urlset)
        assert children == []
        assert urls == ["https://x/a", "https://x/b"]

    def test_gz_fetch(self, tmp_path, monkeypatch):
        body = b"<urlset><url><loc>https://x/speech-1</loc></url></urlset>"
        gz = tmp_path / "sm.xml.gz"
        gz.write_bytes(gzip.compress(body))

        class FakeResp:
            status_code = 200
            content = gzip.compress(body)

            def raise_for_status(self):
                return None

        class FakeClient:
            def get(self, url):
                assert url.endswith(".gz")
                return FakeResp()

        text = nw._fetch_sitemap(FakeClient(), str(gz))  # type: ignore[arg-type]
        assert "speech-1" in text


class TestPageParsing:
    def test_jsonld_path(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type":"Article","headline":"Rates and prices",'
            '"datePublished":"2022-07-10T09:00:00Z",'
            '"author":{"name":"Ms Isabel Schnabel"}}</script></head>'
            "<body><main><p>Inflation is high. " + "x" * 500 + "</p></main></body></html>"
        )
        page = nw.parse_speech_page(html, "https://www.ecb.europa.eu/press/blog/x")
        assert page["date"] == "2022-07-10"
        assert page["title"] == "Rates and prices"
        assert page["author"] == "Ms Isabel Schnabel"
        assert len(page["text"]) > 400

    def test_meta_and_slug_fallbacks(self):
        html = (
            "<html><head><meta property='og:title' content='Governor on inflation'>"
            "<meta property='article:published_time' content='2023-04-05T10:00:00Z'></head>"
            "<body><article><p>Governor speech text. " + "y" * 600 + "</p></article></body></html>"
        )
        page = nw.parse_speech_page(html, "https://example.org/en/speech/")
        assert page["date"] == "2023-04-05"
        assert page["title"] == "Governor on inflation"

        page2 = nw.parse_speech_page(
            "<html><body><main><p>" + "z" * 600 + "</p></main></body></html>",
            "https://example.org/agenda/2021-06-10_an-event/",
        )
        assert page2["date"] == "2021-06-10"

    def test_no_date_returns_none(self):
        page = nw.parse_speech_page(
            "<html><body><main><p>" + "q" * 600 + "</p></main></body></html>",
            "https://example.org/speech/",
        )
        assert page is None

    def test_type_list_jsonld(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type":["Speech","PresentationDigitalDocument"],"name":"A speech",'
            '"datePublished":"2024-01-20","author":"Mr Klaas Knot"}</script></head>'
            "<body><main><p>Text. " + "w" * 500 + "</p></main></body></html>"
        )
        page = nw.parse_speech_page(html, "https://www.dnb.nl/en/speech")
        assert page["author"] == "Mr Klaas Knot"
        assert page["title"] == "A speech"


class TestSpeakerMatching:
    @pytest.fixture
    def variants(self):
        # (person, normalized full, surname, start, end)
        return [
            ("Jens Weidmann", "jens weidmann", "weidmann", "2011-05-11", "2022-01-16"),
            ("Joachim Nagel", "joachim nagel", "nagel", "2022-01-17", "2026-10-22"),
            ("Isabel Schnabel", "isabel schnabel", "schnabel", "2020-01-02", "2026-10-22"),
        ]

    def test_full_name_match(self, variants):
        page = {
            "date": "2021-06-10",
            "author": "",
            "title": "Speech",
            "text": "Opening remarks by Jens Weidmann at the conference." + "x" * 200,
        }
        assert nw.match_speaker(page, variants) == "Jens Weidmann"

    def test_tenure_disambiguates_successors(self, variants):
        # 2023 page mentioning "Nagel" must resolve to Joachim, not Weidmann
        page = {
            "date": "2023-05-02",
            "author": "Dr Joachim Nagel",
            "title": "Speech",
            "text": "y" * 200,
        }
        assert nw.match_speaker(page, variants) == "Joachim Nagel"

    def test_author_field_preferred(self, variants):
        page = {
            "date": "2022-11-01",
            "author": "Ms Isabel Schnabel",
            "title": "Blog post",
            "text": "z" * 200,
        }
        assert nw.match_speaker(page, variants) == "Isabel Schnabel"

    def test_out_of_tenure_no_match(self, variants):
        page = {"date": "2015-01-01", "author": "Isabel Schnabel", "title": "", "text": ""}
        assert nw.match_speaker(page, variants) is None

    def test_surname_mentioned_in_passing_not_matched_when_initial_differs(self, variants):
        # "R. Weidmann" (different initial) during Weidmann's own tenure
        page = {
            "date": "2020-02-02",
            "author": "",
            "title": "",
            "text": "Thanks to R. Weidmann for the data." + "x" * 200,
        }
        assert nw.match_speaker(page, variants) is None


if __name__ == "__main__":
    pytest.main([__file__])


class TestBancaDItalia:
    def test_pdf_date_fallback_patterns(self):
        assert (
            nw._it_pdf_date("https://x/integov2019/en-VISCO_10.12.2019.pdf?language_id=1")
            == "2019-12-10"
        )
        assert (
            nw._it_pdf_date("https://x/20260921-panetta/en-panetta-21-09-2026.pdf") == "2026-09-21"
        )
        assert nw._it_pdf_date("https://x/no-date-here.pdf") is None

    def test_listing_date_parses_abbreviated_months(self):
        from bs4 import BeautifulSoup

        html = (
            '<li><div class="bdi-result-date">Data Pubblicazione: 10 Dec 2019 </div>'
            '<div><a class="bdi-result-title" href="/x/en-VISCO_10.12.2019.pdf">'
            "Conference title</a></div></li>"
        )
        item = BeautifulSoup(html, "html.parser").li
        # reuse the inner parser through a tiny harness
        prefixes = "|".join(m[:3] for m in nw._MONTHS)
        m = nw.re.search(
            rf"(\d{{1,2}})\s+({prefixes})\w*\s+(\d{{4}})",
            item.find(class_="bdi-result-date").get_text(" ", strip=True),
            nw.re.IGNORECASE,
        )
        month_no = {mm[:3].lower(): i for mm, i in nw._MONTHS.items()}[m.group(2).lower()]
        assert nw.date(int(m.group(3)), month_no, int(m.group(1))) == nw.date(2019, 12, 10)

    def test_parse_speech_pdf_needs_text(self, tmp_path):
        from pypdf import PdfWriter

        w = PdfWriter()
        w.add_blank_page(200, 200)
        p = tmp_path / "s.pdf"
        with open(p, "wb") as fh:
            w.write(fh)
        got = nw.parse_speech_pdf(
            p.read_bytes(), "https://x/en-x-01-01-2024.pdf", "t", "2024-01-01"
        )
        assert got is None

    # --- per-year listing -> html (2025->) / pdf (2015-24) entries ----------

    @staticmethod
    def _year_client(pages: dict[str, str]):
        class FakeResp:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

        class FakeClient:
            def get(self, url):
                for year, html in pages.items():
                    if f"integov{year}" in url:
                        return FakeResp(html)
                return FakeResp("<html><body></body></html>")

        return FakeClient()

    def test_old_years_yield_pdf_entries(self):
        client = self._year_client(
            {
                "2019": (
                    "<ul><li><div class='bdi-result-date'>Data Pubblicazione: 10 Dec 2019</div>"
                    "<a href='/pubblicazioni/interventi-governatore/integov2019/"
                    "en-VISCO_10.12.2019.pdf'>A speech</a></li></ul>"
                )
            }
        )
        got = nw.it_speech_entries(client, 2019, 2019)
        (meta,) = got.values()
        assert meta["kind"] == "pdf"
        assert meta["date"] == "2019-12-10"
        assert meta["speaker"] == ""

    def test_recent_years_yield_html_entries_with_slug_date_and_speaker(self):
        client = self._year_client(
            {
                "2026": (
                    "<ul><li><div class='bdi-result-date'>Data Pubblicazione: 22 Sep 2026</div>"
                    "<a href='/pubblicazioni/interventi-governatore/integov2026/"
                    "20260921-panetta/index.html'>The challenge of innovation</a></li></ul>"
                )
            }
        )
        got = nw.it_speech_entries(client, 2026, 2026)
        ((url, meta),) = got.items()
        assert meta["kind"] == "html"
        # slug (speech) date wins over the later publication date
        assert meta["date"] == "2026-09-21"
        assert meta["speaker"] == "panetta"
        assert url.endswith("20260921-panetta/index.html" + nw.IT_ENTRY_LANG)

    def test_english_pdf_preferred_per_day(self):
        client = self._year_client(
            {
                "2016": (
                    "<ul><li><div class='bdi-result-date'>Data Pubblicazione: 5 Feb 2016</div>"
                    "<a href='/x/it_VISCO_05.02.2016.pdf'>It</a></li>"
                    "<li><div class='bdi-result-date'>Data Pubblicazione: 5 Feb 2016</div>"
                    "<a href='/x/en_VISCO_05.02.2016.pdf'>En</a></li></ul>"
                )
            }
        )
        got = nw.it_speech_entries(client, 2016, 2016)
        assert list(got) == ["https://www.bancaditalia.it/x/en_VISCO_05.02.2016.pdf"]

    def test_compact_slug_date_fallback(self):
        page = nw.parse_speech_page(
            "<html><body><main><h1>T</h1><p>" + "a" * 600 + "</p></main></body></html>",
            "https://www.bancaditalia.it/x/20260921-panetta/index.html" + nw.IT_ENTRY_LANG,
        )
        assert page is not None and page["date"] == "2026-09-21"

    def test_speaker_matched_from_slug_surname(self):
        variants = [
            ("Fabio Panetta", "fabio panetta", "panetta", "2023-11-01", "2026-10-22"),
            ("Ignazio Visco", "ignazio visco", "visco", "2011-11-01", "2023-10-31"),
        ]
        # entry-page body names the governor only far from the head
        page = {
            "date": "2026-09-21",
            "author": "panetta",
            "title": "The challenge of innovation",
            "text": "Many thanks to the organizers. " + "x" * 3000,
        }
        assert nw.match_speaker(page, variants) == "Fabio Panetta"

    def test_prefer_it_html_retires_superseded_pdf_rows(self):
        rows = [
            {"date": "2026-09-21", "source": "it", "url": "https://x/a.pdf"},
            {"date": "2026-09-21", "source": "it", "url": "https://x/b" + nw.IT_ENTRY_LANG},
            {"date": "2019-12-10", "source": "it", "url": "https://x/c.pdf"},
            {"date": "2026-09-21", "source": "fi", "url": "https://y/d.pdf"},
        ]
        fetched: dict[str, str] = {}
        kept = nw._prefer_it_html(rows, fetched)
        assert [r["url"] for r in kept] == [
            "https://x/b" + nw.IT_ENTRY_LANG,
            "https://x/c.pdf",
            "https://y/d.pdf",
        ]
        assert fetched == {"https://x/a.pdf": "superseded-by-html"}
