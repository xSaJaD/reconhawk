from __future__ import annotations

from .db import SessionLocal, Finding, Task, Program, Addendum, now
from . import settings_store, notify
from .integrations.hackerone import HackerOneClient, HackerOneError

SEV_MAP = {"info": "none", "low": "low", "medium": "medium", "high": "high", "critical": "critical"}


async def _h1_client() -> HackerOneClient:
    cfg = await settings_store.get_all()
    return HackerOneClient(cfg.get("hackerone_username", ""), cfg.get("hackerone_token", ""))


def build_report_payload(f: Finding, program: Program | None) -> dict:
    """Assemble the HackerOne report fields from a finding."""
    impact = f.summary or "See vulnerability details."
    vuln = f.report_md or f.summary
    # Pull an explicit Impact section out of the markdown if present.
    low = (f.report_md or "").lower()
    if "## impact" in low:
        idx = low.index("## impact")
        impact = f.report_md[idx:idx + 1500]
    return {
        "team_handle": (program.handle if program else "") or "",
        "title": f.title,
        "vulnerability_information": vuln,
        "impact": impact,
        "severity_rating": SEV_MAP.get(f.severity, "low"),
    }


async def submit_finding(finding_id: int) -> dict:
    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        if not f:
            raise ValueError("finding not found")
        task = await s.get(Task, f.task_id)
        program = await s.get(Program, task.program_id) if task and task.program_id else None

    if f.h1_report_id:
        return {"already": True, "id": f.h1_report_id, "url": f.h1_report_url}

    payload = build_report_payload(f, program)
    if not payload["team_handle"]:
        raise HackerOneError("No HackerOne program handle on this scan's program. "
                             "Attach the scan to a HackerOne program (its handle) first.")

    client = await _h1_client()
    res = await client.submit_report(**payload)

    async with SessionLocal() as s:
        f = await s.get(Finding, finding_id)
        f.h1_report_id = res["id"]
        f.h1_report_url = res["url"]
        f.submitted_at = now()
        f.status = "reported"
        await s.commit()
    await notify.push("success", f"Report submitted · #{res['id']}",
                      f"{f.title} → {res['url']}", task.id if task else None)
    return res


def build_comment_payload(a: Addendum, f: Finding) -> str:
    """The text to paste as a comment on the existing report."""
    header = {"poc": "Additional PoC", "retest": "Retest update",
              "bypass": "Bypass / regression", "comment": "Update"}.get(a.kind, "Update")
    return f"**{header}**\n\n{a.body_md}"


async def mark_addendum_posted(addendum_id: int) -> None:
    async with SessionLocal() as s:
        a = await s.get(Addendum, addendum_id)
        if a:
            a.posted = True
            await s.commit()
