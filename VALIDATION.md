# ReconHawk 1.7.1 validation

Validated against version 1.7, commit `3f7e6ac0654438d026b0e6e1209a01a7ecaf59fc`.

- **118 backend regression tests passed.** Coverage includes nullable bounty
  classification, pagination after page 20, malformed/partial scope snapshots, HTTP
  import/retry/status routes, transient failures, rate limits and cooldowns, legacy
  scope retention, URL path/port/query boundaries, custom credential isolation,
  actual provider request parameters, cross-program duplicate isolation, and stale
  or superseded finding reviews. The original suite includes a real local stdio
  MCP transport.
- **Next.js 16.4.6 production build and TypeScript checks passed.**
- **Chromium checks passed on the production build**, with all API, HackerOne,
  provider and WebSocket traffic mocked. Checked paid and legacy `bbp` filtering,
  Unknown classification, conditional provider fields, model drafts, masked-key
  preservation, main/worker model selection, failed-save recovery, per-program
  error display, scope retry and modal refresh. Scope polling continued for
  **58.8 seconds of virtual browser time** and recovered from an injected 503.
  Desktop and 390px mobile Settings views had no JavaScript page errors; the mobile
  modal fit its width. Virtual time checks polling behavior, not live API speed.
- **Real launcher and legacy database smoke check passed** on frontend port 3136
  and backend port 9136, using a disposable pre-upgrade SQLite programs table.
  Startup added missing columns, retained the original scope, served the frontend,
  and allowed both selected local origins and a preconfigured CORS origin.
- The eight optional playbooks passed parser/contract checks. A disposable-database
  import added eight skills, and reimport replaced the same eight; their enabled
  content appeared in the generated prompt context.
- Python compilation, shell syntax, whitespace validation and dependency
  consistency checks passed. Production `npm audit --omit=dev` reported **zero
  vulnerabilities** at validation time.

## Reproduce

After `./setup.sh`, from the repository root:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
cd frontend
npm run build
npm audit --omit=dev
```

For the browser checks, build with the default API base (`http://localhost:8000`),
then run the production frontend in one terminal:

```bash
cd frontend
npm run start -- --port 3135
```

From the repository root in a second terminal:

```bash
UI_TEST_API=http://localhost:8000 \
UI_SCREENSHOT=/tmp/reconhawk-desktop.png \
UI_MOBILE_SCREENSHOT=/tmp/reconhawk-mobile.png \
backend/.venv/bin/python frontend/tests/ui_release.py
```

The test uses mocked endpoints and needs only the frontend server. If you build
with another `NEXT_PUBLIC_API_BASE`, set `UI_TEST_API` to that exact origin.
`UI_TEST_URL` can override the frontend URL. Chromium must be installed through
Playwright; `PLAYWRIGHT_BROWSERS_PATH` can select a non-default browser directory.

Tests use temporary databases, synthetic evidence and mocked external destinations.
No live target assessment, paid model request, HackerOne submission or Telegram
delivery was performed. External providers and account permissions need validation
with the operator's credentials. No vulnerability-detection benchmark or live
scan success rate is claimed. See [START_AR.md](START_AR.md) for upgrade instructions
and [the ten-agent review](docs/AGENT_COMPETITION_AR.md) for further proposals.
