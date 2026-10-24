"""Approximate four-hour clock sync, retried on connection and after failures."""
import time

INTERVAL=4*60*60


class ClockSync:
    def __init__(self):
        self.next_due=0

    async def update(self, protocol, monotonic=None, unix=None):
        monotonic=time.monotonic() if monotonic is None else monotonic
        if monotonic<self.next_due:
            return None
        unix=time.time() if unix is None else unix
        result=await protocol.request({"op":"time_sync","unix":int(unix)})
        self.next_due=monotonic+(INTERVAL if result.get("ok") else 60)
        return bool(result.get("ok"))
