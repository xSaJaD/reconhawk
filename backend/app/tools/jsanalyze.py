from __future__ import annotations

import re

import httpx

# Patterns to pull interesting things out of JS bundles.
ENDPOINT_RE = re.compile(
    r"""["'`](/[a-zA-Z0-9_\-./]{1,120}|https?://[a-zA-Z0-9_\-./?=&%]{1,200})["'`]"""
)
SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|authorization|bearer|aws_access|client_secret)"
    r"""["'`\s:=]{1,4}["'`]?([A-Za-z0-9_\-\.]{12,})"""
)


async def fetch_js(url: str, timeout: float = 20.0, max_chars: int = 400_000) -> str:
    async with httpx.AsyncClient(follow_redirects=True, verify=False, timeout=timeout) as client:
        resp = await client.get(url, headers={"User-Agent": "ReconHawk/0.1"})
        return resp.text[:max_chars]


def analyze(source: str) -> dict:
    endpoints = set()
    for m in ENDPOINT_RE.finditer(source):
        val = m.group(1)
        # filter obvious noise
        if val in ("/", "//") or val.endswith((".png", ".jpg", ".svg", ".css", ".woff", ".gif")):
            continue
        if len(val) < 2:
            continue
        endpoints.add(val)

    secrets = []
    for m in SECRET_RE.finditer(source):
        secrets.append({"label": m.group(1), "value_preview": m.group(2)[:40]})

    # API-ish paths get ranked up
    interesting = sorted(
        e for e in endpoints
        if any(k in e.lower() for k in ("api", "v1", "v2", "graphql", "admin", "user", "auth", "upload", "token", "internal"))
    )

    return {
        "endpoints": sorted(endpoints)[:200],
        "interesting_endpoints": interesting[:100],
        "possible_secrets": secrets[:50],
        "endpoint_count": len(endpoints),
    }


async def fetch_and_analyze(url: str) -> dict:
    src = await fetch_js(url)
    result = analyze(src)
    result["url"] = url
    result["bytes"] = len(src)
    return result
