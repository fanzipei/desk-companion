# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
import json
import queue
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
from unittest.mock import Mock,MagicMock,patch

from pc.bridge import Bridge
from pc.core import Repository,HackerNews,GdeltDoc
from pc.news_network import connection_routes,proxy_handler
from pc.summarizer import article_text,related_reports,Summarizer
from tests.test_briefs import BRIEF
from tests.test_mixed_news import hn

CUSTOM={"news_proxy_mode":"custom","news_proxy_url":"http://127.0.0.1:7897"}


class NewsProxyTests(unittest.TestCase):
    def test_actual_http_requests_from_both_feeds_and_article_reach_custom_proxy(self):
        paths=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if '/newstories.json' in self.path:
                    body=b'[1]'; kind='application/json'
                elif '/item/1.json' in self.path:
                    body=json.dumps({'id':1,'type':'story','title':'Test','url':'http://article.example.invalid/story'}).encode(); kind='application/json'
                elif 'gdelt.example.invalid' in self.path:
                    body=json.dumps({'articles':[{'title':'Test','url':'http://article.example.invalid/story'}]}).encode(); kind='application/json'
                else:
                    body=('<article><p>'+BRIEF+'</p></article>').encode(); kind='text/html'
                self.send_response(200); self.send_header('Content-Type',kind+'; charset=utf-8')
                self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args):
                pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True)
        thread.start()
        config={**CUSTOM,'news_proxy_url':f'http://127.0.0.1:{server.server_port}'}
        try:
            with patch.object(HackerNews,'BASE','http://hn.example.invalid/v0/'), \
                    patch.object(GdeltDoc,'BASE','http://gdelt.example.invalid/doc'), \
                    patch.dict('os.environ',{'no_proxy':'','NO_PROXY':''}), \
                    patch('pc.summarizer.public_article_url'):
                self.assertEqual(len(HackerNews().fetch(1,config=config)),1)
                self.assertEqual(len(GdeltDoc().fetch(1,config=config)),1)
                self.assertEqual(article_text('http://article.example.invalid/story',config=config),BRIEF)
            self.assertEqual(len(paths),4)
            self.assertTrue(all(path.startswith('http://') for path in paths))
            self.assertTrue(any('gdelt.example.invalid' in path for path in paths))
            self.assertTrue(any('article.example.invalid' in path for path in paths))
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_settings_migrate_persist_and_invalid_values_do_not_replace_them(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=Repository(directory)
            self.assertEqual(repo.snapshot()["config"]["news_proxy_mode"],"auto")
            repo.configure(**CUSTOM)
            saved=Repository(directory).snapshot()["config"]
            self.assertEqual(saved["news_proxy_url"],CUSTOM["news_proxy_url"])
            for invalid in ({"news_proxy_mode":"unknown"},{"news_proxy_url":""},
                    {"news_proxy_url":"socks5://localhost:7897"},{"news_proxy_url":"http://localhost:0"},
                    {"news_proxy_url":"http://user:secret@localhost:7897"},
                    {"news_proxy_url":"http://localhost:7897?secret=1"},{"news_proxy_url":"http://localhost:7897/path"}):
                with self.assertRaises(ValueError):
                    repo.configure(**invalid)
                self.assertEqual(repo.snapshot()["config"],saved)

    def test_explicit_proxy_or_direct_never_silently_falls_back(self):
        self.assertEqual(connection_routes(CUSTOM,"direct"),["custom"])
        self.assertEqual(connection_routes({"news_proxy_mode":"direct"},"system"),["direct"])
        self.assertEqual(connection_routes({},"custom"),["system","direct"])
        self.assertEqual(proxy_handler("custom",CUSTOM).proxies,{"http":CUSTOM["news_proxy_url"],"https":CUSTOM["news_proxy_url"]})
        self.assertEqual(proxy_handler("direct").proxies,{})
        with patch("pc.summarizer._article_text",side_effect=OSError()) as fetch:
            with self.assertRaises(ValueError):
                article_text("https://example.com",config=CUSTOM)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(fetch.call_args.args[1],"custom")

    def test_both_feeds_use_only_the_custom_proxy_on_failure(self):
        for cls in (HackerNews,GdeltDoc):
            api=cls()
            with patch.object(api,"_get",side_effect=OSError("unavailable")) as get:
                with self.assertRaises(RuntimeError):
                    api.fetch(config=CUSTOM)
                self.assertEqual(get.call_count,1)
                self.assertEqual(get.call_args.args[-1],"custom")
                self.assertEqual(api.network,CUSTOM)

    def test_feed_handlers_receive_proxy_for_http_and_https(self):
        response=Mock(); response.read.return_value=b'[]'
        opener=MagicMock(); opener.open.return_value.__enter__.return_value=response
        with patch("pc.core.build_opener",return_value=opener) as build:
            for api,request in ((HackerNews(),lambda api:api._get("newstories.json","custom")),
                                (GdeltDoc(),lambda api:api._get("test",20,"custom"))):
                api.network=CUSTOM
                request(api)
                self.assertEqual(build.call_args.args[0].proxies["https"],CUSTOM["news_proxy_url"])

    def test_related_search_and_article_enrichment_share_proxy(self):
        response=Mock(); response.read.return_value=b'<rss><channel/></rss>'
        opener=MagicMock(); opener.open.return_value.__enter__.return_value=response
        with patch("pc.summarizer.build_opener",return_value=opener) as build:
            self.assertEqual(related_reports(hn(1),config=CUSTOM),[])
            self.assertEqual(build.call_args.args[0].proxies["https"],CUSTOM["news_proxy_url"])
        response.read.return_value=json.dumps({"choices":[{"message":{"content":BRIEF}}]}).encode()
        keys=Mock(); keys.load.return_value="test-key"
        config={**CUSTOM,"llm_base_url":"https://api.example.com/v1","llm_model":"test"}
        with patch("pc.summarizer.article_text",return_value="") as article, \
                patch("pc.summarizer.related_reports",return_value=[]) as search, \
                patch("pc.summarizer.build_opener",return_value=opener) as build:
            Summarizer(keys).summarize(hn(1),config)
            self.assertEqual(article.call_args.kwargs["config"],config)
            self.assertEqual(search.call_args.kwargs["config"],config)
            self.assertFalse(any(hasattr(arg,"proxies") for arg in build.call_args.args))

    def test_proxy_change_retries_news_and_summaries_without_touching_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=Repository(directory); bridge=Bridge(repo,queue.Queue())
            repo.configure(screen_font_size=24); bridge.settings_changed()
            self.assertFalse(bridge.news_refresh.is_set())
            repo.configure(**CUSTOM); bridge.settings_changed()
            self.assertTrue(bridge.news_refresh.is_set())
            self.assertTrue(bridge.summary_wakeup.is_set())
