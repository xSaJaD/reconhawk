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


async def sync_scopes(handles: list[str], *, client=None, cfg=None, sleep=None) -> dict:
    """Bounded job; injectable client and delay keep tests offline."""
    global _state, _cooldown_until
    handles = list(dict.fromkeys(handles))
    cfg = cfg if cfg is not None else await settings_store.get_all()
    sleep = sleep or asyncio.sleep
    _state = {"id": str(uuid.uuid4()), "status": "running", "total": len(handles), "completed": 0,
              "succeeded": 0, "failed": 0, "results": [], "started_at": now().isoformat()}
    try:
        if client is None:
            client = HackerOneClient(cfg.get("hackerone_username", ""), cfg.get("hackerone_token", ""), min_request_interval=1.3)
        cooldown = None
        for handle in handles:
            if cooldown is not None:
                message = "Deferred after HackerOne rate limiting; retry this program after the cooldown."
                if cooldown.get("retry_after") is not None:
                    message += f" Server requested {cooldown['retry_after']} seconds."
                error = HackerOneError(message, status_code=429, retry_after=cooldown.get("retry_after"))
                await _set_error(handle, error, cfg)
                result = {"handle": handle, "status": "rate_limited", "error": message,
                          "retry_after": cooldown.get("retry_after")}
            else:
                result = await _fetch_one(handle, client, cfg, sleep)
                if result["status"] == "rate_limited":
                    # A cooldown applies to the account, not just this one program. Preserve
                    # and expose the remaining work instead of sending requests too early.
                    cooldown = result
                    _cooldown_until = time.monotonic() + max(1.0, float(result.get("retry_after") or 60.0))
            _state["results"].append(result)
            _state["completed"] += 1
            _state["succeeded" if result["status"] in ("synced", "empty") else "failed"] += 1
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
    await _notify("success" if _state["status"] == "complete" else "warn", "HackerOne scope sync finished",
                  f"{_state['succeeded']}/{len(handles)} synchronized; {_state['failed']} failed. See program sync status for details.")
    return status()


async def queue_scopes(handles: list[str]) -> dict:
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
        _task = asyncio.create_task(sync_scopes(handles))
        return status()


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
    global _task, _cooldown_until
    if running():
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
    _task = None
    _cooldown_until = 0.0
