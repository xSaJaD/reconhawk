from __future__ import annotations

import asyncio
import json

from ..config import settings
from ..db import (
    SessionLocal, Task, Program, Endpoint, Finding, Lead, SubAgent, Addendum, RepeaterRequest,
    Idea, Asset, TokenUsage, Identity, now,
)
from ..events import bus
from ..scope import check_url, load_scope, ScopeError
from ..tools.browser import BrowserSession
from ..tools import http_client, jsanalyze, workspace
from .. import settings_store, intel, notify, verify, reporting, telegram_bot, knowledge, pricing
from ..llm import build_llm
from .prompts import SYSTEM_PROMPT, task_kickoff
from .tools import TOOLS, WORKSPACE_TOOLS
from sqlalchemy import select, desc

STOP_FLAGS: dict[int, asyncio.Event] = {}
INBOX: dict[int, asyncio.Queue] = {}  # operator messages queued into a running agent


def request_stop(task_id: int) -> None:
    ev = STOP_FLAGS.get(task_id)
    if ev:
        ev.set()


def instruct(task_id: int, text: str) -> bool:
    """Queue an operator message into a running agent. Returns False if not running."""
    q = INBOX.get(task_id)
    if q is None:
        return False
    q.put_nowait(text)
    return True


class AgentRun:
    def __init__(self, task_id: int):
        self.task_id = task_id
        self.browser = BrowserSession(task_id)
        self.scope_in: list[str] = []
        self.scope_out: list[str] = []
        self.target = ""
        self.stop = asyncio.Event()
        STOP_FLAGS[task_id] = self.stop
        self.inbox: asyncio.Queue = asyncio.Queue()
        INBOX[task_id] = self.inbox
        self.effort: str | None = None  # mutable, set by set_effort tool
        self.program_id: int | None = None
        self.is_subagent = False  # sub-agents reuse AgentRun.dispatch but can't spawn further
        # running token/cost totals
        self.tok_in = 0
        self.tok_out = 0
        self.cost = 0.0
        self.budget_usd = 0.0  # 0 = unlimited

    def _drain_inbox(self) -> list[str]:
        msgs = []
        while not self.inbox.empty():
            try:
                msgs.append(self.inbox.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs

    async def _open_work(self) -> list[str]:
        """Open ideas + unresolved leads — drives the continuation loop so the agent doesn't quit early."""
        out = []
        async with SessionLocal() as s:
            ideas = (await s.execute(
                select(Idea).where(Idea.task_id == self.task_id, Idea.status.in_(["queued", "chasing"]))
                .order_by(Idea.priority).limit(15))).scalars().all()
            out.extend(f"idea: {i.text}" for i in ideas)
            leads = (await s.execute(
                select(Lead).where(Lead.task_id == self.task_id, Lead.status.in_(["open", "chasing"]))
                .limit(15))).scalars().all()
            out.extend(f"lead: {l.title}" for l in leads)
        return out

    async def _on_usage(self, tin: int, tout: int) -> None:
        self.tok_in += tin
        self.tok_out += tout
        c = pricing.estimate_cost(self._model_name, tin, tout)
        if c:
            self.cost += c
        async with SessionLocal() as s:
            s.add(TokenUsage(task_id=self.task_id, provider=self._provider_name,
                             model=self._model_name, input_tokens=tin, output_tokens=tout))
            t = await s.get(Task, self.task_id)
            if t:
                t.tokens_in = self.tok_in
                t.tokens_out = self.tok_out
                t.cost_usd = round(self.cost, 4)
                t.steps = (t.steps or 0) + 1
            await s.commit()
        # Budget stop (pause, don't drop evidence)
        if self.budget_usd and self.cost >= self.budget_usd:
            await bus.emit(self.task_id, "log",
                           f"⏸ Budget reached (${self.cost:.2f} ≥ ${self.budget_usd:.2f}) — stopping.", {})
            self.stop.set()

    async def _verify_and_notify(self, finding_id: int) -> None:
        """Run the skeptical verification pass, then (if confirmed) auto-deliver to Telegram
        and optionally auto-submit, driven by settings."""
        try:
            cfg = await settings_store.get_all()
            if cfg.get("auto_verify", "true") == "true":
                res = await verify.verify_finding(finding_id)
                await bus.emit(self.task_id, "log",
                               f"Verification: confidence={res['confidence']}% verified={res['verified']}",
                               res)
                verified = res["verified"]
            else:
                verified = True
            if verified and cfg.get("auto_telegram", "true") == "true":
                await telegram_bot.send_report(finding_id)
            if verified and cfg.get("auto_submit", "false") == "true":
                try:
                    await reporting.submit_finding(finding_id)
                except Exception as e:  # noqa: BLE001
                    await bus.emit(self.task_id, "log", f"Auto-submit skipped: {e}", {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            await bus.emit(self.task_id, "log", f"Verification error: {e}", {"error": str(e)})

    async def dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        try:
            if "url" in args and isinstance(args["url"], str) and args["url"] \
                    and name in ("browser_navigate", "analyze_js", "analyze_source_map",
                                 "http_request", "http_request_as", "record_endpoint"):
                check_url(args["url"], self.scope_in, self.scope_out)
            if name == "register_test_account" and args.get("signup_url"):
                check_url(args["signup_url"], self.scope_in, self.scope_out)

            # If the headless browser couldn't launch, steer browser tools to the HTTP path.
            if name.startswith("browser_") and not self.browser.available:
                return ("Browser is unavailable in this environment. Use http_request to fetch pages "
                        "and analyze_js to inspect scripts instead."), True

            if name == "update_plan":
                await bus.emit(self.task_id, "plan", "Plan updated", {"steps": args.get("steps", [])})
                return "Plan recorded.", False

            if name == "note":
                async with SessionLocal() as s:
                    idea = Idea(task_id=self.task_id, text=args.get("text", ""),
                                priority=int(args.get("priority", 3)))
                    s.add(idea)
                    await s.commit()
                    await s.refresh(idea)
                    iid = idea.id
                await bus.emit(self.task_id, "idea", args.get("text", ""),
                               {"id": iid, "text": args.get("text", ""), "priority": int(args.get("priority", 3))})
                return f"Queued idea #{iid}.", False

            if name == "pull_next_idea":
                async with SessionLocal() as s:
                    idea = (await s.execute(
                        select(Idea).where(Idea.task_id == self.task_id, Idea.status == "queued")
                        .order_by(Idea.priority, Idea.id).limit(1)
                    )).scalars().first()
                    if not idea:
                        return "No queued ideas.", False
                    idea.status = "chasing"
                    await s.commit()
                    out = {"id": idea.id, "text": idea.text, "priority": idea.priority}
                await bus.emit(self.task_id, "idea", f"Pulling idea #{out['id']}", out)
                return json.dumps(out), False

            if name == "resolve_idea":
                async with SessionLocal() as s:
                    idea = await s.get(Idea, int(args["idea_id"]))
                    if idea:
                        idea.status = args.get("status", "done")
                        await s.commit()
                return "Idea updated.", False

            if name == "graph_add_node":
                await knowledge.add_node(self.task_id, args["kind"], args["key"], args.get("label", ""))
                await bus.emit(self.task_id, "graph", f"node {args['key']}", {"kind": args["kind"]})
                return "Node added.", False

            if name == "graph_link":
                await knowledge.link(self.task_id, args["src_key"], args["dst_key"], args["rel"])
                await bus.emit(self.task_id, "graph", f"{args['src_key']} -{args['rel']}-> {args['dst_key']}", {})
                return "Edge added.", False

            if name == "record_asset":
                try:
                    if args["host"]:
                        check_url(args["host"], self.scope_in, self.scope_out)
                except ScopeError as e:
                    return f"SCOPE ERROR: {e}", True
                await knowledge.upsert_asset(self.task_id, args["host"], args.get("kind", "host"),
                                             source="agent", program_id=self.program_id)
                await bus.emit(self.task_id, "asset", args["host"], {"kind": args.get("kind", "host")})
                return "Asset recorded.", False

            if name == "set_effort":
                self.effort = args["effort"]
                await bus.emit(self.task_id, "log", f"Effort set to {args['effort']}", {"effort": args["effort"]})
                return f"Effort set to {args['effort']}.", False

            if name == "remember":
                host = knowledge.host_of(self.target)
                scope = f"host:{host}" if host else "global"
                await knowledge.remember(args["content"], args.get("category", "note"),
                                         scope=scope, task_id=self.task_id)
                await bus.emit(self.task_id, "memory", args["content"][:80],
                               {"category": args.get("category", "note")})
                return "Remembered.", False

            if name == "extract_intel":
                await knowledge.add_intel(self.task_id, args["kind"], args["value"], args.get("source", ""))
                await bus.emit(self.task_id, "intel", f"{args['kind']}: {args['value'][:60]}",
                               {"kind": args["kind"], "value": args["value"][:120]})
                await knowledge.add_node(self.task_id, args["kind"],
                                         f"{args['kind']}:{args['value'][:200]}", args["value"][:200])
                return "Intel recorded.", False

            if name == "write_file":
                res = workspace.write_file(self.task_id, args["path"], args.get("content", ""))
                await bus.emit(self.task_id, "file", f"wrote {args['path']} ({res['bytes']}b)", res)
                return json.dumps(res), False

            if name == "read_file":
                return workspace.read_file(self.task_id, args["path"]), False

            if name == "run_command":
                await bus.emit(self.task_id, "command", args["command"][:120], {"command": args["command"]})
                res = await workspace.run_command(self.task_id, args["command"])
                return json.dumps(res), (res.get("return_code", 0) != 0)

            if name == "browser_navigate":
                res = await self.browser.navigate(args["url"])
                await bus.emit(self.task_id, "log", f"Navigated → {res.get('final_url')}", res)
                return json.dumps(res), False

            if name == "browser_read":
                mode = args.get("mode", "text")
                content = await (self.browser.text() if mode == "text" else self.browser.content())
                return content, False

            if name == "browser_links":
                return json.dumps((await self.browser.links())[:150]), False

            if name == "browser_forms":
                return json.dumps(await self.browser.forms()), False

            if name == "browser_screenshot":
                fname = await self.browser.screenshot()
                await bus.emit(self.task_id, "screenshot", "Screenshot", {"file": fname})
                return f"Screenshot saved: {fname}", False

            if name == "list_js":
                return json.dumps(self.browser.discovered_js()), False

            if name == "list_network":
                return json.dumps(self.browser.recorded_requests()[:200]), False

            if name == "analyze_js":
                res = await jsanalyze.fetch_and_analyze(args["url"])
                await bus.emit(self.task_id, "log",
                               f"Analyzed JS ({res['endpoint_count']} endpoints) {args['url']}",
                               {"url": args["url"], "interesting": res["interesting_endpoints"][:20],
                                "secrets": res["possible_secrets"]})
                if res["possible_secrets"]:
                    await notify.push("warn", "Possible secret in JS",
                                      f"{len(res['possible_secrets'])} candidate(s) in {args['url']}", self.task_id)
                return json.dumps(res), False

            if name == "analyze_source_map":
                from ..tools import sourcemap
                res = await sourcemap.analyze(self.task_id, args["url"])
                await bus.emit(self.task_id, "log",
                               f"Source map: {res.get('files_recovered', 0)} src files, "
                               f"{len(res.get('endpoints', []))} endpoints, "
                               f"{len(res.get('request_shapes', []))} request shapes",
                               {"url": args["url"], "auth_signals": res.get("auth_signals", []),
                                "endpoints": res.get("endpoints", [])[:30]})
                return json.dumps(res)[:12000], False

            if name == "record_endpoint":
                async with SessionLocal() as s:
                    s.add(Endpoint(task_id=self.task_id, method=(args.get("method") or "GET").upper(),
                                   url=args["url"], source=args.get("source", ""), notes=args.get("notes", "")))
                    await s.commit()
                await bus.emit(self.task_id, "endpoint", args["url"],
                               {"method": args.get("method", "GET"), "url": args["url"], "source": args.get("source", "")})
                # auto-populate assets + graph (host hosts endpoint)
                host = knowledge.host_of(args["url"])
                if host:
                    await knowledge.upsert_asset(self.task_id, host, "host", "endpoint", self.program_id)
                    await knowledge.link(self.task_id, f"host:{host}", f"endpoint:{args['url'][:380]}",
                                         "hosts", src_kind="host", dst_kind="endpoint")
                return "Endpoint recorded.", False

            if name == "http_request":
                res = await http_client.send_request(args["method"], args["url"],
                                                     headers=args.get("headers"), body=args.get("body"))
                async with SessionLocal() as s:
                    s.add(RepeaterRequest(
                        task_id=self.task_id, method=args["method"].upper(), url=args["url"],
                        request_headers=json.dumps(args.get("headers") or {}), request_body=args.get("body") or "",
                        status_code=res["status_code"], response_headers=json.dumps(res["response_headers"]),
                        response_body=res["response_body"], duration_ms=res["duration_ms"]))
                    await s.commit()
                await bus.emit(self.task_id, "repeater",
                               f"{args['method'].upper()} {args['url']} → {res['status_code']}",
                               {"method": args["method"].upper(), "url": args["url"],
                                "status": res["status_code"], "duration_ms": res["duration_ms"]})
                fb = {k: res.get(k) for k in ("status_code", "response_headers", "duration_ms", "final_url", "truncated", "classification")}
                fb["response_body"] = res["response_body"][:8000]
                return json.dumps(fb), False

            if name == "list_identities":
                from .. import identities as idmod
                async with SessionLocal() as s:
                    t = await s.get(Task, self.task_id)
                    pid = t.program_id if t else None
                    rows = (await s.execute(
                        select(Identity).where((Identity.program_id == pid) | (Identity.program_id.is_(None)))
                    )).scalars().all()
                return json.dumps([{"label": r.label, "auth_type": r.auth_type, "username": r.username} for r in rows]), False

            if name in ("http_request_as", "replay_as"):
                from .. import identities as idmod
                label = args.get("identity") or args.get("as_identity")
                ident = await idmod.get_by_label(self.task_id, label)
                if not ident:
                    return f"No test identity '{label}' configured. The operator adds identities in the UI.", True
                if name == "replay_as":
                    async with SessionLocal() as s:
                        orig = await s.get(RepeaterRequest, int(args["repeater_id"]))
                        if not orig:
                            return f"No repeater request #{args['repeater_id']}.", True
                        method, url = orig.method, orig.url
                        base_headers = json.loads(orig.request_headers or "{}")
                        body = orig.request_body
                    check_url(url, self.scope_in, self.scope_out)
                else:
                    method, url, base_headers, body = args["method"], args["url"], args.get("headers") or {}, args.get("body")
                # strip any existing auth, inject the identity's
                base_headers = {k: v for k, v in base_headers.items()
                                if k.lower() not in ("cookie", "authorization")}
                base_headers.update(idmod.auth_headers(ident))
                res = await http_client.send_request(method, url, headers=base_headers, body=body)
                async with SessionLocal() as s:
                    s.add(RepeaterRequest(task_id=self.task_id, method=method.upper(), url=url,
                        request_headers=json.dumps({**base_headers, "Cookie": "[identity:" + label + "]",
                                                    "Authorization": "[identity:" + label + "]"}),
                        request_body=body or "", status_code=res["status_code"],
                        response_headers=json.dumps(res["response_headers"]),
                        response_body=res["response_body"], duration_ms=res["duration_ms"]))
                    await s.commit()
                await bus.emit(self.task_id, "repeater", f"as {label}: {method.upper()} {url} → {res['status_code']}",
                               {"identity": label, "method": method.upper(), "url": url, "status": res["status_code"]})
                fb = {k: res.get(k) for k in ("status_code", "duration_ms", "classification")}
                fb["response_body"] = res["response_body"][:8000]
                fb["note"] = ("IDOR/BOLA check: if this response (as " + label + ") returns another user's "
                              "object, you've proven it. Capture ONE adjacent object, REDACT PII.")
                return json.dumps(fb), False

            if name == "register_test_account":
                from .. import identities as idmod
                if (await settings_store.get("allow_account_provisioning", "false")) != "true":
                    return ("Account provisioning is OFF. The operator must enable it in Settings and confirm "
                            "the program permits test accounts. Ask them to add identities manually instead."), True
                try:
                    cap = int(await settings_store.get("max_test_accounts", "2"))
                except ValueError:
                    cap = 2
                async with SessionLocal() as s:
                    t = await s.get(Task, self.task_id)
                    pid = t.program_id if t else None
                    existing = (await s.execute(select(Identity).where(
                        Identity.program_id == pid, Identity.source == "provisioned"))).scalars().all()
                if len(existing) >= cap:
                    return f"Test-account cap reached ({cap}). Enough for IDOR/BOLA; do not create more.", True
                res = await http_client.send_request("POST", args["signup_url"],
                    headers={**(args.get("headers") or {}), "Content-Type": "application/json"},
                    body=args.get("body"))
                # HARD STOP on any human-verification / anti-bot control — never bypass.
                challenge = idmod.detect_human_verification(res["status_code"], res["response_headers"], res["response_body"])
                if challenge:
                    await bus.emit(self.task_id, "log", f"⛔ Signup needs human verification ({challenge}) — stopping, not bypassing.", {})
                    return (f"BLOCKED: the signup requires human verification ({challenge}). I will not bypass "
                            "CAPTCHA/OTP/verification. Ask the operator to create this account manually and add it "
                            "as an identity."), True
                # Try to capture an auth token from Set-Cookie or a JSON field.
                material, atype, hname = "", "cookie", ""
                setck = res["response_headers"].get("set-cookie") or res["response_headers"].get("Set-Cookie")
                if setck:
                    material, atype = setck.split(";")[0], "cookie"
                else:
                    try:
                        j = json.loads(res["response_body"])
                        cap_field = args.get("auth_capture", "")
                        tok = (j.get(cap_field) if cap_field else None) or j.get("token") or j.get("access_token")
                        if tok:
                            material, atype = str(tok), "bearer"
                    except Exception:
                        pass
                if res["status_code"] >= 400 or not material:
                    return (f"Signup returned {res['status_code']} and no usable session was captured. "
                            "Likely the wrong schema or account-creation isn't allowed. Not retrying."), True
                async with SessionLocal() as s:
                    s.add(Identity(program_id=pid, label=args["label"], auth_type=atype, material=material,
                                   source="provisioned", note="auto-provisioned test account"))
                    await s.commit()
                await bus.emit(self.task_id, "log", f"Provisioned test identity '{args['label']}' (bounded, scope-checked).", {})
                return f"Test account '{args['label']}' provisioned and stored as an identity. Use replay_as for IDOR/BOLA.", False

            if name == "record_lead":
                async with SessionLocal() as s:
                    s.add(Lead(task_id=self.task_id, title=args["title"], url=args.get("url", ""),
                               severity_guess=args.get("severity_guess", "info"), rationale=args.get("rationale", "")))
                    await s.commit()
                await bus.emit(self.task_id, "lead", args["title"],
                               {"url": args.get("url", ""), "severity_guess": args.get("severity_guess", "info")})
                return "Lead recorded.", False

            if name == "spawn_subagent":
                from .subagent import SubAgentRun
                if self.is_subagent:
                    return "Sub-agents cannot spawn further sub-agents.", True
                brief = args.get("brief") or args.get("summary") or args.get("task") or ""
                report = await SubAgentRun(self, args["name"], args.get("role", "recon"), brief).run()
                return f"Sub-agent '{args['name']}' finished. Its report:\n{report}", False

            if name == "spawn_subagents":
                from .subagent import SubAgentRun
                if self.is_subagent:
                    return "Sub-agents cannot spawn further sub-agents.", True
                try:
                    cap = int(await settings_store.get("max_subagents", "4"))
                except ValueError:
                    cap = 4
                specs = (args.get("agents") or [])[:cap]
                if not specs:
                    return "No agents specified.", True
                runs = [SubAgentRun(self, a.get("name", f"agent{i}"), a.get("role", "recon"),
                                    a.get("brief") or a.get("task") or "")
                        for i, a in enumerate(specs)]
                reports = await asyncio.gather(*[r.run() for r in runs], return_exceptions=True)
                out = []
                for r, rep in zip(runs, reports):
                    out.append(f"### {r.name} ({r.role})\n{rep if not isinstance(rep, Exception) else 'error: ' + str(rep)}")
                return "Sub-agents finished:\n\n" + "\n\n".join(out), False

            if name == "record_finding":
                rel = await intel.classify_finding(args["title"], args.get("summary", ""), exclude_task_id=self.task_id)
                async with SessionLocal() as s:
                    f = Finding(task_id=self.task_id, title=args["title"], severity=args.get("severity", "info"),
                                summary=args.get("summary", ""), report_md=args.get("report_md", ""),
                                relation=rel["relation"], related_to=rel["related_to"])
                    s.add(f)
                    await s.commit()
                    await s.refresh(f)
                    fid = f.id
                # Write the report to disk (HackerOne-style file) + add a graph node.
                try:
                    workspace.write_file(self.task_id, f"reports/finding-{fid}.md",
                                         f"# {args['title']}\n\n{args.get('report_md', '')}")
                except Exception:
                    pass
                await knowledge.add_node(self.task_id, "finding", f"finding:{fid}", args["title"])
                await bus.emit(self.task_id, "finding", args["title"],
                               {"id": fid, "severity": args.get("severity"), "summary": args.get("summary"),
                                "relation": rel["relation"]})
                lvl = "critical" if args.get("severity") in ("high", "critical") else "success"
                await notify.push(lvl, f"Finding: {args['title']}",
                                  f"severity={args.get('severity')} · relation={rel['relation']}", self.task_id)
                # Auto-verify then deliver to Telegram (non-blocking).
                asyncio.create_task(self._verify_and_notify(fid))
                extra = ""
                if rel["relation"] == "same":
                    extra = " NOTE: likely DUPLICATE of an earlier finding."
                elif rel["relation"] == "bypass":
                    extra = " NOTE: similar issue was previously FIXED — document the BYPASS/regression."
                return f"Finding recorded with id={fid} (relation={rel['relation']}).{extra} " \
                       f"Verification runs automatically. Use add_evidence with this id for new PoC later.", False

            if name == "add_evidence":
                async with SessionLocal() as s:
                    f = await s.get(Finding, int(args["finding_id"]))
                    if not f or f.task_id != self.task_id:
                        return f"No finding with id={args.get('finding_id')} in this scan.", True
                    a = Addendum(finding_id=f.id, task_id=self.task_id,
                                 kind=args.get("kind", "poc"), body_md=args.get("body_md", ""))
                    s.add(a)
                    await s.commit()
                    await s.refresh(a)
                    aid, reported = a.id, bool(f.h1_report_id)
                await bus.emit(self.task_id, "addendum", f"Evidence for finding #{args['finding_id']}",
                               {"finding_id": args["finding_id"], "kind": args.get("kind", "poc")})
                # If the parent was already reported, surface it as a comment to post.
                if reported:
                    await telegram_bot.send_comment(aid)
                    await notify.push("info", "New evidence → comment ready",
                                      f"Report #{f.h1_report_id}: new {args.get('kind','poc')} evidence", self.task_id)
                return "Evidence attached" + (" and sent to Telegram as a comment to post." if reported
                                              else "."), False

            return f"Unknown tool: {name}", True

        except ScopeError as e:
            await bus.emit(self.task_id, "log", f"⛔ Scope block: {e}", {"error": str(e)})
            return f"SCOPE ERROR: {e}", True
        except Exception as e:  # noqa: BLE001
            await bus.emit(self.task_id, "log", f"Tool error in {name}: {e}", {"error": str(e)})
            return f"ERROR: {e}", True

    async def run(self) -> None:
        cfg = await settings_store.llm_config()
        async with SessionLocal() as s:
            task = await s.get(Task, self.task_id)
            program_id = task.program_id
            self.program_id = program_id
            program = await s.get(Program, task.program_id) if task.program_id else None
            self.target = task.target
            objective = task.objective
            self.scope_in, self.scope_out = load_scope(program)
            provider = task.provider or cfg["provider"]
            model = task.model or cfg["model"]
            task.status = "running"
            task.provider = provider
            task.model = model
            await s.commit()
        self._provider_name = provider
        self._model_name = model
        try:
            self.budget_usd = float(await settings_store.get("budget_usd_per_task", "0") or 0)
        except ValueError:
            self.budget_usd = 0.0

        if not await settings_store.provider_has_key(provider):
            msg = (f"No API key configured for provider '{provider}'. "
                   "Open Settings, add a key, and Test connection — then start a new scan.")
            await bus.emit(self.task_id, "log", "⚠️ " + msg, {"error": msg})
            async with SessionLocal() as s:
                t = await s.get(Task, self.task_id)
                t.status = "error"
                t.finished_at = now()
                await s.commit()
            await bus.emit(self.task_id, "status", "error", {"status": "error", "final": msg})
            await notify.push("warn", "Scan needs an API key", msg, self.task_id)
            return

        await bus.emit(self.task_id, "status", "running", {"status": "running"})
        await notify.push("info", f"Scan started · {self.target}", f"{provider}/{model}", self.task_id)

        try:
            await self.browser.start()
        except Exception as e:  # noqa: BLE001
            self.browser.start_error = str(e)
            await bus.emit(self.task_id, "log",
                           "⚠️ Browser unavailable — continuing with HTTP-only tools.",
                           {"error": str(e),
                            "hint": "On Linux run: python -m playwright install --with-deps chromium"})
            await notify.push("warn", "Browser unavailable",
                              "Chromium could not launch; the agent will use http_request / analyze_js only. "
                              "Fix with: python -m playwright install --with-deps chromium", self.task_id)

        async def emit(kind, title, body):
            await bus.emit(self.task_id, kind, title, body)

        final_text = ""
        status = "done"
        try:
            # Build the system prompt with loaded skills + recalled memory for this target.
            host = knowledge.host_of(self.target)
            system = SYSTEM_PROMPT
            skills_ctx = await knowledge.skills_context()
            if skills_ctx:
                system += "\n\n=== LOADED SKILLS ===\n" + skills_ctx
            mem_ctx = await knowledge.memory_context(host, program_id)
            kickoff = task_kickoff(self.target, objective, self.scope_in, self.scope_out)
            if mem_ctx:
                kickoff = mem_ctx + "\n\n" + kickoff

            # Workspace shell tools are opt-in (they run commands on the host machine).
            tools = list(TOOLS)
            if (await settings_store.get("enable_shell", "false")) == "true":
                from .. import recon_tools
                tools = tools + WORKSPACE_TOOLS
                installed = recon_tools.installed_names()
                system += ("\n\nYou have a per-scan WORKSPACE: use write_file to save custom probing "
                           "scripts and run_command to execute them in that directory. "
                           "Stay within the authorized scope; keep everything non-destructive.")
                if installed:
                    system += ("\n\nInstalled recon/pentest tools you may invoke via run_command "
                               "(use them ONLY against in-scope assets): " + ", ".join(installed) + ".")

            llm = build_llm(cfg, provider=provider, model=model)
            try:
                max_passes = int(await settings_store.get("max_passes", "3"))
            except ValueError:
                max_passes = 3
            passes = 0
            while True:
                final_text = await llm.agent_loop(
                    system=system,
                    kickoff=kickoff,
                    tools=tools, dispatch=self.dispatch, emit=emit,
                    should_stop=lambda: self.stop.is_set(), max_iterations=cfg["max_iterations"],
                    on_usage=self._on_usage, inbox=self._drain_inbox,
                    get_effort=lambda: self.effort or cfg["effort"],
                )
                passes += 1
                if self.stop.is_set() or passes >= max_passes:
                    break
                if self.budget_usd and self.cost >= self.budget_usd:
                    break
                open_work = await self._open_work()
                if not open_work:
                    break
                await bus.emit(self.task_id, "log", f"Continuation pass {passes + 1}: {len(open_work)} open items",
                               {"open": open_work[:10]})
                kickoff = ("Continue the assessment. You still have UNFINISHED work — do NOT conclude "
                           "while these remain. For each, either confirm it, disprove it, or record why "
                           "it's parked, then go deeper:\n- " + "\n- ".join(open_work[:25]))
            status = "stopped" if self.stop.is_set() else "done"
        except Exception as e:  # noqa: BLE001
            status = "error"
            await bus.emit(self.task_id, "log", f"Run error: {e}", {"error": str(e)})
        finally:
            await self.browser.close()
            STOP_FLAGS.pop(self.task_id, None)
            INBOX.pop(self.task_id, None)

        async with SessionLocal() as s:
            task = await s.get(Task, self.task_id)
            task.status = status
            task.finished_at = now()
            await s.commit()
        await bus.emit(self.task_id, "status", status, {"status": status, "final": final_text})
        await notify.push("info" if status == "done" else "warn",
                          f"Scan {status} · {self.target}", final_text[:200], self.task_id)


async def start_task(task_id: int) -> None:
    await AgentRun(task_id).run()
