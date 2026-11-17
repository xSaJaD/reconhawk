from __future__ import annotations

from urllib.parse import urlparse

import json as _json

from sqlalchemy import select, desc

from .db import SessionLocal, Memory, Skill, Intel, GraphNode, GraphEdge, Asset, now


def host_of(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    return (urlparse(url).hostname or "").lower()


async def remember(content: str, category: str = "note", scope: str = "global",
                   task_id: int | None = None) -> int:
    async with SessionLocal() as s:
        m = Memory(content=content, category=category, scope=scope, task_id=task_id)
        s.add(m)
        await s.commit()
        await s.refresh(m)
        return m.id


async def recall(host: str = "", program_id: int | None = None, limit: int = 40) -> list[Memory]:
    """Memories relevant to this run: global + this host + this program."""
    scopes = ["global"]
    if host:
        scopes.append(f"host:{host}")
    if program_id:
        scopes.append(f"program:{program_id}")
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Memory).where(Memory.scope.in_(scopes)).order_by(desc(Memory.id)).limit(limit)
        )).scalars().all()
    return rows


async def add_intel(task_id: int, kind: str, value: str, source: str = "") -> int:
    async with SessionLocal() as s:
        it = Intel(task_id=task_id, kind=kind, value=value, source=source)
        s.add(it)
        await s.commit()
        await s.refresh(it)
        return it.id


async def add_node(task_id: int, kind: str, key: str, label: str = "", attrs: dict | None = None) -> int:
    """Upsert a graph node by (task_id, key); returns its id."""
    async with SessionLocal() as s:
        existing = (await s.execute(
            select(GraphNode).where(GraphNode.task_id == task_id, GraphNode.key == key)
        )).scalars().first()
        if existing:
            return existing.id
        n = GraphNode(task_id=task_id, kind=kind, key=key, label=label or key,
                      attrs=_json.dumps(attrs or {}))
        s.add(n)
        await s.commit()
        await s.refresh(n)
        return n.id


async def link(task_id: int, src_key: str, dst_key: str, rel: str,
               src_kind: str = "node", dst_kind: str = "node") -> None:
    src = await add_node(task_id, src_kind, src_key)
    dst = await add_node(task_id, dst_kind, dst_key)
    async with SessionLocal() as s:
        dup = (await s.execute(
            select(GraphEdge).where(GraphEdge.task_id == task_id, GraphEdge.src_id == src,
                                    GraphEdge.dst_id == dst, GraphEdge.rel == rel)
        )).scalars().first()
        if dup:
            return
        s.add(GraphEdge(task_id=task_id, src_id=src, dst_id=dst, rel=rel))
        await s.commit()


async def upsert_asset(task_id: int, host: str, kind: str = "host", source: str = "",
                       program_id: int | None = None) -> None:
    if not host:
        return
    async with SessionLocal() as s:
        existing = (await s.execute(
            select(Asset).where(Asset.task_id == task_id, Asset.host == host)
        )).scalars().first()
        if existing:
            return
        s.add(Asset(task_id=task_id, host=host, kind=kind, source=source, program_id=program_id))
        await s.commit()


async def enabled_skills() -> list[Skill]:
    async with SessionLocal() as s:
        return (await s.execute(select(Skill).where(Skill.enabled == True))).scalars().all()  # noqa: E712


async def skills_context() -> str:
    skills = await enabled_skills()
    if not skills:
        return (
            "No testing skills are loaded. Your coverage is limited to generic recon. "
            "Tell the operator that adding skills (Knowledge → Skills) will make the scan "
            "deeper and more targeted."
        )
    names = ", ".join(sk.name for sk in skills)
    lines = [
        "# LOADED SKILLS — THESE DRIVE YOUR SCAN",
        "",
        "The operator has loaded the playbooks below. They are your PRIMARY methodology, not "
        "optional hints. Your scan's depth and quality come from these skills: cover EVERY enabled "
        "skill that could apply to this target, follow each one's steps precisely, and use its exact "
        "payloads and bypass techniques. Do not fall back to a shallow generic pass while any loaded "
        "skill still has untested steps against this target. In your plan, create a step per applicable "
        "skill and track it to a concrete result (confirmed / disproven with evidence / parked with reason).",
        f"\nLoaded skills: {names}",
    ]
    for sk in skills:
        lines.append(f"\n## {sk.name}  ·  [{sk.category}]")
        if sk.summary:
            lines.append(f"_{sk.summary}_")
        if sk.playbook:
            lines.append(f"\n**Playbook / steps:**\n{sk.playbook}")
        if (sk.payloads_json or "").strip():
            lines.append(f"\n**Payloads to try (use these exact values, adapt to the endpoint):**\n{sk.payloads_json}")
        if (sk.bypass_table_md or "").strip():
            lines.append(f"\n**Bypass / evasion techniques:**\n{sk.bypass_table_md}")
        if (sk.references_md or "").strip():
            lines.append(f"\n**References:**\n{sk.references_md}")
    return "\n".join(lines)


async def memory_context(host: str = "", program_id: int | None = None) -> str:
    mems = await recall(host, program_id)
    if not mems:
        return ""
    lines = ["Recalled knowledge from earlier work (use it; verify before relying on stale facts):"]
    for m in mems:
        lines.append(f"- [{m.category}] {m.content}")
    return "\n".join(lines)
