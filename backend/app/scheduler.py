from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import select

from .db import SessionLocal, Routine, Task, now
from .agent.runner import start_task

_task: asyncio.Task | None = None


async def _tick() -> None:
    async with SessionLocal() as s:
        routines = (await s.execute(select(Routine).where(Routine.enabled == True))).scalars().all()  # noqa: E712
        due = []
        for r in routines:
            last = r.last_run
            if last is None or (now() - last) >= dt.timedelta(minutes=r.interval_minutes):
                due.append(r)
        launched = []
        for r in due:
            t = Task(target=r.target, objective=r.objective, program_id=r.program_id)
            s.add(t)
            r.last_run = now()
            await s.flush()
            launched.append(t.id)
        await s.commit()
    for tid in launched:
        asyncio.create_task(start_task(tid))


async def _loop() -> None:
    while True:
        try:
            await _tick()
        except Exception:
            pass
        await asyncio.sleep(60)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())
