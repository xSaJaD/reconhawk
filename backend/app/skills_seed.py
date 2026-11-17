from __future__ import annotations

import json

from sqlalchemy import delete, select

from .db import SessionLocal, Setting, Skill

# Setting key holding the JSON list of built-in skill names the operator has deleted, so
# a later restart does not re-create them.
_DELETED_KEY = "deleted_builtin_skills"


async def _deleted_builtins(session) -> set[str]:
    row = await session.get(Setting, _DELETED_KEY)
    if row is None:
        return set()
    try:
        value = json.loads(row.value)
        return set(value) if isinstance(value, list) else set()
    except (ValueError, TypeError):
        return set()


async def mark_builtin_deleted(session, name: str) -> None:
    """Record that a built-in was deleted; caller commits."""
    current = await _deleted_builtins(session)
    current.add(name)
    row = await session.get(Setting, _DELETED_KEY)
    if row is None:
        session.add(Setting(key=_DELETED_KEY, value=json.dumps(sorted(current))))
    else:
        row.value = json.dumps(sorted(current))

# Built-in testing playbooks, one per vulnerability class. Each is consulting guidance
# for authorized, in-scope bug-bounty testing — a condensed, well-referenced methodology
# modelled on the community sources operators already trust (PortSwigger Web Security
# Academy, OWASP WSTG, PayloadsAllTheThings, HackTricks, ProjectDiscovery nuclei).
#
# Every playbook follows the same shape: WHERE to look, PROBE (a safe detection signal),
# CONFIRM (a single minimal proof), and STOP. The rules below are absolute and repeated
# so the agent never drifts into abuse:
#   * Stay inside program scope; a finding on an out-of-scope asset is not reported.
#   * Prove with ONE object / ONE request — never bulk-extract data or mass-exploit.
#   * Keep every action non-destructive and reversible; redact PII in evidence.
#   * A payload that only *detects* is enough; do not escalate beyond minimal proof.

_REFS = {
    "swa": "PortSwigger Web Security Academy",
    "patterns": "PayloadsAllTheThings (github.com/swisskyrepo/PayloadsAllTheThings)",
    "hacktricks": "HackTricks (book.hacktricks.xyz)",
    "wstg": "OWASP Web Security Testing Guide (OWASP-WSTG)",
    "nuclei": "ProjectDiscovery nuclei-templates",
}


def _refs(*keys: str) -> str:
    return "References: " + "; ".join(_REFS[k] for k in keys)


# Each entry: (name, category, summary, playbook, references_md, tags)
DEFAULT_SKILLS: list[tuple[str, str, str, str, str, str]] = [
    ("IDOR / BOLA (object-level authorization)", "access-control",
     "Reach another tenant's object by tampering an identifier.",
     "WHERE: numeric/UUID/encoded ids in URLs, JSON bodies, headers, and JS (id, uuid, *_id,\n"
     "order/account/document refs); GraphQL node ids; sequential or guessable keys.\n"
     "PROBE: with your own authenticated session fetch an object you own, then change ONE id to\n"
     "an adjacent value (and try a second account's id).\n"
     "CONFIRM: a clean two-identity control-diff — account A can read/modify A's object; A can\n"
     "also read/modify B's object. One cross-account object is proof. Cover every verb\n"
     "(GET/PUT/PATCH/DELETE) and sibling routes for the same action; test read AND write\n"
     "separately. Watch for ids echoed in responses, exports, and websocket frames.\n"
     "STOP after one object. Redact PII. Never enumerate a range of ids.",
     _refs("swa", "patterns", "hacktricks", "wstg"), "idor,bola,access-control,owasp-a01"),

    ("Broken access control & privilege escalation", "access-control",
     "Vertical/horizontal authz gaps and forced browsing.",
     "WHERE: role-gated actions, admin panels, function-level endpoints, UI-hidden buttons.\n"
     "PROBE: map roles (anon, user, staff, admin) and sensitive actions; capture an authorized\n"
     "admin request, then replay it as a lower-privileged or anonymous principal.\n"
     "CONFIRM: the action succeeds without the required role (vertical), or one user reaches\n"
     "another user's function (horizontal). Test method override (X-HTTP-Method-Override, verb\n"
     "swap), path tricks (/admin vs /Admin, //, ;, %2e), and referer/origin-based gating.\n"
     "Check that authz is enforced server-side, not just hidden in the UI.\n"
     "STOP at one proven action; keep it non-destructive.",
     _refs("swa", "hacktricks", "wstg"), "access-control,privesc,forced-browsing,owasp-a01"),

    ("Authentication & session weaknesses", "auth",
     "Login, reset, MFA, and session-lifecycle flaws.",
     "WHERE: login, signup, refresh, logout, password reset, email/phone change, MFA.\n"
     "PROBE: drop the Cookie/Authorization header on protected endpoints; inspect token\n"
     "invalidation on logout/reset; look at reset-token entropy and reuse; compare responses and\n"
     "timing for user enumeration.\n"
     "CONFIRM: missing auth (endpoint serves data without a session), session fixation (pre-login\n"
     "cookie still valid after login), token not invalidated after logout/reset, predictable or\n"
     "reusable reset token, or MFA bypass (step skip, response tampering, backup-code abuse).\n"
     "Use only your own test accounts. STOP once the control fails once.",
     _refs("swa", "hacktricks", "wstg"), "auth,session,mfa,password-reset,owasp-a07"),

    ("JWT & token attacks", "auth",
     "Signature, algorithm, and claim-trust flaws in tokens.",
     "WHERE: Bearer tokens, cookies, and API keys that decode as JWT (header.payload.signature).\n"
     "PROBE: decode and inspect alg, kid, and claims (sub, role, scope, exp, aud).\n"
     "CONFIRM: alg:none accepted; algorithm confusion (RS256->HS256 signed with the public key);\n"
     "unverified signature (tamper role/sub and it is accepted); weak HMAC secret; kid path/\n"
     "SQL injection; missing exp/aud enforcement; JWK/jku header pointing at attacker URL.\n"
     "Prove with ONE forged token that grants a claim you should not have. STOP there.",
     _refs("swa", "patterns", "hacktricks"), "jwt,jws,auth,token,owasp-a07"),

    ("OAuth / OIDC & SSO flaws", "auth",
     "Redirect, state, and token-handling abuse in delegated auth.",
     "WHERE: /authorize, /callback, redirect_uri, state, nonce, response_type, PKCE, SAML POSTs.\n"
     "PROBE: tamper redirect_uri (subdomain, path, open-redirect chain, @, %2f tricks); remove or\n"
     "replay state; swap authorization codes between sessions; downgrade response_type.\n"
     "CONFIRM: code/token leaks to an attacker-controlled redirect, CSRF via missing state, code\n"
     "reuse, or account takeover by binding your login to a victim identifier. Use your own\n"
     "accounts and operator-controlled redirect endpoints only. STOP at a single proof.",
     _refs("swa", "hacktricks", "wstg"), "oauth,oidc,sso,saml,account-takeover,owasp-a07"),

    ("SQL injection", "injection",
     "Database query injection (error, boolean, time, union, OOB).",
     "WHERE: any parameter reaching a query — query string, body, JSON, headers, cookies, GraphQL\n"
     "args, ORDER BY / LIMIT.\n"
     "PROBE: a single quote and its balanced pair; then a boolean pair that should be logically\n"
     "equivalent vs different (e.g. ' AND 1=1-- vs ' AND 1=2--); then a short time-based probe.\n"
     "CONFIRM minimally: a consistent boolean differential, a measured time delay that tracks the\n"
     "injected sleep, or an out-of-band DNS/HTTP callback for blind cases. Note DBMS, point, and\n"
     "type (error/boolean/time/union/OOB). Do NOT dump tables or read data in bulk — one benign\n"
     "differential proves it. Re-test to rule out latency noise. STOP.",
     _refs("swa", "patterns", "hacktricks", "wstg"), "sqli,nosqli,injection,owasp-a03"),

    ("Cross-site scripting (XSS)", "client",
     "Reflected, stored, and DOM script injection.",
     "WHERE: any input rendered back into HTML, attribute, JS, or URL context; DOM sinks.\n"
     "PROBE: inject a unique, harmless marker and find where/how it is reflected; identify the\n"
     "context (HTML body, attribute, script string, URL) and what encoding is applied.\n"
     "CONFIRM: a context-appropriate non-intrusive PoC that executes (e.g. a unique alert token\n"
     "or DOM write you can observe) — reflected, stored, or DOM. For DOM XSS trace sources\n"
     "(location, document.referrer, postMessage, name) to sinks (innerHTML, eval, setTimeout,\n"
     "document.write). Never target real users or run mass payloads; a single self-observed\n"
     "execution is proof. STOP.",
     _refs("swa", "patterns", "hacktricks", "wstg"), "xss,dom-xss,client,owasp-a03"),

    ("Server-side template injection (SSTI)", "injection",
     "Template-engine expression evaluation.",
     "WHERE: reflected input that flows into a server-side template (email bodies, names, docs,\n"
     "report fields, error pages).\n"
     "PROBE: engine-detection expressions that only do arithmetic ({{7*7}}, ${7*7}, <%=7*7%>,\n"
     "#{7*7}); a 49 in output signals evaluation.\n"
     "CONFIRM: identify the engine from its distinctive syntax, then show a confined proof of code\n"
     "context (e.g. reading a benign config/constant the engine exposes). Keep payloads\n"
     "non-destructive; do not run system commands beyond a harmless identity probe where RCE is\n"
     "clearly reachable. One proof of evaluation + engine is enough. STOP.",
     _refs("swa", "patterns", "hacktricks"), "ssti,injection,template,owasp-a03"),

    ("OS command injection / RCE", "injection",
     "Shell command execution through unsanitized input.",
     "WHERE: parameters passed to shells or system utilities (ping, dns lookup, convert/imagemagick,\n"
     "pdf/doc export, filename, archive, git/ffmpeg wrappers).\n"
     "PROBE: benign separators and substitutions (; | && newline, $(), backticks) that would run a\n"
     "harmless command; for blind cases use an out-of-band DNS/HTTP callback.\n"
     "CONFIRM: a single harmless command proves execution (an identity/hostname echo, or a unique\n"
     "OOB callback). Never run destructive, persistent, or data-reading commands; never pivot.\n"
     "Capture the one proof output and STOP.",
     _refs("swa", "patterns", "hacktricks", "wstg"), "rce,command-injection,oob,owasp-a03"),

    ("Server-side request forgery (SSRF)", "server-side",
     "Coerce the server into making attacker-chosen requests.",
     "WHERE: parameters taking URLs/hosts (webhook, import, preview, image/pdf fetch, proxy,\n"
     "oembed, xml/svg, pdf renderer, link unfurling).\n"
     "PROBE: point the value at an operator-controlled collaborator and watch for inbound DNS/HTTP;\n"
     "test full-response vs blind.\n"
     "CONFIRM reach only: hit the collaborator, then (to show internal reach) a single internal\n"
     "indicator such as a cloud metadata endpoint or an internal-only host responding — WITHOUT\n"
     "exfiltrating credentials or data. Note filters/allowlists and bypasses (redirects, DNS\n"
     "rebinding, alternate IP encodings, [::], 0x/0 octal). One proof of reach is enough. STOP.",
     _refs("swa", "patterns", "hacktricks", "wstg"), "ssrf,cloud-metadata,server-side,owasp-a10"),

    ("XML external entity (XXE)", "injection",
     "External-entity processing in XML parsers.",
     "WHERE: XML intake — SOAP, SAML, SVG, DOCX/XLSX, RSS, sitemap, config import, any endpoint\n"
     "accepting application/xml.\n"
     "PROBE: define a benign local/OOB entity; for blind parsers use an out-of-band DTD on a\n"
     "collaborator and parameter entities.\n"
     "CONFIRM: a benign local resource reflected back, or an OOB callback for blind XXE. Prove\n"
     "reach minimally — never read sensitive files in bulk. Also note billion-laughs/DoS vectors\n"
     "but do NOT trigger them. One proof and STOP.",
     _refs("swa", "patterns", "hacktricks", "wstg"), "xxe,xml,oob,owasp-a05"),

    ("File upload & path handling", "upload",
     "Unsafe upload storage, type checks, and traversal.",
     "WHERE: avatar/document/import/attachment uploads and any filename-controlled write.\n"
     "PROBE: content-type and extension bypass, double extensions, magic-byte spoofing, SVG/XML\n"
     "(stored XSS / XXE), and path traversal in the filename (../, encoded, UNC).\n"
     "CONFIRM: a harmless marker file becomes web-accessible where it should not be, a content-type\n"
     "guard is bypassed, or a traversal writes/reads outside the intended directory. Prove with\n"
     "one benign marker; never upload a real webshell or execute arbitrary code. STOP.",
     _refs("patterns", "hacktricks", "wstg"), "file-upload,path-traversal,lfi,owasp-a04"),

    ("Path traversal & local file read", "server-side",
     "Reading files outside the intended directory.",
     "WHERE: parameters naming files/paths (download, template, include, lang, theme, report).\n"
     "PROBE: traversal sequences (../, ..\\, encoded %2e%2f, double-encoded, null byte, absolute\n"
     "paths) toward a benign known file.\n"
     "CONFIRM: retrieval of one benign out-of-scope file (a predictable, non-sensitive marker such\n"
     "as a readme or a well-known config header) proves traversal. Do not read secrets or bulk\n"
     "files. One proof and STOP.",
     _refs("patterns", "hacktricks", "wstg"), "lfi,path-traversal,file-read,owasp-a01"),

    ("Business-logic & workflow abuse", "logic",
     "Abuse of intended functionality and state machines.",
     "WHERE: multi-step flows — checkout, transfer, invite, coupon, refund, quota, approval.\n"
     "PROBE: negative/overflow quantities, price/currency tampering, step-skipping, replay,\n"
     "parameter/role tampering, mass-assignment of hidden fields, toggling isAdmin/price/state.\n"
     "CONFIRM: a reproducible abuse with real impact (free/discounted purchase, double-spend,\n"
     "skipped approval, quota bypass) using reversible test resources and operator accounts.\n"
     "Keep it non-destructive and undo any state change. One clear proof and STOP.",
     _refs("swa", "hacktricks", "wstg"), "business-logic,workflow,mass-assignment,owasp-a04"),

    ("Race conditions", "logic",
     "Concurrency windows that bypass limits.",
     "WHERE: limited or one-time actions — coupon redeem, withdraw, vote, invite, rate limit,\n"
     "balance, stock.\n"
     "PROBE: send a small burst of identical requests in parallel (single-packet / last-byte sync)\n"
     "against a reversible test resource.\n"
     "CONFIRM: the limit is exceeded (e.g. a coupon applied twice) in a repeatable way. Keep the\n"
     "burst small, use test accounts, and reverse any state change. Prove the window exists once\n"
     "and STOP.",
     _refs("swa", "patterns", "hacktricks"), "race-condition,toctou,concurrency,owasp-a04"),

    ("API & GraphQL testing", "api",
     "REST/GraphQL authz, exposure, and abuse.",
     "WHERE: REST endpoints and GraphQL (/graphql, introspection, operations, aliases).\n"
     "PROBE REST: verb tampering, missing per-endpoint authz, mass-assignment, excessive data in\n"
     "responses, id-based access (BOLA). GraphQL: run introspection, map queries/mutations, test\n"
     "authz per field/object, alias-based batching and depth/cost.\n"
     "CONFIRM: one unauthorized field/object reached, one over-exposed response, or one no-op\n"
     "idempotent mutation that proves reachability without changing data. STOP at one proof;\n"
     "never batch-abuse for load.",
     _refs("swa", "hacktricks", "wstg"), "api,graphql,bola,introspection,owasp-a01"),

    ("CORS & cross-origin trust", "client",
     "Misconfigured cross-origin resource sharing.",
     "WHERE: responses with Access-Control-Allow-Origin / Allow-Credentials on authenticated APIs.\n"
     "PROBE: send Origin: https://attacker.example and variants (null, trusted-subdomain, prefix/\n"
     "suffix, regex gaps) and read the ACAO/ACAC response.\n"
     "CONFIRM: the server reflects an attacker origin with Allow-Credentials:true (or trusts null),\n"
     "then PoC a read of a victim-authenticated endpoint from an attacker origin using your own\n"
     "test accounts. Trusting only *.trusted with credentials still counts if a subdomain is\n"
     "takeoverable. One PoC and STOP.",
     _refs("swa", "patterns", "hacktricks"), "cors,cross-origin,client,owasp-a05"),

    ("CSRF & state-changing requests", "client",
     "Forgeable cross-site state changes.",
     "WHERE: state-changing POST/PUT/DELETE and sensitive GET actions.\n"
     "PROBE: remove/alter the CSRF token and the Origin/Referer; check SameSite on session cookies;\n"
     "try token-not-tied-to-session and method/content-type relaxations.\n"
     "CONFIRM: a cross-site request performs the action without a valid token (a minimal self-\n"
     "submitting PoC against your own account). Keep it reversible. One proof and STOP.",
     _refs("swa", "patterns", "wstg"), "csrf,samesite,client,owasp-a01"),

    ("Open redirect & URL handling", "client",
     "Unvalidated redirects and their chains.",
     "WHERE: redirect params (next, return, url, redirect, continue, dest, callback) and Location.\n"
     "PROBE: absolute URLs, protocol-relative //evil, and bypasses (@, \\\\, whitespace, %2f, data:,\n"
     "trusted-prefix tricks).\n"
     "CONFIRM: the app redirects to an attacker-controlled host. Report standalone, and note where\n"
     "it chains into OAuth/SSO token theft. One proof and STOP; do not phish real users.",
     _refs("patterns", "hacktricks", "wstg"), "open-redirect,client,owasp-a01"),

    ("HTTP request smuggling & desync", "server-side",
     "Front-end/back-end parsing disagreement.",
     "WHERE: chained proxies/CDNs/load balancers in front of an origin.\n"
     "PROBE: controlled CL.TE / TE.CL / TE.TE probes measured against a safe, self-targeted\n"
     "endpoint using timing and response-queue differentials.\n"
     "CONFIRM: a reproducible desync signal that affects ONLY your own subsequent request (self-\n"
     "poison), never a captured victim request. This test is high-risk: keep it minimal, avoid\n"
     "poisoning shared caches or other users, and stop at the first clear differential. STOP.",
     _refs("swa", "hacktricks"), "request-smuggling,desync,server-side,owasp-a06"),

    ("Web cache poisoning & deception", "server-side",
     "Unkeyed input that corrupts or exposes cached content.",
     "WHERE: cached responses keyed on a subset of the request (CDN, reverse proxy).\n"
     "PROBE: unkeyed headers (X-Forwarded-Host/Scheme, X-Host, etc.) and parameter cloaking; look\n"
     "for reflection into cached output; test cache-key normalization for deception.\n"
     "CONFIRM: a benign, self-targeted marker persists in a cached response you then fetch cleanly —\n"
     "WITHOUT poisoning content served to other users. Use cache-busting query values so only your\n"
     "own key is affected. One contained proof and STOP.",
     _refs("swa", "hacktricks"), "cache-poisoning,cache-deception,server-side,owasp-a05"),

    ("Information disclosure & secret leakage", "info",
     "Leaked data, secrets, and exposed internals.",
     "WHERE: error/debug pages, stack traces, source maps, .git/.env/backup files, directory\n"
     "listing, verbose headers, API responses, and JS bundles.\n"
     "PROBE: request common sensitive paths; inspect responses and JS for PII, internal hostnames,\n"
     "tokens, and keys.\n"
     "CONFIRM: one redacted example of the exposure (a single leaked secret proven live-but-not-\n"
     "abused, a source map reconstructing code, a readable .git). Report keys; do NOT use them.\n"
     "Never bulk-collect. One proof and STOP.",
     _refs("patterns", "hacktricks", "wstg", "nuclei"), "info-disclosure,secrets,source-map,owasp-a05"),

    ("Security misconfiguration & known CVEs", "info",
     "Default configs, exposed admin, and vulnerable components.",
     "WHERE: admin/actuator/debug consoles, default credentials, permissive methods (TRACE/PUT),\n"
     "verbose version banners, and outdated components.\n"
     "PROBE: fingerprint tech/versions; check for exposed management endpoints and default creds on\n"
     "operator-authorized assets; match versions to known CVEs.\n"
     "CONFIRM: one reachable misconfiguration or a version-confirmed CVE demonstrated with a single\n"
     "non-destructive proof. Prefer version/behaviour evidence over running public exploits; never\n"
     "run a destructive exploit. One proof and STOP.",
     _refs("hacktricks", "wstg", "nuclei"), "misconfig,cve,default-creds,owasp-a06"),

    ("Subdomain takeover", "recon",
     "Dangling DNS pointing at an unclaimed service.",
     "WHERE: CNAMEs to third-party platforms (cloud storage, pages, CDNs, SaaS) within program\n"
     "scope.\n"
     "PROBE: resolve in-scope subdomains and flag dangling records whose target returns a known\n"
     "'unclaimed' fingerprint.\n"
     "CONFIRM: only to the extent the program permits — show the fingerprint and that the resource\n"
     "is claimable; do NOT serve content on the victim domain unless the policy explicitly allows\n"
     "it. A fingerprinted dangling record is the proof. STOP.",
     _refs("patterns", "hacktricks", "nuclei"), "subdomain-takeover,dns,recon,owasp-a05"),

    ("Attack-surface recon (in-scope)", "recon",
     "Map subdomains, hosts, tech, and forgotten assets.",
     "WHERE: strictly in-scope domains/wildcards as defined by the program.\n"
     "PROBE: passive sources (CT logs, public datasets) plus measured active resolution/probing;\n"
     "fingerprint tech and find staging/legacy/forgotten hosts.\n"
     "CONFIRM: build a live, scoped host inventory and feed it to the endpoint and vuln skills.\n"
     "Stay within scope and the program's rate limits; recon is enumeration, not exploitation.",
     _refs("patterns", "hacktricks", "nuclei"), "recon,subdomains,asset-discovery"),

    ("JS source & endpoint analysis", "recon",
     "Mine client code for routes, params, and secrets.",
     "WHERE: every loaded JS bundle, inline script, and source map on in-scope origins.\n"
     "PROBE: fetch and parse bundles for API routes, GraphQL operations, parameters, feature\n"
     "flags, and leaked keys; reconstruct code from source maps; diff old vs new bundles across\n"
     "visits for newly shipped, under-tested endpoints.\n"
     "CONFIRM: produce an endpoint inventory marking auth requirements and interesting verbs; report\n"
     "any leaked key without abusing it. This feeds the authz/injection skills. STOP at inventory.",
     _refs("patterns", "hacktricks"), "recon,javascript,endpoints,source-map"),

    ("AI / LLM application security", "ai",
     "Prompt injection and unsafe LLM feature design.",
     "WHERE: chat/assistant features, RAG over user/third-party content, and LLM-backed tools.\n"
     "PROBE: direct and indirect prompt injection (instructions hidden in retrieved content) to\n"
     "override the system prompt; probe for system-prompt leakage and tool/function abuse.\n"
     "CONFIRM: a reproducible instruction override, a leaked system prompt, or authz bypass via the\n"
     "model (reaching another tenant's data through a tool). Keep probes benign and non-\n"
     "destructive; one proof and STOP. (OWASP LLM Top 10: LLM01 prompt injection.)",
     _refs("hacktricks", "wstg"), "ai,llm,prompt-injection,owasp-llm01"),
]


async def seed_skills() -> None:
    """Install/refresh the built-in playbooks and remove retired built-ins.

    Idempotent and upgrade-safe: each built-in is upserted by name (content refreshed,
    the operator's enabled/disabled choice preserved), built-ins that are no longer
    shipped are deleted, and operator-authored custom skills are never touched."""
    wanted = {name: (cat, summary, playbook, refs, tags)
              for (name, cat, summary, playbook, refs, tags) in DEFAULT_SKILLS}
    async with SessionLocal() as s:
        rows = (await s.execute(select(Skill))).scalars().all()
        by_name = {row.name: row for row in rows}
        custom_names = {row.name for row in rows if row.is_custom or row.source == "custom"}
        deleted = await _deleted_builtins(s)

        # Upsert the shipped built-ins (never overwrite a custom skill with the same name,
        # and never resurrect a built-in the operator has deleted).
        for name, (cat, summary, playbook, refs, tags) in wanted.items():
            if name in custom_names or (name in deleted and name not in by_name):
                continue
            row = by_name.get(name)
            if row is None:
                s.add(Skill(name=name, category=cat, summary=summary, playbook=playbook,
                            references_md=refs, tags=tags, enabled=True,
                            source="builtin", is_custom=False))
            else:
                row.category, row.summary, row.playbook = cat, summary, playbook
                row.references_md, row.tags = refs, tags
                row.source, row.is_custom = "builtin", False

        # Retire built-ins that are no longer shipped; keep all custom skills.
        stale = [row.name for row in rows
                 if not (row.is_custom or row.source == "custom") and row.name not in wanted]
        if stale:
            await s.execute(delete(Skill).where(Skill.name.in_(stale)))
        await s.commit()
