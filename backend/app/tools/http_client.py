from __future__ import annotations

import time

import httpx


async def send_request(
    method: str,
    url: str,
    headers: dict | None = None,
    body: str | None = None,
    timeout: float = 20.0,
    max_response_chars: int = 20000,
) -> dict:
    headers = headers or {}
    headers.setdefault("User-Agent", "ReconHawk/0.1 (security research)")
    started = time.perf_counter()
    async with httpx.AsyncClient(follow_redirects=True, verify=False, timeout=timeout) as client:
        resp = await client.request(
            method.upper(),
            url,
            headers=headers,
            content=body.encode() if body else None,
        )
        text = resp.text
    duration_ms = int((time.perf_counter() - started) * 1000)
    # Classify the response so the agent treats an SPA shell / auth gate as a SIGNAL, not a dead end.
    ct = resp.headers.get("content-type", "").lower()
    low = text[:2000].lower()
    is_spa = ("text/html" in ct) and ('id="root"' in low or "<!doctype html" in low or "<div id=app" in low)
    if is_spa:
        hint = "SPA_SHELL — wrong request shape (not a dead endpoint). Supply the schema's required body/params/auth."
    elif resp.status_code in (401, 403):
        hint = "AUTH_GATE — a real handler is reached; it needs an auth context/credentials."
    elif resp.status_code >= 500:
        hint = "SERVER_ERROR — a code path was triggered; vary inputs toward the expected schema."
    elif "application/json" in ct:
        hint = "JSON — real API response."
    else:
        hint = ""
    return {
        "status_code": resp.status_code,
        "response_headers": dict(resp.headers),
        "response_body": text[:max_response_chars],
        "truncated": len(text) > max_response_chars,
        "duration_ms": duration_ms,
        "final_url": str(resp.url),
        "classification": hint,
    }
