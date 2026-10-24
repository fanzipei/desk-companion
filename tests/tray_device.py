# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
"""Non-destructive acceptance for the configurable device note limit."""
import asyncio
import json
from pathlib import Path
import time
import uuid

from tests.device_target import device_address, sdk_python_path
from bleak import BleakClient
from pc.protocol import Protocol


async def main():
    checks, owned = [], []
    async with BleakClient(await device_address(), timeout=15,
                           winrt={"use_cached_services": False}) as client:
        protocol = Protocol(client, lambda event: None)
        await protocol.start()
        initial = await protocol.request({"op": "state"})
        assert initial["ok"], initial
        old_limit = initial["note_limit"]
        notes = initial["notes"]
        try:
            invalid = await protocol.request({"op": "config", "note_limit": 21})
            assert not invalid["ok"] and invalid["error"] == "invalid_limit", invalid
            checks.append("reject out-of-range limit")
            if not notes:
                assert (await protocol.request({"op": "config", "note_limit": 2}))["ok"]
                for i in range(2):
                    identifier = "tray-" + uuid.uuid4().hex[:30]
                    owned.append(identifier)
                    assert (await protocol.request({"op": "add", "id": identifier,
                        "text": f"Tray acceptance {i+1}", "created": int(time.time())}))["ok"]
                    assert (await protocol.request({"op": "pin", "id": identifier, "pinned": True}))["ok"]
                identifier = "tray-" + uuid.uuid4().hex[:30]
                owned.append(identifier)
                blocked = await protocol.request({"op": "add", "id": identifier, "text": "Tray replacement"})
                assert not blocked["ok"] and blocked["error"] == "all_pinned", blocked
                checks.append("custom limit of two preserves pinned notes")
                below = await protocol.request({"op": "config", "note_limit": 1})
                assert not below["ok"] and below["error"] == "limit_below_count", below
                assert (await protocol.request({"op": "pin", "id": owned[0], "pinned": False}))["ok"]
                replacement = await protocol.request({"op": "add", "id": identifier, "text": "Tray replacement"})
                assert replacement["ok"] and replacement["evicted"] == owned[0], replacement
                state = await protocol.request({"op": "state"})
                assert state["note_limit"] == 2 and len(state["notes"]) == 2
                checks.append("lowering below count is rejected; oldest unpinned note is replaced")
            else:
                target = max(len(notes), 2)
                assert (await protocol.request({"op": "config", "note_limit": target}))["ok"]
                assert (await protocol.request({"op": "state"}))["note_limit"] == target
                checks.append("custom limit applied without changing existing notes")
        finally:
            for identifier in owned:
                await protocol.request({"op": "delete", "id": identifier})
            assert (await protocol.request({"op": "config", "note_limit": old_limit}))["ok"]
            await protocol.request({"op": "mode", "mode": initial.get("mode", 0)})
        final = await protocol.request({"op": "state"})
        assert final["notes"] == notes, "Original notes changed"
        assert final["note_limit"] == old_limit
        checks.append("original notes, mode and limit restored")
    result = {"passed": True, "checks": checks, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    Path("logs/tray-device-test.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
