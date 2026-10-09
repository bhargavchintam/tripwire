"""SSE fan-out (master §4 envelope): one bounded queue per subscriber, drop-oldest."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from tripwire.contracts import StreamEvent, StreamType


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class Bus:
    def __init__(self, maxsize: int = 1000) -> None:
        self.seq = 0
        self.maxsize = maxsize
        self.subscribers: set[asyncio.Queue[StreamEvent]] = set()
        self.dropped = 0

    def subscribe(self) -> asyncio.Queue[StreamEvent]:
        q: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=self.maxsize)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[StreamEvent]) -> None:
        self.subscribers.discard(q)

    def make(self, type_: StreamType, data: dict[str, Any], seq: int | None = None) -> StreamEvent:
        return StreamEvent(seq=self.seq if seq is None else seq, type=type_, ts_ms=now_ms(), data=data)

    def publish(self, type_: StreamType, data: dict[str, Any]) -> StreamEvent:
        self.seq += 1
        ev = StreamEvent(seq=self.seq, type=type_, ts_ms=now_ms(), data=data)
        for q in list(self.subscribers):
            if q.full():
                try:
                    q.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(ev)
        return ev
