"""Monotonic refresh phases: HN at 0/600/1200, GDELT at 300/900/1500."""
import math


class NewsSchedule:
    def __init__(self, now, interval=600):
        self.interval = max(60, float(interval))
        self.reset(now)

    def reset(self, now):
        self.next_due = {"hn": now, "gdelt": now+self.interval/2}

    def due(self, now):
        source = min(self.next_due, key=self.next_due.get)
        return source if self.next_due[source] <= now else None

    def started(self, source, now):
        deadline = self.next_due[source]
        self.next_due[source] += self.interval * max(1, math.floor((now-deadline)/self.interval)+1)

    def delay(self, now):
        return max(0, min(self.next_due.values())-now)

    def defer(self, source, now, seconds):
        # Keep the original phase while skipping slots covered by Retry-After.
        while self.next_due[source] < now+seconds:
            self.next_due[source] += self.interval
