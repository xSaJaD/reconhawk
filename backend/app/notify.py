from __future__ import annotations

import httpx

from .db import SessionLocal, Notification
from .events import bus
from . import settings_store


async def push(level: str, title: str, body: str = "", task_id: int | None = None) -> None:
    """Persist an in-app notification, broadcast it live, and relay to Telegram if configured."""
    async with SessionLocal() as s:
        n = Notification(level=level, title=title, body=body, task_id=task_id)
        s.add(n)
        await s.commit()
        await s.refresh(n)
        nid = n.id
    await bus.emit(task_id or 0, "notification",
                   title, {"id": nid, "level": level, "title": title, "body": body}, persist=False)
    await _telegram(f"[{level.upper()}] {title}\n{body}".strip())


async def _telegram(text: str) -> None:
    cfg = await settings_store.get_all()
    token = cfg.get("telegram_bot_token")
    chat = cfg.get("telegram_chat_id")
    if not token or not chat:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text[:4000]},
            )
    except Exception:
        pass  # notifications must never break the agent
