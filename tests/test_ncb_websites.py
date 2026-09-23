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
