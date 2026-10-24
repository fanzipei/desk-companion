from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import threading
import time
import unicodedata
from urllib.parse import urlsplit, urlencode, parse_qsl
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError
import uuid
from .news_network import connection_routes,proxy_handler,validate_proxy

MAX_NOTES = 20
MAX_NEWS = 50
MAX_NOTE_BYTES = 3072
NEWS_INTERVAL = 600
GDELT_ID_BASE = 1 << 48
DEFAULT_CONFIG = {
    "device_address": "",
    "capture_hotkey": "Ctrl+Alt+J",
    "compose_hotkey": "Ctrl+Alt+Space",
    "news_limit": 20,
    "news_interval": NEWS_INTERVAL,
    "gdelt_query": "sourcelang:english",
    "news_proxy_mode": "auto",
    "news_proxy_url": "",
    "note_limit": MAX_NOTES,
    "llm_enabled": False,
    "llm_base_url": "",
    "llm_model": "",
    "screen_theme": "light",
    "screen_show_hints": True,
    "screen_font_size": 16,
    "screen_line_spacing": 7,
    "screenshot_hotkey": "Ctrl+Alt+K",
    "reminder_hotkey": "Ctrl+Alt+L",
    "screenshot_enabled": False,
}


def clip_utf8(text, max_bytes):
    return str(text).encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def safe_web_url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ("http", "https") and parsed.hostname and not parsed.username:
            return value
    except ValueError:
        pass
    return None


def normalize_story(item):
    if not isinstance(item, dict) or item.get("type") != "story" or item.get("deleted") or item.get("dead"):
        return None
    if not isinstance(item.get("id"), int) or item["id"] <= 0 or not isinstance(item.get("title"), str):
        return None
    title = html.unescape(item["title"]).strip()
    if not title:
        return None
    fallback = f"https://news.ycombinator.com/item?id={item['id']}"
    url = safe_web_url(item.get("url")) or fallback
    return {"id": item["id"], "source": "hn", "title": clip_utf8(title, 480), "url": url,
            "domain": clip_utf8(urlsplit(url).hostname or "news.ycombinator.com", 96),
            "time": int(item.get("time") or 0), "score": int(item.get("score") or 0),
            "comments": int(item.get("descendants") or 0)}


def source_of(story):
    return "gdelt" if story.get("source") == "gdelt" or story.get("id", 0) >= GDELT_ID_BASE else "hn"


def article_key(url):
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid", "mc_cid", "mc_eid")]
    return (parsed.netloc.lower().removeprefix("www."), parsed.path.rstrip("/"), tuple(sorted(query)))


def article_digest(url):
    return hashlib.sha256(json.dumps(article_key(url), ensure_ascii=False).encode()).hexdigest()


def normalize_gdelt(item):
    if not isinstance(item, dict) or not isinstance(item.get("title"), str):
        return None
    url = safe_web_url(item.get("url"))
    title = " ".join(html.unescape(item["title"]).split())
    if not url or not title:
        return None
    stable_key = json.dumps(article_key(url), ensure_ascii=False, separators=(",", ":"))
    # Below 2**53: cJSON's double representation preserves these IDs exactly.
    identifier = GDELT_ID_BASE + int.from_bytes(hashlib.sha256(stable_key.encode()).digest()[:6], "big")
    try:
        timestamp = int(datetime.strptime(item.get("seendate", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError):
        timestamp = 0
    return {"id": identifier, "source": "gdelt", "title": clip_utf8(title, 480), "url": url,
            "domain": clip_utf8(urlsplit(url).hostname or "", 96), "time": timestamp,
            "score": 0, "comments": 0}


def merge_news(feeds, limit=20):
    """Interleave newest stories from each feed so neither crowds out the other."""
    queues = {source: iter(sorted(feeds.get(source, []), key=lambda x: x.get("time", 0), reverse=True))
              for source in ("hn", "gdelt")}
    result, seen = [], set()
    while queues and len(result) < limit:
        for source in list(queues):
            for story in queues[source]:
                key = article_key(story["url"])
                if key not in seen:
                    seen.add(key)
                    result.append({**story, "source": source})
                    break
            else:
                del queues[source]
            if len(result) >= limit:
                break
    return result


class Repository:
    """Device owns confirmed notes; durable PC outbox holds unacknowledged operations."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.lock = threading.RLock()
        self.data = {"notes": [], "outbox": [], "news": [], "news_history": [], "news_feeds": {},
                     "news_deleted": [], "news_deleted_urls": [], "news_pins": {},
                     "news_source_updated": {}, "news_revision": 0, "news_updated": 0, "mode": 0,
                     "config": DEFAULT_CONFIG.copy()}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not all(isinstance(saved.get(k, []), list) for k in ("notes", "news", "outbox")):
                raise ValueError("本地数据文件格式有误，请保留 data/state.json 并检查备份。")
            self.data.update(saved)
            self.data["config"] = {**DEFAULT_CONFIG, **saved.get("config", {})}
        # Migrate the existing HN cache without changing confirmed notes or the outbox.
        for source in ("hn", "gdelt"):
            self.data["news_feeds"].setdefault(source, [{**item, "source": source} for item in self.data["news"] if source_of(item) == source])
            self.data["news_source_updated"].setdefault(source, self.data["news_updated"] if self.data["news_feeds"][source] else 0)

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.path)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def configure(self, **settings):
        with self.lock:
            from .display import display_packet
            display_packet({**self.data["config"], **settings})
            validate_proxy({**self.data["config"], **settings})
            limit = settings.get("note_limit", self.data["config"]["note_limit"])
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_NOTES:
                raise ValueError("便签上限应为 1–20 的整数。")
            minimum = max(len(self.data["notes"]), sum(op["op"] == "add" for op in self.data["outbox"]))
            if limit < minimum:
                raise ValueError(f"已有 {minimum} 条便签或待发送内容，请先删除便签，再降低上限。")
            news_limit = settings.get("news_limit", self.data["config"]["news_limit"])
            if isinstance(news_limit, bool) or not isinstance(news_limit, int) or not 1 <= news_limit <= MAX_NEWS:
                raise ValueError(f"新闻上限应为 1–{MAX_NEWS} 的整数。")
            if news_limit < len(self.data["news_pins"]):
                raise ValueError("新闻上限不能少于已置顶的新闻数，请先取消部分置顶。")
            publishing_changed = any(key in settings and settings[key] != self.data["config"][key]
                                     for key in ("news_limit", "llm_enabled", "llm_base_url", "llm_model"))
            self.data["config"].update(settings)
            self._rebuild_news()
            if publishing_changed:
                self.data["news_revision"] += 1
            self._save()

    def enqueue_note(self, text):
        text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").strip()
        if not text:
            raise ValueError("请输入或选中一段文字。")
        if len(text.encode("utf-8")) > MAX_NOTE_BYTES:
            raise ValueError(f"这段文字超过 {MAX_NOTE_BYTES} UTF-8 字节，请分成几条发送（约 1024 个汉字）。")
        with self.lock:
            limit = int(self.data["config"]["note_limit"])
            if sum(op["op"] == "add" for op in self.data["outbox"]) >= limit:
                raise ValueError(f"已有 {limit} 条待发送便签，请先连接设备完成同步。")
            notes = self.data["notes"]
            if len(notes) >= limit and all(n.get("pinned") for n in notes):
                raise ValueError(f"{limit} 条便签均已置顶，请先删除一条或取消置顶。")
            note = {"id": str(uuid.uuid4()), "text": text, "created": int(time.time()), "pinned": False}
            self.data["outbox"].append({"qid": str(uuid.uuid4()), "op": "add", **note})
            self._save()
            return note

    def enqueue_mutation(self, operation, note_id, **fields):
        if operation not in ("delete", "pin"):
            raise ValueError("Invalid note operation")
        with self.lock:
            self.data["outbox"].append({"qid": str(uuid.uuid4()), "op": operation,
                                        "id": note_id, **fields})
            self._save()

    def next_operation(self):
        with self.lock:
            return copy.deepcopy(self.data["outbox"][0]) if self.data["outbox"] else None

    def acknowledge(self, qid):
        with self.lock:
            delivered=next((op for op in self.data["outbox"] if op["qid"]==qid),None)
            if delivered and delivered["op"]=="add":
                self.data["last_sent_note"]={key:delivered[key] for key in ("id","text","created","pinned")}
            self.data["outbox"] = [op for op in self.data["outbox"] if op["qid"] != qid]
            self._save()

    def last_sent_note(self):
        with self.lock:
            note=self.data.get("last_sent_note")
            if note is None and self.data["notes"]:
                note=max(self.data["notes"],key=lambda n:n.get("created",0))
            return copy.deepcopy(note)

    def apply_state(self, response):
        notes = response.get("notes")
        if not isinstance(notes, list) or len(notes) > MAX_NOTES:
            raise ValueError("设备返回了无效的便签列表。")
        for note in notes:
            if not isinstance(note, dict) or not isinstance(note.get("id"), str) or not isinstance(note.get("text"), str):
                raise ValueError("设备便签格式无效。")
        with self.lock:
            self.data["notes"] = notes
            self.data["mode"] = int(bool(response.get("mode")))
            if isinstance(response.get("news_meta"), dict):
                self._apply_news_meta(response["news_meta"])
            self._save()

    def _apply_news_meta(self, metadata):
        deleted = set(self.data["news_deleted"]) | set(metadata.get("deleted", []))
        self.data["news_deleted"] = sorted(deleted)
        # The hardware is authoritative for pin/unpin changes made while offline.
        visible = set(metadata.get("ids", []))
        pinned = set(metadata.get("pinned", []))
        for identifier in visible:
            key = str(identifier)
            if identifier in pinned:
                self.data["news_pins"].setdefault(key, time.time_ns())
            else:
                self.data["news_pins"].pop(key, None)
        for identifier in deleted:
            self.data["news_pins"].pop(str(identifier), None)
        self._remember_deleted_urls(deleted)
        self._rebuild_news()

    def _remember_deleted_urls(self, identifiers):
        urls = set(self.data["news_deleted_urls"])
        for item in self.data["news"] + self.data["news_history"] + [item for feed in self.data["news_feeds"].values() for item in feed]:
            if item["id"] in identifiers:
                urls.add(article_digest(item["url"]))
        self.data["news_deleted_urls"] = sorted(urls)

    def apply_event(self, event):
        with self.lock:
            kind = event.get("event")
            if kind == "note_deleted":
                self.data["notes"] = [n for n in self.data["notes"] if n["id"] != event.get("id")]
            elif kind == "note_pinned":
                for note in self.data["notes"]:
                    if note["id"] == event.get("id"):
                        note["pinned"] = bool(event.get("pinned"))
            elif kind == "news_deleted":
                identifier = int(event["id"])
                self.data["news_deleted"] = sorted(set(self.data["news_deleted"]) | {identifier})
                self._remember_deleted_urls({identifier})
                self.data["news_pins"].pop(str(identifier), None)
                self._rebuild_news()
            elif kind == "news_pinned":
                key = str(int(event["id"]))
                if event.get("pinned"):
                    self.data["news_pins"][key] = time.time_ns()
                else:
                    self.data["news_pins"].pop(key, None)
                self._rebuild_news()
            if "mode" in event:
                self.data["mode"] = int(bool(event["mode"]))
            self._save()

    def note(self, identifier):
        with self.lock:
            return next((copy.deepcopy(note) for note in self.data["notes"] if note["id"] == identifier), None)

    def _rebuild_news(self):
        deleted = set(self.data["news_deleted"])
        pins = self.data["news_pins"]
        known = {item["id"]: item for item in self.data["news_history"] + self.data["news"]}
        for stories in self.data["news_feeds"].values():
            for story in stories:
                known[story["id"]] = {**known.get(story["id"], {}), **story}
        # A URL deleted through either provider stays deleted through the other.
        deleted_urls = set(self.data["news_deleted_urls"]) | {article_digest(known[i]["url"]) for i in deleted if i in known}
        available = {i: item for i, item in known.items()
                     if i not in deleted and article_digest(item["url"]) not in deleted_urls}
        pinned = sorted((item for i, item in available.items() if str(i) in pins),
                        key=lambda item: pins[str(item["id"])], reverse=True)
        feeds = {source: [item for item in available.values() if source_of(item) == source
                          and str(item["id"]) not in pins] for source in ("hn", "gdelt")}
        limit = int(self.data["config"]["news_limit"])
        pinned_urls = {article_key(item["url"]) for item in pinned}
        feeds = {source: [item for item in feed if article_key(item["url"]) not in pinned_urls] for source, feed in feeds.items()}
        items = pinned + merge_news(feeds, limit)
        items = [{**item, "pinned": str(item["id"]) in pins} for item in items[:limit]]
        if items != self.data["news"]:
            self.data["news"] = items
            self.data["news_revision"] += 1

    def adopt_device_news(self, items):
        """Keep content for an offline-pinned story even after the feed ages out."""
        with self.lock:
            known = {item["id"]: item for item in self.data["news_history"] + self.data["news"]}
            for item in items:
                if item["id"] in known:
                    for field in ("summary", "summary_basis"):
                        if item.get(field) and not known[item["id"]].get(field):
                            known[item["id"]][field] = item[field]
            self.data["news_history"] = list(known.values())[-500:]
            self._rebuild_news()
            self._save()

    def pending_summaries(self, signature):
        with self.lock:
            return [copy.deepcopy(item) for item in self.data["news"]
                    if item.get("summary_signature") != signature or not item.get("summary")]

    def save_summary(self, identifier, text, basis, signature):
        with self.lock:
            for collection in [self.data["news"], self.data["news_history"], *self.data["news_feeds"].values()]:
                for item in collection:
                    if item["id"] == identifier:
                        item.update(summary=text, summary_basis=basis, summary_signature=signature)
            self.data["news_revision"] += 1
            self._save()

    def update_news(self, stories, updated):
        self.update_news_source("hn", stories, updated)

    def update_news_source(self, source, stories, updated):
        if source not in ("hn", "gdelt"):
            raise ValueError("Unknown news source")
        with self.lock:
            stories = [{**item, "source": source} for item in stories]
            history = {item["id"]: item for item in self.data.get("news_history", []) + self.data["news"]}
            history.update({item["id"]: item for item in stories})
            # Preserve successful briefs when a feed repeats the same article.
            for item in stories:
                old = next((n for n in self.data["news"] if n["id"] == item["id"]), {})
                for field in ("summary", "summary_basis", "summary_signature"):
                    if field in old:
                        item[field] = old[field]
            self.data["news_history"] = list(history.values())[-500:]
            self.data["news_feeds"][source] = stories
            self.data["news_source_updated"][source] = int(updated)
            self._rebuild_news()
            self.data["news_updated"] = int(updated)
            # Two updates in the same second must still trigger separate BLE syncs.
            self.data["news_revision"] += 1
            self._save()

    def news_url(self, story_id):
        with self.lock:
            for story in self.data["news"] + self.data.get("news_history", []):
                if story["id"] == story_id:
                    return safe_web_url(story["url"])
        return None

    def news_packet(self):
        with self.lock:
            config = self.data["config"]
            items = self.data["news"]
            if config["llm_enabled"]:
                from .summarizer import config_signature
                signature = config_signature(config)
                items = [item for item in items if item.get("summary") and item.get("summary_signature") == signature]
            return {"op": "news", "updated": self.data["news_updated"], "revision": self.data["news_revision"],
                    "limit": config["news_limit"],
                    "items": [{k: story[k] for k in ("id", "source", "title", "domain", "time", "score", "comments",
                                                     "summary", "summary_basis", "pinned") if k in story} for story in items]}


class HackerNews:
    BASE = "https://hacker-news.firebaseio.com/v0/"

    def __init__(self):
        self.mode = None
        self.network = {}

    def _get(self, path, mode):
        opener = build_opener(proxy_handler(mode,self.network))
        request = Request(self.BASE + path, headers={"User-Agent": "DeskCompanion/1.0"})
        with opener.open(request, timeout=10) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("HN response is too large")
        return json.loads(raw)

    def fetch(self, limit=20, config=None):
        self.network=config or {}
        errors = []
        modes = connection_routes(self.network,self.mode)
        for mode in modes:
            try:
                identifiers = self._get("newstories.json", mode)
                if not isinstance(identifiers, list):
                    raise ValueError("HN list format is invalid")
                self.mode = mode
                break
            except Exception as exc:
                errors.append(str(exc))
        else:
            self.mode = None
            raise RuntimeError("Hacker News 暂时不可达，继续保留缓存。" + " / ".join(errors))
        identifiers = [i for i in identifiers[:limit*2] if isinstance(i, int) and i > 0]

        def read_story(identifier):
            try:
                return normalize_story(self._get(f"item/{identifier}.json", self.mode))
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            stories = [item for item in pool.map(read_story, identifiers) if item][:limit]
        if not stories:
            raise RuntimeError("未获得有效新闻，继续保留上次缓存。")
        return stories


class NewsRateLimited(RuntimeError):
    def __init__(self, retry_after=600):
        super().__init__("GDELT 请求受限（HTTP 429），保留缓存并在稍后自动重试。")
        self.retry_after = retry_after


class GdeltDoc:
    BASE = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self):
        self.mode = None
        self.network = {}

    def _get(self, query, limit, mode):
        params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": min(250, max(1, limit)),
                  "timespan": "1d", "sort": "datedesc"}
        opener = build_opener(proxy_handler(mode,self.network))
        request = Request(self.BASE+"?"+urlencode(params), headers={"User-Agent": "DeskCompanion/1.1"})
        with opener.open(request, timeout=20) as response:
            raw = response.read(4*1024*1024+1)
        if len(raw) > 4*1024*1024:
            raise ValueError("GDELT response is too large")
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("GDELT 未返回有效 JSON，请检查查询条件或稍后重试。") from exc

    def fetch(self, limit=20, query=DEFAULT_CONFIG["gdelt_query"], config=None):
        self.network=config or {}
        errors = []
        modes = connection_routes(self.network,self.mode)
        for mode in modes:
            try:
                value = self._get(query, limit, mode)
                if not isinstance(value, dict) or not isinstance(value.get("articles"), list):
                    raise ValueError("GDELT 文章列表格式无效。")
                self.mode = mode
                articles, seen = [], set()
                for item in value["articles"]:
                    story = normalize_gdelt(item)
                    if story and story["id"] not in seen:
                        seen.add(story["id"])
                        articles.append(story)
                return sorted(articles, key=lambda item: item["time"], reverse=True)[:limit]
            except HTTPError as exc:
                if exc.code == 429:
                    try:
                        delay = max(600, int(exc.headers.get("Retry-After", "600")))
                    except (ValueError, AttributeError):
                        delay = 600
                    exc.close()
                    raise NewsRateLimited(delay) from exc
                errors.append(str(exc))
                exc.close()
            except Exception as exc:
                errors.append(str(exc))
        self.mode = None
        raise RuntimeError("GDELT 暂时不可达，继续保留缓存。"+" / ".join(errors))
