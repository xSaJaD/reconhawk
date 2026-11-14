from __future__ import annotations

import json
import re

from ..guard import request
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import workspace

UA = {"User-Agent": "ReconHawk/0.1 (security research)"}

# Call-site patterns over RECONSTRUCTED (readable) source — far more tractable than minified code.
FETCH_RE = re.compile(r"""fetch\(\s*([`'"].*?[`'"])""", re.DOTALL)
AXIOS_RE = re.compile(r"""axios\.(get|post|put|patch|delete)\(\s*([`'"].*?[`'"])""", re.IGNORECASE)
URL_LITERAL_RE = re.compile(r"""[`'"](/[a-zA-Z0-9_\-./{}$]+|https?://[^`'"\s]+)[`'"]""")
AUTH_RE = re.compile(r"(?i)(authorization|bearer|access[_-]?token|x-[a-z-]*token|cookie|localstorage\.getitem\([^)]*token)")


async def _fetch(url: str, timeout: float = 25.0, max_chars: int = 3_000_000) -> str:
    r = await request('GET', url, timeout=timeout, max_bytes=max_chars*4)
    r.raise_for_status()
    return r.text[:max_chars]


async def find_map_url(js_url: str, js_text: str | None = None) -> str | None:
    if js_text is None:
        try:
            js_text = await _fetch(js_url)
        except Exception:
            js_text = ""
    m = re.search(r"//#\s*sourceMappingURL=(\S+)", js_text or "")
    if m:
        ref = m.group(1).strip()
        return urljoin(js_url, ref)
    # convention: <bundle>.js.map
    return urlunsplit(urlsplit(js_url)._replace(path=urlsplit(js_url).path + '.map'))


async def reconstruct(task_id: int, map_url: str) -> dict:
    """Fetch a .js.map, write its original sources to the workspace, and extract request shapes.
    Uses sourcesContent directly (no VLQ decode needed)."""
    raw = await _fetch(map_url)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "source map is not valid JSON", "map_url": map_url}

    sources = data.get("sources", []) or []
    contents = data.get("sourcesContent", []) or []
    recovered = 0
    endpoints: set[str] = set()
    request_shapes: list[dict] = []
    auth_hits: set[str] = set()

    for i, src in enumerate(sources):
        if i >= len(contents) or contents[i] is None:
            continue
        body = contents[i]
        from ..shapes import capture
        request_shapes.extend(await capture(task_id, body, map_url + '#' + str(src)))
        # sanitize the webpack path into a safe workspace path
        rel = re.sub(r"^webpack://", "", src).lstrip("/.")
        rel = re.sub(r"[^a-zA-Z0-9_\-./]", "_", rel) or f"src_{i}.js"
        try:
            workspace.write_file(task_id, f"srcmap/{rel}", body)
            recovered += 1
        except Exception:
            pass
        # endpoints
        for mm in URL_LITERAL_RE.finditer(body):
            v = mm.group(1)
            if v in ("/", "//") or v.endswith((".png", ".jpg", ".svg", ".css", ".woff", ".gif", ".ico")):
                continue
            if "/api" in v.lower() or v.startswith("http") or "service" in v.lower():
                endpoints.add(v)
        # request call sites
        for mm in FETCH_RE.finditer(body):
            request_shapes.append({"kind": "fetch", "url": mm.group(1).strip("`'\""), "file": rel})
        for mm in AXIOS_RE.finditer(body):
            request_shapes.append({"kind": "axios", "method": mm.group(1).upper(),
                                   "url": mm.group(2).strip("`'\""), "file": rel})
        # auth flow hints
        for mm in AUTH_RE.finditer(body):
            auth_hits.add(mm.group(1).lower())

    return {
        "map_url": map_url,
        "files_recovered": recovered,
        "total_sources": len(sources),
        "endpoints": sorted(endpoints)[:200],
        "request_shapes": request_shapes[:120],
        "auth_signals": sorted(auth_hits),
        "workspace_dir": "srcmap/",
    }


async def analyze(task_id: int, js_url: str) -> dict:
    map_url = await find_map_url(js_url)
    if not map_url:
        return {"error": "no source map found", "js_url": js_url}
    res = await reconstruct(task_id, map_url)
    res["js_url"] = js_url
    return res
