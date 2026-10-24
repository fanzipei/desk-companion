# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
import base64
from datetime import datetime,timedelta
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from PIL import Image
from pc.ai import AiClient
from pc.capture import RegionCapture
from pc.core import Repository
from pc.reminders import LOCAL,ReminderStore,parse_time,propose
from pc.summarizer import article_text,facts_only


class CaptureTests(unittest.TestCase):
    def test_only_selected_region_is_encoded(self):
        capture=RegionCapture.__new__(RegionCapture)
        capture.width=capture.height=40
        capture.image=Image.new("RGB",(40,40),"red")
        capture.image.paste("green",(10,10,30,30))
        capture.start=(30,30)
        capture.window=Mock()
        capture.on_image=Mock(); capture.on_close=Mock()
        capture.finish(SimpleNamespace(x=10,y=10))
        selected=capture.on_image.call_args.args[0]
        data=AiClient.image_content(selected,"read")[1]["image_url"]["url"].split(",",1)[1]
        image=Image.open(io.BytesIO(base64.b64decode(data)))
        self.assertEqual(image.size,(20,20))
        self.assertEqual(image.getextrema(),((0,0),(128,128),(0,0)))
        self.assertIsNone(capture.image)

    def test_cancel_and_tiny_region_never_submit(self):
        capture=RegionCapture.__new__(RegionCapture)
        capture.width=capture.height=40; capture.start=(10,10)
        capture.image=Image.new("RGB",(40,40)); capture.window=Mock()
        capture.on_image=Mock(); capture.on_close=Mock()
        capture.finish(SimpleNamespace(x=11,y=11))
        capture.on_image.assert_not_called()
        capture.on_close.assert_called_once()

    def test_vision_validation_tracks_model_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            keys=SimpleNamespace(load=lambda:"key1")
            ai=AiClient(keys,directory)
            config={"llm_base_url":"https://example.com/v1","llm_model":"model1"}
            self.assertFalse(ai.verified(config))
            ai.path.write_text(json.dumps({"signature":ai.signature(config),"text":True,"vision":True}))
            self.assertTrue(ai.verified(config))
            self.assertFalse(ai.verified({**config,"llm_model":"model2"}))
            keys.load=lambda:"key2"
            self.assertFalse(ai.verified(config))
            with patch.object(ai,"complete") as call:
                with self.assertRaises(ValueError):
                    ai.screenshot_note(config,Image.new("RGB",(40,40)))
                call.assert_not_called()


class ReminderTests(unittest.TestCase):
    def test_last_successfully_sent_note_excludes_pending_note(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=Repository(directory)
            first=repo.enqueue_note("first")
            repo.acknowledge(repo.next_operation()["qid"])
            repo.enqueue_note("not delivered yet")
            self.assertEqual(repo.last_sent_note()["id"],first["id"])
            self.assertEqual(Repository(directory).last_sent_note()["id"],first["id"])

    def test_ai_proposal_does_not_save_and_rejects_missing_or_past_times(self):
        now=datetime(2026,10,24,14,0,tzinfo=LOCAL)
        ai=Mock()
        for reply in ({"has_time":False,"due_at":"2026-10-25T15:00:00+08:00"},
                      {"has_time":True,"due_at":"2026-10-24T13:00:00+08:00"},
                      {"has_time":True,"due_at":"2026-10-25T15:00:00"}):
            ai.complete.return_value=json.dumps(reply)
            self.assertIsNone(propose(ai,{}, {"text":"data"},now)["time"])
        ai.complete.return_value=json.dumps({"has_time":True,"due_at":"2026-10-25T15:00:00+08:00"})
        self.assertEqual(propose(ai,{}, {"text":"data"},now)["time"],"2026-10-25 15:00")

    def test_only_confirmation_persists_and_due_reminder_fires_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store=ReminderStore(directory)
            self.assertFalse(store.path.exists())
            with self.assertRaises(ValueError):
                store.confirm({"id":"x","text":"test"},"2020-01-01 10:00")
            future=(datetime.now(LOCAL)+timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
            item=store.confirm({"id":"x","text":"test"},future)
            reloaded=ReminderStore(directory)
            self.assertEqual(len(reloaded.take_due(item["due"]+1)),1)
            self.assertEqual(ReminderStore(directory).take_due(item["due"]+10),[])


class NewsFactsTests(unittest.TestCase):
    def test_no_fetching_disclaimers(self):
        for text in ("因正文无法获取，以上仅根据标题整理。","由于无法获取新闻正文，只能概述。"):
            self.assertFalse(facts_only(text))
        self.assertTrue(facts_only("研究团队发布了新的机器人控制方法。"))

    def test_article_retries_direct_after_system_proxy_failure(self):
        with patch("pc.summarizer._article_text",side_effect=[OSError(),"article"]) as fetch:
            self.assertEqual(article_text("https://example.com"),"article")
            self.assertEqual([call.args[1] for call in fetch.call_args_list],["system","direct"])
