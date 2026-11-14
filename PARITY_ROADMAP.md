# ReconHawk → ExecHawk Parity Roadmap

**Thesis.** ReconHawk already owns the *engine* ExecHawk advertises — an autonomous,
reasoning-first tool-use loop (`agent/runner.py` + `llm/*.agent_loop`), per-domain memory,
skeptical verification, real HackerOne submit, scope guard, and a live dashboard. The real
distance to ExecHawk is **three functional capabilities** (MCP tool federation, a knowledge
graph, genuinely-running sub-agents + deep-think escalation), **two accounting capabilities**
(cost/budget, evidence-quality), and a **UX shell** (a tabbed session with Assets / Health /
Tools / Conversation views). Everything else ExecHawk shows is either already built or an
inflated marketing counter we must **not** fake.

The existing stack makes most of this cheap: new SQLAlchemy models are created by
`Base.metadata.create_all`, and new columns are added by the generic `_migrate_sync`
auto-migration in `db.py` — so the schema grows with **no hand-written migrations**. The neutral
tool spec in `llm/base.py` (`Tool = {name, description, input_schema}`) means new agent tools and
MCP tools drop straight into the same `TOOLS` list and `dispatch()` switch, working across all
four providers.

> **Synthesis note.** This roadmap was produced via an "arena" of two independent candidates
> (one `claude-opus-5-5`, one `claude-fable-5-2`) that converged strongly on the same shape —
> which is itself a signal the plan is sound. This document is the opus base, grafted with the
> fable candidate's three stronger specific designs: **reports-to-disk** (P0.4), the
> **running-agent INBOX** for "Instruct agent" (P1.6, replacing a weaker spawn-a-task approach),
> and **graph auto-creation side-effects** (P1.2). The cross-family judge step was skipped
> because only Claude models were runnable here; the base was chosen by rubric directly.

---

## (a) Gap table

| ExecHawk capability | ReconHawk status now | The gap | Priority |
|---|---|---|---|
| Autonomous recon→disclosure loop | **Built** — `AgentRun.run`, `agent_loop`, 5-phase prompt | none (engine parity) | — |
| Reasoning-first / deep-think core | Partial — `thinking:adaptive` + `output_config.effort` set **once** from cfg | no per-step escalation / "Deep hunt" | P1 |
| 179 tools via **7 MCP servers** | **No MCP client**; shells to CLIs via `run_command` (shell-gated) | MCP client layer + tool registry + Tools tab | **P1 (flagship)** |
| 703 tradecraft skills | 21 real playbooks (`skills_seed.py`), injected into prompt | quality/bypass-table depth (count is marketing) | P1 |
| Deep-think escalation + step-down | effort is static | dynamic effort/model bump tool + heuristic | P1 |
| Per-domain memory (389 facts) | **Built** — `Memory` scoped global/host/program, recalled in kickoff | count is marketing; add decay/dedup polish | P2 |
| Auto PoC + direct submit | **Built** — `record_finding`→`verify`→`reporting.submit_finding`→H1 | reports not written to disk as files | **P0** |
| Cost-aware routing (flash⇄pro) | Partial — `worker_provider/model` used for verify only | no token/$ accounting, no budget, no display | **P0/P1** |
| Knowledge Graph (nodes·edges) | **None** — only flat `Intel` + `Memory` | graph store + derived bootstrap + panel | **P1 (flagship)** |
| Ideas Queue (prioritized, agent pulls) | Partial — `note`→Event(kind=idea), flat, write-only | first-class queue w/ priority+status+pull tool | P0 |
| Multi-agent sub-agents (parallel) | **Recorded only** — `spawn_subagent` writes a row, status="done" | actually run nested bounded loops | P1 |
| Tabbed session view (9 tabs) | Single-scroll `Console` of panels in `page.tsx` | refactor to tabs; add Conversation/Activity | P0 |
| Assets view (hosts/subdomains/tech) | **None** as first-class; only `Endpoint` | `Asset` model + derive + tab | P1 |
| Health tab (errors/rate-limits/tool fails) | **None** — data exists in `Event(kind=log,error=…)` | aggregate query + tab | P0 |
| "Instruct agent" into a running loop | **None** — Repeater is send-only | INBOX queue drained each iteration + box | **P1** |
| Cost / task-limit / "Usage & limits" | **None** — no budget, no per-session cap | budget enforcement in loop + settings section | P1 |
| Evidence & quality scoring | Partial — `confidence`/`verified` from `verify.py` | explicit evidence-quality rubric + panel | P1 |
| Settings sections + accent + desktop install | Flat `SettingsModal`; no accent; web only | reorganize; accent var; optional Tauri wrapper | P2 |
| Status bar "mcp N · tools N · scope on" | Partial — `tools N · skills N · scope` in topbar | add `mcp N` once client lands | P2 |
| Related arsenal (Burp/Chrome exts) | n/a | **out of core scope — do not build** | — |

---

## (b) Phased work

### P0 — quick wins (days; additive, leverage existing data)

**P0.1 Tabbed session view** — *frontend only, highest visual-parity-per-effort.*
Replace the single-scroll `view === "console"` block in `page.tsx` with a tab bar:
`Conversation | Overview | Assets | Findings | Reports | Activity | Health | Files | Tools`.
Most tabs re-home existing panels: Overview=`OverviewStrip`+grid, Activity=the live timeline,
Findings/Reports=`FindingCard`, Files=`WorkspacePanel`+`FilesView`, Tools=`/api/tools`. One
`useState("tab")`, no backend change. Stub Assets/Health/Conversation with "coming in P1" so the
tab row is complete immediately. Add a **"Deep hunt"** button next to Stop (ties to P1.3).

**P0.2 Ideas Queue as a first-class queue.** Promote `note`→`Event(kind=idea)` to a model:
```python
class Idea(Base):                 # db.py — auto-created by create_all
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=3)   # 1 (hottest) – 5
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|chasing|done|dropped
```
Route `note` to insert an `Idea`; add a `pull_next_idea` tool so the model explicitly pulls the
top-priority queued idea (tightens the "reasoning-first, no rigid pipeline" story) and a
`resolve_idea(id, status)` tool. Routes `GET /api/tasks/{tid}/ideas`, `PUT /api/ideas/{id}`.
Replace the Ideas panel with a sortable queue. `Lead` stays the *confirmed-signal* tier; `Idea`
is the hypothesis tier.

**P0.3 Health tab (data already exists).** `dispatch` already emits `Event(kind="log")` with an
`{"error": …}` body for every tool error and scope block. Add `GET /api/tasks/{tid}/health` that
counts error-events, groups by tool (parse `"Tool error in {name}"`), and surfaces
browser-unavailable / provider-key / 429 warnings. New `HealthView` panel. No schema change.

**P0.4 Reports-to-disk + Reports tab.** In `record_finding` dispatch, after insert also
`workspace.write_file(task_id, f"reports/finding-{fid}.md", report_md)` so every finding is a
HackerOne-style file on disk (ExecHawk's "writes reports to disk"), reusing the existing per-task
workspace. The **Reports** tab filters findings with a report, showing verify confidence, H1
submit state (`h1_report_url`), and the existing `h1_payload` copy action.

**P0.5 Token/cost accounting foundation.** `resp.usage` is available in both `agent_loop`s.
Thread an `on_usage` callback through the `LLM` Protocol in `base.py` that accumulates per step.
Add `Task` columns `tokens_in`, `tokens_out`, `cost_usd` (Float), `steps` (Integer) — all added
by `_migrate_sync`. A small `pricing.py` maps model→($/Mtok) with an **"unknown model → tokens
only, no price"** fallback (never invent a price). Surface measured usage in `task_overview` and
`OverviewStrip`. Honest: reports measured usage, not a marketing number.

### P1 — core parity (the flagship work)

**P1.1 MCP client layer (the single biggest functional gap).** ReconHawk shells to CLIs;
ExecHawk federates tools over MCP. Add `backend/app/mcp/client.py` + `mcp/registry.py` using the
official `mcp` Python SDK (stdio + SSE transports):
- New setting `mcp_servers` (JSON list `{name, transport, command/url, env}`) edited in a Settings
  → **Integrations** section.
- On `AgentRun.run` (after scope load), `McpManager.connect_all()` → for each server `list_tools()`
  and register each as a neutral `Tool` namespaced `mcp__<server>__<tool>` (schema maps 1:1 onto
  what `base.py` already expects).
- Extend `dispatch()`: `if name.startswith("mcp__"): return await self.mcp.call(name, args)`.
- **Scope safety (hard requirement):** before any MCP call, scan `args` for URL/host-shaped values
  and run each through `scope.check_url`; refuse out-of-scope — keeping the scope guard
  authoritative even for third-party tools.
- `GET /api/mcp/servers` feeds a **Tools tab** and the `mcp N · tools N` status pill. **Count real
  tools from `list_tools()`** — never hardcode 179/7.
- Keep `recon_tools.py`/`run_command` as the CLI fallback; optionally ship a thin "CLI-over-MCP"
  server wrapping nuclei/ffuf/httpx so the two models converge.

**P1.2 Knowledge Graph (nodes·edges).** Add `GraphNode`/`GraphEdge` in `db.py`:
```python
class GraphNode(Base):
    id; task_id -> tasks.id
    kind: str   # host|endpoint|param|identity|token|tech|finding|object
    key: str    # natural key for dedup, e.g. "host:api.x.com"
    label: str; attrs: str = "{}"   # JSON
class GraphEdge(Base):
    id; task_id; src_id; dst_id -> GraphNode
    rel: str    # hosts|calls|auth_by|references|exposes|idor_to
```
Three write paths: (1) **derive** a bootstrap graph from existing `Endpoint`/`Intel`/`Finding`/
`Memory` rows (cheap, immediate value, works for old tasks); (2) **auto-create** nodes/edges as a
side-effect inside `record_endpoint`/`extract_intel`/`record_finding` in `dispatch()` so the agent
doesn't double-record; (3) explicit `graph_add_node`/`graph_link` tools for relations the model can
only know by reasoning (e.g. endpoint A `idor_to` object B). `GET /api/tasks/{tid}/graph` →
`{nodes, edges}`; frontend renders `nodes·edges` counts + a lightweight hand-rolled force/SVG view
in `panels.tsx` (no heavy dep). The graph is the structured view; `Memory` stays the durable prose.

**P1.3 Real sub-agents + deep-think escalation + "Deep hunt".**
- Make `spawn_subagent` actually run: factor the loop body of `AgentRun` into a reusable
  `run_subagent(parent_task_id, role, brief, max_iterations=small)` that **shares `scope_in/out`**
  and gets a restricted tool subset (no `spawn_subagent`, to prevent recursion). Run up to **4 in
  parallel** with `asyncio.gather` + a semaphore (literally ExecHawk's "task limit 4/session").
  Persist live `SubAgent.status` transitions (`spawned→working→done`) instead of writing `done`
  immediately; stream child events under the parent `task_id`.
- **Deep-think escalation:** add a `set_effort` tool and pass a *mutable* effort into `agent_loop`
  (extend the `LLM` protocol for a per-call effort/model override) so the model raises it on hard
  steps (entering EXPLOIT/PROVE, repeated tool errors, high-severity lead) and the runner
  auto-steps-down after N quiet iterations. A **"Deep hunt"** toggle on `NewTaskModal` starts a task
  pinned to `max` effort (and the `pro` model if configured). Pair with P1.5 so it can't run away.

**P1.4 Assets view.** Add `Asset(task_id, program_id, host, kind=domain|subdomain|ip|service,
tech_json, status=live|dead|unknown, source, first_seen, last_seen)`. Auto-upsert an Asset whenever
a new host appears in `record_endpoint`/`browser_navigate`; optional `record_asset` tool. Only
record **in-scope** hosts (reuse `scope.check_url`/`load_scope`). `GET /api/tasks/{tid}/assets`
feeds an **Assets tab** and becomes a natural node source for the graph (P1.2).

**P1.5 Cost budget + Usage & limits.** Build on P0.5. Add settings `budget_usd_per_task`,
`max_steps_per_task`, `max_subagents` (default 4). Extend `should_stop` to also trip when
accumulated `cost_usd`/`steps` exceed the cap; emit `status: "stopped: budget"` (reuse the
`STOP_FLAGS` mechanism — **pause, never silently drop evidence**). New Settings → **Usage & limits**
section; Overview gains a "Budget left" stat. Measured and enforceable.

**P1.6 "Instruct agent" into the running loop (INBOX).** Hand a request/message to the *running*
agent, matching ExecHawk's "Message… (Enter queues it; delivered when the current step finishes)".
- `agent/runner.py`: add `INBOX: dict[int, asyncio.Queue]` alongside `STOP_FLAGS`. Pass an optional
  `inbox` drain callable into `agent_loop` (symmetric across both providers via the `LLM` protocol);
  before each model call, drain the inbox and splice any messages in as a `user` turn.
- `main.py`: `POST /api/tasks/{tid}/instruct {text}` pushes to the task's inbox (clear no-op message
  if the task isn't running).
- Frontend: a **Conversation** tab (box posting to `/instruct`, streaming agent `log` text) and an
  **"Instruct agent"** button on each `RepeaterPanel` history row that sends the captured
  `method/url/headers/body` as an instruction ("analyze this request for authz/IDOR/injection").

**P1.7 Skills depth (honest, not inflated).** Enrich the `Skill` schema with `payloads_json`,
`bypass_table_md`, `references_md`, `tags` (auto-migrated). Deepen the 21 playbooks with real
bypass tables (403-bypass headers, WAF-safe SQLi differentials, JWT confusion matrix) and add
genuinely new classes (web-cache deception, GraphQL batching, OAuth/SSO, request smuggling,
prototype pollution). `knowledge.skills_context()` already injects enabled skills — include the new
fields. **Report the real count** in `/api/health`; never display 703.

**P1.8 Evidence & quality scoring.** Extend `verify.py` to return an `evidence_quality` sub-score
(has request/response pair, redaction present, reproducibility, impact shown) stored on `Finding`
(`evidence_score` column). Surface an "Evidence & quality" panel/stat — formalizing what
`confidence`/`verdict`/`weakness` already hint at.

### P2 — polish

- **Settings reorganization** into ExecHawk's sections (General / Model & agent / Reports /
  Integrations / Usage & limits / System) — regroup the flat `SettingsModal`. Add an **accent
  color** picker writing the CSS var `--accent` (already drives the theme in `globals.css`).
- **Model "auto (flash ⇄ pro per step)"** display: once P1.3/P1.5 land, show the active tier per
  step in the status bar and Activity.
- **Real parallel sub-agent polish / Review & performance panel:** confirmed/total, duplicate rate
  (from `intel.classify_finding` relations), task-limit usage.
- **Memory polish:** dedup + light decay on `knowledge.recall` ordering.
- **Desktop app packaging:** wrap the Next.js UI + FastAPI in **Tauri** (lighter than Electron) for
  "install as app". Cosmetic; keep self-hostable web as the primary path.

---

## (c) What NOT to fake (marketing) and what is out-of-bounds (safety)

**Marketing numbers — measure, never fabricate:**
- **"179 tools / 7 MCP servers"** → report *actual* tool/server counts from `list_tools()` and the
  configured `mcp_servers`. A fresh install may show `mcp 0 · tools 12`; that is the honest state.
- **"703 skills"** → ship the real playbooks we author (21, growing); `/api/health` already returns
  the true enabled count. Keep it.
- **"389 memory facts"** → show the real per-scope `Memory` count from `task_overview`.
- **"human-grade judgement" / "deep-think"** → capability framings; fine as UI copy **only if** the
  underlying escalation (P1.3) genuinely exists. Don't badge a static-effort loop as "deep-think".
- **Cost/$** must come from measured `resp.usage` × a known price table, with an explicit "price
  unknown" state for custom/local models — never a hardcoded dollar figure.

**Out-of-bounds (hard constraints that stay):**
- The **scope guard** (`scope.py`, `check_url`, SSRF/private-IP/metadata blocklist) remains
  authoritative — and must wrap **every new tool path**: MCP calls (P1.1), sub-agents (P1.3),
  assets (P1.4).
- **No CAPTCHA/WAF bypass, no mass PII extraction, no automated mass account creation, no
  out-of-scope testing.** The IDOR/BOLA rule stays: **one adjacent redacted object** as proof
  (enforced in `prompts.py` + the IDOR skill — keep both). Real parallel sub-agents and MCP fuzzers
  must not become a bulk-extraction or high-volume engine; keep request volume low and gated.
- Shell/`run_command` and any MCP server that runs local binaries stay **opt-in** (the existing
  `enable_shell` gate); they execute on the host.
- Budget enforcement (P1.5) **pauses**, never silently drops evidence; `auto_submit` stays off by
  default.
- The **related arsenal** (Authz-ExeC Burp ext, ExecEndpoints Chrome ext, Burp-Repeater-Renamer) is
  **not** part of the core — do not build or claim it; at most accept an ExecEndpoints export into
  the Assets/Endpoints import.
- Everything stays on the existing stack (FastAPI + async SQLAlchemy/SQLite + multi-provider LLM +
  Next.js), self-hostable, with no change abandoning the auto-migration model.

---

## Suggested execution order

1. **P0 batch** (tabs, ideas queue, health, reports-to-disk, token foundation) — a weekend; huge
   visible parity jump, almost no risk.
2. **P1.1 MCP** + **P1.2 Graph** — the two flagship functional gaps; land them next.
3. **P1.3 sub-agents + deep-think**, **P1.5 budget**, **P1.6 instruct-agent** — the "autonomous for
   days, cost-aware" story.
4. **P1.4 Assets**, **P1.7 skills depth**, **P1.8 evidence quality** — fill out the tabs & quality.
5. **P2 polish** — settings sections, accent, Tauri, memory decay.
