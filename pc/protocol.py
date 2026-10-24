import asyncio
import json
import uuid

SERVICE = "9b7e0001-3c1a-4c25-bad1-6d1a9f3e6200"
RX = "9b7e0002-3c1a-4c25-bad1-6d1a9f3e6200"
TX = "9b7e0003-3c1a-4c25-bad1-6d1a9f3e6200"


class FrameDecoder:
    def __init__(self, maximum=200_000):
        self.buffer = bytearray()
        self.maximum = maximum

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > self.maximum:
            self.buffer.clear()
            raise ValueError("BLE frame exceeds the limit")
        messages = []
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            if line:
                value = json.loads(line.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("BLE message must be an object")
                messages.append(value)
        return messages


class Protocol:
    def __init__(self, client, on_event):
        self.client = client
        self.on_event = on_event
        self.decoder = FrameDecoder()
        self.pending = {}
        self.lock = asyncio.Lock()
        self.loop = asyncio.get_running_loop()

    async def start(self):
        await self.client.start_notify(TX, self.notification)

    def notification(self, sender, data):
        try:
            for value in self.decoder.feed(data):
                rid = value.get("rid")
                future = self.pending.get(rid)
                if future is not None and not future.done():
                    future.set_result(value)
                elif "event" in value:
                    self.on_event(value)
        except Exception as exc:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(exc)

    async def request(self, operation, timeout=30):
        async with self.lock:
            rid = uuid.uuid4().hex
            payload = (json.dumps({**operation, "rid": rid}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(payload) > 16384:
                raise ValueError("请求过长，请缩短文字后重试。")
            future = self.loop.create_future()
            self.pending[rid] = future
            try:
                # Works even when Windows negotiates the minimum 23-byte ATT MTU.
                mtu = getattr(self.client, "mtu_size", 23)
                chunk_size = min(180, max(20, mtu-3))
                for offset in range(0, len(payload), chunk_size):
                    await self.client.write_gatt_char(RX, payload[offset:offset+chunk_size], response=True)
                return await asyncio.wait_for(future, timeout)
            finally:
                self.pending.pop(rid, None)

    async def sync_news(self, packet):
        """Stage bounded frames and publish only after the complete list arrives."""
        token = uuid.uuid4().hex
        items = packet["items"]
        reply = await self.request({"op": "news_begin", "token": token, "count": len(items),
                                    "limit": packet.get("limit", 20), "updated": packet["updated"]})
        if not reply.get("ok"):
            return reply
        for offset in range(0, len(items), 5):
            reply = await self.request({"op": "news_chunk", "token": token,
                                        "offset": offset, "items": items[offset:offset+5]})
            if not reply.get("ok"):
                return reply
        return await self.request({"op": "news_commit", "token": token})

    async def sync_reminders(self, items):
        token=uuid.uuid4().hex
        reply=await self.request({"op":"schedule_begin","token":token,"count":len(items)})
        if not reply.get("ok"):
            return reply
        for offset in range(0,len(items),5):
            reply=await self.request({"op":"schedule_chunk","token":token,"offset":offset,"items":items[offset:offset+5]})
            if not reply.get("ok"):
                return reply
        return await self.request({"op":"schedule_commit","token":token})
