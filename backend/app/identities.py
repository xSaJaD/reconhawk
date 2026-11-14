from __future__ import annotations

import re

from sqlalchemy import select

from .db import SessionLocal, Identity, Task


async def get_by_label(task_id: int, label: str) -> Identity | None:
    """Find a test identity by label, scoped to the task's program (or global)."""
    async with SessionLocal() as s:
        t = await s.get(Task, task_id)
        pid = t.program_id if t else None
        rows = (await s.execute(select(Identity).where(Identity.label == label))).scalars().all()
    # prefer one tied to this program, else any
    for r in rows:
        if r.program_id == pid:
            return r
    return next((row for row in rows if row.program_id is None), None)


def auth_headers(identity: Identity) -> dict:
    """Build the auth header(s) this identity injects into a request."""
    if not identity or not identity.material:
        return {}
    if identity.auth_type == "cookie":
        return {"Cookie": identity.material}
    if identity.auth_type == "bearer":
        return {"Authorization": f"Bearer {identity.material}"}
    if identity.auth_type == "header":
        return {identity.header_name or "Authorization": identity.material}
    return {}


# Signals that a human-verification / anti-automation control is present. We NEVER try to bypass
# these — provisioning stops and hands off to the operator.
_CHALLENGE_RE = re.compile(
    r"(?i)(recaptcha|hcaptcha|g-recaptcha|cf-turnstile|captcha|are you (a )?human|"
    r"verify (your )?(email|phone|identity)|one[\- ]?time (code|password)|\botp\b|"
    r"confirmation code|challenge|cloudflare|px-captcha|funcaptcha|arkose)"
)


def detect_human_verification(status_code: int, headers: dict, body: str) -> str | None:
    """Return a short reason if the response indicates a human-verification/anti-bot gate."""
    hdrs = " ".join(f"{k}:{v}" for k, v in (headers or {}).items())
    if "cf-mitigated" in hdrs.lower() or "x-captcha" in hdrs.lower():
        return "anti-bot header present"
    m = _CHALLENGE_RE.search((body or "")[:8000]) or _CHALLENGE_RE.search(hdrs)
    if m:
        return f"challenge detected: '{m.group(1)}'"
    if status_code in (403, 429) and ("captcha" in (body or "").lower() or "blocked" in (body or "").lower()):
        return "blocked / rate-limited (likely anti-automation)"
    return None
