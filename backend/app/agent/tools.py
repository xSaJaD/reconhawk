"""Tool schemas exposed to Claude. Dispatch lives in runner.py."""

# Task-local file tools; only run_command requires enable_shell.
WORKSPACE_TOOLS = [
    {
        "name": "write_file",
        "description": "Write a file into your per-scan workspace (e.g. a custom Python probing "
                       "script or a notes file). Returns the path and byte count.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a byte-addressed page of a workspace file. Follow next_offset to read more; source_range uses byte offsets.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 100000}},
            "required": ["path"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command inside your workspace directory (e.g. run a Python "
                       "script you wrote, or a curl/jq pipeline). Returns stdout, stderr, exit code. "
                       "Stay within the authorized scope; keep commands non-destructive.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
]

TOOLS = [
    {
        "name": "update_plan",
        "description": "Record or update your current plan as a list of short steps. "
                       "Call this whenever your plan changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["steps"],
        },
    },
    {
        "name": "note",
        "description": "Queue a hypothesis / follow-up into your Ideas Queue to revisit later. "
                       "Give it a priority (1 = hottest, 5 = cold).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "priority": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["text"],
        },
    },
    {
        "name": "pull_next_idea",
        "description": "Pull the highest-priority unresolved idea from your Ideas Queue to work on next.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_idea",
        "description": "Mark an idea done or dropped once you've chased it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "idea_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["done", "dropped", "chasing"]},
            },
            "required": ["idea_id", "status"],
        },
    },
    {
        "name": "graph_add_node",
        "description": "Add an entity to the knowledge graph (a host, endpoint, identity, token, tech, "
                       "or object you discovered).",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["host", "endpoint", "param", "identity", "token", "tech", "object"]},
                "key": {"type": "string", "description": "unique key, e.g. 'host:api.x.com'"},
                "label": {"type": "string"},
            },
            "required": ["kind", "key"],
        },
    },
    {
        "name": "graph_link",
        "description": "Link two knowledge-graph entities by a relation you inferred "
                       "(e.g. endpoint X idor_to object Y, host A hosts endpoint B).",
        "input_schema": {
            "type": "object",
            "properties": {
                "src_key": {"type": "string"},
                "dst_key": {"type": "string"},
                "rel": {"type": "string", "enum": ["hosts", "calls", "auth_by", "references", "exposes", "idor_to"]},
            },
            "required": ["src_key", "dst_key", "rel"],
        },
    },
    {
        "name": "record_asset",
        "description": "Record a discovered in-scope asset (host/subdomain/service) for the Assets inventory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "kind": {"type": "string", "enum": ["domain", "subdomain", "ip", "service", "host"]},
            },
            "required": ["host"],
        },
    },
    {
        "name": "set_effort",
        "description": "Escalate or step down your reasoning effort for upcoming steps. Use 'max' on hard "
                       "exploitation/proof steps, step back to 'low'/'medium' for routine recon to control cost.",
        "input_schema": {
            "type": "object",
            "properties": {"effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh", "max"]}},
            "required": ["effort"],
        },
    },
    {
        "name": "remember",
        "description": "Store a durable fact you learned about this target (tech stack, auth scheme, "
                       "id format, a quirk) so future scans of the same host/program recall it. "
                       "Keep it concise and factual.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "category": {"type": "string", "enum": ["tech", "auth", "endpoint", "behavior", "note"]},
            },
            "required": ["content"],
        },
    },
    {
        "name": "extract_intel",
        "description": "Record a piece of extracted intelligence found during recon (a parameter, "
                       "identifier format, header, technology, or a secret — report secrets, never abuse them).",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["param", "secret", "token", "id", "email", "tech", "header"]},
                "value": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["kind", "value"],
        },
    },
    {
        "name": "browser_navigate",
        "description": "Open a URL in the real browser. Returns final URL, HTTP status, and page title. "
                       "Network requests are recorded automatically.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_read",
        "description": "Get the visible text (mode='text') or raw HTML (mode='html') of the current page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["text", "html"]},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "browser_links",
        "description": "List all absolute links found on the current page.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_forms",
        "description": "List forms on the current page with their action, method, and inputs.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_screenshot",
        "description": "Capture a screenshot of the current page (saved as an artifact).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_js",
        "description": "List JavaScript files observed loading in the browser so far.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_network",
        "description": "List network requests (method + URL) the browser has recorded. "
                       "Great for finding XHR/fetch API calls.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "analyze_js",
        "description": "Read complete bounded JavaScript files, discover supported webpack chunks, recover static API contracts, "
                       "and save a coverage manifest. GET static assets only. Follow read_analysis pages; symbolic values require review.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}, "include_chunks": {"type": "boolean", "default": True}},
            "required": ["url"],
        },
    },
    {
        "name": "analyze_source_map",
        "description": "Read a complete bounded external/inline source map and embedded indexed sections; save embedded sources and "
                       "static request contracts. Returns a manifest; use read_analysis and read_file for pages. "
                       "Missing sources and symbolic expressions are explicitly partial, not exact runtime schemas.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "the .js bundle URL (map is auto-discovered)"}},
            "required": ["url"],
        },
    },
    {
        "name": "record_endpoint",
        "description": "Save a discovered endpoint to the dashboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "url": {"type": "string"},
                "source": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "http_request",
        "description": "Send an arbitrary HTTP request (your Repeater) and get the full response. "
                       "Use for probing endpoints. Keep volume low and deliberate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "url": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {"type": "string"},
            },
            "required": ["method", "url"],
        },
    },
    {
        "name": "record_lead",
        "description": "Record a promising but unconfirmed signal to chase later (a lead), "
                       "with your rationale and a severity guess.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "url": {"type": "string"},
                "severity_guess": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"]},
                "rationale": {"type": "string"},
            },
            "required": ["title", "rationale"],
        },
    },
    {
        "name": "spawn_subagent",
        "description": "Delegate a focused sub-task to a specialist sub-agent that ACTUALLY RUNS a "
                       "nested analysis loop and returns its real findings. Use for deep work like "
                       "reconstructing a JS source map and extracting request schemas. Roles: "
                       "js_analyst, auth_tester, api_tester, recon.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "role": {"type": "string", "enum": ["js_analyst", "auth_tester", "api_tester", "recon"]},
                "brief": {"type": "string", "description": "Exactly what the sub-agent should do."},
            },
            "required": ["name", "role", "brief"],
        },
    },
    {
        "name": "spawn_subagents",
        "description": "Run several specialist sub-agents IN PARALLEL (each actually executes). "
                       "Great for fanning out deep analysis across endpoints/bundles.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string", "enum": ["js_analyst", "auth_tester", "api_tester", "recon"]},
                            "brief": {"type": "string"},
                        },
                        "required": ["name", "role", "brief"],
                    },
                },
            },
            "required": ["agents"],
        },
    },
    {
        "name": "add_evidence",
        "description": "Attach NEW evidence/PoC to an EXISTING finding you already recorded "
                       "(use the finding id returned by record_finding). Use this when continued "
                       "testing uncovers a stronger PoC or higher-impact proof for an issue that "
                       "was already reported — it becomes a comment on that report.",
        "input_schema": {
            "type": "object",
            "properties": {
                "finding_id": {"type": "integer"},
                "kind": {"type": "string", "enum": ["poc", "retest", "bypass", "comment"]},
                "body_md": {"type": "string", "description": "The new evidence in Markdown."},
            },
            "required": ["finding_id", "body_md"],
        },
    },
    {
        "name": "http_request_as",
        "description": "Send an HTTP request using a stored test IDENTITY's credentials (for authenticated "
                       "testing). Same as http_request but injects the identity's cookie/bearer/header.",
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {"type": "string", "description": "the identity label, e.g. userA"},
                "method": {"type": "string"},
                "url": {"type": "string"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {"type": "string"},
            },
            "required": ["identity", "method", "url"],
        },
    },
    {
        "name": "replay_as",
        "description": "Core IDOR/BOLA test: re-send a previously captured request (by its repeater id) "
                       "as a DIFFERENT identity, and diff the responses. If identity B can read identity A's "
                       "object, that single adjacent (redacted) object proves BOLA. Low volume, one object.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repeater_id": {"type": "integer", "description": "id of a prior http_request/http_request_as"},
                "as_identity": {"type": "string", "description": "the OTHER identity's label"},
            },
            "required": ["repeater_id", "as_identity"],
        },
    },
    {
        "name": "list_identities",
        "description": "List the test identities available for this scan (labels only).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "register_test_account",
        "description": "Provision ONE test account via the target's normal signup endpoint, for authorized "
                       "IDOR/BOLA testing (needs two identities). HARD LIMITS: off unless the operator enabled "
                       "it, capped (default 2), and it will STOP if it meets a CAPTCHA / OTP / email-or-phone "
                       "verification — it never bypasses human-verification. Only use when the program permits "
                       "test accounts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "signup_url": {"type": "string"},
                "label": {"type": "string", "description": "identity label to store, e.g. userB"},
                "body": {"type": "string", "description": "JSON signup payload (from the discovered schema)"},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                "auth_capture": {"type": "string", "description": "where the auth token appears on success "
                                 "(e.g. 'Set-Cookie' or a JSON field name)"},
            },
            "required": ["signup_url", "label", "body"],
        },
    },
    {
        "name": "record_finding",
        "description": "Record a vulnerability finding with a full Markdown report. Returns a finding id. "
                       "The platform automatically runs a skeptical verification pass and (if confirmed) "
                       "delivers it to Telegram for one-click reporting. Write a complete report: "
                       "description, reproduction steps with the exact requests, impact, and remediation. "
                       "For IDOR/BOLA, prove cross-user access with the MINIMUM necessary (one adjacent "
                       "object) and REDACT any real PII — never dump bulk user data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"]},
                "summary": {"type": "string"},
                "report_md": {"type": "string", "description": "Full report in Markdown: "
                              "description, steps to reproduce, impact, remediation."},
            },
            "required": ["title", "severity", "summary", "report_md"],
        },
    },
]

for t in TOOLS:
    if t['name']=='record_finding':
        t['input_schema']['properties']['evidence_ids']={'type':'array','items':{'type':'integer'}}
        t['input_schema']['required'].append('evidence_ids')
TOOLS.extend([
 {'name':'list_analyses','description':'List saved JavaScript/source-map analyses, coverage and artifact IDs for this scan.','input_schema':{'type':'object','properties':{}}},
 {'name':'read_analysis','description':'Read one complete JSON page of a saved analysis section; follow next_offset until null.','input_schema':{'type':'object','properties':{'analysis_id':{'type':'string'},'section':{'type':'string','enum':['request_shapes','files','endpoints','possible_secrets','warnings','auth_signals']},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}},'required':['analysis_id']}},
 {'name':'resolve_lead','description':'Close or promote a lead with a factual reason.','input_schema':{'type':'object','properties':{'lead_id':{'type':'integer'},'status':{'type':'string','enum':['open','chasing','closed','promoted']},'reason':{'type':'string'}},'required':['lead_id','status','reason']}},
 {'name':'list_specs','description':'List saved AST API request shapes.','input_schema':{'type':'object','properties':{}}},
 {'name':'probe_spec','description':'Send one reviewed saved request shape. Supply symbolic values explicitly and confirm static inferences.','input_schema':{'type':'object','properties':{'spec_id':{'type':'integer'},'body':{},'headers':{'type':'object'},'params':{'type':'object'},'confirm_write':{'type':'boolean'},'confirm_shape':{'type':'boolean'}},'required':['spec_id']}}
])
