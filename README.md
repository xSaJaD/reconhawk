# ReconHawk 1.7

An autonomous, self-hosted web security research console for authorized targets.
FastAPI + SQLite + Next.js 16, with Anthropic/OpenAI-compatible model adapters.

Start with [the Arabic operation and upgrade guide](START_AR.md). The release changes
are recorded in [CHANGELOG.md](CHANGELOG.md).

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

This release reconstructs the changes discussed in the project conversation from base
commit 7d0c1f5883964ff33cdadb684a76a6261767bead. The older roadmap documents are historical
planning material; they are not a statement that every future roadmap item is implemented.
