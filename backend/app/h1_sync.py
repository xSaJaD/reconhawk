"""Observable, bounded HackerOne sync; failed fetches preserve last-known authorization."""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import hashlib
import json
import logging
import math
import time
import uuid

import httpx
from sqlalchemy import select, update

from . import notify, settings_store
from .db import Program, SessionLocal, now
from .integrations.hackerone import HackerOneClient, HackerOneError, classify_program, normalize_scope_details

log = logging.getLogger(__name__)
IMPORT_LOCK = asyncio.Lock()
_queue_lock = asyncio.Lock()
_task: asyncio.Task | None = None
_cooldown_until = 0.0
_state = {"status": "idle", "total": 0, "completed": 0, "succeeded": 0, "failed": 0}
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY = 60.0
DEFAULT_CONCURRENCY = 6
DEFAULT_REQUESTS_PER_SECOND = 8.0
DEFAULT_TTL_HOURS = 12.0
_startup_task: asyncio.Task | None = None


def _number(cfg: dict, key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(cfg.get(key) or default)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def request_interval(cfg: dict) -> float:
    """Seconds between request starts, from the configured requests/second."""
    return 1.0 / _number(cfg, "h1_requests_per_second", DEFAULT_REQUESTS_PER_SECOND, 1, 10)


async def close_client(client) -> None:
    """Close a pooled client if it has one (test doubles and old clients may not)."""
    closer = getattr(client, "aclose", None)
    if closer is not None:
        await closer()


def make_client(cfg: dict) -> HackerOneClient:
    return HackerOneClient(cfg.get("hackerone_username", ""), cfg.get("hackerone_token", ""),
                           min_request_interval=request_interval(cfg))


def timestamp(value) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)).isoformat()


class SyncBusy(ValueError):
    pass


def status() -> dict:
    return {**copy.deepcopy(_state), "retry_after_seconds": max(0, math.ceil(_cooldown_until - time.monotonic()))}


def running() -> bool:
    return _task is not None and not _task.done()


def ensure_available() -> None:
    if running():
        raise SyncBusy("A scope sync is already running; inspect its status and wait for completion.")
    remaining = math.ceil(_cooldown_until - time.monotonic())
    if remaining > 0:
        raise SyncBusy(f"HackerOne rate limited this account; retry in {remaining} seconds.")


def scope_hash(scope_in: list[str], scope_out: list[str]) -> str:
    return hashlib.sha256(json.dumps([sorted(scope_in), sorted(scope_out)]).encode()).hexdigest()[:16]


async def import_programs(programs: list[dict]) -> dict:
    """Refresh supplied H1 metadata only; a manual row with the same handle is unrelated."""
    if not isinstance(programs, list):
        raise ValueError("HackerOne programs response is not a list")
    added = updated = skipped = 0
    handles = []
    async with SessionLocal() as session:
        existing = {p.handle: p for p in (await session.execute(
            select(Program).where(Program.platform == "hackerone")
        )).scalars().all() if p.handle}
        for program in programs:
            attrs = program.get("attributes", {}) if isinstance(program, dict) else {}
            if not isinstance(attrs, dict):
                skipped += 1
                continue
            handle = attrs.get("handle")
            if not isinstance(handle, str) or not handle.strip():
                skipped += 1
                continue
            handle = handle.strip()
            if handle in handles:
                continue
            handles.append(handle)
            row = existing.get(handle)
            if row is None:
                row = Program(name=str(attrs.get("name") or handle), platform="hackerone", handle=handle,
                              scope_in="[]", scope_out="[]", scope_hash="", program_type=classify_program(attrs))
                session.add(row)
                existing[handle] = row
                added += 1
            else:
                row.name = str(attrs.get("name") or row.name or handle)
                row.program_type = classify_program(attrs)
                updated += 1
            if isinstance(attrs.get("policy"), str):
                row.policy = attrs["policy"]
            icon = attrs.get("profile_picture")
            if isinstance(icon, dict):
                icon = icon.get("medium") or icon.get("small") or next(iter(icon.values()), "")
            if isinstance(icon, str) and icon:
                row.icon_url = icon
        await session.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "total": len(handles), "handles": handles}


def _status_code(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)


def _safe_error(exc: Exception, cfg: dict) -> str:
    message = str(exc) or type(exc).__name__
    for key in ("hackerone_token", "hackerone_username"):
        if cfg.get(key):
            message = message.replace(str(cfg[key]), "[REDACTED]")
    return message[:500]


async def _notify(level: str, title: str, body: str) -> None:
    try:
        await notify.push(level, title, body, None)
    except Exception:
        log.exception("Could not publish HackerOne sync notification")


async def _program(session, handle):
    return (await session.execute(select(Program).where(
        Program.platform == "hackerone", Program.handle == handle
    ))).scalars().first()


async def _set_error(handle: str, exc: Exception, cfg: dict) -> None:
    async with SessionLocal() as session:
        row = await _program(session, handle)
        if row:
            row.scope_sync_status = "rate_limited" if _status_code(exc) == 429 else "error"
            row.scope_sync_error = _safe_error(exc, cfg)
            await session.commit()


async def _fetch_one(handle: str, client, cfg: dict, sleep) -> dict:
    async with SessionLocal() as session:
        row = await _program(session, handle)
        if not row:
            return {"handle": handle, "status": "deleted", "error": "Program removed before sync"}
        row.scope_sync_status, row.scope_sync_error = "syncing", ""
        row.scope_sync_started_at, row.scope_sync_attempts = now(), 0
        await session.commit()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        async with SessionLocal() as session:
            row = await _program(session, handle)
            if row:
                row.scope_sync_attempts = attempt
                await session.commit()
        try:
            scopes = await client.get_scopes(handle)
            details = normalize_scope_details(scopes)
            scope_in, scope_out = details["scope_in"], details["scope_out"]
            async with SessionLocal() as session:
                row = await _program(session, handle)
                if not row:
                    return {"handle": handle, "status": "deleted", "error": "Program removed during sync"}
                new_hash = scope_hash(scope_in, scope_out)
                changed = bool(row.scope_hash and row.scope_hash != new_hash)
                # Replace only after every page and normalization completed successfully.
                row.scope_in, row.scope_out, row.scope_hash = json.dumps(scope_in), json.dumps(scope_out), new_hash
                row.scope_sync_status, row.scope_sync_error = "synced" if scope_in else "empty", ""
                row.scope_sync_warnings = json.dumps(details.get("warnings", []))
                row.scope_assets_json = json.dumps(details.get("assets", []))
                row.scope_asset_count = details.get("asset_count", len(scopes))
                row.scope_web_asset_count = len(scope_in) + len(scope_out)
                row.scope_synced_at = now()
                await session.commit()
                result = {"handle": handle, "status": row.scope_sync_status,
                          "asset_count": row.scope_asset_count, "web_asset_count": row.scope_web_asset_count,
                          "warnings": details.get("warnings", [])}
            if changed:
                await _notify("warn", f"Scope changed · {handle}", "HackerOne scope changed; review the refreshed asset list.")
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = _status_code(exc)
            transient = code == 429 or (code is not None and 500 <= code <= 599) or isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
            retry_after = getattr(exc, "retry_after", None)
            delay = max(0.0, float(retry_after)) if retry_after is not None else 2.0 ** attempt
            if transient and attempt < MAX_ATTEMPTS and delay <= MAX_RETRY_DELAY:
                await sleep(delay)
                continue
            await _set_error(handle, exc, cfg)
            return {"handle": handle, "status": "rate_limited" if code == 429 else "error",
                    "error": _safe_error(exc, cfg), "retry_after": retry_after}
    raise RuntimeError("Unreachable attempt state")


async def sync_scopes(handles: list[str], *, client=None, cfg=None, sleep=None,
                      concurrency: int | None = None) -> dict:
    """Bounded job; injectable client and delay keep tests offline.

    Programs are fetched by a small pool of workers (h1_sync_concurrency) sharing one
    rate-limited client. A 429 is an account-wide signal: once any worker sees one,
    every remaining program is deferred visibly instead of being requested too early.
    """
    global _state, _cooldown_until
    handles = list(dict.fromkeys(handles))
    cfg = cfg if cfg is not None else await settings_store.get_all()
    sleep = sleep or asyncio.sleep
    workers = int(concurrency or _number(cfg, "h1_sync_concurrency", DEFAULT_CONCURRENCY, 1, 16))
    _state = {"id": str(uuid.uuid4()), "status": "running", "total": len(handles), "completed": 0,
              "succeeded": 0, "failed": 0, "results": [], "started_at": now().isoformat(),
              "concurrency": workers}
    owned_client = client is None
    try:
        if client is None:
            client = make_client(cfg)
        pending = list(handles)
        shared: dict = {"cooldown": None}

        async def run_one(handle: str) -> dict:
            global _cooldown_until
            cooldown = shared["cooldown"]
            if cooldown is not None:
                message = "Deferred after HackerOne rate limiting; retry this program after the cooldown."
                if cooldown.get("retry_after") is not None:
                    message += f" Server requested {cooldown['retry_after']} seconds."
                error = HackerOneError(message, status_code=429, retry_after=cooldown.get("retry_after"))
                await _set_error(handle, error, cfg)
                return {"handle": handle, "status": "rate_limited", "error": message,
                        "retry_after": cooldown.get("retry_after")}
            result = await _fetch_one(handle, client, cfg, sleep)
            if result["status"] == "rate_limited" and shared["cooldown"] is None:
                # A cooldown applies to the account, not just this one program.
                shared["cooldown"] = result
                _cooldown_until = time.monotonic() + max(1.0, float(result.get("retry_after") or 60.0))
            return result

        async def worker() -> None:
            while pending:
                handle = pending.pop(0)
                result = await run_one(handle)
                _state["results"].append(result)
                _state["completed"] += 1
                _state["succeeded" if result["status"] in ("synced", "empty") else "failed"] += 1

        await asyncio.gather(*(worker() for _ in range(max(1, min(workers, len(handles) or 1)))))
        _state["status"] = "complete" if not _state["failed"] else "partial"
    except asyncio.CancelledError:
        _state["status"] = "interrupted"
        await _mark_interrupted(handles)
        raise
    except Exception as exc:
        _state["status"], _state["error"] = "error", _safe_error(exc, cfg)
        for handle in handles:
            await _set_error(handle, exc, cfg)
        _state["failed"] = len(handles)
    finally:
        _state["finished_at"] = now().isoformat()
        if owned_client and client is not None:
            await close_client(client)
    await _notify("success" if _state["status"] == "complete" else "warn", "HackerOne scope sync finished",
                  f"{_state['succeeded']}/{len(handles)} synchronized; {_state['failed']} failed. See program sync status for details.")
    return status()


async def queue_scopes(handles: list[str], *, client=None, cfg=None) -> dict:
    global _task, _state
    async with _queue_lock:
        ensure_available()
        handles = list(dict.fromkeys(handles))
        async with SessionLocal() as session:
            await session.execute(update(Program).where(
                Program.platform == "hackerone", Program.handle.in_(handles)
            ).values(scope_sync_status="queued", scope_sync_error="", scope_sync_attempts=0))
            await session.commit()
        _state = {"status": "queued", "total": len(handles), "completed": 0, "succeeded": 0, "failed": 0, "results": []}
        extra = {key: value for key, value in (("client", client), ("cfg", cfg)) if value is not None}
        _task = asyncio.create_task(sync_scopes(handles, **extra))
        return status()


async def stale_handles(handles: list[str], ttl_hours: float, *, at=None) -> list[str]:
    """Programs whose scope needs a refresh, never-synced and failed ones first.

    A program synced successfully within ttl_hours is skipped, so a restart refreshes
    only what is missing or old instead of re-downloading every scope.
    """
    if not handles:
        return []
    current = at or now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    cutoff = current - dt.timedelta(hours=ttl_hours)
    async with SessionLocal() as session:
        rows = (await session.execute(select(Program).where(
            Program.platform == "hackerone", Program.handle.in_(handles)
        ))).scalars().all()
    by_handle = {row.handle: row for row in rows}
    urgent, old = [], []
    for handle in dict.fromkeys(handles):
        row = by_handle.get(handle)
        synced = row.scope_synced_at if row else None
        if synced is not None and synced.tzinfo is None:
            synced = synced.replace(tzinfo=dt.timezone.utc)
        healthy = row is not None and row.scope_sync_status in ("synced", "empty")
        if row is None or synced is None or not healthy:
            urgent.append(handle)
        elif ttl_hours <= 0 or synced < cutoff:
            old.append((synced, handle))
    return urgent + [handle for _, handle in sorted(old)]


async def startup_sync(*, client=None, cfg=None) -> dict:
    """Background refresh at service start: list programs, then sync only stale scopes."""
    cfg = cfg if cfg is not None else await settings_store.get_all()
    if cfg.get("h1_auto_sync", "true") != "true":
        return {"status": "disabled"}
    if not (cfg.get("hackerone_username") and cfg.get("hackerone_token")) and client is None:
        return {"status": "not_configured"}
    owned = client is None
    client = client or make_client(cfg)
    try:
        async with IMPORT_LOCK:
            if running():
                return {"status": "busy"}
            programs = await client.list_programs()
            summary = await import_programs(programs)
        ttl = _number(cfg, "h1_scope_ttl_hours", DEFAULT_TTL_HOURS, 0, 168)
        handles = await stale_handles(summary["handles"], ttl)
        if not handles:
            if owned:
                await close_client(client)
            return {"status": "fresh", "programs": summary["total"]}
        # The sync job takes ownership of the client and closes it when it finishes.
        await queue_scopes(handles, client=client, cfg=cfg)
        if owned:
            _task.add_done_callback(lambda _t: asyncio.ensure_future(close_client(client)))
        return {"status": "queued", "programs": summary["total"], "stale": len(handles)}
    except asyncio.CancelledError:
        if owned:
            await close_client(client)
        raise
    except Exception as exc:
        log.warning("HackerOne startup sync skipped: %s", _safe_error(exc, cfg))
        if owned:
            await close_client(client)
        return {"status": "error", "error": _safe_error(exc, cfg)}


def start_startup_sync() -> None:
    """Fire-and-forget: never delays API startup, even when HackerOne is slow."""
    global _startup_task
    if _startup_task is None or _startup_task.done():
        _startup_task = asyncio.create_task(startup_sync())


async def _mark_interrupted(handles=None) -> None:
    async with SessionLocal() as session:
        statement = update(Program).where(
            Program.platform == "hackerone", Program.scope_sync_status.in_(["queued", "syncing"])
        )
        if handles is not None:
            statement = statement.where(Program.handle.in_(handles))
        await session.execute(statement.values(scope_sync_status="interrupted", scope_sync_error="Scope sync interrupted; retry to refresh it."))
        await session.commit()


async def recover_interrupted() -> None:
    await _mark_interrupted()


async def stop() -> None:
    global _task, _cooldown_until, _startup_task
    if _startup_task is not None and not _startup_task.done():
        _startup_task.cancel()
        await asyncio.gather(_startup_task, return_exceptions=True)
    _startup_task = None
    if running():
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
    _task = None
    _cooldown_until = 0.0
