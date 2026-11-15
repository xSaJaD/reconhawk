from __future__ import annotations

import json
import re

from sqlalchemy import select, desc, update

from .db import SessionLocal, Finding, RepeaterRequest
from . import settings_store
from .llm import build_llm
from . import redact

VERIFY_SYSTEM = """You are a senior bug-bounty triager doing a SKEPTICAL review of a candidate finding \
before it is reported. Your job is to catch false positives. A report that gets marked N/A or \
Not-Applicable damages the researcher's reputation, so be rigorous.

Judge ONLY on the evidence provided. Consider:
- Is the vulnerability actually demonstrated, or merely asserted?
- Is there a concrete, reproducible PoC (request/response proving impact)?
- For IDOR/BOLA: is cross-user/cross-tenant access actually shown (e.g. accessing another
  object you shouldn't), with the minimum necessary proof — NOT mass data extraction?
- Could this be expected behavior, a honeypot, or a self-only action?

Respond with STRICT JSON only, no prose:
{"confidence": <0-100 integer>, "verdict": "confirmed"|"needs_more_evidence"|"false_positive",
 "weakness": "<short class e.g. IDOR, BOLA, XSS, SSRF, Auth Bypass>",
 "reasons": "<2-4 sentences>",
 "missing_evidence": "<what PoC would raise confidence, or empty>",
 "checks": {"reproducible": false, "impact_proven": false, "redacted": false}}

Only use confidence >= 80 when a concrete reproducible PoC proves real impact."""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _finding_snapshot(f):
    return {k: getattr(f, k) for k in ('task_id', 'title', 'severity', 'summary', 'report_md', 'evidence_ids', 'verification_revision')}


def _request_snapshot(rows):
    return [(r.id, r.task_id, r.method, r.url, r.request_headers, r.request_body,
             r.status_code, r.response_headers, r.response_body) for r in rows]


def _headers(raw):
    try:
        parsed = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}
    return redact.headers(parsed) if isinstance(parsed, dict) else {}


async def verify_finding(finding_id: int, on_usage=None) -> dict:
    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        if not f:
            raise ValueError("finding not found")
        initial = _finding_snapshot(f)
        ids=json.loads(f.evidence_ids or '[]')
        reqs = (await s.execute(
            select(RepeaterRequest).where(RepeaterRequest.task_id == f.task_id, RepeaterRequest.id.in_(ids))
            .order_by(desc(RepeaterRequest.id)).limit(20)
        )).scalars().all()
        conditions = [Finding.id == finding_id] + [getattr(Finding, k) == v for k, v in initial.items()]
        result_begin = await s.execute(update(Finding).where(*conditions).values(
            verification_revision=Finding.verification_revision + 1).returning(Finding.verification_revision))
        revision = result_begin.scalar_one_or_none()
        if revision is None:
            raise ValueError('Finding changed before verification; verify again.')
        await s.commit()

    original = {**initial, 'verification_revision': revision}
    original_requests = _request_snapshot(reqs)
    evidence = "\n".join(
        f"- Request #{r.id}: {r.method} {r.url} -> {r.status_code} ({r.duration_ms}ms)\n"
        f"  request_headers (credentials redacted; identity labels retained): {_headers(r.request_headers)}\n"
        f"  request_body excerpt: {redact.text(r.request_body or '')[:1000]}\n"
        f"  response_headers: {_headers(r.response_headers)}\n"
        f"  response_body excerpt: {redact.text(r.response_body or '')[:1600]}\n"
        f"  excerpt_truncated: {len(r.request_body or '') > 1000 or len(r.response_body or '') > 1600}"
        for r in reqs
    ) or "(no HTTP evidence captured)"

    prompt = (f"FINDING\nTitle: {f.title}\nSeverity: {f.severity}\nSummary: {f.summary}\n\n"
              f"REPORT (markdown):\n{f.report_md[:6000]}\n\n"
              f"HTTP EVIDENCE (explicitly linked excerpts; not independent reproduction):\n{evidence[:24000]}")

    cfg = await settings_store.llm_config()
    from .db import Task
    raw_cfg=await settings_store.get_all()
    async with SessionLocal() as session: task=await session.get(Task,f.task_id)
    cfg['worker_provider']=raw_cfg.get('worker_provider') or (task.provider if task else '') or cfg['provider']
    cfg['worker_model']=raw_cfg.get('worker_model') or (task.model if task else '') or cfg['model']
    # Verification is a light step — route it to the cheaper worker model when configured.
    llm = build_llm(cfg, provider=cfg["worker_provider"], model=cfg["worker_model"])
    from .usage import record
    raw = await llm.complete(VERIFY_SYSTEM, prompt,on_usage=on_usage or (lambda tin,tout:record(f.task_id,cfg['worker_provider'],cfg['worker_model'],tin,tout)))
    result = _extract_json(raw)

    try:
        confidence = max(0, min(100, int(result.get("confidence", 0) or 0)))
    except (TypeError, ValueError):
        confidence = 0
    verdict = result.get("verdict", "needs_more_evidence")
    weakness = result.get("weakness", "")
    reasons = result.get("reasons", "")
    missing = result.get("missing_evidence", "")

    try:
        threshold = int(await settings_store.get("verify_threshold", "80"))
    except ValueError:
        threshold = 80

    # Evidence quality is based on linked pairs and the structured verifier rubric.
    checks=result.get('checks',{})
    checks = checks if isinstance(checks, dict) else {}
    has_pairs=bool(reqs) and len(reqs)==len(set(ids)) and all(
        r.status_code is not None and (r.response_body or _headers(r.response_headers)) for r in reqs)
    ev_score=(40 if has_pairs else 0)+sum(20 for key in ('reproducible','impact_proven','redacted') if has_pairs and checks.get(key) is True)

    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        current_requests = (await s.execute(select(RepeaterRequest).where(
            RepeaterRequest.task_id == original['task_id'], RepeaterRequest.id.in_(ids)
        ).order_by(desc(RepeaterRequest.id)).limit(20))).scalars().all()
        if (not f or _finding_snapshot(f) != original or _request_snapshot(current_requests) != original_requests):
            raise ValueError('Finding or proof changed during verification; review and verify again.')
        verified = bool(has_pairs and verdict == "confirmed" and confidence >= threshold
                        and checks.get("reproducible") is True and checks.get("impact_proven") is True)
        values = dict(confidence=confidence, evidence_score=ev_score, verified=verified,
                      verdict=f"[{verdict}] {reasons}" + (f"\nMissing: {missing}" if missing else ""))
        if weakness:
            values['weakness'] = str(weakness)[:120]
        if verified and f.status == 'new':
            values['status'] = 'confirmed'
        conditions = [Finding.id == finding_id] + [getattr(Finding, k) == v for k, v in original.items()]
        result_update = await s.execute(update(Finding).where(*conditions).values(**values))
        if result_update.rowcount != 1:
            raise ValueError('Finding changed during verification; verify again.')
        await s.commit()
        await s.refresh(f)
        out = {"id": f.id, "confidence": f.confidence, "verified": f.verified,
               "evidence_score": ev_score, "verdict": verdict, "weakness": f.weakness,
               "reasons": reasons, "missing_evidence": missing, "threshold": threshold}
    return out
