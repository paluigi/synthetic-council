"""Tests for the BIS-hub collectors (listing cards + sitemap pages)."""

from __future__ import annotations

from synthetic_council.collectors import ncb_speeches as bis


class TestParseListingCards:
    CARD_HTML = """
    <div class="card-wrapper">
      <a href="/speeches/20240416-digital-bridges" class="card-link"></a>
      <span class="card-date">16 Apr 2024</span>
      <h5 class="card-heading">Digital bridges for financial inclusion</h5>
      <div class="card-author">by <a>Klaas Knot</a></div>
    </div>
    """

    def test_card_fields(self):
        cards = bis.parse_cards(self.CARD_HTML)
        assert cards == [
            {
                "url": "/speeches/20240416-digital-bridges",
                "date": "2024-04-16",
                "title": "Digital bridges for financial inclusion",
                "author": "Klaas Knot",
            }
        ]


class TestParseSpeechPage:
    PAGE = (
        "<html><body><main><h1>Economic activity and prices</h1>"
        "By\nGediminas Šimkus\n"
        "Type\nSpeech\nSubtype\nCentral banker's speech\n"
        "Date delivered\n17 April 2025\n"
        "Country\nLithuania\n"
        "Speech by Mr Gediminas Šimkus, Chairman of the Board of the Bank of "
        "Lithuania, at the conference, Vilnius.\n"
        '<div class="text__component">Inflation is falling towards target this year.</div>'
        '<div class="text__component">' + "x" * 100 + "</div>"
        "</main></body></html>"
    )

    def test_fields_from_metadata_block(self):
        page = bis.parse_speech_page(self.PAGE, "/speeches/20250417-test-speech")
        assert page["date"] == "2025-04-17"  # from the URL, not the block
        assert page["title"] == "Economic activity and prices"
        assert page["author"] == "Gediminas Šimkus"
        assert page["country"] == "Lithuania"
        assert "Inflation is falling" in page["text"]

    def test_author_fallback_speech_by_line(self):
        html = self.PAGE.replace("By\nGediminas Šimkus\n", "")
        page = bis.parse_speech_page(html, "/speeches/20250417-test-speech")
        assert page["author"] == "Gediminas Šimkus"

    def test_undated_url_returns_none(self):
        assert bis.parse_speech_page("<html></html>", "/speeches/no-date-here") is None


class TestCountryScope:
    def test_euro_map(self):
        assert bis.COUNTRY_ISO2["Netherlands"] == "NL"
        assert bis.COUNTRY_ISO2["Slovenia"] == "SI"
        assert "Japan" not in bis.COUNTRY_ISO2
        assert "Euro area" not in bis.COUNTRY_ISO2
