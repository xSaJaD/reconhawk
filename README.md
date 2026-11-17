# ReconHawk 1.7.2

An autonomous, self-hosted web security research console for authorized targets.
FastAPI + SQLite + Next.js 16, with Anthropic/OpenAI-compatible model adapters.

Start with [the Arabic operation and upgrade guide](START_AR.md). The release changes
are recorded in [CHANGELOG.md](CHANGELOG.md).

Version 1.7.2 repairs the JavaScript analysis pipeline: complete bounded downloads,
supported webpack chunks, client aliases and local wrappers, `fetchConfig` contracts,
response-handler/reducer evidence, complete source maps, paginated artifacts, and
explicit partial specialist results. Static inference preserves runtime values and
requires review before probing inferred or alternative contracts. See
[the analysis guide](docs/JS_ANALYSIS_AR.md).

Version 1.7.1 fixes Bounty classification (including existing `bbp` records), adds
observable HackerOne scope synchronization with per-program retries, and shows only
the selected model provider's fields in Settings. It also preserves URL scope
restrictions, rejects stale finding reviews, and separates duplicate suggestions by
program and endpoint. See the [ten-agent review](docs/AGENT_COMPETITION_AR.md) and
the [eight optional quality playbooks](skills/README_AR.md).

```bash
./setup.sh
./run.sh
```

Open http://localhost:3000 and configure your provider in Settings. No API keys are included.
Use only assets you own or that are explicitly authorized by the program scope.

The console includes browser and HTTP tools, JavaScript/source-map analysis, AST request
shapes, endpoint and lead inventories, durable memory, graph views, specialist agents,
explicit finding proof, skeptical model review, reports, Repeater, routines, and optional
HackerOne/Telegram integrations. MCP requires explicitly configured trusted servers.

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
cd frontend && npm run build
```

This release builds on the Claude branch through commit
`9e05ba4619c2bb1bd51ebb57558b88d1e36f2ba7`, including the prior 1.7/1.7.1 work. The older roadmap documents are historical
planning material; completed changes and further proposals are distinguished in the
ten-agent review.
