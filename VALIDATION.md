# Release validation

Rebuilt from GitHub base `7d0c1f5883964ff33cdadb684a76a6261767bead`.

- 28 offline regression tests passed, including a real local stdio MCP transport.
- Next.js 16.4.6 production build and TypeScript checks passed.
- Python compilation, shell syntax checks and whitespace validation passed.
- Python dependency consistency check passed.
- Production npm audit reported zero vulnerabilities at the time of validation.
- Chromium checked ten session tabs, settings save, desktop and mobile views without
  JavaScript page errors.

Tests use temporary databases, synthetic evidence and mocked network destinations.
No live target assessment, paid model request, HackerOne submission or Telegram delivery
was performed. External providers and account credentials need validation by the operator.
See START_AR.md for upgrade instructions and functional limitations.
