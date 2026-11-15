from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager

import httpx

from fastapi import Request, Response, FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, desc, update, func

from .config import settings
from .db import (
    init_db, SessionLocal, Program, Task, Event, Endpoint, Finding, Lead, SubAgent,
    Addendum, RepeaterRequest, Routine, Notification, Memory, Skill, Intel,
)
from .events import bus
from .scope import check_url, load_scope, ScopeError
from .guard import policy
from .agent.runner import start_task, request_stop
from .tools import http_client
from .llm import PROVIDERS, build_llm
from . import settings_store, chat, scheduler, notify, verify, reporting, telegram_bot, skills_seed, knowledge
from .integrations.hackerone import HackerOneClient, HackerOneError, normalize_scopes
from . import h1_sync

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.artifacts_dir, exist_ok=True)
    db_path = settings.database_url.split("///")[-1]
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    await init_db()
    await h1_sync.recover_interrupted()
    await skills_seed.seed_skills()
    settings.scope_enforced=(await settings_store.get('scope_enforced'))=='true'
    async with SessionLocal() as session:
        await session.execute(update(Task).where(Task.status.in_(['running','queued'])).values(status='paused',stop_reason='service restarted'))
        await session.commit()
    scheduler.start()
    telegram_bot.start()
    # Refresh HackerOne programs and stale scopes in the background right away;
    # the API is usable immediately and the UI shows progress via /sync_h1/status.
    h1_sync.start_startup_sync()
    try:
        yield
    finally:
        await h1_sync.stop()
        await scheduler.stop()
        from .agent.runner import ACTIVE
        workers=list(ACTIVE.values())
        for tid in list(ACTIVE): request_stop(tid)
        if workers:
            done,pending=await asyncio.wait(workers,timeout=10)
            for t in pending: t.cancel()
            await asyncio.gather(*pending,return_exceptions=True)


app = FastAPI(title="ReconHawk API", version="1.7.1", lifespan=lifespan)
@app.middleware('http')
async def authenticate(request:Request,call_next):
    from .auth import allowed,origin_ok
    if request.method=='OPTIONS' or (request.url.path=='/api/session' and request.method=='POST' and origin_ok(request)):
        return await call_next(request)
    if not allowed(request): return JSONResponse({'detail':'Authentication required'},status_code=401)
    return await call_next(request)

app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins.split(','),allow_credentials=True,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type','Authorization'])

@app.exception_handler(ValueError)
async def invalid_value(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=400)

@app.post('/api/session')
async def login(body:dict,request:Request,response:Response):
    import hmac
    if not settings.api_token or not hmac.compare_digest(str(body.get('token','')),settings.api_token): raise HTTPException(401)
    response.set_cookie('reconhawk_session',settings.api_token,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=43200)
    return {'ok':True}

@app.delete('/api/session')
async def logout(response:Response):
    response.delete_cookie('reconhawk_session')
    return {'ok':True}


# ---------- schemas ----------
class ProgramIn(BaseModel):
    name: str
    platform: str = "manual"
    handle: str | None = None
    policy: str | None = None
    scope_in: list[str] = []
    scope_out: list[str] = []
    program_type: str = "unknown"  # bounty|vdp|unknown; legacy bbp remains readable
    icon_url: str = ""


class TaskIn(BaseModel):
    target: str
    objective: str = ""
    program_id: int | None = None
    provider: str | None = None
    model: str | None = None
    effort: str = ''


class RepeaterIn(BaseModel):
    method: str = "GET"
    url: str
    headers: dict[str, str] = {}
    body: str = ""
    task_id: int | None = None


class AddendumIn(BaseModel):
    finding_id: int
    kind: str = "comment"
    body_md: str = ""


class RoutineIn(BaseModel):
    name: str
    target: str
    objective: str = ""
    program_id: int | None = None
    interval_minutes: int = 1440
    enabled: bool = True


class ChatIn(BaseModel):
    thread: str = "general"
    mode: str = "general"
    content: str


class FindingStatusIn(BaseModel):
    status: str


def _prog_dict(p: Program) -> dict:
    return {"id": p.id, "name": p.name, "platform": p.platform, "handle": p.handle,
            "policy": p.policy, "scope_in": json.loads(p.scope_in or "[]"),
            "scope_out": json.loads(p.scope_out or "[]"),
            "program_type": getattr(p, "program_type", "unknown") or "unknown",
            "icon_url": getattr(p, "icon_url", "") or "",
            "scope_assets": json.loads(p.scope_assets_json or "[]"),
            "scope_sync": {
                "status": p.scope_sync_status or "never", "error": p.scope_sync_error or "",
                "warnings": json.loads(p.scope_sync_warnings or "[]"),
                "started_at": h1_sync.timestamp(p.scope_sync_started_at),
                "synced_at": h1_sync.timestamp(p.scope_synced_at),
                "asset_count": p.scope_asset_count or 0, "web_asset_count": p.scope_web_asset_count or 0,
                "attempts": p.scope_sync_attempts or 0,
            },
            "created_at": p.created_at.isoformat()}


def _task_dict(t: Task) -> dict:
    return {"id": t.id, "program_id": t.program_id, "target": t.target, "objective": t.objective,
            "status": t.status, "provider": t.provider, "model": t.model,
            "created_at": t.created_at.isoformat(),
            "finished_at": t.finished_at.isoformat() if t.finished_at else None}


def _scope_hash(scope_in: list[str], scope_out: list[str]) -> str:
    return hashlib.sha256(json.dumps([sorted(scope_in), sorted(scope_out)]).encode()).hexdigest()[:16]


# ---------- providers / settings ----------
@app.get("/api/providers")
async def providers():
    return PROVIDERS


@app.get("/api/settings")
async def get_settings():
    return await settings_store.get_all(mask=True)


@app.put("/api/settings")
async def put_settings(body: dict):
    await settings_store.set_many(body)
    return await settings_store.get_all(mask=True)


@app.post("/api/settings/test")
async def test_provider(body: dict):
    """Quick connectivity check for the configured/selected provider."""
    cfg = await settings_store.llm_config()
    try:
        llm = build_llm(cfg, provider=body.get("provider"), model=body.get("model"))
        out = await llm.complete("You are a connectivity probe.", "Reply with the single word: OK")
        return {"ok": True, "reply": out.strip()[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ---------- programs ----------
@app.get("/api/programs")
async def list_programs():
    async with SessionLocal() as s:
        rows = (await s.execute(select(Program).order_by(desc(Program.id)))).scalars().all()
        return [_prog_dict(p) for p in rows]


@app.post("/api/programs")
async def create_program(body: ProgramIn):
    async with SessionLocal() as s:
        p = Program(name=body.name, platform=body.platform, handle=body.handle, policy=body.policy,
                    scope_in=json.dumps(body.scope_in), scope_out=json.dumps(body.scope_out),
                    program_type=body.program_type, icon_url=body.icon_url,
                    scope_hash=_scope_hash(body.scope_in, body.scope_out))
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return _prog_dict(p)


@app.put("/api/programs/{pid}")
async def update_program(pid: int, body: ProgramIn):
    """Updating scope detects changes and fires a scope-change alert."""
    async with SessionLocal() as s:
        p = await s.get(Program, pid)
        if not p:
            raise HTTPException(404)
        new_hash = _scope_hash(body.scope_in, body.scope_out)
        changed = p.scope_hash and p.scope_hash != new_hash
        p.name, p.platform, p.handle, p.policy = body.name, body.platform, body.handle, body.policy
        p.scope_in = json.dumps(body.scope_in)
        p.scope_out = json.dumps(body.scope_out)
        p.program_type = body.program_type
        if body.icon_url:
            p.icon_url = body.icon_url
        p.scope_hash = new_hash
        await s.commit()
        await s.refresh(p)
    if changed:
        await notify.push("warn", f"Scope changed · {body.name}",
                          "The program scope was updated — review new assets.", None)
    return _prog_dict(p)


async def _enrich_h1_scopes(handles: list[str]) -> None:
    """Compatibility wrapper around the observable sync service."""
    await h1_sync.sync_scopes(handles)


@app.post("/api/programs/sync_h1")
async def sync_h1_programs():
    """Import program metadata, then queue a paced, visible scope refresh."""
    if h1_sync.IMPORT_LOCK.locked() or h1_sync.running():
        raise HTTPException(409, "A HackerOne sync is already running; inspect its status first.")
    async with h1_sync.IMPORT_LOCK:
        try:
            h1_sync.ensure_available()
        except h1_sync.SyncBusy as exc:
            raise HTTPException(409, str(exc))
        cfg = await settings_store.get_all()
        try:
            client = h1_sync.make_client(cfg)
        except HackerOneError as exc:
            raise HTTPException(400, str(exc))
        try:
            programs = await client.list_programs()
        except (HackerOneError, httpx.HTTPError) as exc:
            raise HTTPException(502, str(exc))
        finally:
            await h1_sync.close_client(client)
        summary = await h1_sync.import_programs(programs)
        handles = summary.pop("handles")
        job = await h1_sync.queue_scopes(handles) if handles else h1_sync.status()
        return {**summary, "scopes": "queued" if handles else "no programs", "sync": job}


@app.get("/api/programs/sync_h1/status")
async def h1_sync_status():
    return {**h1_sync.status(), "importing": h1_sync.IMPORT_LOCK.locked()}


@app.post("/api/programs/{pid}/sync_scope")
async def retry_h1_scope(pid: int):
    if h1_sync.IMPORT_LOCK.locked():
        raise HTTPException(409, "Program import is still running.")
    async with h1_sync.IMPORT_LOCK:
        async with SessionLocal() as session:
            program = await session.get(Program, pid)
            if not program:
                raise HTTPException(404, "Program not found")
            if program.platform != "hackerone" or not program.handle:
                raise HTTPException(400, "Scope refresh is available for HackerOne programs only.")
            handle = program.handle
        try:
            return await h1_sync.queue_scopes([handle])
        except h1_sync.SyncBusy as exc:
            raise HTTPException(409, str(exc))


@app.post("/api/programs/{pid}/scan")
async def scan_program(pid: int):
    """Workflow: launch a scan across the whole program scope (policy-aware)."""
    async with SessionLocal() as s:
        p = await s.get(Program, pid)
        if not p:
            raise HTTPException(404)
        scope_in = json.loads(p.scope_in or "[]")
        if not scope_in:
            raise HTTPException(400, "Program has no in-scope assets to scan.")
        if not await settings_store.provider_has_key():
            raise HTTPException(400, "No LLM API key set — add one in Settings first.")
        # Pick the first concrete URL/host asset as the entry target; policy guides the agent.
        target = next((a for a in scope_in if "*" not in a), scope_in[0]).replace("*.", "")
        if not target.startswith("http"):
            target = "https://" + target
        objective = (f"Full authorized assessment of HackerOne program '{p.name}'. "
                     f"In-scope: {', '.join(scope_in[:20])}. "
                     f"Follow the program policy. Prove impact minimally; no mass data extraction.")
        t = Task(target=target, objective=objective, program_id=p.id)
        s.add(t)
        await s.commit()
        await s.refresh(t)
        tid = t.id
    asyncio.create_task(start_task(tid))
    async with SessionLocal() as s:
        return _task_dict(await s.get(Task, tid))


@app.delete("/api/programs/{pid}")
async def delete_program(pid: int):
    async with SessionLocal() as s:
        p = await s.get(Program, pid)
        if p:
            await s.delete(p)
            await s.commit()
    return {"ok": True}


# ---------- tasks ----------
@app.get("/api/tasks")
async def list_tasks():
    async with SessionLocal() as s:
        rows = (await s.execute(select(Task).order_by(desc(Task.id)))).scalars().all()
        return [_task_dict(t) for t in rows]


@app.post("/api/tasks")
async def create_task(body: TaskIn):
    async with SessionLocal() as s:
        program = await s.get(Program, body.program_id) if body.program_id else None
        if body.program_id and not program: raise HTTPException(404,"Program not found")
        si, so = load_scope(program)
        try:
            check_url(body.target, si, so)
        except ScopeError as e:
            raise HTTPException(status_code=400, detail=f"Target rejected: {e}")
        if not await settings_store.provider_has_key(body.provider or None):
            prov = body.provider or (await settings_store.llm_config())["provider"]
            raise HTTPException(status_code=400,
                                detail=f"No API key set for provider '{prov}'. "
                                       "Open ⚙ Settings, add a key, and Test connection.")
        t = Task(target=body.target, objective=body.objective, program_id=body.program_id,
                 provider=body.provider or "", model=body.model or "", effort=body.effort)
        s.add(t)
        await s.commit()
        await s.refresh(t)
        tid = t.id
    asyncio.create_task(start_task(tid))
    async with SessionLocal() as s:
        return _task_dict(await s.get(Task, tid))


@app.get("/api/tasks/{tid}")
async def get_task(tid: int):
    async with SessionLocal() as s:
        t = await s.get(Task, tid)
        if not t:
            raise HTTPException(404)
        return _task_dict(t)


@app.post("/api/tasks/{tid}/stop")
async def stop_task(tid: int):
    request_stop(tid)
    return {"ok": True}


@app.delete("/api/tasks/{tid}")
async def delete_task(tid: int):
    """Delete a task and all its child rows (stops it first if running)."""
    request_stop(tid)
    from .agent.runner import ACTIVE
    if tid in ACTIVE: raise HTTPException(409,'Scan is stopping; retry after it stops')
    from sqlalchemy import delete as sqldelete
    from .db import (Idea, GraphNode, GraphEdge, Asset, TokenUsage, Memory, EndpointSpec)
    async with SessionLocal() as s:
        t = await s.get(Task, tid)
        if not t:
            raise HTTPException(404)
        for model in (Event, Endpoint, Finding, Lead, SubAgent, RepeaterRequest, Intel,
                      Idea, GraphNode, GraphEdge, Asset, TokenUsage, EndpointSpec):
            await s.execute(sqldelete(model).where(model.task_id == tid))
        # addenda reference findings of this task — clear by task_id too
        await s.execute(sqldelete(Addendum).where(Addendum.task_id == tid))
        await s.execute(sqldelete(Memory).where(Memory.task_id == tid))
        await s.delete(t)
        await s.commit()
    return {"ok": True}


@app.get("/api/tasks/{tid}/events")
async def task_events(tid: int, kind: str | None = None, limit: int = 1000):
    async with SessionLocal() as s:
        q = select(Event).where(Event.task_id == tid)
        if kind:
            q = q.where(Event.kind == kind)
        rows = (await s.execute(q.order_by(desc(Event.id)).limit(max(1,min(limit,5000))))).scalars().all()
        rows=list(reversed(rows))
        return [{"id": e.id, "kind": e.kind, "title": e.title, "body": e.body,
                 "created_at": e.created_at.isoformat()} for e in rows]


@app.get("/api/tasks/{tid}/endpoints")
async def task_endpoints(tid: int):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Endpoint).where(Endpoint.task_id == tid).order_by(desc(Endpoint.id)))).scalars().all()
        return [{"id": e.id, "method": e.method, "url": e.url, "source": e.source,
                 "notes": e.notes, "created_at": e.created_at.isoformat()} for e in rows]


@app.get("/api/tasks/{tid}/leads")
async def task_leads(tid: int):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Lead).where(Lead.task_id == tid).order_by(desc(Lead.id)))).scalars().all()
        return [{"id": l.id, "title": l.title, "url": l.url, "severity_guess": l.severity_guess,
                 "rationale": l.rationale, "status": l.status, "created_at": l.created_at.isoformat()} for l in rows]


@app.get("/api/tasks/{tid}/subagents")
async def task_subagents(tid: int):
    async with SessionLocal() as s:
        rows = (await s.execute(select(SubAgent).where(SubAgent.task_id == tid).order_by(desc(SubAgent.id)))).scalars().all()
        return [{"id": a.id, "name": a.name, "role": a.role, "status": a.status,
                 "summary": a.summary, "created_at": a.created_at.isoformat()} for a in rows]


@app.get("/api/tasks/{tid}/findings")
async def task_findings(tid: int):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Finding).where(Finding.task_id == tid).order_by(desc(Finding.id)))).scalars().all()
        return [_finding_dict(f) for f in rows]


def _finding_dict(f: Finding) -> dict:
    return {"id": f.id, "task_id": f.task_id, "title": f.title, "severity": f.severity, "status": f.status,
            "summary": f.summary, "report_md": f.report_md, "relation": f.relation,
            "related_to": f.related_to, "evidence_ids": json.loads(f.evidence_ids or "[]"), "confidence": f.confidence, "verified": f.verified,
            "evidence_score": f.evidence_score, "verdict": f.verdict, "weakness": f.weakness,
            "h1_report_id": f.h1_report_id,
            "h1_report_url": f.h1_report_url,
            "submitted_at": f.submitted_at.isoformat() if f.submitted_at else None,
            "created_at": f.created_at.isoformat()}


@app.get("/api/tasks/{tid}/repeater")
async def task_repeater(tid: int):
    async with SessionLocal() as s:
        rows = (await s.execute(select(RepeaterRequest).where(RepeaterRequest.task_id == tid).order_by(desc(RepeaterRequest.id)))).scalars().all()
        return [_repeater_dict(r) for r in rows]


def _repeater_dict(r: RepeaterRequest) -> dict:
    return {"id": r.id, "task_id": r.task_id, "method": r.method, "url": r.url,
            "request_headers": json.loads(r.request_headers or "{}"), "request_body": r.request_body,
            "status_code": r.status_code, "response_headers": json.loads(r.response_headers or "{}"),
            "response_body": r.response_body, "duration_ms": r.duration_ms, "created_at": r.created_at.isoformat()}


# ---------- findings: status + addenda ----------
@app.put("/api/findings/{fid}/status")
async def set_finding_status(fid: int, body: FindingStatusIn):
    async with SessionLocal() as s:
        f = await s.get(Finding, fid)
        if not f:
            raise HTTPException(404)
        f.status = body.status
        await s.commit()
        return _finding_dict(f)


@app.get("/api/findings/{fid}/addenda")
async def list_addenda(fid: int):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Addendum).where(Addendum.finding_id == fid).order_by(Addendum.id))).scalars().all()
        return [{"id": a.id, "kind": a.kind, "body_md": a.body_md, "created_at": a.created_at.isoformat()} for a in rows]


@app.post("/api/addenda")
async def create_addendum(body: AddendumIn):
    async with SessionLocal() as s:
        f = await s.get(Finding, body.finding_id)
        if not f:
            raise HTTPException(404, "finding not found")
        a = Addendum(finding_id=body.finding_id, task_id=f.task_id, kind=body.kind, body_md=body.body_md)
        s.add(a)
        await s.commit()
        await s.refresh(a)
        aid = a.id
    return {"id": aid, "kind": body.kind, "body_md": body.body_md}


# ---------- verification + HackerOne reporting ----------
@app.post("/api/findings/{fid}/verify")
async def verify_finding_ep(fid: int):
    if not await settings_store.provider_has_key():
        raise HTTPException(400, "No LLM API key set — add one in Settings to run verification.")
    try:
        return await verify.verify_finding(fid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e))


@app.post("/api/findings/{fid}/submit")
async def submit_finding_ep(fid: int):
    try:
        return await reporting.submit_finding(fid)
    except HackerOneError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, str(e))


@app.get("/api/findings/{fid}/h1_payload")
async def h1_payload(fid: int):
    """The exact report text to copy-paste into HackerOne."""
    async with SessionLocal() as s:
        f = await s.get(Finding, fid)
        if not f:
            raise HTTPException(404)
        task = await s.get(Task, f.task_id)
        program = await s.get(Program, task.program_id) if task and task.program_id else None
    p = reporting.build_report_payload(f, program)
    return {**p, "report_url": f.h1_report_url, "report_id": f.h1_report_id}


@app.post("/api/findings/{fid}/telegram")
async def finding_telegram(fid: int):
    ok = await telegram_bot.send_report(fid)
    if not ok:
        raise HTTPException(400, "Telegram not configured (set bot token + chat id in Settings).")
    return {"ok": True}


@app.get("/api/addenda/{aid}/payload")
async def addendum_payload(aid: int):
    async with SessionLocal() as s:
        a = await s.get(Addendum, aid)
        if not a:
            raise HTTPException(404)
        f = await s.get(Finding, a.finding_id)
    return {"text": reporting.build_comment_payload(a, f),
            "report_url": f.h1_report_url,
            "report_id": f.h1_report_id}


@app.post("/api/addenda/{aid}/telegram")
async def addendum_telegram(aid: int):
    ok = await telegram_bot.send_comment(aid)
    if not ok:
        raise HTTPException(400, "Telegram not configured.")
    await reporting.mark_addendum_posted(aid)
    return {"ok": True}


# ---------- manual repeater ----------
@app.post("/api/repeater/send")
async def repeater_send(body: RepeaterIn):
    si, so = [], []
    if body.task_id:
        async with SessionLocal() as s:
            t = await s.get(Task, body.task_id)
            if not t: raise HTTPException(404)
            program = await s.get(Program, t.program_id) if t.program_id else None
            si, so = load_scope(program)
    try:
        check_url(body.url, si, so)
    except ScopeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token=policy.set((si,so))
    try:
        res = await http_client.send_request(body.method, body.url, headers=body.headers, body=body.body)
    finally: policy.reset(token)
    async with SessionLocal() as s:
        rr = RepeaterRequest(task_id=body.task_id, method=body.method.upper(), url=body.url,
                             request_headers=json.dumps(body.headers), request_body=body.body,
                             status_code=res["status_code"], response_headers=json.dumps(res["response_headers"]),
                             response_body=res["response_body"], duration_ms=res["duration_ms"])
        s.add(rr)
        await s.commit()
        await s.refresh(rr)
        return _repeater_dict(rr)


# ---------- routines ----------
@app.get("/api/routines")
async def list_routines():
    async with SessionLocal() as s:
        rows = (await s.execute(select(Routine).order_by(desc(Routine.id)))).scalars().all()
        return [_routine_dict(r) for r in rows]


def _routine_dict(r: Routine) -> dict:
    return {"id": r.id, "name": r.name, "target": r.target, "objective": r.objective,
            "program_id": r.program_id, "interval_minutes": r.interval_minutes, "enabled": r.enabled,
            "last_run": r.last_run.isoformat() if r.last_run else None, "created_at": r.created_at.isoformat()}


@app.post("/api/routines")
async def create_routine(body: RoutineIn):
    async with SessionLocal() as s:
        r = Routine(name=body.name, target=body.target, objective=body.objective,
                    program_id=body.program_id, interval_minutes=body.interval_minutes, enabled=body.enabled)
        s.add(r)
        await s.commit()
        await s.refresh(r)
        return _routine_dict(r)


@app.put("/api/routines/{rid}/toggle")
async def toggle_routine(rid: int):
    async with SessionLocal() as s:
        r = await s.get(Routine, rid)
        if not r:
            raise HTTPException(404)
        r.enabled = not r.enabled
        await s.commit()
        return _routine_dict(r)


@app.delete("/api/routines/{rid}")
async def delete_routine(rid: int):
    async with SessionLocal() as s:
        r = await s.get(Routine, rid)
        if r:
            await s.delete(r)
            await s.commit()
    return {"ok": True}


# ---------- notifications ----------
@app.get("/api/notifications")
async def list_notifications(limit: int = 50):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Notification).order_by(desc(Notification.id)).limit(limit))).scalars().all()
        return [{"id": n.id, "task_id": n.task_id, "level": n.level, "title": n.title,
                 "body": n.body, "read": n.read, "created_at": n.created_at.isoformat()} for n in rows]


@app.post("/api/notifications/read")
async def mark_read():
    async with SessionLocal() as s:
        await s.execute(update(Notification).values(read=True))
        await s.commit()
    return {"ok": True}


# ---------- chat (general / triage / retest) ----------
@app.get("/api/chat/{thread}")
async def chat_history(thread: str):
    from .db import ChatMessage
    async with SessionLocal() as s:
        rows = (await s.execute(select(ChatMessage).where(ChatMessage.thread == thread).order_by(ChatMessage.id))).scalars().all()
        return [{"id": m.id, "role": m.role, "mode": m.mode, "content": m.content,
                 "created_at": m.created_at.isoformat()} for m in rows]


@app.post("/api/chat")
async def chat_send(body: ChatIn):
    if not await settings_store.provider_has_key():
        cfg = await settings_store.llm_config()
        raise HTTPException(400, f"No API key set for provider '{cfg['provider']}'. "
                                 "Open ⚙ Settings, add a key, and Test connection.")
    try:
        return await chat.reply(body.thread, body.mode, body.content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Provider error: {e}")


# ---------- file browser ----------
@app.get("/api/files")
async def list_files():
    d = settings.artifacts_dir
    os.makedirs(d, exist_ok=True)
    out = []
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if os.path.isfile(path):
            st = os.stat(path)
            out.append({"name": name, "size": st.st_size, "modified": time.ctime(st.st_mtime),
                        "url": f"/api/artifacts/{name}"})
    return out


@app.get("/api/artifacts/{fname}")
async def artifact(fname: str):
    safe = os.path.basename(fname)
    path = os.path.join(settings.artifacts_dir, safe)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path)


# ---------- overview stats (ExecHawk-style dashboard) ----------
@app.get("/api/tasks/{tid}/overview")
async def task_overview(tid: int):
    from .tools import workspace as ws
    async with SessionLocal() as s:
        t = await s.get(Task, tid)
        if not t:
            raise HTTPException(404)
        program_id = t.program_id
        host = knowledge.host_of(t.target)

        async def count(model, *conds):
            return await s.scalar(select(func.count()).select_from(model).where(*conds))

        endpoints = await count(Endpoint, Endpoint.task_id == tid)
        findings = (await s.execute(select(Finding).where(Finding.task_id == tid))).scalars().all()
        leads = await count(Lead, Lead.task_id == tid)
        intel = await count(Intel, Intel.task_id == tid)
        subagents = await count(SubAgent, SubAgent.task_id == tid)
        repeater = await count(RepeaterRequest, RepeaterRequest.task_id == tid)
        from .db import Idea as _Idea, GraphNode as _GN, GraphEdge as _GE, Asset as _Asset
        ideas = await count(_Idea, _Idea.task_id == tid, _Idea.status.in_(["queued", "chasing"]))
        graph_nodes = await count(_GN, _GN.task_id == tid)
        graph_edges = await count(_GE, _GE.task_id == tid)
        assets = await count(_Asset, _Asset.task_id == tid)
        pending_add = await count(Addendum, Addendum.task_id == tid, Addendum.posted == False)  # noqa: E712
        # latest plan
        plan_ev = (await s.execute(select(Event).where(Event.task_id == tid, Event.kind == "plan")
                                   .order_by(desc(Event.id)).limit(1))).scalars().first()
        plan_steps = []
        if plan_ev:
            try:
                plan_steps = json.loads(plan_ev.body).get("steps", [])
            except Exception:
                plan_steps = []
        # memory scoped to this host/program
        mem_scopes = ["global"] + ([f"host:{host}"] if host else []) + ([f"program:{program_id}"] if program_id else [])
        memory = await s.scalar(select(func.count()).select_from(Memory).where(Memory.scope.in_(mem_scopes)))

    reported = [f for f in findings if f.h1_report_id]
    confirmed = [f for f in findings if f.verified]
    by_sev = {}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    files = ws.list_files(tid)
    duration = None
    if t.finished_at:
        duration = int((t.finished_at - t.created_at).total_seconds())

    budget = float(await settings_store.get("budget_usd_per_task", "0") or 0)
    return {
        "status": t.status, "target": t.target, "model": f"{t.provider}/{t.model}",
        "duration_seconds": duration,
        "plan": {"total": len(plan_steps), "steps": plan_steps},
        "endpoints": endpoints, "leads": leads, "intel": intel, "subagents": subagents,
        "ideas": ideas, "repeater": repeater, "memory": memory,
        "assets": assets, "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "findings": {"total": len(findings), "confirmed": len(confirmed), "by_severity": by_sev},
        "reports": {"submitted": len(reported)},
        "pending_addenda": pending_add,
        "files": len(files),
        "cost": {"tokens_in": t.tokens_in or 0, "tokens_out": t.tokens_out or 0,
                 "price_unknown": t.unknown_price, "usd": round(t.cost_usd or 0, 6), "steps": t.steps or 0, "budget_usd": budget},
    }


@app.get("/api/tasks/{tid}/workspace")
async def task_workspace(tid: int):
    from .tools import workspace as ws
    files = ws.list_files(tid)
    import datetime as _dt
    return [{"path": f["path"], "size": f["size"],
             "modified": _dt.datetime.fromtimestamp(f["modified"]).isoformat()} for f in files]


@app.get("/api/tasks/{tid}/workspace/file")
async def task_workspace_file(tid: int, path: str):
    from .tools import workspace as ws
    return {"path": path, "content": ws.read_file(tid, path, max_chars=100000)}


# ---------- test identities (authenticated IDOR/BOLA testing) ----------
@app.get("/api/identities")
async def list_identities(program_id: int | None = None):
    from .db import Identity
    async with SessionLocal() as s:
        q = select(Identity)
        if program_id is not None:
            q = q.where(Identity.program_id == program_id)
        rows = (await s.execute(q.order_by(desc(Identity.id)))).scalars().all()
        # mask the secret material
        return [{"id": r.id, "program_id": r.program_id, "label": r.label, "auth_type": r.auth_type,
                 "header_name": r.header_name, "username": r.username, "note": r.note,
                 "source": r.source, "has_material": bool(r.material),
                 "created_at": r.created_at.isoformat()} for r in rows]


@app.post("/api/identities")
async def create_identity(body: dict):
    from .db import Identity
    async with SessionLocal() as s:
        idn = Identity(program_id=body.get("program_id"), label=body.get("label", "userX"),
                       auth_type=body.get("auth_type", "cookie"), header_name=body.get("header_name", ""),
                       material=body.get("material", ""), username=body.get("username", ""),
                       note=body.get("note", ""), source="operator")
        s.add(idn)
        await s.commit()
        await s.refresh(idn)
        return {"id": idn.id, "label": idn.label}


@app.delete("/api/identities/{iid}")
async def delete_identity(iid: int):
    from .db import Identity
    async with SessionLocal() as s:
        r = await s.get(Identity, iid)
        if r:
            await s.delete(r)
            await s.commit()
    return {"ok": True}


# ---------- knowledge: memory / skills / intel ----------
@app.get("/api/memory")
async def list_memory(limit: int = 200):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Memory).order_by(desc(Memory.id)).limit(limit))).scalars().all()
        return [{"id": m.id, "scope": m.scope, "category": m.category, "content": m.content,
                 "created_at": m.created_at.isoformat()} for m in rows]


@app.delete("/api/memory/{mid}")
async def delete_memory(mid: int):
    async with SessionLocal() as s:
        m = await s.get(Memory, mid)
        if m:
            await s.delete(m)
            await s.commit()
    return {"ok": True}


@app.get("/api/skills")
async def list_skills():
    async with SessionLocal() as s:
        rows = (await s.execute(select(Skill).order_by(Skill.category, Skill.name))).scalars().all()
        return [{"name": sk.name, "category": sk.category, "summary": sk.summary,
                 "playbook": sk.playbook, "enabled": sk.enabled, "is_custom": sk.is_custom,
                 "tags": sk.tags, "source": sk.source} for sk in rows]


@app.post("/api/skills/toggle")
@app.put("/api/skills/{name:path}/toggle")
async def toggle_skill(name: str):
    async with SessionLocal() as s:
        sk = await s.get(Skill, name)
        if not sk:
            raise HTTPException(404)
        sk.enabled = not sk.enabled
        await s.commit()
        return {"name": sk.name, "enabled": sk.enabled}


def _parse_skill_file(filename: str, content: str) -> dict:
    """Parse an uploaded skill (.md / .json / .yaml). Returns normalized fields."""
    name = filename.rsplit(".", 1)[0]
    data = {"name": name, "category": "custom", "summary": "", "playbook": content,
            "tags": "", "bypass_table_md": "", "references_md": "", "payloads_json": ""}
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "json":
        try:
            j = json.loads(content)
            data["name"] = j.get("name", name)
            data["category"] = j.get("category", "custom")
            data["summary"] = j.get("summary", j.get("description", ""))
            data["playbook"] = j.get("playbook", j.get("instructions", j.get("steps", content)))
            if isinstance(data["playbook"], list):
                data["playbook"] = "\n".join(str(x) for x in data["playbook"])
            data["tags"] = ",".join(j.get("tags", [])) if isinstance(j.get("tags"), list) else j.get("tags", "")
            data["references_md"] = j.get("references", "")
            data["payloads_json"] = json.dumps(j.get("payloads", [])) if j.get("payloads") else ""
        except json.JSONDecodeError:
            pass
    elif ext in ("yaml", "yml"):
        # lightweight YAML: key: value lines, rest is playbook body
        lines = content.splitlines()
        body = []
        for ln in lines:
            if ln.lower().startswith("name:"):
                data["name"] = ln.split(":", 1)[1].strip()
            elif ln.lower().startswith("category:"):
                data["category"] = ln.split(":", 1)[1].strip()
            elif ln.lower().startswith("summary:") or ln.lower().startswith("description:"):
                data["summary"] = ln.split(":", 1)[1].strip()
            elif ln.lower().startswith("tags:"):
                data["tags"] = ln.split(":", 1)[1].strip()
            else:
                body.append(ln)
        data["playbook"] = "\n".join(body).strip() or content
    else:  # markdown / txt
        first = content.strip().splitlines()[0] if content.strip() else name
        if first.startswith("#"):
            data["name"] = first.lstrip("# ").strip()[:120] or name
            data["summary"] = ""
    return data


@app.post("/api/skills/upload")
async def upload_skills(body: dict):
    """Import one or more operator-authored skills. Body: {files: [{filename, content}]}.
    Enabled custom skills are injected into the agent prompt like built-ins."""
    files = body.get("files", [])
    if not files:
        raise HTTPException(400, "No files provided.")
    added, replaced, skipped = 0, 0, 0
    names = []
    async with SessionLocal() as s:
        for fobj in files[:50]:
            fn = (fobj.get("filename") or "skill.md")[:200]
            content = (fobj.get("content") or "")[:100_000]  # size cap
            if not content.strip():
                skipped += 1
                continue
            d = _parse_skill_file(fn, content)
            name = d["name"]
            existing = await s.get(Skill, name)
            # If the name collides with a BUILT-IN, give the custom skill a distinct name
            # instead of silently skipping it, so the user's upload always lands.
            if existing and not existing.is_custom:
                name = f"{name} (custom)"
                existing = await s.get(Skill, name)
            if existing:  # existing custom skill → update in place
                existing.category = d["category"]; existing.summary = d["summary"][:300]
                existing.playbook = d["playbook"]; existing.tags = d["tags"][:300]
                existing.filename = fn; existing.references_md = d["references_md"]
                existing.payloads_json = d["payloads_json"]; existing.enabled = True
                replaced += 1
            else:
                s.add(Skill(name=name, category=d["category"], summary=d["summary"][:300],
                            playbook=d["playbook"], tags=d["tags"][:300], references_md=d["references_md"],
                            payloads_json=d["payloads_json"], enabled=True, is_custom=True,
                            source="custom", filename=fn))
                added += 1
            names.append(name)
        await s.commit()
    return {"added": added, "replaced": replaced, "skipped": skipped, "names": names}


@app.put("/api/skills")
@app.put("/api/skills/{name:path}")
async def edit_skill(name: str, body: dict):
    async with SessionLocal() as s:
        sk = await s.get(Skill, name)
        if not sk:
            raise HTTPException(404)
        if "playbook" in body:
            sk.playbook = body["playbook"]
        if "summary" in body:
            sk.summary = body["summary"][:300]
        if "category" in body and isinstance(body["category"], str) and body["category"].strip():
            sk.category = body["category"].strip()[:60]
        if "references_md" in body:
            sk.references_md = body["references_md"]
        await s.commit()
        return {"name": sk.name, "ok": True}


@app.delete("/api/skills")
@app.delete("/api/skills/{name:path}")
async def delete_skill(name: str):
    async with SessionLocal() as s:
        sk = await s.get(Skill, name)
        if not sk:
            raise HTTPException(404)
        await s.delete(sk)
        # Remember a deleted built-in so startup re-seeding does not resurrect it.
        if not sk.is_custom:
            from .skills_seed import mark_builtin_deleted
            await mark_builtin_deleted(s, name)
        await s.commit()
    return {"ok": True}


@app.get("/api/tasks/{tid}/ideas")
async def task_ideas(tid: int):
    from .db import Idea
    async with SessionLocal() as s:
        rows = (await s.execute(select(Idea).where(Idea.task_id == tid)
                                .order_by(Idea.priority, desc(Idea.id)))).scalars().all()
        return [{"id": i.id, "text": i.text, "priority": i.priority, "status": i.status,
                 "created_at": i.created_at.isoformat()} for i in rows]


@app.put("/api/ideas/{iid}")
async def update_idea(iid: int, body: dict):
    from .db import Idea
    async with SessionLocal() as s:
        i = await s.get(Idea, iid)
        if not i:
            raise HTTPException(404)
        if "priority" in body:
            i.priority = int(body["priority"])
        if "status" in body:
            i.status = body["status"]
        await s.commit()
        return {"id": i.id, "priority": i.priority, "status": i.status}


@app.get("/api/tasks/{tid}/health")
async def task_health(tid: int):
    """Aggregate tool errors / scope blocks / warnings from the event log."""
    async with SessionLocal() as s:
        rows = (await s.execute(select(Event).where(Event.task_id == tid, Event.kind == "log"))).scalars().all()
    tool_errors, scope_blocks, warnings = {}, 0, []
    for e in rows:
        try:
            body = json.loads(e.body) if e.body else {}
        except Exception:
            body = {}
        if "⛔ Scope block" in e.title:
            scope_blocks += 1
        elif e.title.startswith("Tool error in "):
            name = e.title.replace("Tool error in ", "").split(":")[0].strip()
            tool_errors[name] = tool_errors.get(name, 0) + 1
        elif "⚠️" in e.title or (isinstance(body, dict) and body.get("error")):
            warnings.append(e.title[:120])
    return {"tool_errors": tool_errors, "scope_blocks": scope_blocks,
            "warnings": warnings[:20], "total_errors": sum(tool_errors.values())}


@app.get("/api/tasks/{tid}/graph")
async def task_graph(tid: int):
    from .db import GraphNode, GraphEdge
    async with SessionLocal() as s:
        nodes = (await s.execute(select(GraphNode).where(GraphNode.task_id == tid))).scalars().all()
        edges = (await s.execute(select(GraphEdge).where(GraphEdge.task_id == tid))).scalars().all()
        return {
            "nodes": [{"id": n.id, "kind": n.kind, "key": n.key, "label": n.label} for n in nodes],
            "edges": [{"src": e.src_id, "dst": e.dst_id, "rel": e.rel} for e in edges],
        }


@app.get("/api/tasks/{tid}/assets")
async def task_assets(tid: int):
    from .db import Asset
    async with SessionLocal() as s:
        rows = (await s.execute(select(Asset).where(Asset.task_id == tid).order_by(desc(Asset.id)))).scalars().all()
        return [{"id": a.id, "host": a.host, "kind": a.kind, "status": a.status,
                 "tech": json.loads(a.tech or "[]"), "source": a.source,
                 "created_at": a.created_at.isoformat()} for a in rows]


@app.post("/api/tasks/{tid}/instruct")
async def instruct_task(tid: int, body: dict):
    from .agent.runner import instruct
    ok = instruct(tid, body.get("text", ""))
    if not ok:
        raise HTTPException(400, "Task is not running — can't queue a message.")
    return {"ok": True}


@app.get("/api/tasks/{tid}/intel")
async def task_intel(tid: int):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Intel).where(Intel.task_id == tid).order_by(desc(Intel.id)))).scalars().all()
        return [{"id": i.id, "kind": i.kind, "value": i.value, "source": i.source,
                 "created_at": i.created_at.isoformat()} for i in rows]


@app.get("/api/addenda/pending")
async def pending_addenda():
    """Draft comments not yet posted to a report — for the Pending Addenda panel."""
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Addendum).where(Addendum.posted == False).order_by(desc(Addendum.id))  # noqa: E712
        )).scalars().all()
        out = []
        for a in rows:
            f = await s.get(Finding, a.finding_id)
            out.append({"id": a.id, "finding_id": a.finding_id, "kind": a.kind, "body_md": a.body_md,
                        "created_at": a.created_at.isoformat(),
                        "finding_title": f.title if f else "", "severity": f.severity if f else "",
                        "h1_report_id": f.h1_report_id if f else "", "h1_report_url": f.h1_report_url if f else ""})
        return out


@app.put("/api/addenda/{aid}")
async def edit_addendum(aid: int, body: dict):
    async with SessionLocal() as s:
        a = await s.get(Addendum, aid)
        if not a:
            raise HTTPException(404)
        if "body_md" in body:
            a.body_md = body["body_md"]
        if body.get("discard"):
            await s.delete(a)
            await s.commit()
            return {"discarded": True}
        await s.commit()
        return {"id": a.id, "body_md": a.body_md}


# ---------- live stream ----------
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    from .auth import allowed
    if not allowed(websocket): await websocket.close(code=1008);return
    await websocket.accept()
    q = bus.subscribe()
    async def send():
        while True: await websocket.send_json(await q.get())
    async def receive():
        while True: await websocket.receive_text()
    tasks=[asyncio.create_task(send()),asyncio.create_task(receive())]
    try:
        await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        bus.unsubscribe(q)


@app.get("/api/tools")
async def recon_tools_ep():
    from . import recon_tools
    return recon_tools.detect()


@app.get("/api/health")
async def health():
    from . import recon_tools
    cfg = await settings_store.llm_config()
    shell_on = (await settings_store.get("enable_shell", "false")) == "true"
    async with SessionLocal() as s:
        skills = await s.scalar(select(func.count()).select_from(Skill).where(Skill.enabled == True))  # noqa: E712
    return {"ok": True, "provider": cfg["provider"], "model": cfg["model"],
            "has_key": await settings_store.provider_has_key(cfg["provider"]),
            "scope_enforced": settings.scope_enforced,
            "shell": shell_on,
            "tools": len(__import__("app.agent.tools",fromlist=["TOOLS"]).TOOLS)+2+int(shell_on),
            "skills": skills or 0}

@app.post('/api/tasks/{tid}/resume')
async def resume(tid:int):
    from .agent.runner import ACTIVE
    if tid in ACTIVE: raise HTTPException(409,'Already active')
    async with SessionLocal() as session:
        t=await session.get(Task,tid)
        if not t: raise HTTPException(404)
        if t.status in ('queued','running'): raise HTTPException(409)
        p=await session.get(Program,t.program_id) if t.program_id else None
        check_url(t.target,*load_scope(p))
        result=await session.execute(update(Task).where(Task.id==tid,Task.status==t.status).values(status='queued',finished_at=None,stop_reason=''))
        if result.rowcount!=1: raise HTTPException(409)
        await session.commit()
    asyncio.create_task(start_task(tid))
    return {'ok':True}

@app.post('/api/tasks/{tid}/effort')
async def effort(tid:int,body:dict):
    from .agent.runner import RUNS
    value=body.get('effort','high')
    if value not in ('low','medium','high','xhigh','max'): raise HTTPException(400)
    async with SessionLocal() as session:
        t=await session.get(Task,tid)
        if not t: raise HTTPException(404)
        t.effort=value;await session.commit()
    if tid in RUNS: RUNS[tid].effort=value
    return {'ok':True}

@app.get('/api/tasks/{tid}/specs')
async def specs(tid:int):
    from .db import EndpointSpec
    async with SessionLocal() as session:
        rows=(await session.execute(select(EndpointSpec).where(EndpointSpec.task_id==tid))).scalars().all()
    return [{'id':r.id,'url':r.url,'method':r.method,'shape':json.loads(r.shape)} for r in rows]

@app.put('/api/findings/{fid}/evidence')
async def link_evidence(fid:int,body:dict):
    async with SessionLocal() as session:
        f=await session.get(Finding,fid)
        if not f: raise HTTPException(404)
        ids=list(dict.fromkeys(int(i) for i in body.get('evidence_ids',[])))
        if len(ids)>20: raise HTTPException(400)
        for rid in ids:
            r=await session.get(RepeaterRequest,rid)
            if not r or r.task_id!=f.task_id: raise HTTPException(400,'Evidence must belong to this scan')
        f.evidence_ids=json.dumps(ids);f.verified=False;f.confidence=0;f.evidence_score=0
        f.verification_revision = (f.verification_revision or 0) + 1
        await session.commit()
    return {'ok':True}

@app.get('/api/mcp/servers')
async def mcp_status():
    from .mcp_client import STATUS
    return STATUS

@app.post('/api/mcp/test')
async def mcp_test():
    from .mcp_client import Manager
    manager=Manager()
    try:
        await manager.connect(await settings_store.get_all())
        return {'tools':manager.tools}
    finally: await manager.close()
