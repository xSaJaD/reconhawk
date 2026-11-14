from __future__ import annotations

from sqlalchemy import select, func

from .db import SessionLocal, Skill

# Professional, authorized-testing playbooks. Each is guidance the agent consults; all
# emphasize minimal, redacted proof — never bulk extraction or out-of-scope actions.
DEFAULT_SKILLS = [
    ("IDOR / BOLA hunting", "access-control",
     "Find broken object-level authorization.",
     "1. Enumerate object identifiers in URLs, JSON bodies, and JS (ids, uuids, *_id, contract/account refs).\n"
     "2. Note the identifier format and whether ids look sequential or guessable.\n"
     "3. With your own session, request an object you own; then change ONE id to an adjacent value.\n"
     "4. If you receive another tenant's data, you've proven BOLA. Stop at ONE object.\n"
     "5. Capture a clean two-id control-diff (yours vs the adjacent one). REDACT any PII in evidence.\n"
     "6. Check every verb (GET/POST/PUT/DELETE) and sibling controllers (same action, different route)."),
    ("Broken authentication", "auth",
     "Session, token, and login weaknesses.",
     "1. Inspect auth flow: login, refresh, logout, password reset, MFA.\n"
     "2. Test for missing auth on endpoints (drop the Cookie/Authorization header).\n"
     "3. Check token invalidation on logout/reset; look for predictable reset tokens.\n"
     "4. Try user enumeration via differing responses/timing on login & reset."),
    ("JWT attacks", "auth",
     "JSON Web Token flaws.",
     "1. Decode the JWT; inspect alg, claims (sub, role, exp), and kid.\n"
     "2. Test alg:none, algorithm confusion (RS256->HS256 with public key), weak HMAC secret.\n"
     "3. Tamper role/sub claims and check server validation.\n"
     "4. Check expiry enforcement and signature verification."),
    ("SSRF discovery", "server-side",
     "Server-Side Request Forgery.",
     "1. Find parameters that take URLs/hostnames (webhooks, import, preview, image fetch).\n"
     "2. Point them at a collaborator you control and watch for inbound requests.\n"
     "3. Test internal targets ONLY to prove reach (e.g. metadata endpoints) — do not exfiltrate.\n"
     "4. Note blind vs full-response SSRF and any filters/allowlists."),
    ("Access-control matrix", "access-control",
     "Vertical & horizontal privilege checks.",
     "1. Map roles (anon, user, admin) and sensitive actions.\n"
     "2. For each action, replay it as a lower-privileged (or anonymous) principal.\n"
     "3. Horizontal: same role, another user's resource. Vertical: lower role, admin action.\n"
     "4. Forced browsing to admin routes; check function-level authz, not just UI hiding."),
    ("Business-logic flaws", "logic",
     "Abuse of intended functionality.",
     "1. Map multi-step flows (checkout, transfer, invite, coupon).\n"
     "2. Test negative/overflow quantities, race conditions, step-skipping, replay.\n"
     "3. Parameter/price/role tampering; mass-assignment of hidden fields.\n"
     "4. Confirm real impact; keep tests non-destructive."),
    ("JS & endpoint recon", "recon",
     "Deep source and API-surface analysis.",
     "1. Enumerate every loaded JS bundle; fetch and analyze for routes, params, feature flags.\n"
     "2. Extract API paths, GraphQL operations, and any leaked keys (report, don't abuse).\n"
     "3. Build an endpoint inventory; mark auth requirements and interesting verbs.\n"
     "4. Diff old vs new bundles across visits to spot newly-shipped, under-tested endpoints."),
    ("Information disclosure", "info",
     "Leaked data and secrets.",
     "1. Check error messages, debug endpoints, source maps, .git/.env, backup files.\n"
     "2. Inspect responses for PII, internal hostnames, stack traces, tokens.\n"
     "3. Prove exposure with a single redacted example; never bulk-collect."),
    ("SQL injection", "injection",
     "Database injection.",
     "1. Probe parameters with a single quote, then boolean (1=1 vs 1=2) and time-based payloads.\n"
     "2. Confirm with a benign differential (e.g. sleep) — do NOT dump data; prove the vuln minimally.\n"
     "3. Note DBMS, injection point, and whether it's error/blind/union. Report, don't exfiltrate."),
    ("Command injection / RCE", "injection",
     "OS command execution.",
     "1. Find params passed to shells (ping, convert, export, filename).\n"
     "2. Test with benign separators (; | && `id`) and out-of-band (DNS/HTTP collaborator).\n"
     "3. Prove execution with a harmless command (whoami/id) — never destructive. Capture output."),
    ("XSS", "client",
     "Cross-site scripting.",
     "1. Reflected: inject a unique marker, see if it renders unencoded in HTML/JS/attr context.\n"
     "2. Stored: submit a benign payload (alert with a unique token) and find where it executes.\n"
     "3. DOM: trace sources (location, postMessage) to sinks (innerHTML, eval).\n"
     "4. PoC with a non-intrusive payload; never target real users."),
    ("SSTI", "injection",
     "Server-Side Template Injection.",
     "1. Inject template probes ({{7*7}}, ${7*7}, <%= 7*7 %>) into reflected inputs.\n"
     "2. If math evaluates, identify the engine and escalate to confined proof of code context.\n"
     "3. Keep payloads non-destructive."),
    ("API & GraphQL testing", "api",
     "REST and GraphQL abuse.",
     "1. REST: test verb tampering, mass-assignment, missing authz per endpoint, excessive data exposure.\n"
     "2. GraphQL: run introspection, map queries/mutations, test authz per field, batching/aliasing abuse.\n"
     "3. Look for idempotent no-op mutations to prove reachability without modifying data."),
    ("File upload", "upload",
     "Malicious upload handling.",
     "1. Test content-type / extension bypass, double extensions, magic-byte spoofing.\n"
     "2. Check if uploads are web-accessible and executable; prove with a harmless marker file.\n"
     "3. Look for path traversal in filenames and SVG/XML (XSS/XXE)."),
    ("XXE", "injection",
     "XML External Entity.",
     "1. Find XML intake (SOAP, SAML, file import, SVG).\n"
     "2. Test entity expansion to read a benign local resource or trigger OOB to a collaborator.\n"
     "3. Prove reach minimally; never read sensitive files in bulk."),
    ("CORS misconfiguration", "client",
     "Cross-origin trust flaws.",
     "1. Send Origin: evil.example and check Access-Control-Allow-Origin reflection + Allow-Credentials:true.\n"
     "2. Test null origin and subdomain trust.\n"
     "3. PoC a read of a victim-authenticated endpoint from an attacker origin (your own test accounts)."),
    ("Open redirect", "client",
     "Unvalidated redirects.",
     "1. Find redirect params (return, next, url, redirect).\n"
     "2. Test absolute URLs, protocol-relative (//evil), and bypass tricks (@, \\, whitespace).\n"
     "3. Chain into OAuth/SSO token theft where applicable."),
    ("Race conditions", "logic",
     "Concurrency abuse.",
     "1. Identify limited actions (coupon, withdraw, vote, invite).\n"
     "2. Fire N concurrent identical requests; check if the limit is bypassed.\n"
     "3. Keep it small and non-destructive; prove the window exists."),
    ("Cloud & SSRF-to-metadata", "cloud",
     "Cloud metadata exposure.",
     "1. Where SSRF exists, test reach to cloud metadata (169.254.169.254) ONLY to prove reachability.\n"
     "2. Check for exposed buckets, misconfigured CORS on storage, signed-URL flaws.\n"
     "3. Never exfiltrate credentials; a single redacted proof of reach is enough."),
    ("AI / LLM security", "ai",
     "Prompt injection & LLM abuse.",
     "1. Test prompt injection (direct and via retrieved content) to override instructions.\n"
     "2. Check for system-prompt leakage, tool/function abuse, and unsafe output handling.\n"
     "3. Test authz on LLM-backed features (can you reach another tenant's data via the model?)."),
    ("Subdomain & asset recon", "recon",
     "Attack-surface expansion (in-scope only).",
     "1. Enumerate in-scope subdomains (passive sources, wordlists) — stay within the program scope.\n"
     "2. Probe live hosts, fingerprint tech, find forgotten/staging apps.\n"
     "3. Feed discovered hosts back into the endpoint inventory."),
]


async def seed_skills() -> None:
    """Ensure the default playbooks exist. Idempotent: adds any that are missing by name,
    so a partially-seeded or upgraded database always ends up with the full set."""
    async with SessionLocal() as s:
        existing = {row[0] for row in (await s.execute(select(Skill.name))).all()}
        added = 0
        for name, cat, summary, playbook in DEFAULT_SKILLS:
            if name not in existing:
                s.add(Skill(name=name, category=cat, summary=summary, playbook=playbook, enabled=True))
                added += 1
        if added:
            await s.commit()
