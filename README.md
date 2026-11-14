# ReconHawk 🦅

An **autonomous web-security research console** — an original, self-hostable platform
inspired by the ExecHawk concept. A Claude-powered agent opens a real browser, enumerates
the attack surface, extracts and analyzes JavaScript, discovers endpoints, probes them with
a built-in Repeater, and writes up findings — while a live dashboard streams every step.

> ⚠️ **Authorized use only.** Point ReconHawk exclusively at targets you own or that are
> explicitly in scope for a bug-bounty program. The agent is sandboxed by a scope guard that
> blocks out-of-scope hosts, internal IPs, and cloud-metadata endpoints, and it is instructed
> to avoid destructive actions — but *you* are responsible for staying within authorization.

## Capabilities

| Capability | Status |
|---|---|
| Autonomous agent loop (plan → recon → probe → report) | ✅ |
| **Multi-provider LLM** — Anthropic, OpenAI, OpenRouter, any OpenAI-compatible server (Ollama/Groq/LM Studio) | ✅ |
| Provider/model/effort selectable in UI; per-scan override | ✅ |
| Real browser (Playwright/Chromium): navigate, read, links, forms, screenshots | ✅ |
| Network capture + JS discovery and static analysis (endpoints / secrets) | ✅ |
| Endpoint recording · **Leads** · **Sub-agents** delegation | ✅ |
| Built-in **Repeater** (agent + manual) with full request/response history | ✅ |
| **Scope enforcement** (allowlist / blocklist / SSRF guard) | ✅ |
| Findings / Reports (Markdown) with status + **Addenda** (triager / retest / bypass) | ✅ |
| **Report-diff intelligence** — same / new / bypass classification | ✅ |
| **Routines** — scheduled recurring scans (background scheduler) | ✅ |
| **Notifications** — in-app bell + Telegram relay | ✅ |
| **Scope-change alerts** on program updates | ✅ |
| **File browser** for agent artifacts | ✅ |
| **Chat** — General / Triage / Retest modes | ✅ |
| Live dashboard over WebSocket: Tasks · Plan · Ideas · Sub-agents · Endpoints · Leads · Timeline · Reports · Repeater | ✅ |
| Programs with scope (manual + HackerOne-ready data model) | ✅ |

### Choosing a provider
Open **⚙ Settings** in the UI: pick a provider, set the model + API key, and click **Test connection**.
Everything is stored server-side (editable live, no restart). You can also override provider/model per scan.

## Architecture

```
frontend/  Next.js 14 dashboard  ──HTTP/WebSocket──▶  backend/  FastAPI
                                                          ├─ agent/   Claude tool-use loop
                                                          ├─ tools/   browser · http · js-analyze
                                                          ├─ scope    allowlist + SSRF guard
                                                          ├─ events   in-proc pub/sub → WS
                                                          └─ SQLite   tasks/events/endpoints/findings/repeater
```

## Quick start (one command each)

```bash
./setup.sh     # one time: venv + deps + Chromium + frontend install
./run.sh       # starts backend + frontend; frees stale ports; wires them together
```

Then open **http://localhost:3000**, click **⚙ Settings**, choose a provider, paste an API
key, hit **Test connection** — then **+ New scan**.

> `run.sh` kills stale `uvicorn`/`next` processes, picks the backend python from `.venv`
> automatically, and writes `frontend/.env.local` to match the backend port — so the usual
> "port already in use" / "module not found" / "wrong path" issues can't happen.
> Override ports with `BACKEND_PORT=9000 FRONTEND_PORT=3001 ./run.sh`.

### Manual start (if you prefer)

```bash
# terminal 1 — backend (note: .venv/bin/python, not bare uvicorn)
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000
# terminal 2 — frontend
cd frontend && npm run dev
```

### Browser on Kali / unsupported distros

Playwright may warn *"your OS is not officially supported"* and Chromium can fail to launch
if system libraries are missing. Install them with:

```bash
cd backend && .venv/bin/python -m playwright install --with-deps chromium
```

If Chromium still can't start, **scans continue anyway** — the agent falls back to its HTTP
request + JavaScript-analysis tools and tells you in the timeline.

## Configuration (`backend/.env`)

| Var | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for the agent to run. |
| `AGENT_MODEL` | `claude-opus-5-5` | Any current Claude model. |
| `AGENT_EFFORT` | `high` | `low`–`max`. |
| `AGENT_MAX_ITERATIONS` | `40` | Hard cap on agent tool-loop turns. |
| `SCOPE_ENFORCED` | `true` | When a task has a program, restrict to its allowlist. |
| `HEADLESS` | `true` | Browser headless mode. |

## Safety model

- **Scope guard** (`app/scope.py`) runs before any network tool: resolves the host, blocks
  private/loopback/link-local IPs and the cloud-metadata address, honors per-program
  allow/deny lists (supports `*.example.com`).
- The agent **system prompt** forbids destructive actions, mass data changes, DoS, and real
  data exfiltration, and tells it to prove impact minimally and prefer read-only verification.
- Every action is persisted and streamed, so there's a full audit trail per scan.

## Confirmation → report → HackerOne workflow

1. The agent records a finding only with a concrete, reproducible PoC.
2. A **skeptical verification pass** scores confidence (0–100) and labels the weakness
   (IDOR/BOLA/…); only high-confidence findings are marked *verified* (threshold configurable).
3. Verified findings are **delivered to Telegram** with a one-click **Submit to HackerOne**
   button (inline-keyboard callback → `POST /v1/hackers/reports`).
4. In the console each finding has **Verify · Copy report · Telegram · Submit** actions.
5. As the scan continues, new PoC for an already-reported issue becomes an **addendum**
   (`add_evidence`) → a prepared **comment** with a **Copy** button and a link that opens the
   report to paste it (HackerOne has no hacker comment API), plus a Telegram relay.

**Workflow mode:** **⟳ H1** in the sidebar syncs your HackerOne programs (scope + policy); each
program has **▶ Scan whole program**, which launches a policy-aware scan across its whole scope.

Set your HackerOne API username/token and Telegram bot token + chat id in **⚙ Settings → HackerOne / Telegram**.
Automation toggles there: auto-verify, auto-send-to-Telegram, and (off by default) auto-submit.

> **IDOR/BOLA proof is minimal by design:** the agent accesses one adjacent object and redacts
> PII. ReconHawk does not do bulk data extraction, CAPTCHA/WAF bypass, mass account creation,
> or testing outside the configured scope — those get researchers banned and are out of scope
> for this tool.

## Roadmap

- HackerOne report **comments via API** once/if a hacker endpoint exists (today: assisted copy-paste).
- Swap SQLite → **Postgres** and add a **Redis-backed worker** for horizontal scale.

## License / responsibility

Provided for authorized security research and education. You are solely responsible for
ensuring you have permission to test any target.
