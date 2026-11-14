from __future__ import annotations

import asyncio
import json
from typing import Any

from .db import SessionLocal, Event


class EventBus:
    """In-process pub/sub so the dashboard WebSocket gets live updates,
    and every event is also persisted for the panels / history."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def emit(
        self,
        task_id: int,
        kind: str,
        title: str = "",
        body: Any = "",
        persist: bool = True,
    ) -> dict:
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False, default=str)

        row_id = None
        if persist:
            async with SessionLocal() as s:
                ev = Event(task_id=task_id, kind=kind, title=title[:300], body=body)
                s.add(ev)
                await s.commit()
                await s.refresh(ev)
                row_id = ev.id
                created = ev.created_at.isoformat()
        else:
            created = None

        payload = {
            "id": row_id,
            "task_id": task_id,
            "kind": kind,
            "title": title,
            "body": body,
            "created_at": created,
        }
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)
        return payload


bus = EventBus()
