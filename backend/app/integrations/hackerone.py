from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import math
import os
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

# Overridable for testing; defaults to the real HackerOne Hacker API.
BASE = os.environ.get("HACKERONE_BASE", "https://api.hackerone.com/v1/hackers")


class HackerOneError(Exception):
    """API failure with machine-readable metadata for the sync service's retry policy."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        delay = float(value)
        return max(0.0, delay) if 0 <= delay < float("inf") else None
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=dt.timezone.utc)
            return max(0.0, (target - dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


def classify_program(attributes: dict) -> str:
    """Classify payment from the documented nullable program offers_bounties flag.

    Individual assets' eligible_for_bounty flags are independent from program type.
    Missing/null data is unknown; it is never evidence that the program is a VDP.
    Reference: https://api.hackerone.com/hacker-reference/#program
    """
    if not isinstance(attributes, dict):
        return "unknown"
    if attributes.get("offers_bounties") is True:
        return "bounty"
    if attributes.get("offers_bounties") is False:
        return "vdp"
    return "unknown"


class HackerOneClient:
    """Thin client over the HackerOne Hacker API (JSON:API, HTTP Basic auth).
    username = API token identifier, token = API token value."""

    def __init__(self, username: str, token: str, *, min_request_interval: float = 0):
        if not username or not token:
            raise HackerOneError("HackerOne credentials are not configured.")
        if not math.isfinite(min_request_interval) or min_request_interval < 0:
            raise ValueError("HackerOne request interval must be a finite nonnegative number")
        self._auth = (username, token)
        self.min_request_interval = min_request_interval
        self._last_request_at: float | None = None
        self._request_lock = asyncio.Lock()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        # Pacing covers every page, not just the outer program-sync loop.
        async with self._request_lock:
            if self._last_request_at is not None:
                delay = self.min_request_interval - (time.monotonic() - self._last_request_at)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_request_at = time.monotonic()
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
        failure_meta = {"status_code": r.status_code,
                        "retry_after": _retry_after(r.headers.get("retry-after"))}
        if r.status_code == 401:
            raise HackerOneError("HackerOne auth failed (401) — check your API username/token.",
                                 **failure_meta)
        try:
            data = r.json()
        except ValueError:
            raise HackerOneError(f"HackerOne returned non-JSON ({r.status_code}).", **failure_meta)
        if not isinstance(data, dict):
            raise HackerOneError(f"HackerOne returned a malformed object ({r.status_code}).",
                                 **failure_meta)
        if r.status_code >= 400:
            errs = data.get("errors", [])
            detail = "; ".join(str(e.get("detail") or e.get("title") or "")
                               for e in errs if isinstance(e, dict)) if isinstance(errs, list) else ""
            detail = detail or r.reason_phrase or "API request failed"
            raise HackerOneError(f"HackerOne error {r.status_code}: {detail[:500]}", **failure_meta)
        return data

    async def _collection(self, path: str, page_size: int = 100) -> list[dict]:
        """Read every documented page or fail; never return a silent partial snapshot.

        Pagination uses page[number]/page[size], as documented by the Hacker API.
        Do not follow an arbitrary links.next URL with account credentials attached.
        """
        if not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise ValueError("HackerOne page size must be between 1 and 100")
        rows, seen_next = [], set()
        for page in range(1, 101):
            data = await self._get(path, {"page[number]": page, "page[size]": page_size})
            chunk = data.get("data")
            links = data.get("links")
            if links is None:
                links = {}
            if not isinstance(chunk, list) or any(not isinstance(row, dict) for row in chunk):
                raise HackerOneError("HackerOne returned a malformed collection; old data was preserved.")
            if not isinstance(links, dict):
                raise HackerOneError("HackerOne returned malformed pagination links.")
            next_link = links.get("next")
            if next_link and (not chunk or not isinstance(next_link, str) or next_link in seen_next):
                raise HackerOneError("HackerOne pagination did not advance; refusing partial scope data.")
            rows.extend(chunk)
            if not next_link:
                return rows
            seen_next.add(next_link)
        raise HackerOneError("HackerOne pagination limit reached; refusing partial data.")

    # ---- reads ----
    async def list_programs(self, page_size: int = 100) -> list[dict]:
        return await self._collection("/programs", page_size)

    async def get_program(self, handle: str) -> dict:
        return (await self._get(f"/programs/{quote(handle, safe='')}")).get("data", {})

    async def get_scopes(self, handle: str) -> list[dict]:
        return await self._collection(f"/programs/{quote(handle, safe='')}/structured_scopes")

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


WEB_ASSET_TYPES = {"URL", "WILDCARD", "DOMAIN", "API", "IP_ADDRESS"}


def _web_target(identifier: str, asset_type: str) -> str | None:
    """Normalize only representable HTTP targets, preserving path/port restrictions.

    A source-code repository, mobile app ID, contract address or CIDR is not permission
    to test its hosting domain. Such assets remain visible metadata, not network targets.
    """
    if not identifier or any(char.isspace() for char in identifier):
        return None
    if asset_type == "IP_ADDRESS":
        try:
            address = ipaddress.ip_address(identifier)
            return f"https://[{address}]" if address.version == 6 else str(address)
        except ValueError:
            pass
    explicit_scheme = "://" in identifier
    try:
        parsed = urlsplit(identifier if explicit_scheme else "https://" + identifier)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return None
    try:
        port = parsed.port
        host = parsed.hostname
        if not host or port == 0:
            return None
        if asset_type == "IP_ADDRESS":
            address = ipaddress.ip_address(host)
            host = f"[{address}]" if address.version == 6 else str(address)
        else:
            try:
                address = ipaddress.ip_address(host)
                host = f"[{address}]" if address.version == 6 else str(address)
            except ValueError:
                # IDNA-normalize ordinary labels; retain only glob syntax the guard
                # actually supports. A bare '*' must never authorize the Internet.
                labels = host.rstrip(".").split(".")
                if len(labels) < 2:
                    return None
                normalized_labels = []
                for label in labels:
                    ascii_label = label.encode("idna").decode("ascii")
                    if not re.fullmatch(r"[a-zA-Z0-9_*-]+", ascii_label):
                        return None
                    normalized_labels.append(ascii_label.lower())
                concrete_suffix = 0
                for label in reversed(normalized_labels):
                    if "*" in label:
                        break
                    concrete_suffix += 1
                if concrete_suffix < 2:
                    return None
                host = ".".join(normalized_labels)
    except (ValueError, UnicodeError):
        return None
    normalized = parsed._replace(netloc=host + (f":{port}" if port is not None else ""))
    if explicit_scheme:
        return urlunsplit(normalized)
    # Host-only patterns can stay host-only; paths/ports need a scheme so the
    # existing scope guard parses their host instead of treating '/path' as a glob.
    if parsed.path or parsed.query or parsed.fragment or port is not None:
        return urlunsplit(normalized)
    return host


def normalize_scope_details(scopes: list[dict]) -> dict:
    """Preserve all structured assets and explain which ones the web agent can use.

    Required eligible_for_submission is an explicit boolean in the Hacker API schema.
    Unknown eligibility is denied and reported for operator review, never assumed true.
    Bounty eligibility remains metadata and does not determine testing authorization.
    Reference: https://api.hackerone.com/hacker-reference/#structured-scope
    """
    if not isinstance(scopes, list):
        raise HackerOneError("Structured scope must be a complete list of assets.")
    scope_in, scope_out, assets, warnings = [], [], [], []
    unsupported_count, ambiguous_count = 0, 0
    for row in scopes:
        if not isinstance(row, dict) or not isinstance(row.get("attributes"), dict):
            raise HackerOneError("Malformed structured scope row; refusing an incomplete scope snapshot.")
        attributes = row["attributes"]
        identifier = attributes.get("asset_identifier")
        asset_type = attributes.get("asset_type")
        if not isinstance(identifier, str) or not identifier.strip() or not isinstance(asset_type, str) or not asset_type.strip():
            raise HackerOneError("Structured scope is missing its identifier or type.")
        identifier, asset_type = identifier.strip(), asset_type.strip().upper()
        submission = attributes.get("eligible_for_submission")
        bounty = attributes.get("eligible_for_bounty")
        submission = submission if isinstance(submission, bool) else None
        bounty = bounty if isinstance(bounty, bool) else None
        asset = {
            "id": str(row["id"]) if row.get("id") is not None else None,
            "asset_identifier": identifier,
            "asset_type": asset_type,
            "eligible_for_submission": submission,
            "eligible_for_bounty": bounty,
            "instruction": attributes.get("instruction"),
            "max_severity": attributes.get("max_severity"),
            "web_target": None,
        }
        if asset_type not in WEB_ASSET_TYPES:
            asset.update(normalization_status="unsupported", normalization_reason="Not an HTTP asset; retained for manual review.")
            unsupported_count += 1
        else:
            try:
                target = _web_target(identifier, asset_type)
            except ValueError:
                target = None
            asset["web_target"] = target
            if target is None:
                raise HackerOneError(f"Invalid web scope identifier {identifier[:120]!r}; refusing partial scope.")
            if submission is True:
                scope_in.append(target)
                asset.update(normalization_status="in_scope", normalization_reason="Explicitly eligible for submission.")
            else:
                scope_out.append(target)
                if submission is False:
                    asset.update(normalization_status="out_of_scope", normalization_reason="Explicitly ineligible for submission.")
                else:
                    ambiguous_count += 1
                    asset.update(normalization_status="review_required", normalization_reason="Missing or invalid submission eligibility; denied until reviewed.")
                    warnings.append(f"Submission eligibility is unknown for {identifier}; excluded from automatic testing.")
        assets.append(asset)
    scope_out = list(dict.fromkeys(scope_out))
    denied = set(scope_out)
    scope_in = [target for target in dict.fromkeys(scope_in) if target not in denied]
    if unsupported_count:
        warnings.append(f"{unsupported_count} non-HTTP asset(s) retained for manual review; none were expanded into web targets.")
    return {"scope_in": scope_in, "scope_out": scope_out, "assets": assets,
            "warnings": warnings, "unsupported_count": unsupported_count,
            "ambiguous_count": ambiguous_count, "asset_count": len(assets)}


def normalize_scopes(scopes: list[dict]) -> tuple[list[str], list[str]]:
    """Compatibility wrapper for callers that only need the web allow/deny lists."""
    details = normalize_scope_details(scopes)
    return details["scope_in"], details["scope_out"]
