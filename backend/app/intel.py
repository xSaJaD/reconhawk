from __future__ import annotations

import re

from sqlalchemy import select

from .db import SessionLocal, Finding


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def classify_finding(title: str, summary: str, exclude_task_id: int | None = None) -> dict:
    """Compare a new finding against prior ones to label its relation.

    Returns {relation, related_to}. relation ∈ new|same|bypass.
    - 'same'   : a very similar finding already exists and is still open/confirmed (likely duplicate).
    - 'bypass' : a very similar finding was previously marked fixed → it has regressed / been bypassed.
    - 'new'    : nothing similar found.
    """
    async with SessionLocal() as s:
        rows = (await s.execute(select(Finding))).scalars().all()

    best = None
    best_score = 0.0
    for f in rows:
        if exclude_task_id and f.task_id == exclude_task_id:
            continue
        score = 0.7 * _similar(title, f.title) + 0.3 * _similar(summary, f.summary)
        if score > best_score:
            best, best_score = f, score

    if best and best_score >= 0.45:
        if best.status == "fixed":
            return {"relation": "bypass", "related_to": best.id}
        return {"relation": "same", "related_to": best.id}
    return {"relation": "new", "related_to": None}
