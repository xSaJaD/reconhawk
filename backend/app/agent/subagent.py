from __future__ import annotations

from ..db import SessionLocal, SubAgent
from ..events import bus
from .. import settings_store
from ..llm import build_llm
from .tools import TOOLS

# Restricted tool allow-lists per role. Sub-agents NEVER get spawn_* (no recursion) or
# record_finding (one accountable author = the lead agent). They are HTTP-only (no browser_*,
# which can't share a single Playwright page safely).
ROLE_TOOLS = {
    "js_analyst": ["analyze_js", "analyze_source_map", "http_request", "record_endpoint",
                   "extract_intel", "record_lead", "note", "remember", "graph_add_node", "graph_link"],
    "auth_tester": ["http_request", "http_request_as", "replay_as", "list_identities",
                    "register_test_account", "record_lead", "add_evidence", "note", "graph_link"],
    "api_tester": ["http_request", "record_endpoint", "extract_intel", "record_lead", "note"],
    "recon": ["http_request", "analyze_js", "analyze_source_map", "record_endpoint",
              "record_asset", "extract_intel", "note", "record_lead"],
}
DEFAULT_ROLE_TOOLS = ROLE_TOOLS["recon"]

ROLE_SYSTEM = (
    "You are a focused specialist sub-agent in an authorized security assessment. Do ONLY the "
    "delegated task, go deep, and report exactly what you found with concrete evidence (the real "
    "requests/responses and the records you created). Never claim work you did not do. Stay strictly "
    "in scope; the platform blocks out-of-scope hosts. When done, give a short factual summary."
)


def _tools_for(role: str) -> list[dict]:
    allow = set(ROLE_TOOLS.get(role, DEFAULT_ROLE_TOOLS))
    return [t for t in TOOLS if t["name"] in allow]


class SubAgentRun:
    """A real nested bounded agent. Reuses the parent's dispatch (same scope guard + same task
    rows) and the existing llm.agent_loop, so nothing is faked and everything lands on the task."""

    def __init__(self, parent, name: str, role: str, brief: str):
        self.parent = parent
        self.name = name
        self.role = role or "recon"
        self.brief = brief

    async def _emit(self, kind, title, body):
        # tag sub-agent activity so the dashboard shows who did what
        await bus.emit(self.parent.task_id, kind, f"[{self.name}] {title}", body)

    async def run(self) -> str:
        async with self.parent.subagent_semaphore:
            if self.parent.stop.is_set(): return 'Paused'
            return await self._run()

    async def _run(self) -> str:
        cfg = await settings_store.llm_config()
        raw=await settings_store.get_all()
        cfg['worker_provider']=raw.get('worker_provider') or self.parent._provider_name
        cfg['worker_model']=raw.get('worker_model') or self.parent._model_name
        async with SessionLocal() as s:
            row = SubAgent(task_id=self.parent.task_id, name=self.name, role=self.role, status="working")
            s.add(row)
            await s.commit()
            await s.refresh(row)
            rid = row.id
        await bus.emit(self.parent.task_id, "subagent", f"{self.name} · {self.role} (working)",
                       {"name": self.name, "role": self.role, "status": "working"})
        final = ""
        status = "done"
        try:
            # Sub-agents inherit the operator's loaded skills so delegated work follows the
            # same playbooks/payloads as the lead — stronger skills ⇒ stronger sub-agents.
            from .. import knowledge
            sys_prompt = ROLE_SYSTEM + '\nPROGRAM POLICY:\n' + getattr(self.parent, 'policy', '')
            skills_ctx = await knowledge.skills_context()
            if skills_ctx:
                sys_prompt += "\n\n=== LOADED SKILLS (your methodology) ===\n" + skills_ctx
            # Prefer the cheaper worker model for sub-agents.
            llm = build_llm(cfg, provider=cfg["worker_provider"], model=cfg["worker_model"])
            allowed={t['name'] for t in _tools_for(self.role)}
            async def dispatch(name,args):
                if name not in allowed: return 'Tool not permitted for specialist.',True
                return await self.parent.dispatch(name,args)
            final = await llm.agent_loop(
                system=sys_prompt,
                kickoff=f"Specialist role: {self.role}\nDelegated task:\n{self.brief}\n\n"
                        f"Target: {self.parent.target}. Work in scope, go deep, report evidence.",
                tools=_tools_for(self.role),
                dispatch=dispatch,          # SAME dispatch → same scope guard + task rows
                emit=self._emit,
                should_stop=lambda: self.parent.stop.is_set(),
                max_iterations=int(await settings_store.get("subagent_max_iterations", "14")),
                on_usage=lambda tin,tout:self.parent._on_usage(tin,tout,cfg['worker_provider'],cfg['worker_model']),         # tokens/cost roll up → budget stop still fires
                get_effort=lambda: self.parent.effort or cfg["effort"],
            )
        except Exception as e:  # noqa: BLE001
            status = "error"
            final = f"Sub-agent error: {e}"
        async with SessionLocal() as s:
            row = await s.get(SubAgent, rid)
            if row:
                row.status = status
                row.summary = (final or "")[:4000]
                await s.commit()
        await bus.emit(self.parent.task_id, "subagent", f"{self.name} · {status}",
                       {"name": self.name, "role": self.role, "status": status, "summary": final[:200]})
        return final or "(sub-agent produced no output)"
