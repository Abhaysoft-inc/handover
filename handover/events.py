"""In-process pub/sub that fans agent activity out to every open dashboard (SSE)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from . import db


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _deliver(self, event: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def publish(self, event: dict[str, Any]) -> None:
        """Safe from the event loop and from FastAPI's worker threads."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            self._loop.call_soon_threadsafe(self._deliver, event)
        else:
            self._deliver(event)


bus = EventBus()


def notify(event_type: str, **data: Any) -> None:
    """Global (non-run) notification, e.g. data changed or a reminder fired."""
    bus.publish({"type": event_type, "ts": db.now_iso(), "data": data})


def changed(*scopes: str) -> None:
    notify("data.changed", scopes=list(scopes))


def sse_format(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
