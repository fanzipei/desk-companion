"""Explicit first-install hardware acceptance. Refuses to overwrite existing notes."""
import asyncio
import json
from pathlib import Path
import time
import uuid

from tests.device_target import device_address, sdk_python_path
from bleak import BleakClient
from pc.protocol import Protocol


async def main():
    events, checks, owned = [], [], []
    async with BleakClient(await device_address(), timeout=15, winrt={"use_cached_services": False}) as client:
        protocol = Protocol(client, events.append)
        await protocol.start()
        initial = await protocol.request({"op": "state"})
        if initial.get("notes"):
            raise RuntimeError("Hardware capacity test requires an empty first-install note store.")
        try:
            for i in range(20):
                identifier = "test-" + uuid.uuid4().hex[:30]
                owned.append(identifier)
                text = "长便签边界测试。" * 100 if i == 0 else f"SELFTEST {i:02d} 中英文 BLE"
                result = await protocol.request({"op": "add", "id": identifier, "text": text, "created": i})
                assert result["ok"], result
            state = await protocol.request({"op": "state"})
            assert len(state["notes"]) == 20
            assert state["notes"][-1]["text"] == "长便签边界测试。" * 100
            checks.append("20 notes and fragmented long UTF-8 payload")
            for identifier in owned:
                result = await protocol.request({"op": "pin", "id": identifier, "pinned": True})
                assert result["ok"], result
            extra = "test-" + uuid.uuid4().hex[:30]
            result = await protocol.request({"op": "add", "id": extra, "text": "capacity", "created": 21})
            assert not result["ok"] and result["error"] == "all_pinned", result
            checks.append("all-pinned capacity preserves all 20 notes")
            assert (await protocol.request({"op": "pin", "id": owned[0], "pinned": False}))["ok"]
            result = await protocol.request({"op": "add", "id": extra, "text": "replacement", "created": 22})
            owned.append(extra)
            assert result["ok"] and result["evicted"] == owned[0], result
            checks.append("oldest unpinned note is evicted")
            result = await protocol.request({"op": "add", "id": owned[0], "text": "must not resurrect", "created": 0})
            assert result["ok"] and result["duplicate"], result
            state = await protocol.request({"op": "state"})
            assert len(state["notes"]) == 20 and owned[0] not in [n["id"] for n in state["notes"]]
            checks.append("retry after removal does not resurrect a note")
            result = await protocol.request({"op": "add", "id": "oversized", "text": "字"*1025})
            assert not result["ok"] and result["error"] == "invalid_note"
            checks.append("oversized note is rejected without truncation")
            assert (await protocol.request({"op": "news", "updated": int(time.time()), "items": [
                {"id": 8863, "title": "Hacker News API / 新闻连接测试", "score": 10,
                 "comments": 2, "domain": "news.ycombinator.com", "time": 1175714200}]}))["ok"]
            assert (await protocol.request({"op": "mode", "mode": 1}))["ok"]
            assert (await protocol.request({"op": "navigate", "action": "open"}))["ok"]
            await asyncio.sleep(0.3)
            assert any(e.get("event") == "open_news" and e.get("id") == 8863 for e in events), events
            checks.append("news sync and device-to-PC open event")
        finally:
            for identifier in owned:
                await protocol.request({"op": "delete", "id": identifier})
            await protocol.request({"op": "mode", "mode": 0})
        welcome_id = "welcome-" + uuid.uuid4().hex[:28]
        result = await protocol.request({"op": "add", "id": welcome_id, "created": int(time.time()),
            "text": "你好，我是桌面小伴侣。\n\n双击 KEY：便签 / 新闻\n短按 BOOT / KEY：翻页\n长按 BOOT：删除便签\n长按 KEY：固定或取消固定\n\nPC 选中文字后按 Ctrl+Alt+J，或用 Ctrl+Alt+空格 输入。"})
        assert result["ok"], result
        checks.append("temporary test notes cleaned; welcome note saved")
    output = {"passed": True, "checks": checks, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path("logs/companion-device-tests.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
