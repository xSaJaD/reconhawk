from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, parse_qsl

from sqlalchemy import select

from .db import SessionLocal, Finding, Task


_URL = re.compile(r"https?://[^\s<>\"`]+", re.IGNORECASE)
_PATH = re.compile(r"(?<![\w:/])(/[A-Za-z0-9][^\s<>\"`]*)")


def _location(url: str) -> tuple | None:
    """Canonical location without query values or fragments; never resolve DNS.

    Query parameter names distinguish e.g. two search injection points, while per-request
    canaries, object IDs and credentials in query values do not become comparison keys.
    Paths retain case and encoding because servers can interpret those differently.
    """
    try:
        parsed = urlsplit(url if "://" in url else "https://" + url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return (parsed.scheme, parsed.hostname.lower().rstrip("."), port,
                parsed.path or "/", tuple(sorted({key for key, _ in parse_qsl(parsed.query)})))
    except ValueError:
        return None


def _locations(title: str, summary: str, target: str) -> set[tuple]:
    """Prefer explicit same-asset endpoints; an assessment homepage is not bug location."""
    anchor = _location(target)
    if anchor is None:
        return set()
    text = f"{title}\n{summary}"
    locations = set()
    for match in _URL.finditer(text):
        location = _location(match.group(0).rstrip(".,;:!)]}"))
        if location and location[:3] == anchor[:3]:
            locations.add(location)
    # Relative paths are useful when a report names "GET /api/orders" rather than a URL.
    for match in _PATH.finditer(text):
        location = _location(urljoin(target, match.group(1).rstrip(".,;:!)]}")))
        if location and location[:3] == anchor[:3]:
            locations.add(location)
    if not locations and anchor[3] != "/":
        locations.add(anchor)
    return locations


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


async def classify_finding(title: str, summary: str, exclude_task_id: int | None = None) -> dict:
    """Suggest a relation only for comparable reports in the current program and location.

    Returns {relation, related_to}. relation ∈ new|same|bypass.
    - 'same'   : similar finding at the same recorded location (a review suggestion, not proof).
    - 'bypass' : a very similar finding was previously marked fixed → it has regressed / been bypassed.
    - 'new'    : no comparable match, including insufficient task/location context.

    An identical generic title from another program, host or unknown endpoint is never enough.
    This helper does not suppress findings or change their confirmation/reporting status.
    """
    if exclude_task_id is None:
        return {"relation": "new", "related_to": None}
    async with SessionLocal() as s:
        current = await s.get(Task, exclude_task_id)
        if current is None:
            return {"relation": "new", "related_to": None}
        locations = _locations(title, summary, current.target)
        anchor = _location(current.target)
        if not locations or anchor is None:
            return {"relation": "new", "related_to": None}
        query = select(Finding, Task).join(Task, Finding.task_id == Task.id).where(
            Finding.task_id != current.id,
            Task.program_id == current.program_id if current.program_id is not None else Task.program_id.is_(None),
        )
        rows = (await s.execute(query)).all()

    best = None
    best_score = 0.0
    for f, prior_task in rows:
        prior_anchor = _location(prior_task.target)
        if prior_anchor is None or prior_anchor[:3] != anchor[:3]:
            continue
        prior_locations = _locations(f.title, f.summary, prior_task.target)
        if not locations.intersection(prior_locations):
            continue
        title_score = _similar(title, f.title)
        score = 0.7 * title_score + 0.3 * _similar(summary, f.summary)
        if title_score < 0.65:
            continue
        if score > best_score:
            best, best_score = f, score

    if best and best_score >= 0.65:
        if best.status == "fixed":
            return {"relation": "bypass", "related_to": best.id}
        return {"relation": "same", "related_to": best.id}
    return {"relation": "new", "related_to": None}
