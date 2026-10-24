# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from pc.core import Repository, normalize_story, safe_web_url, clip_utf8
from pc.protocol import FrameDecoder, Protocol
from pc.windows import parse_hotkey


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = Repository(self.directory.name)

    def test_outbox_survives_restart_until_application_ack(self):
        note = self.repository.enqueue_note("  中英文\nhello  ")
        restarted = Repository(self.directory.name)
        operation = restarted.next_operation()
        self.assertEqual(operation["id"], note["id"])
        self.assertEqual(operation["text"], "中英文\nhello")
        self.assertEqual(restarted.snapshot()["notes"], [])
        restarted.acknowledge(operation["qid"])
        self.assertIsNone(Repository(self.directory.name).next_operation())

    def test_limits_count_utf8_bytes_and_never_truncate(self):
        self.repository.enqueue_note("字" * 1024)
        with self.assertRaises(ValueError):
            self.repository.enqueue_note("字" * 1025)
        for i in range(19):
            self.repository.enqueue_note(str(i))
        with self.assertRaises(ValueError):
            self.repository.enqueue_note("21st pending")

    def test_device_state_wins_after_offline_delete(self):
        self.repository.apply_state({"notes": [{"id": "a", "text": "old", "pinned": True}], "mode": 0})
        self.repository.apply_state({"notes": [], "mode": 1})
        self.assertEqual(self.repository.snapshot()["notes"], [])
        self.assertEqual(self.repository.snapshot()["mode"], 1)

    def test_all_pinned_does_not_queue_unfulfillable_note(self):
        notes = [{"id": str(i), "text": "keep", "pinned": True} for i in range(20)]
        self.repository.apply_state({"notes": notes})
        with self.assertRaises(ValueError):
            self.repository.enqueue_note("extra")
        self.assertEqual(len(self.repository.snapshot()["notes"]), 20)

    def test_physical_button_events_update_pc_cache(self):
        self.repository.apply_state({"notes": [{"id": "a", "text": "hello", "pinned": False}]})
        self.repository.apply_event({"event": "note_pinned", "id": "a", "pinned": True})
        self.assertTrue(self.repository.snapshot()["notes"][0]["pinned"])
        self.repository.apply_event({"event": "note_deleted", "id": "a"})
        self.assertEqual(self.repository.snapshot()["notes"], [])

    def test_news_open_requires_an_id_from_cached_hn_items(self):
        story = normalize_story({"id": 1, "type": "story", "title": "A &amp; B", "url": "https://example.com/hello"})
        self.repository.update_news([story], 100)
        self.assertEqual(story["title"], "A & B")
        self.assertEqual(self.repository.news_url(1), "https://example.com/hello")
        self.assertIsNone(self.repository.news_url(2))
        self.assertNotIn("url", self.repository.news_packet()["items"][0])


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_arbitrary_byte_boundaries_and_multiple_frames(self):
        text = '{"text":"中文🙂"}\n{"ok":true}\n'.encode()
        decoder = FrameDecoder()
        values = []
        for byte in text:
            values.extend(decoder.feed(bytes([byte])))
        self.assertEqual(values, [{"text": "中文🙂"}, {"ok": True}])

    def test_frame_bound(self):
        with self.assertRaises(ValueError):
            FrameDecoder(4).feed(b"12345")

    async def test_write_response_is_not_an_application_ack(self):
        class Client:
            mtu_size = 23
            def __init__(self):
                self.parts = []
            async def start_notify(self, char, callback):
                self.callback = callback
            async def write_gatt_char(self, char, data, response):
                self.parts.append(data)
                self.assertions = response
                if b"\n" in data:
                    request = json.loads(b"".join(self.parts))
                    self.callback(None, json.dumps({"rid": request["rid"], "ok": False, "error": "all_pinned"}).encode()+b"\n")
        client = Client()
        protocol = Protocol(client, lambda event: None)
        await protocol.start()
        reply = await protocol.request({"op": "add", "text": "汉字" * 40})
        self.assertFalse(reply["ok"])
        self.assertTrue(all(len(part) <= 20 for part in client.parts))
        self.assertEqual(protocol.pending, {})


class InputAndNewsTests(unittest.TestCase):
    def test_url_validation(self):
        for invalid in ("javascript:alert(1)", "file:///C:/Windows", "https://user:pass@example.com", "https://", "https://example.com/\nhi"):
            self.assertIsNone(safe_web_url(invalid))
        self.assertEqual(safe_web_url("https://example.com/a"), "https://example.com/a")

    def test_deleted_news_and_missing_external_link(self):
        self.assertIsNone(normalize_story({"id": 1, "type": "story", "title": "gone", "dead": True}))
        story = normalize_story({"id": 42, "type": "story", "title": "Ask HN"})
        self.assertEqual(story["url"], "https://news.ycombinator.com/item?id=42")
        self.assertEqual(clip_utf8("甲乙", 4), "甲")

    def test_hotkey_configuration(self):
        self.assertEqual(parse_hotkey("Ctrl+Alt+j"), (0x4003, ord("J")))
        self.assertEqual(parse_hotkey("Ctrl+Alt+Space"), (0x4003, 0x20))
        for bad in ("J", "Ctrl+Ctrl+J", "Ctrl+F25", "Foo+J"):
            with self.assertRaises(ValueError):
                parse_hotkey(bad)


if __name__ == "__main__":
    unittest.main()
