"""Non-destructive mixed-source BLE acceptance; restores the previous news cache."""
import asyncio
import json
from pathlib import Path
import time

from tests.device_target import device_address, sdk_python_path
from bleak import BleakClient
from pc.core import normalize_gdelt
from pc.protocol import Protocol


async def main():
    previous = json.loads(Path("backups/pre-gdelt-pc-state.json").read_text(encoding="utf-8"))
    received = []
    gdelt = normalize_gdelt({"title": "GDELT 来源标记验证", "url": "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
                             "seendate": "20261023T160000Z"})
    hn = {"id": 8863, "title": "HN 来源标记验证", "source": "hn", "domain": "news.ycombinator.com", "time": 1, "score": 10, "comments": 2}
    async with BleakClient(await device_address(), timeout=15, winrt={"use_cached_services": False}) as client:
        protocol = Protocol(client, received.append)
        await protocol.start()
        before = await protocol.request({"op": "state"})
        assert "news_source_counts" in before, "New source-label firmware is not active"
        try:
            result = await protocol.request({"op": "news", "updated": int(time.time()),
                "items": [hn, {k: v for k, v in gdelt.items() if k != "url"}]})
            assert result["ok"], result
            assert (await protocol.request({"op": "mode", "mode": 1}))["ok"]
            state = await protocol.request({"op": "state"})
            assert state["news_source_counts"] == {"hn": 1, "gdelt": 1}, state
            assert state["current_news_source"] == "hn"
            assert (await protocol.request({"op": "navigate", "action": "open"}))["ok"]
            assert (await protocol.request({"op": "navigate", "action": "next"}))["ok"]
            state = await protocol.request({"op": "state"})
            assert state["current_news_source"] == "gdelt"
            assert state["current_news_id"] == gdelt["id"], "The large GDELT ID lost precision"
            assert (await protocol.request({"op": "navigate", "action": "open"}))["ok"]
            await asyncio.sleep(0.2)
            ids = [event.get("id") for event in received if event.get("event") == "open_news"]
            assert ids == [hn["id"], gdelt["id"]], ids
        finally:
            restored = await protocol.request({"op": "news", "updated": previous["news_updated"],
                "items": [{k: v for k, v in item.items() if k != "url"} for item in previous["news"]]})
            assert restored["ok"], restored
            await protocol.request({"op": "mode", "mode": before["mode"]})
        after = await protocol.request({"op": "state"})
        assert after["notes"] == before["notes"], "Notes changed during source testing"
    result = {"passed": True, "notes_preserved": True, "note_count": len(after["notes"]),
              "source_counts_verified": {"hn": 1, "gdelt": 1}, "gdelt_id_exact": gdelt["id"],
              "both_open_events_received": True, "previous_news_restored": True,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path("logs/gdelt-device-test.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
