# ReconHawk 1.7.1

An autonomous, self-hosted web security research console for authorized targets.
FastAPI + SQLite + Next.js 16, with Anthropic/OpenAI-compatible model adapters.

Start with [the Arabic operation and upgrade guide](START_AR.md). The release changes
are recorded in [CHANGELOG.md](CHANGELOG.md).

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

This release builds on version 1.7, commit
`3f7e6ac0654438d026b0e6e1209a01a7ecaf59fc`. The older roadmap documents are historical
planning material; completed changes and further proposals are distinguished in the
ten-agent review.
