"""Tests for the TinyFish press-coverage collector (no network).

All collaborators are injectable parameters of ``collect`` — the fakes
below are plain callables and every path is redirected to tmp_path, so no
patching is needed.
"""

from __future__ import annotations

from datetime import date as d

import polars as pl

from synthetic_council.collectors import press_speeches as ps


class TestArticleDate:
    def test_published_date_wins(self):
        assert ps.article_date("2024-05-27T11:20:44+02:00", "https://x/no-date") == "2024-05-27"

    def test_url_fallbacks(self):
        assert ps.article_date(None, "https://reuters.com/x/ecbs-knot-2024-04-29/") == "2024-04-29"
        assert ps.article_date(None, "https://x/2024/05/27/slug/") == "2024-05-27"

    def test_undated(self):
        assert ps.article_date(None, "https://x.com/article/slug") is None
        assert ps.article_date("yesterday", "https://x.com/article") is None


class TestPolicySignal:
    def test_monetary_headline_passes(self):
        assert ps._has_policy_signal("ECB to focus on quarterly meetings with rate cuts", "gradual")

    def test_non_english_stem_passes(self):
        assert ps._has_policy_signal("ECB-medlem åbner for rentenedsættelse", "mere end 100 bp")

    def test_court_case_rejected(self):
        assert not ps._has_policy_signal(
            "As arraignment looms, Edward Scicluna represents Malta at ECB's meeting",
            "the former finance minister faces charges",
        )
        assert not ps._has_policy_signal(
            "A (not so) brief 'criminal' history of the ECB", "scandals over the decades"
        )


class TestIsSubject:
    def test_full_name(self):
        assert ps._is_subject("ECB's Klaas Knot: increasingly confident", "", "Klaas Knot")

    def test_surname_with_institutional_prefix(self):
        assert ps._is_subject("ECB's Knot says caution still warranted", "", "Klaas Knot")

    def test_other_person_same_surname_rejected(self):
        assert not ps._is_subject("Interview with Mark Knot on markets", "", "Klaas Knot")

    def test_absent_surname_rejected(self):
        assert not ps._is_subject("ECB cuts rates as inflation falls", "", "Klaas Knot")

    def test_first_name_prefix_accepted(self):
        assert ps._is_subject("Klaas Knot on the Dutch economy", "", "Klaas Knot")

    def test_other_member_headlining_rejected(self):
        assert not ps._is_subject(
            "President of the ECB, Christine Lagarde, thanks Scicluna",
            "",
            "Edward Scicluna",
            others=("Christine Lagarde",),
        )

    def test_wire_headline_without_name_accepted_via_body(self):
        # syndicated wire story: headline is the news, the speaker is named
        # at the top of the body
        head = (
            "FRANKFURT, May 6 (Reuters) - The ECB can gradually reduce rates, "
            "Klaas Knot said in an interview. " + "x" * 800
        )
        assert ps._is_subject(
            "ECB to focus on quarterly meetings with slow, gradual rate cuts", head, "Klaas Knot"
        )

    def test_surname_only_in_body_far_from_first_name_rejected(self):
        # another person's story that mentions the member in passing
        head = "Lagarde opened the meeting. Later the council heard from "
        "experts including one presentation mentioning Knot in a footnote. " + "y" * 900
        assert not ps._is_subject("ECB meeting opened by President", head, "Klaas Knot")


LONG = "Knot said the ECB can gradually reduce rates. " + "x" * 900


def _fake_search_factory(seen_queries: list, results: list[dict]):
    def fake_search(query, after, before, domain_type="news"):
        seen_queries.append((query, after, before))
        return {"results": [r for r in results if r["query_hit"]]}

    return fake_search


def _fake_fetch_factory(errors: list[dict] | None = None):
    def fake_fetch(urls):
        table = {r["url"]: r for r in _FETCH_TABLE.values()}
        results = [table[u] for u in urls if u in table]
        missing = [
            {"url": u, "error": "target_http_error", "status": 401} for u in urls if u not in table
        ]
        return {"results": results, "errors": (errors or []) + missing}

    return fake_fetch


_FETCH_TABLE: dict[str, dict] = {}


class TestCollect:
    def _corpus(self) -> pl.DataFrame:
        # Wunsch has a real speech in-window -> never searched; Knot does not
        return pl.DataFrame(
            {
                "speaker": ["Pierre Wunsch"],
                "date": [d(2024, 5, 2)],
                "title": ["A speech"],
                "contents": ["x" * 500],
                "source": ["cbs"],
                "url": ["https://x/wunsch"],
            }
        )

    def _roster(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "meeting_date": ["2024-06-06", "2024-06-06"],
                "person": ["Klaas Knot", "Pierre Wunsch"],
            }
        )

    def test_pipeline_window_subject_and_dedup(self, tmp_path):
        _FETCH_TABLE.clear()
        _FETCH_TABLE.update(
            {
                "https://news.example/knot-ok/": {
                    "url": "https://news.example/knot-ok/",
                    "title": "ECB's Knot: rate cut a milestone",
                    "text": LONG,
                    "published_date": "2024-05-06T09:00:00+02:00",
                    "language": "en",
                },
                "https://news.example/knot-late/": {  # published after the meeting
                    "url": "https://news.example/knot-late/",
                    "title": "ECB's Knot speaks again",
                    "text": LONG,
                    "published_date": "2024-06-20T09:00:00+02:00",
                },
                "https://news.example/lane/": {  # not the subject
                    "url": "https://news.example/lane/",
                    "title": "ECB's Lane on projections",
                    "text": LONG,
                    "published_date": "2024-05-01T09:00:00+02:00",
                },
            }
        )
        search_results = [
            {
                "query_hit": True,
                "url": "https://news.example/knot-ok/",
                "title": "ECB's Knot: rate cut a milestone",
                "publisher": "Example Wire",
                "site_name": "news.example",
            },
            {
                "query_hit": True,
                "url": "https://news.example/knot-late/",
                "title": "ECB's Knot speaks again on rates",
            },
            {
                "query_hit": True,
                "url": "https://news.example/lane/",
                "title": "ECB's Lane on rates and projections",
            },
        ]
        seen: list = []
        out = ps.collect(
            search_fn=_fake_search_factory(seen, search_results),
            fetch_fn=_fake_fetch_factory(),
            windows=[("2024-04-11", "2024-06-06")],
            corpus=self._corpus(),
            roster=self._roster(),
            existing_pairs=set(),
            out_path=tmp_path / "press.parquet",
            fetched_path=tmp_path / "press_fetched.parquet",
            search_dir=tmp_path / "press_searches",
        )
        assert out.height == 1
        r = out.row(0, named=True)
        assert (r["speaker"], r["date"], r["source"], r["publisher"]) == (
            "Klaas Knot",
            "2024-05-06",
            "press",
            "Example Wire",
        )
        # window enforced server-side in production; asserted on the fake
        assert all(a == "2024-04-11" and b == "2024-06-06" for _, a, b in seen)
        # Wunsch had a real speech in-window: only Knot searched
        assert all("knot" in q.lower() for q, _, _ in seen)
        # search responses archived
        assert len(list((tmp_path / "press_searches").glob("*.json"))) >= 1

        # resume: the pair is marked done -> no further searches on re-run
        out2 = ps.collect(
            search_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-searched")),
            fetch_fn=_fake_fetch_factory(),
            windows=[("2024-04-11", "2024-06-06")],
            corpus=self._corpus(),
            roster=self._roster(),
            existing_pairs=set(),
            out_path=tmp_path / "press.parquet",
            fetched_path=tmp_path / "press_fetched.parquet",
            search_dir=tmp_path / "press_searches",
        )
        assert out2.height == 1

    def test_primary_source_collision_dropped(self, tmp_path):
        _FETCH_TABLE.clear()
        _FETCH_TABLE["https://news.example/wunsch/"] = {
            "url": "https://news.example/wunsch/",
            "title": "Wunsch warns on inflation",
            "text": "Wunsch on inflation. " + "y" * 900,
            "published_date": "2024-05-10T08:00:00Z",
        }
        out = ps.collect(
            search_fn=_fake_search_factory(
                [],
                [
                    {
                        "query_hit": True,
                        "url": "https://news.example/wunsch/",
                        "title": "Wunsch warns on inflation",
                        "publisher": "Wire",
                    }
                ],
            ),
            fetch_fn=_fake_fetch_factory(),
            windows=[("2024-04-11", "2024-06-06")],
            corpus=pl.DataFrame(
                {
                    "speaker": ["Someone Else"],
                    "date": [d(2024, 5, 2)],
                    "title": ["t"],
                    "contents": ["x" * 500],
                    "source": ["ecb"],
                    "url": ["https://x"],
                }
            ),
            roster=pl.DataFrame({"meeting_date": ["2024-06-06"], "person": ["Pierre Wunsch"]}),
            existing_pairs={("Pierre Wunsch", "2024-05-10")},  # already on BIS/CBS
            out_path=tmp_path / "press2.parquet",
            fetched_path=tmp_path / "f2.parquet",
            search_dir=tmp_path / "s2",
        )
        assert out.height == 0


class TestPressFallbackInJoin:
    def test_press_never_displaces_real_speech(self):
        from synthetic_council.speech_union import _latest_with_press_fallback

        win = pl.DataFrame(
            {
                "speaker": ["A", "B", "B", "C"],
                "date": [d(2024, 5, 1), d(2024, 5, 1), d(2024, 5, 20), d(2024, 5, 3)],
                "title": ["real", "real", "press-later", "press-only"],
                "contents": ["x", "y", "z", "w"],
                "source": ["ecb", "bis", "press", "press"],
                "url": ["", "", "", ""],
            }
        )
        latest = _latest_with_press_fallback(win)
        by = {r["speaker"]: r["source"] for r in latest.iter_rows(named=True)}
        assert by == {"A": "ecb", "B": "bis", "C": "press"}


class TestPressPromptSelection:
    def test_press_rows_get_press_prompt(self):
        from synthetic_council.member_profiles.speech_sections import (
            _PRESS_SECTION_SYSTEM,
            _SECTION_SYSTEM,
            _system_prompt,
        )

        assert _system_prompt("press") is _PRESS_SECTION_SYSTEM
        assert _system_prompt("bis") is _SECTION_SYSTEM
        assert _system_prompt("ecb") is _SECTION_SYSTEM
