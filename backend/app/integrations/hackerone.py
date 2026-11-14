from __future__ import annotations

import os

import httpx

# Overridable for testing; defaults to the real HackerOne Hacker API.
BASE = os.environ.get("HACKERONE_BASE", "https://api.hackerone.com/v1/hackers")


class HackerOneError(Exception):
    pass


class HackerOneClient:
    """Thin client over the HackerOne Hacker API (JSON:API, HTTP Basic auth).
    username = API token identifier, token = API token value."""

    def __init__(self, username: str, token: str):
        if not username or not token:
            raise HackerOneError("HackerOne credentials are not configured.")
        self._auth = (username, token)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(BASE + path, auth=self._auth,
                            headers={"Accept": "application/json"}, params=params)
            return self._handle(r)

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(BASE + path, auth=self._auth, json=body,
                             headers={"Accept": "application/json", "Content-Type": "application/json"})
            return self._handle(r)

    @staticmethod
    def _handle(r: httpx.Response) -> dict:
        if r.status_code == 401:
            raise HackerOneError("HackerOne auth failed (401) — check your API username/token.")
        try:
            data = r.json()
        except Exception:
            raise HackerOneError(f"HackerOne returned non-JSON ({r.status_code}).")
        if r.status_code >= 400:
            errs = data.get("errors", [])
            detail = "; ".join(e.get("detail", e.get("title", "")) for e in errs) or str(data)
            raise HackerOneError(f"HackerOne error {r.status_code}: {detail}")
        return data

    # ---- reads ----
    async def list_programs(self, page_size: int = 100) -> list[dict]:
        out, page = [], 1
        while True:
            data = await self._get("/programs", {"page[number]": page, "page[size]": page_size})
            rows = data.get("data", [])
            out.extend(rows)
            if not data.get("links", {}).get("next") or not rows:
                break
            page += 1
            if page > 20:
                break
        return out

    async def get_program(self, handle: str) -> dict:
        return (await self._get(f"/programs/{handle}")).get("data", {})

    async def get_scopes(self, handle: str) -> list[dict]:
        rows=[]
        for page in range(1,101):
            data=await self._get(f"/programs/{handle}/structured_scopes",{'page[size]':100,'page[number]':page})
            rows.extend(data.get('data',[]))
            if not data.get('links',{}).get('next'): return rows
        raise HackerOneError('Incomplete scope pagination; refusing partial scope')

    async def get_report(self, report_id: str) -> dict:
        return (await self._get(f"/reports/{report_id}")).get("data", {})

    # ---- write: submit a report ----
    async def submit_report(self, *, team_handle: str, title: str,
                            vulnerability_information: str, impact: str,
                            severity_rating: str | None = None,
                            weakness_id: int | None = None,
                            structured_scope_id: int | None = None) -> dict:
        attrs = {
            "team_handle": team_handle,
            "title": title[:255],
            "vulnerability_information": vulnerability_information,
            "impact": impact,
        }
        if severity_rating:
            attrs["severity_rating"] = severity_rating
        if weakness_id:
            attrs["weakness_id"] = weakness_id
        if structured_scope_id:
            attrs["structured_scope_id"] = structured_scope_id
        data = await self._post("/reports", {"data": {"type": "report", "attributes": attrs}})
        rep = data.get("data", {})
        rid = rep.get("id", "")
        return {"id": rid, "url": f"https://hackerone.com/reports/{rid}" if rid else "",
                "state": rep.get("attributes", {}).get("state", "")}


def normalize_scopes(scopes: list[dict]) -> tuple[list[str], list[str]]:
    """Turn H1 structured scopes into ReconHawk in/out host lists.
    URL-type, submission-eligible assets go in-scope."""
    scope_in,scope_out = [],[]
    for s in scopes:
        a = s.get("attributes", {})
        ident = (a.get("asset_identifier") or "").strip()
        atype = (a.get("asset_type") or "").upper()
        if not ident:
            continue
        if atype in ("URL", "WILDCARD", "DOMAIN") and a.get("eligible_for_submission", True):
            scope_in.append(ident)
        elif atype in ('URL','WILDCARD','DOMAIN'):
            scope_out.append(ident)
    return scope_in, scope_out
