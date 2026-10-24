# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from pc.core import (GDELT_ID_BASE, GdeltDoc, NewsRateLimited, Repository, merge_news,
                     normalize_gdelt, normalize_story)
from pc.news_schedule import NewsSchedule


def hn(number, timestamp=100):
    return normalize_story({"type": "story", "id": number, "title": f"HN {number}",
                            "time": timestamp, "url": f"https://hn-example.com/{number}"})


def gdelt(number):
    return normalize_gdelt({"title": f"GDELT {number}", "url": f"https://gdelt-example.com/{number}",
                            "seendate": "20261023T160000Z"})


class MixedNewsTests(unittest.TestCase):
    def test_gdelt_normalization_stable_safe_exact_integer_id(self):
        item = {"title": "A &amp; B", "url": "https://example.com/story?utm_source=x", "seendate": "20261023T160000Z"}
        first = normalize_gdelt(item)
        second = normalize_gdelt({**item, "url": "https://example.com/story?utm_source=y"})
        self.assertEqual(first["id"], second["id"])
        self.assertGreaterEqual(first["id"], GDELT_ID_BASE)
        self.assertLess(first["id"], 1 << 53)
        self.assertEqual(int(float(first["id"])), first["id"])
        self.assertEqual(first["title"], "A & B")
        self.assertEqual(first["time"], 1792771200)
        self.assertEqual(first["source"], "gdelt")
        self.assertIsNone(normalize_gdelt({**item, "url": "file:///C:/secret"}))

    def test_both_sources_interleave_and_share_twenty_slots(self):
        merged = merge_news({"hn": [hn(i, i) for i in range(1, 21)], "gdelt": [gdelt(i) for i in range(20)]})
        self.assertEqual(len(merged), 20)
        self.assertEqual([item["source"] for item in merged], ["hn", "gdelt"]*10)
        self.assertEqual(merged[0]["id"], 20)

    def test_deduplicate_same_article_across_providers(self):
        first = hn(1)
        second = normalize_gdelt({"title": "Duplicate", "url": first["url"]+"?utm_campaign=test#top"})
        merged = merge_news({"hn": [first], "gdelt": [second, gdelt(2)]})
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[1]["source"], "gdelt")

    def test_one_available_source_can_fill_all_slots(self):
        self.assertEqual(len(merge_news({"hn": [hn(i) for i in range(1, 21)], "gdelt": []})), 20)

    def test_cache_migration_and_updates_preserve_notes_and_other_source(self):
        with tempfile.TemporaryDirectory() as directory:
            notes = [{"id": "user-note", "text": "保留原便签", "pinned": True}]
            original = {"notes": notes, "outbox": [], "news": [hn(1)], "news_updated": 10}
            Path(directory, "state.json").write_text(json.dumps(original), encoding="utf-8")
            repository = Repository(directory)
            self.assertEqual(len(repository.snapshot()["news_feeds"]["hn"]), 1)
            repository.update_news_source("gdelt", [gdelt(1)], 20)
            self.assertEqual(repository.snapshot()["notes"], notes)
            self.assertEqual(len(repository.snapshot()["news"]), 2)
            self.assertEqual(repository.news_url(gdelt(1)["id"]), gdelt(1)["url"])
            first_revision = repository.news_packet()["revision"]
            repository.update_news_source("hn", [hn(2)], 20)
            packet = repository.news_packet()
            self.assertGreater(packet["revision"], first_revision)
            self.assertEqual({item["source"] for item in packet["items"]}, {"hn", "gdelt"})
            self.assertNotIn("url", packet["items"][0])
            self.assertEqual(Repository(directory).snapshot()["notes"], notes)
            # An article from the previous batch still opens during/after a refresh.
            self.assertEqual(repository.news_url(1), hn(1)["url"])

    def test_api_rate_limit_does_not_trigger_an_immediate_second_request(self):
        api = GdeltDoc()
        with patch.object(api, "_get", side_effect=HTTPError("https://api.gdeltproject.org", 429, "Too Many", {"Retry-After": "1200"}, None)) as get:
            with self.assertRaises(NewsRateLimited) as result:
                api.fetch()
            self.assertEqual(get.call_count, 1)
            self.assertEqual(result.exception.retry_after, 1200)

    def test_api_schema_validation_and_duplicate_results(self):
        item = {"title": "A", "url": "https://example.com/a"}
        with patch.object(GdeltDoc, "_get", return_value={"articles": [item, item, {"title": "invalid"}]}):
            self.assertEqual(len(GdeltDoc().fetch()), 1)
        with patch.object(GdeltDoc, "_get", return_value={"error": "bad query"}):
            with self.assertRaises(RuntimeError):
                GdeltDoc().fetch()


class ScheduleTests(unittest.TestCase):
    def test_independent_ten_minute_cycles_staggered_by_five_minutes(self):
        schedule = NewsSchedule(0)
        starts = []
        for now in (0, 300, 600, 900, 1200, 1500):
            source = schedule.due(now)
            starts.append((source, now))
            schedule.started(source, now)
        self.assertEqual(starts, [("hn", 0), ("gdelt", 300), ("hn", 600), ("gdelt", 900), ("hn", 1200), ("gdelt", 1500)])

    def test_request_duration_does_not_accumulate_schedule_drift(self):
        schedule = NewsSchedule(0)
        schedule.started("hn", 0)
        self.assertIsNone(schedule.due(100))
        self.assertEqual(schedule.next_due["hn"], 600)
        schedule.started("gdelt", 335)
        self.assertEqual(schedule.next_due["gdelt"], 900)
        schedule.started("hn", 1300)
        self.assertEqual(schedule.next_due["hn"], 1800)

    def test_retry_after_and_manual_refresh(self):
        schedule = NewsSchedule(0)
        schedule.started("gdelt", 300)
        schedule.defer("gdelt", 301, 1200)
        self.assertGreaterEqual(schedule.next_due["gdelt"], 1501)
        schedule.reset(2000)
        self.assertEqual(schedule.next_due, {"hn": 2000, "gdelt": 2300})


if __name__ == "__main__":
    unittest.main()
