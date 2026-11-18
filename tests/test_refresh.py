"""Проверки новой логики обновления подборки (без обращений к сети)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import app as app_module
import news_sources
import visitor_history


def make_item(title: str, url: str, published: str) -> dict:
    return {
        "id": news_sources.item_key({"title": title, "url": url}),
        "title": title,
        "summary": "Проверочный материал из метаданных источника.",
        "source": "Test Source",
        "via": "test",
        "date": published,
        "url": url,
        "topics": ["materials"],
        "tags": ["Материалы"],
        "visual": "MAT",
        "tone": "cyan",
        "reading_time": 2,
    }


class SlidingPeriodTests(unittest.TestCase):
    def test_calendar_month_is_recalculated(self):
        self.assertEqual(
            news_sources.sliding_period(date(2026, 11, 18)),
            (date(2026, 10, 18), date(2026, 11, 18)),
        )
        self.assertEqual(
            news_sources.sliding_period(date(2026, 3, 31)),
            (date(2026, 2, 28), date(2026, 3, 31)),
        )
        self.assertEqual(
            news_sources.sliding_period(date(2024, 3, 31)),
            (date(2024, 2, 29), date(2024, 3, 31)),
        )

    def test_endpoint_uses_dynamic_date(self):
        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 1, 15)

        items = [
            make_item("Старый материал", "https://example.org/old", "2025-12-10"),
            make_item("Свежий материал", "https://example.org/fresh", "2026-01-10"),
        ]
        fake = lambda today, timeout, per_query: items

        with patch.object(news_sources, "date", FakeDate), patch.object(
            news_sources, "FETCHERS", (("Fake", fake),)
        ):
            response = app_module.app.test_client().get("/api/refresh")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual([item["title"] for item in payload["items"]], ["Свежий материал"])
        self.assertIn("15 января 2026", payload["period_display"])

    def test_initial_page_uses_dynamic_period(self):
        with patch.object(
            news_sources,
            "sliding_period",
            return_value=(date(2026, 1, 15), date(2026, 2, 15)),
        ):
            html = app_module.app.test_client().get("/").get_data(as_text=True)

        self.assertIn(
            "Подборка за период <span data-period-range>с 15 января по 15 февраля 2026</span>",
            html,
        )
        self.assertNotIn("за 9 октября — 9 ноября 2026 года", html)
        self.assertIn("с 15 января по 15 февраля 2026", html)


class CanonicalKeyTests(unittest.TestCase):
    def test_same_url_with_query_and_case_is_one_item(self):
        first = news_sources.item_key({"url": "https://doi.org/10.1000/ABC?utm=x"})
        second = news_sources.item_key({"url": "https://doi.org/10.1000/abc"})
        self.assertEqual(first, second)

    def test_arxiv_versions_are_one_item(self):
        first = news_sources.canonical_url("https://arxiv.org/abs/2511.01234v2")
        second = news_sources.canonical_url("http://www.arxiv.org/pdf/2511.01234v3.pdf")
        self.assertEqual(first, second)

    def test_title_fallback_is_normalized(self):
        first = news_sources.item_key({"title": "Гибкий/датчик: TEST!"})
        second = news_sources.item_key({"title": "гибкий датчик test"})
        self.assertEqual(first, second)


class SelectionTests(unittest.TestCase):
    def test_old_seen_and_duplicates_are_excluded(self):
        start, end = date(2026, 10, 18), date(2026, 11, 18)
        kept = make_item("Новый", "https://example.org/a", "2026-11-01")
        duplicate = make_item("Дубль", "https://example.org/A?from=rss", "2026-11-02")
        seen = make_item("Показанный", "https://example.org/seen", "2026-11-03")
        too_old = make_item("Старый", "https://example.org/old", "2026-10-01")

        selected = news_sources.select_items(
            [kept, duplicate, seen, too_old],
            {news_sources.item_key(seen)},
            10,
            start,
            end,
        )
        self.assertEqual([item["title"] for item in selected], ["Новый"])


class RefreshFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.tempdir.name) / "history.json"
        self.env_patcher = patch.dict(
            os.environ, {"DIGEST_HISTORY_FILE": str(self.history_file)}, clear=False
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        self.tempdir.cleanup()

    def _pool(self):
        start, end = news_sources.sliding_period()
        return [
            make_item("Материал 1", "https://example.org/1", end.isoformat()),
            make_item("Материал 2", "https://example.org/2", start.isoformat()),
            make_item("Материал 3", "https://example.org/3", end.isoformat()),
        ]

    def test_repeated_refreshes_never_repeat_items(self):
        pool = self._pool()
        fake = lambda today, timeout, per_query: list(pool)
        client = app_module.app.test_client()

        with patch.object(news_sources, "FETCHERS", (("Fake", fake),)):
            client.get("/")  # исходные 10 карточек тоже попадают в историю
            titles = []
            for _ in range(4):
                response = client.get("/api/refresh")
                payload = response.get_json()
                if payload["ok"]:
                    titles.extend(item["title"] for item in payload["items"])
                else:
                    self.assertEqual(payload["reason"], "no_new_items")

        self.assertEqual(sorted(titles), ["Материал 1", "Материал 2", "Материал 3"])

    def test_exhaustion_keeps_current_digest(self):
        pool = self._pool()[:1]
        fake = lambda today, timeout, per_query: list(pool)
        client = app_module.app.test_client()

        with patch.object(news_sources, "FETCHERS", (("Fake", fake),)):
            first = client.get("/api/refresh").get_json()
            second = client.get("/api/refresh").get_json()

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "no_new_items")
        self.assertTrue(second["kept_current"])
        self.assertNotIn("items", second)

    def test_source_error_does_not_erase_current_digest(self):
        def broken(today, timeout, per_query):
            raise TimeoutError("source down")

        client = app_module.app.test_client()
        with patch.object(news_sources, "FETCHERS", (("Broken", broken),)):
            response = client.get("/api/refresh")

        payload = response.get_json()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "source_unavailable")
        self.assertTrue(payload["kept_current"])
        self.assertNotIn("items", payload)

    def test_one_failed_source_does_not_block_another(self):
        def broken(today, timeout, per_query):
            raise OSError("down")

        item = make_item("Доступный", "https://example.org/ok", news_sources.sliding_period()[1].isoformat())
        with patch.object(
            news_sources,
            "FETCHERS",
            (("Broken", broken), ("Fake", lambda t, to, n: [item])),
        ):
            payload = app_module.app.test_client().get("/api/refresh").get_json()

        self.assertTrue(payload["ok"])
        self.assertEqual([row["title"] for row in payload["items"]], ["Доступный"])
        self.assertTrue(payload["errors"])

    def test_history_is_separate_for_each_visitor(self):
        first = app_module.app.test_client()
        second = app_module.app.test_client()

        first.get("/")
        second.get("/")
        visitor_one = first.get_cookie("digest_vid").value
        visitor_two = second.get_cookie("digest_vid").value

        self.assertNotEqual(visitor_one, visitor_two)
        self.assertEqual(len(visitor_history.load_seen(visitor_one)), 10)
        self.assertEqual(len(visitor_history.load_seen(visitor_two)), 10)

        visitor_history.add_seen(visitor_one, ["only-for-first"])
        self.assertIn("only-for-first", visitor_history.load_seen(visitor_one))
        self.assertNotIn("only-for-first", visitor_history.load_seen(visitor_two))


class TftTopicTests(unittest.TestCase):
    def test_relevance_priority_and_exclusions(self):
        cases = (
            (
                "Flexible polyimide TFT backplane for foldable OLED displays",
                "A flexible substrate and an active matrix are described.",
                0,
            ),
            (
                "Гибкая TFT-матрица для дисплея на полиимиде",
                "Матрица управляет пикселями гибкого дисплея.",
                0,
            ),
            (
                "a-IGZO TFT array for MicroLED display",
                "The array drives display pixels.",
                1,
            ),
            (
                "Active-matrix MiniLED local dimming backlight",
                "Matrix-controlled backlight for an LCD panel is described.",
                1,
            ),
            (
                "IGZO surface morphology after wet etching",
                "Process study of an oxide semiconductor layer.",
                2,
            ),
            ("LCD television review with a new stand", "No transistor matrix is discussed.", None),
            (
                "MiniLED backlight for a television",
                "Only LED backlighting is discussed, without matrix addressing.",
                None,
            ),
        )

        for title, summary, expected in cases:
            with self.subTest(title=title):
                relevance = news_sources.tft_relevance(title, summary)
                if expected is None:
                    self.assertIsNone(relevance)
                else:
                    self.assertIsNotNone(relevance)
                    self.assertEqual(relevance[0], expected)

    def test_indirect_match_is_explained_and_tagged(self):
        card = news_sources._make_card(
            title="IGZO surface morphology after wet etching",
            summary="Process study of an oxide semiconductor layer.",
            source="Test Journal",
            via="test",
            published="2026-11-10",
            url="https://example.org/igzo-etching",
        )
        self.assertIsNotNone(card)
        self.assertEqual(card["topics"][0], "tft")
        self.assertEqual(card["tft_rank"], 2)
        self.assertIn("Косвенная связь", card["tft_note"])
        self.assertIn("TFT matrixes", card["tags"])

    def test_tft_items_are_shown_before_other_topics(self):
        start, end = date(2026, 10, 18), date(2026, 11, 18)
        direct = make_item("Гибкая TFT-матрица", "https://example.org/tft0", "2026-10-20")
        direct.update(topics=["tft"], tft_rank=0)
        indirect = make_item("IGZO process", "https://example.org/tft2", "2026-10-21")
        indirect.update(topics=["tft"], tft_rank=2)
        other = make_item("Свежая статья", "https://example.org/other", end.isoformat())

        selected = news_sources.select_items(
            [other, indirect, direct],
            set(),
            10,
            start,
            end,
            topic="all",
            diversify=False,
        )
        self.assertEqual(
            [item["title"] for item in selected],
            ["Гибкая TFT-матрица", "IGZO process", "Свежая статья"],
        )

    def test_tft_item_is_not_lost_when_it_is_older_than_the_first_page(self):
        start, end = date(2026, 10, 18), date(2026, 11, 18)
        general = [
            make_item(f"Общая статья {index}", f"https://example.org/g{index}", end.isoformat())
            for index in range(11)
        ]
        tft = make_item("Гибкая TFT-матрица", "https://example.org/tft", start.isoformat())
        tft.update(topics=["tft"], tft_rank=0)

        selected = news_sources.select_items(
            general + [tft],
            set(),
            11,
            start,
            end,
            topic="all",
            diversify=False,
        )
        self.assertEqual(selected[0]["title"], "Гибкая TFT-матрица")
        self.assertEqual(sum(1 for item in selected if "tft" in item["topics"]), 1)

    def test_page_preparation_keeps_tft_priority(self):
        start, end = date(2026, 10, 18), date(2026, 11, 18)
        direct = make_item("Гибкая TFT-матрица", "https://example.org/tft0", "2026-10-20")
        direct.update(topics=["tft"], tft_rank=0)
        indirect = make_item("IGZO process", "https://example.org/tft2", "2026-10-21")
        indirect.update(topics=["tft"], tft_rank=2)
        other = make_item("Свежая статья", "https://example.org/other", end.isoformat())

        items, _ = app_module.prepare_items([other, indirect, direct], start, end)
        self.assertEqual(
            [item["title"] for item in items],
            ["Гибкая TFT-матрица", "IGZO process", "Свежая статья"],
        )

    def test_filter_is_available_on_the_page(self):
        with app_module.app.test_request_context("/"):
            data = app_module.build_page_data()
        self.assertIn("tft", data["counts"])
        self.assertTrue(
            any(
                item["slug"] == "tft" and item["label"] == "TFT matrixes"
                for item in data["filters"]
            )
        )


TOPIC_TERM = {
    "flexible": "flexible electronics stretchable",
    "printed": "printed electronics conductive ink",
    "wearable": "wearable biosensor",
    "materials": "hydrogel MXene electronic materials",
    "displays": "flexible display electrochromic",
    "tft": "flexible TFT backplane matrix for displays",
    "medicine": "medical biosensor diagnostics",
}


def make_topic_item(topic: str, index: int, published: str) -> dict:
    item = make_item(
        f"Материал {topic} {index}",
        f"https://example.org/{topic}-{index}",
        published,
    )
    item.update(
        topics=[topic],
        tags=[topic],
        summary=f"Исследование: {TOPIC_TERM[topic]}.",
    )
    return item


class TagAwareRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.tempdir.name) / "history.json"
        self.env_patcher = patch.dict(
            os.environ, {"DIGEST_HISTORY_FILE": str(self.history_file)}, clear=False
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        self.tempdir.cleanup()

    def _refresh(self, tag: str, items: list[dict], fetchers=None):
        fake = lambda today, timeout, per_query, queries=None: list(items)
        active = fetchers or (("Fake", fake),)
        with patch.object(news_sources, "FETCHERS", active):
            return app_module.app.test_client().get(
                f"/api/refresh?tag={tag}"
            ).get_json()

    def test_active_tag_filters_by_content(self):
        end = news_sources.sliding_period()[1].isoformat()
        printed = make_topic_item("printed", 1, end)
        materials = make_topic_item("materials", 2, end)

        payload = self._refresh("printed", [printed, materials])

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["active_tag"], "printed")
        self.assertEqual([row["title"] for row in payload["items"]], [printed["title"]])

    def test_all_diversifies_and_returns_ten(self):
        import random

        end = news_sources.sliding_period()[1].isoformat()
        candidates = [
            make_topic_item(topic, index, end)
            for topic in ("printed", "flexible", "medicine", "wearable")
            for index in range(5)
        ]
        selected = news_sources.select_items(
            candidates,
            set(),
            10,
            *news_sources.sliding_period(),
            topic="all",
            rng=random.Random(7),
        )
        self.assertEqual(len(selected), 10)
        self.assertEqual(
            len({item["topics"][0] for item in selected}), 4
        )

    def test_shortfall_is_reported_without_filling(self):
        end = news_sources.sliding_period()[1].isoformat()
        items = [make_topic_item("medicine", index, end) for index in range(3)]

        payload = self._refresh("medicine", items)

        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["items"]), 3)
        self.assertTrue(payload["shortfall"])
        self.assertIn("меньше 10", payload["message"])
        self.assertTrue(all("medicine" in row["topics"] for row in payload["items"]))

    def test_additional_search_is_used_when_shortfall(self):
        end = news_sources.sliding_period()[1].isoformat()
        first_items = [make_topic_item("printed", index, end) for index in range(2)]
        second_items = [make_topic_item("printed", index + 10, end) for index in range(3)]
        first = {"items": first_items, "errors": [], "sources": ["Fake"]}
        second = {"items": second_items, "errors": [], "sources": ["Fake"]}

        with patch.object(
            news_sources, "collect_candidates", side_effect=[first, second]
        ) as collect:
            payload = app_module.app.test_client().get(
                "/api/refresh?tag=printed"
            ).get_json()

        self.assertEqual(collect.call_count, 2)
        self.assertTrue(collect.call_args.kwargs["fallback"])
        self.assertEqual(len(payload["items"]), 5)

    def test_seen_history_applies_across_tags(self):
        end = news_sources.sliding_period()[1].isoformat()
        item = make_topic_item("materials", 1, end)
        client = app_module.app.test_client()
        client.get("/")
        visitor_id = client.get_cookie("digest_vid").value
        visitor_history.add_seen(visitor_id, [news_sources.item_key(item)])

        fake = lambda today, timeout, per_query, queries=None: [item]
        with patch.object(news_sources, "FETCHERS", (("Fake", fake),)):
            payload = client.get("/api/refresh?tag=materials").get_json()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "no_new_items")

    def test_queries_are_tag_specific(self):
        self.assertEqual(
            news_sources.queries_for_topic("printed"),
            ("printed electronics conductive ink",),
        )
        self.assertIn("TFT array display IGZO", news_sources.queries_for_topic("tft"))
        self.assertNotIn(
            "printed electronics conductive ink",
            news_sources.queries_for_topic("printed", fallback=True),
        )

    def test_invalid_tag_falls_back_to_all(self):
        end = news_sources.sliding_period()[1].isoformat()
        item = make_topic_item("flexible", 1, end)
        payload = self._refresh("unknown", [item])
        self.assertEqual(payload["active_tag"], "all")
        self.assertEqual(len(payload["items"]), 1)


class JobsRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.tempdir.name) / "history.json"
        self.env_patcher = patch.dict(
            os.environ, {"DIGEST_HISTORY_FILE": str(self.history_file)}, clear=False
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        self.tempdir.cleanup()

    def _vacancy(self, index: int) -> dict:
        published = news_sources.sliding_period()[1].isoformat()
        card = news_sources._make_card(
            title=f"Flexible electronics process engineer {index}",
            summary="Работа с printed electronics и wearable sensor в команде разработки.",
            source="Example Employer",
            via="Remotive",
            published=published,
            url=f"https://jobs.example.org/vacancy-{index}",
            topics=["jobs", "printed", "wearable"],
        )
        card["is_vacancy"] = True
        return card

    def test_jobs_returns_vacancies_not_news(self):
        end = news_sources.sliding_period()[1].isoformat()
        news = make_topic_item("printed", 1, end)
        vacancy = self._vacancy(1)
        fake = lambda today, timeout, per_query, queries=None: [news, vacancy]

        with patch.object(news_sources, "JOB_FETCHERS", (("Fake", fake),)):
            payload = app_module.app.test_client().get("/api/refresh?tag=jobs").get_json()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["active_tag"], "jobs")
        self.assertEqual([row["title"] for row in payload["items"]], [vacancy["title"]])
        self.assertTrue(all("jobs" in row["topics"] for row in payload["items"]))

    def test_jobs_without_relevant_vacancies_keeps_current(self):
        end = news_sources.sliding_period()[1].isoformat()
        news = make_topic_item("printed", 1, end)
        fake = lambda today, timeout, per_query, queries=None: [news]

        with patch.object(news_sources, "JOB_FETCHERS", (("Fake", fake),)):
            payload = app_module.app.test_client().get("/api/refresh?tag=jobs").get_json()

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "no_new_items")
        self.assertIn("вакансий", payload["message"])
        self.assertTrue(payload["kept_current"])


if __name__ == "__main__":
    unittest.main()
