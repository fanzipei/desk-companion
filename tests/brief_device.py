# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""BLE acceptance using owned fixtures, restoring all user notes/news afterwards."""
import asyncio
import json
from pathlib import Path
import time
import uuid

from tests.device_target import device_address, sdk_python_path
from bleak import BleakClient
from pc.protocol import Protocol
from tests.test_briefs import BRIEF


async def main():
    events, checks, owned_notes = [], [], []
    base = 8_000_000_000 + int(time.time()) % 100_000_000 * 100
    fixtures = [{"id": base+i, "title": f"自动化验证新闻 {i}", "summary": BRIEF,
                 "summary_basis": "article", "domain": "example.com", "time": i,
                 "source": "hn", "pinned": False} for i in range(50)]
    async with BleakClient(await device_address(), timeout=15, winrt={"use_cached_services": False}) as client:
        p = Protocol(client, events.append)
        await p.start()
        initial = await p.request({"op": "state"})
        assert initial["version"] == 2, "Expected new firmware"
        previous = await p.request({"op": "news_get"}, timeout=90)
        Path("backups/pre-brief-device-news.json").write_text(json.dumps(previous, ensure_ascii=False,indent=2), encoding="utf-8")
        original_pinned = set(initial["news_meta"]["pinned"])
        if len(original_pinned)>45:
            raise RuntimeError("Acceptance needs room for owned fixtures; existing pins were left unchanged")
        # Never replace a user's pinned article in an acceptance test.
        if original_pinned:
            fixtures = fixtures[:50-len(original_pinned)] + [n for n in previous["items"] if n["id"] in original_pinned]
        try:
            assert (await p.sync_news({"items": fixtures, "limit": 50, "updated": 100}))["ok"]
            state = await p.request({"op": "state"})
            assert state["news_count"] == 50 and state["news_limit"] == 50
            readback = await p.request({"op": "news_get"}, timeout=90)
            assert next(n for n in readback["items"] if n["id"] == base)["summary"] == BRIEF
            checks.append("50 full Chinese briefs transferred in bounded atomic chunks")
            token = uuid.uuid4().hex
            assert (await p.request({"op":"news_begin", "token":token, "count":2,"limit":50,"updated":200}))["ok"]
            assert (await p.request({"op":"news_chunk", "token":token,"offset":0,"items":[fixtures[0]]}))["ok"]
            incomplete = await p.request({"op":"news_commit","token":token})
            assert not incomplete["ok"] and (await p.request({"op":"state"}))["news_count"] == 50
            checks.append("incomplete transaction does not replace displayed news")
            # Use three owned articles for direction, pin, delete and open checks.
            own = fixtures[:3]
            packet = {"items": own + [n for n in previous["items"] if n["id"] in original_pinned],
                      "limit":50,"updated":300}
            assert (await p.sync_news(packet))["ok"]
            assert (await p.request({"op":"mode","mode":1}))["ok"]
            # Locate the owned article even if original pinned items sort ahead of it.
            for _ in range(50):
                if (await p.request({"op":"state"})).get("current_news_id") == base:
                    break
                await p.request({"op":"navigate","action":"next"})
            await p.request({"op":"navigate","action":"next"})
            assert (await p.request({"op":"state"}))["current_news_id"] == base+1
            await p.request({"op":"navigate","action":"previous"})
            assert (await p.request({"op":"state"}))["current_news_id"] == base
            await p.request({"op":"navigate","action":"open"})
            assert (await p.request({"op":"state"}))["news_count"] == len(packet["items"])
            await p.request({"op":"navigate","action":"pin"})
            assert base in (await p.request({"op":"state"}))["news_meta"]["pinned"]
            await p.request({"op":"navigate","action":"next"})
            # If there were original pins, find base+1 without changing them.
            for _ in range(50):
                if (await p.request({"op":"state"})).get("current_news_id") == base+1:
                    break
                await p.request({"op":"navigate","action":"next"})
            await p.request({"op":"navigate","action":"delete"})
            assert base+1 in (await p.request({"op":"state"}))["news_meta"]["deleted"]
            assert (await p.sync_news(packet))["ok"]
            state = await p.request({"op":"state"})
            assert base+1 not in state["news_meta"]["ids"]
            assert base in state["news_meta"]["pinned"]
            checks.append("news previous/next, open without deletion, pin retention, no resurrection after delete")
            if len(initial["notes"])+2 <= initial["note_limit"]:
                for i in range(2):
                    identifier = "brief-"+uuid.uuid4().hex[:29]
                    owned_notes.append(identifier)
                    assert (await p.request({"op":"add","id":identifier,"text":f"Brief button test {i}","created":int(time.time())}))["ok"]
                await p.request({"op":"mode","mode":0})
                state = await p.request({"op":"state"})
                index = state["current_note_index"]
                expected = state["notes"][(index+1)%len(state["notes"])]["id"]
                await p.request({"op":"navigate","action":"next"})
                assert (await p.request({"op":"state"}))["current_note_id"] == expected
                await p.request({"op":"navigate","action":"previous"})
                assert (await p.request({"op":"state"}))["current_note_id"] == owned_notes[-1]
                await p.request({"op":"navigate","action":"open"})
                checks.append("note next/previous and device-to-PC preview event")
            await asyncio.sleep(0.2)
            assert any(e.get("event")=="open_news" and e.get("id")==base for e in events)
            if owned_notes:
                assert any(e.get("event")=="open_note" and e.get("id")==owned_notes[-1] for e in events)
        finally:
            # Unpin only owned fixture articles before restoring the original list.
            await p.request({"op":"mode","mode":1})
            for _ in range(55):
                state = await p.request({"op":"state"})
                current = state.get("current_news_id")
                if current == base:
                    if base in state["news_meta"]["pinned"]:
                        await p.request({"op":"navigate","action":"pin"})
                    break
                await p.request({"op":"navigate","action":"next"})
            restored = await p.sync_news({"items": previous["items"], "limit":previous["limit"],"updated":previous["updated"]})
            assert restored["ok"], restored
            for identifier in owned_notes:
                await p.request({"op":"delete","id":identifier})
            await p.request({"op":"mode","mode":initial["mode"]})
        after = await p.request({"op":"state"})
        final_news = await p.request({"op":"news_get"}, timeout=90)
        assert after["notes"] == initial["notes"], "User notes changed"
        assert final_news["items"] == previous["items"], "User news changed"
        assert final_news["limit"] == previous["limit"]
        checks.append("all owned fixtures removed; original notes/news/limit restored")
    report = {"passed":True,"checks":checks,"timestamp":time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path("logs/brief-device-test.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    asyncio.run(main())
