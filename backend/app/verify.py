from __future__ import annotations

import json
import re

from sqlalchemy import select, desc

from .db import SessionLocal, Finding, RepeaterRequest
from . import settings_store
from .llm import build_llm

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
 "missing_evidence": "<what PoC would raise confidence, or empty>"}

Only use confidence >= 80 when a concrete reproducible PoC proves real impact."""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


async def verify_finding(finding_id: int) -> dict:
    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        if not f:
            raise ValueError("finding not found")
        reqs = (await s.execute(
            select(RepeaterRequest).where(RepeaterRequest.task_id == f.task_id)
            .order_by(desc(RepeaterRequest.id)).limit(8)
        )).scalars().all()

    evidence = "\n".join(
        f"- {r.method} {r.url} -> {r.status_code} ({r.duration_ms}ms)\n"
        f"  req_body: {(r.request_body or '')[:300]}\n"
        f"  resp: {(r.response_body or '')[:600]}"
        for r in reqs
    ) or "(no HTTP evidence captured)"

    prompt = (f"FINDING\nTitle: {f.title}\nSeverity: {f.severity}\nSummary: {f.summary}\n\n"
              f"REPORT (markdown):\n{f.report_md[:6000]}\n\n"
              f"HTTP EVIDENCE (most recent):\n{evidence[:6000]}")

    cfg = await settings_store.llm_config()
    # Verification is a light step — route it to the cheaper worker model when configured.
    llm = build_llm(cfg, provider=cfg["worker_provider"], model=cfg["worker_model"])
    raw = await llm.complete(VERIFY_SYSTEM, prompt)
    result = _extract_json(raw)

    confidence = int(result.get("confidence", 0) or 0)
    verdict = result.get("verdict", "needs_more_evidence")
    weakness = result.get("weakness", "")
    reasons = result.get("reasons", "")
    missing = result.get("missing_evidence", "")

    try:
        threshold = int(await settings_store.get("verify_threshold", "80"))
    except ValueError:
        threshold = 80

    # Evidence-quality score: concrete signals that a report is reproducible + responsible.
    ev_score = 0
    if reqs:
        ev_score += 40  # has captured request/response evidence
    async with SessionLocal() as s:
        f0 = await s.get(Finding, finding_id)
        rpt = (f0.report_md or "").lower()
    if any(k in rpt for k in ("## impact", "impact")):
        ev_score += 20
    if any(k in rpt for k in ("step", "reproduce", "poc")):
        ev_score += 20
    if "[redacted]" in rpt or "redact" in rpt:
        ev_score += 20
    ev_score = min(ev_score, 100)

    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        f.confidence = confidence
        f.evidence_score = ev_score
        f.verdict = f"[{verdict}] {reasons}" + (f"\nMissing: {missing}" if missing else "")
        if weakness:
            f.weakness = weakness
        f.verified = verdict == "confirmed" and confidence >= threshold
        if f.verified and f.status == "new":
            f.status = "confirmed"
        await s.commit()
        out = {"id": f.id, "confidence": f.confidence, "verified": f.verified,
               "evidence_score": ev_score, "verdict": verdict, "weakness": f.weakness,
               "reasons": reasons, "missing_evidence": missing, "threshold": threshold}
    return out
