"""Tests for the accounts pipeline: collector date parsing, participants,
CE proposal extraction (deterministic paths only — no network, no LLM)."""

from pathlib import Path

import pytest

from synthetic_council.collectors.accounts import html_to_markdown, parse_meeting_date
from synthetic_council.collectors.decisions import _slug_date
from synthetic_council.mpa_extract.ce_proposal import _direction, extract_one
from synthetic_council.mpa_extract.decision_mode import keyword_harvest, section2
from synthetic_council.mpa_extract.participants import parse_members_block

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    md = (FIXTURES / name).read_text(encoding="utf-8")
    return md.split("---", 2)[2] if md.startswith("---") else md


class TestMeetingDate:
    def test_held_sentence_two_day(self):
        md = _fixture("account_2015-01-22.md")
        assert str(parse_meeting_date(md)) == "2015-01-22"

    def test_title_fallback_single_day(self):
        md = "# Meeting of 7 July 2021\n\nsome body"
        assert str(parse_meeting_date(md, title="Meeting of 7 July 2021")) == "2021-07-07"

    def test_publication_date_never_wins(self):
        md = (
            "* 29 July 2021\n\n"
            "## Account of the monetary policy meeting held in Frankfurt am Main "
            "on Wednesday, 7 July 2021\n"
        )
        assert str(parse_meeting_date(md)) == "2021-07-07"


class TestParticipants:
    def test_2015_layout(self):
        entries, footnote = parse_members_block(_fixture("account_2015-01-22.md"))
        assert len(entries) == 25
        by_name = {e["name"]: e for e in entries}
        assert "Draghi" in by_name
        assert by_name["Hansson"]["voting_right"] is False
        assert by_name["Honohan"]["voting_right"] is False
        assert by_name["Weidmann"]["voting_right"] is True
        assert "not holding a voting right in January 2015" in footnote

    def test_2024_layout(self):
        entries, footnote = parse_members_block(_fixture("account_2024-12-12.md"))
        assert len(entries) == 26
        by_name = {e["name"]: e for e in entries}
        no_vote = sorted(n for n, e in by_name.items() if not e["voting_right"])
        assert no_vote == ["Makhlouf", "Müller", "Nagel", "Stournaras", "Wunsch"]
        assert by_name["Demarco"]["replacing"] == "Scicluna"
        assert by_name["Lane"]["voting_right"] is True

    def test_alternate_to_and_teleconference(self):
        entries, _ = parse_members_block(
            "**Members**\n\n* Mr Bohnec², Alternate to Mr Jazbec\\*\n\n"
            "* Mr Bradeško, via teleconference\n"
        )
        assert entries[0]["name"] == "Bohnec"
        assert entries[0]["replacing"] == "Jazbec"
        assert entries[0]["voting_right"] is False
        assert entries[1]["name"] == "Bradeško"
        assert entries[1]["voting_right"] is True

    def test_deputy_governor_role(self):
        entries, _ = parse_members_block(
            "**Members**\n\n* Mr Kaasik, Acting Governor of Bank of Estonia\n"
        )
        assert entries[0]["name"] == "Kaasik"
        assert entries[0]["voting_right"] is True


class TestCeProposal:
    def test_direction_hold(self):
        d, bp = _direction(
            "Mr Lane proposed that the Governing Council keep the three key ECB "
            "interest rates unchanged. While a further rate cut would help, "
            "conditions did not warrant it."
        )
        assert (d, bp) == ("hold", None)

    def test_direction_cut_with_bp(self):
        d, bp = _direction(
            "Mr Lane therefore proposed that the three key ECB interest rates be "
            "lowered by 25 basis points."
        )
        assert (d, bp) == ("cut", 25)

    def test_direction_hike_with_bp(self):
        d, bp = _direction(
            "Accordingly, Mr Lane proposed raising the three key ECB interest rates "
            "by 50 basis points, in line with prior communication."
        )
        assert (d, bp) == ("hike", 50)

    def test_direction_package_is_none(self):
        d, bp = _direction(
            "With these considerations in mind, Mr Lane proposed announcing a path "
            "for net purchases of €40 billion in April and €30 billion in May."
        )
        assert (d, bp) == (None, None)

    def test_extract_2024_deterministic(self):
        res = extract_one("2024-12-12", _fixture("account_2024-12-12.md"), use_llm_fallback=False)
        assert res.extraction_method == "deterministic"
        assert res.ce_mismatch is False
        assert res.chief_economist == "Philip R. Lane"
        assert "Lane" in (res.proposal_speaker or "")
        assert res.proposed_direction == "cut"
        assert res.proposed_bp == 25

    def test_extract_2015_needs_fallback(self):
        # the QE account has no "<CE> proposed" sentence -> flagged, not faked
        res = extract_one("2015-01-22", _fixture("account_2015-01-22.md"), use_llm_fallback=False)
        assert res.extraction_method == "failed"
        assert res.proposal_text == ""


class TestDecisionMode:
    def test_section2_and_keyword_harvest(self):
        sec = section2(_fixture("account_2015-01-22.md"))
        assert "Governing Council" not in sec[:30]  # heading itself excluded
        quotes = keyword_harvest(sec)
        assert any("consensus" in q.lower() for q in quotes)

    def test_keyword_harvest_empty_on_plain_text(self):
        assert keyword_harvest("Members discussed the outlook. Inflation was low.") == []


class TestSlugDate:
    def test_old_style(self):
        assert str(_slug_date("/press/pr/date/2008/html/pr081008.en.html")) == "2008-10-08"

    def test_modern_no_hash(self):
        assert str(_slug_date("/press/pr/date/2017/html/ecb.mp170427.en.html")) == "2017-04-27"

    def test_modern_hashed(self):
        url = "/press/pr/date/2020/html/ecb.mp200312~8d3aec3ff2.en.html"
        assert str(_slug_date(url)) == "2020-03-12"

    def test_no_slug(self):
        assert _slug_date("https://example.org/other.html") is None


class TestHtmlToMarkdown:
    def test_attendees_survive_conversion(self):
        html = """
        <html><body><nav>nav</nav>
        <main>
        <h1>Account of the monetary policy meeting</h1>
        <h2>Members</h2>
        <ul><li>Ms Lagarde, President</li><li>Mr Makhlouf*</li></ul>
        <p>* Members not holding a voting right in December 2024.</p>
        </main>
        <footer>CONTACT European Central Bank</footer>
        </body></html>
        """
        md = html_to_markdown(html)
        assert "Lagarde" in md and "Makhlouf" in md
        assert "voting right" in md
        assert "CONTACT" not in md


if __name__ == "__main__":
    pytest.main([__file__])
