from __future__ import annotations

from sqlalchemy import select

from .db import SessionLocal, ChatMessage, Finding
from . import settings_store
from .llm import build_llm

MODE_SYSTEM = {
    "general": "You are ReconHawk's assistant — a knowledgeable security researcher. "
               "Answer the operator's questions directly and practically. No restrictions on security topics "
               "within authorized research.",
    "triage": "You are helping the researcher reply to a bug-bounty triager about a specific finding. "
              "Be precise, professional, and evidence-based. Draft replies that clarify impact and reproduction.",
    "retest": "You are helping the researcher perform a retest of a previously reported finding. "
              "Propose concrete retest steps and draft a clear retest conclusion (fixed / still vulnerable / bypass).",
}


async def reply(thread: str, mode: str, user_content: str) -> dict:
    cfg = await settings_store.llm_config()
    llm = build_llm(cfg)

    # Pull finding context when the thread is attached to one.
    context = ""
    if thread.startswith("finding:"):
        try:
            fid = int(thread.split(":", 1)[1])
        except ValueError:
            fid = None
        if fid:
            async with SessionLocal() as s:
                f = await s.get(Finding, fid)
                if f:
                    context = (f"\n\n[FINDING CONTEXT]\nTitle: {f.title}\nSeverity: {f.severity}\n"
                               f"Status: {f.status}\nSummary: {f.summary}\n\nReport:\n{f.report_md}\n")

    # Recent history for continuity.
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(ChatMessage).where(ChatMessage.thread == thread).order_by(ChatMessage.id).limit(20)
        )).scalars().all()
    history = "\n".join(f"{m.role}: {m.content}" for m in rows)

    system = MODE_SYSTEM.get(mode, MODE_SYSTEM["general"])
    prompt = (f"{context}\n[CONVERSATION SO FAR]\n{history}\n\n[NEW MESSAGE]\n{user_content}"
              if history or context else user_content)

    answer = await llm.complete(system, prompt)

    async with SessionLocal() as s:
        s.add(ChatMessage(thread=thread, mode=mode, role="user", content=user_content))
        s.add(ChatMessage(thread=thread, mode=mode, role="assistant", content=answer))
        await s.commit()
    return {"answer": answer}
