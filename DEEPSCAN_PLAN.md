# ReconHawk Deep-Scan Plan (arena-synthesized)

Why the OYO/`patron.oyorooms.com` pass was shallow, and how to fix it. Produced by an arena of two
independent candidates (`claude-opus-5-5` + `claude-fable-5-2`) that **converged almost completely**
— strong agreement. Base = opus; grafted from fable: the **SPA-fallback detector**, the catch that
`max_subagents` is defined-but-unused, and tying `verify.py` to the specific proof requests.
(Cross-family judge skipped — only Claude models were runnable; base chosen by rubric.)

## Root-cause → fix

| # | Cause | Fix | Files |
|---|---|---|---|
| 1 | **Sub-agents are fake** — `spawn_subagent` only writes a `SubAgent` row (`status="done"`), runs nothing. The OYO "delegated to JS analyst" did nothing. | Real nested bounded `SubAgentRun` reusing `llm.agent_loop` + the parent's `dispatch` (same scope guard, same task rows); batch `spawn_subagents` runs them with `asyncio.gather`, capped by `max_subagents`; status walks `working→done`, summary = real output. | `agent/subagent.py`, `runner.py`, `agent/tools.py` |
| 2 | **Shallow JS** — `jsanalyze.py` regex only; never fetches `.js.map`, never reconstructs source. | `analyze_source_map`: fetch `.js.map`, reconstruct `sourcesContent`→`webpack:///src/*` to the workspace (no VLQ decode needed), extract endpoints + request shapes from readable source; follow chunks. | `tools/sourcemap.py`, `jsanalyze.py` |
| 3 | **Probing not schema-driven** — empty bodies → SPA HTML looked "dead." | SPA-fallback detector classifies HTML/`<div id=root>` responses as "wrong shape, not dead"; schema-driven `probe` from extracted shapes; 401/500 = signal. | `tools/http_client.py`, `runner.py` |
| 4 | **Stops too early** — one short pass. | Multi-pass continuation loop: keep going while open leads/ideas remain and budget allows. | `runner.py` |
| 5 | **No authenticated testing** for IDOR/BOLA. | Operator-supplied `Identity` creds + `replay_as` (one adjacent redacted object). | `db.py`, `http_client.py`, `runner.py` |
| 6 | **Weak exploitation discipline** — leads not converted; 401/500 = dead end. | Prompt: every lead → a close-plan; don't conclude while open work remains; verify ties to the real PoC request. | `prompts.py`, `verify.py` |
| 7 | **No resumption** across passes. | `resume_task` rebuilds the kickoff from persisted open ideas/leads; routines resume instead of restart. | `runner.py`, `scheduler.py` |

## Phases
- **P0 (this build):** real sub-agents · source-map reconstruction + deeper extraction · SPA-fallback detector · multi-pass continuation loop. These are exactly what the OYO pass failed on.
- **P1:** `EndpointSpec` table + `probe_spec` schema-driven probing · `resume_task` cross-restart persistence.
- **P2:** operator `Identity` creds + `replay_as` for authenticated IDOR/BOLA · verify→proof-request linkage.
- **P3:** AST-based extraction (node/acorn under shell) · graph auth-flow enrichment · promote schemas to host memory.

## Safety (unchanged, every new path)
Scope guard on every new URL tool and on sub-agents (they reuse the parent dispatch); minimal
redacted proof (one adjacent object); operator-supplied identities only, no OTP/auth/CAPTCHA/WAF
abuse, low volume; no faked metrics (status/cost/evidence all from real rows); auto-migration.
