# ReconHawk 1.7.2 validation

Base: Claude branch commit `9e05ba4619c2bb1bd51ebb57558b88d1e36f2ba7`.

- **185 backend tests passed.** New regressions cover complete files beyond the old
  character limit, aliases/local wrappers, sparse webpack module IDs, lexical
  shadowing, conditional/nested body fields, fetchConfig action creators, reducer
  evidence, request variants, safe static inference, scoped task artifacts, JSON
  paging, UTF-8 byte pages, large/inline/indexed maps, missing sources, limits,
  specialist cancellation/iteration stops, full reports and read-only JS roles.
- **Next.js production build and TypeScript passed.**
- **Two production Chromium suites passed**, with all API/provider/H1/WebSocket
  destinations mocked: the previous provider/program/scope checks, plus analysis
  partial-state display, pages 0/10/20 through 25 contracts, warnings, full specialist
  reports over 4,000 characters, and desktop/390px mobile layout. No JS page errors.
- **Manual offline replay passed** against three previously retrieved JS assets
  totaling **2,724,686 bytes**. Through the mocked static-file transport, the
  pipeline read all three files, recovered all **nine requested endpoint paths**,
  the ten signup body keys, nested metadata fields, login response-token usage,
  and the browser Maps-key candidate. No parser/budget warnings occurred. Runtime
  values stayed explicitly symbolic, so semantic coverage remained partial.
  This is contract-recovery validation, not a vulnerability-detection benchmark.
- **Real legacy-database upgrade smoke check passed.** A database created with
  version 1.7.1 started under the new backend, gained the SubAgent coverage column,
  preserved its report, marked interrupted specialists paused, and served the new
  analyses API with HTTP 200.
- Python compilation, shell syntax, whitespace and dependency-consistency checks
  passed. Production npm audit reported **zero vulnerabilities** at validation time.

No new target requests were made during these repairs; the replay used files
retrieved earlier for the requested read-only JS review. No paid provider calls,
HackerOne submissions or Telegram deliveries were performed.

After setup, reproduce the backend/build checks from the root:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
cd frontend
npm run build
npm audit --omit=dev
```

Run `npm run start -- --port 3135` from frontend, then in a second terminal at the
repository root (build with the default `http://localhost:8000` API base):

```bash
UI_TEST_API=http://localhost:8000 backend/.venv/bin/python frontend/tests/ui_release.py
UI_TEST_API=http://localhost:8000 backend/.venv/bin/python frontend/tests/ui_analysis.py
```

These browser tests need only the frontend; they mock the backend. Chromium must
be installed. `UI_TEST_URL`, `UI_TEST_API` and `PLAYWRIGHT_BROWSERS_PATH` can select
other local test URLs/browser locations. Screenshots default to the paths documented
inside each script and can be overridden with their screenshot environment variables.

See [the Arabic JS analysis guide](docs/JS_ANALYSIS_AR.md) for coverage semantics,
supported inference, review gates and resource limits.

## Historical 1.7.1 validation

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
