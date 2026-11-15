"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import {programType} from '../lib/programs';
import {ScopeStatus} from './scope-status';

export function Panel({ title, children, grow }: any) {
  return (
    <div className="panel" style={grow ? { maxHeight: 460 } : {}}>
      <h3>{title}</h3>
      <div className="body">{children}</div>
    </div>
  );
}

export function parseBody(b: string): any {
  try { return JSON.parse(b); } catch { return b; }
}

/* ------------------------- Settings modal ------------------------- */
export function SettingsModal({ onClose }: any) {
  const [cfg, setCfg] = useState<any>(null);
  const [providers, setProviders] = useState<any>({});
  const [test, setTest] = useState<string>("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [mainModels, setMainModels] = useState<Record<string, string>>({});
  const [workerModels, setWorkerModels] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    Promise.all([api.getSettings(), api.providers()]).then(([settings, catalog]) => {
      if (!active) return;
      setCfg({ ...settings, worker_provider: settings.worker_provider || (settings.worker_model ? settings.llm_provider : "") });
      setProviders(catalog);
      setMainModels({ [settings.llm_provider]: settings.llm_model || "" });
      setWorkerModels({ [settings.worker_provider || settings.llm_provider]: settings.worker_model || "" });
    }).catch((error: any) => { if (active) setLoadError(error.message); });
    return () => { active = false; };
  }, []);
  if (!cfg) return <div className="modal-bg" onClick={onClose}><div className="modal" role="dialog" aria-modal="true" aria-labelledby="settings-heading" onClick={(e) => e.stopPropagation()}>
    <h2 id="settings-heading">Settings</h2><p role={loadError ? "alert" : "status"}>{loadError || "Loading settings…"}</p><button onClick={onClose}>Close</button>
  </div></div>;

  const prov = providers[cfg.llm_provider] || {};
  const workerEnabled = Boolean(cfg.worker_provider || cfg.worker_model);
  const workerProvider = cfg.worker_provider || cfg.llm_provider;
  const workerProv = providers[workerProvider] || {};
  const upd = (k: string, v: any) => {
    setCfg((current: any) => ({ ...current, [k]: v }));
    setSaved(false); setTest("");
  };
  const changeMainProvider = (provider: string) => {
    setMainModels((current) => ({ ...current, [cfg.llm_provider]: cfg.llm_model }));
    setCfg((current: any) => ({ ...current, llm_provider: provider,
      llm_model: mainModels[provider] ?? providers[provider]?.models?.[0] ?? "" }));
    setSaved(false); setTest("");
  };
  const changeWorkerProvider = (provider: string) => {
    setWorkerModels((current) => ({ ...current, [workerProvider]: cfg.worker_model }));
    setCfg((current: any) => ({ ...current, worker_provider: provider,
      worker_model: workerModels[provider] || providers[provider]?.models?.[0] || "" }));
    setSaved(false); setTest("");
  };
  const toggleWorker = (enabled: boolean) => {
    setCfg((current: any) => ({ ...current, worker_provider: enabled ? current.llm_provider : "",
      worker_model: enabled ? current.llm_model : "" }));
    setSaved(false); setTest("");
  };
  const credentials = (provider: string, prefix: string) => {
    const key = ({ anthropic: "anthropic_api_key", openai: "openai_api_key", openrouter: "openrouter_api_key", custom: "custom_api_key" } as Record<string, string>)[provider];
    const label = provider === "anthropic" ? "Anthropic (Claude)" : provider === "custom" ? "Custom" : providers[provider]?.label || provider;
    return <>
      {provider === "custom" && <div className="field"><label htmlFor={`${prefix}-base-url`}>Custom base URL</label><input id={`${prefix}-base-url`} disabled={busy} type="url" placeholder="http://localhost:11434/v1" value={cfg.custom_base_url || ""} onChange={(e) => upd("custom_base_url", e.target.value)} /></div>}
      {provider === "openai" && <div className="field"><label htmlFor={`${prefix}-base-url`}>OpenAI base URL</label><input id={`${prefix}-base-url`} disabled={busy} type="url" placeholder="https://api.openai.com/v1" value={cfg.openai_base_url || ""} onChange={(e) => upd("openai_base_url", e.target.value)} /></div>}
      {key && <div className="field"><label htmlFor={`${prefix}-api-key`}>{label} API key{provider === "custom" ? " (optional for local servers)" : ""}</label><input id={`${prefix}-api-key`} disabled={busy} type="password" autoComplete="new-password" spellCheck={false} value={cfg[key] || ""} onChange={(e) => upd(key, e.target.value)} /></div>}
    </>;
  };

  const save = async () => {
    setBusy(true); setTest("");
    try {
      const settings = await api.putSettings(cfg);
      setCfg(settings);
      document.documentElement.style.setProperty("--accent", settings.accent_color);
      setSaved(true); setTimeout(() => setSaved(false), 1500);
    } catch (error: any) { setTest(error.message); }
    finally { setBusy(false); }
  };
  const runTest = async (worker = false) => {
    setBusy(true); setSaved(false); setTest(worker ? "Testing worker connection…" : "Testing main connection…");
    try {
      const settings = await api.putSettings(cfg);
      setCfg(settings);
      const r = await api.testProvider({ provider: worker ? workerProvider : cfg.llm_provider,
        model: worker ? cfg.worker_model || cfg.llm_model : cfg.llm_model });
      setTest(r.ok ? `✓ ${worker ? "Worker" : "Main"}: ${r.reply}` : `✗ ${r.error}`);
    } catch (error: any) { setTest(error.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="settings-heading" onClick={(e) => e.stopPropagation()}>
        <h2 id="settings-heading">Settings</h2>

        <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <h3>Main model</h3>
        <div className="field"><label htmlFor="main-provider">Provider</label>
          <select id="main-provider" disabled={busy} value={cfg.llm_provider} onChange={(e) => changeMainProvider(e.target.value)}>
            {Object.entries(providers).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>

        {credentials(cfg.llm_provider, "main")}
        <div className="field"><label htmlFor="main-model">Model</label>
          <input id="main-model" list="main-models" disabled={busy} value={cfg.llm_model} onChange={(e) => upd("llm_model", e.target.value)} />
          <datalist id="main-models">{(prov.models || []).map((m: string) => <option key={m} value={m} />)}</datalist>
          <small className="muted">Choose a suggestion or enter a model supported by this provider.</small>
        </div>

        {cfg.llm_provider === "anthropic" && (
          <div className="field"><label htmlFor="main-effort">Effort</label>
            <select id="main-effort" disabled={busy} value={cfg.llm_effort} onChange={(e) => upd("llm_effort", e.target.value)}>
              {["low", "medium", "high", "xhigh", "max"].map((x) => <option key={x}>{x}</option>)}
            </select></div>
        )}

        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, margin: "12px 0" }}>
          <label htmlFor="worker-enabled" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input id="worker-enabled" type="checkbox" style={{ width: "auto" }} disabled={busy} checked={workerEnabled} onChange={(e) => toggleWorker(e.target.checked)} />
            Use a separate model for verification and specialists
          </label>
          {!workerEnabled && <p className="muted" style={{ fontSize: 12 }}>These steps use the main provider and model.</p>}
          {workerEnabled && <>
            <h3 style={{ marginTop: 12 }}>Worker model</h3>
            <div className="field"><label htmlFor="worker-provider">Worker provider</label>
              <select id="worker-provider" disabled={busy} value={workerProvider} onChange={(e) => changeWorkerProvider(e.target.value)}>
                {Object.entries(providers).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            </div>
            {workerProvider !== cfg.llm_provider ? credentials(workerProvider, "worker") :
              <p className="muted" style={{ fontSize: 12 }}>Uses the main provider's API key and connection settings.</p>}
            <div className="field"><label htmlFor="worker-model">Worker model</label>
              <input id="worker-model" list="worker-models" disabled={busy} placeholder={cfg.llm_model} value={cfg.worker_model || ""} onChange={(e) => upd("worker_model", e.target.value)} />
              <datalist id="worker-models">{(workerProv.models || []).map((m: string) => <option key={m} value={m} />)}</datalist>
            </div>
            <button disabled={busy} onClick={() => runTest(true)}>Test worker connection</button>
          </>}
        </div>

        <div className="field"><label>Agent max iterations</label>
          <input value={cfg.agent_max_iterations} onChange={(e) => upd("agent_max_iterations", e.target.value)} /></div>

        <div className="repeater-grid">
          <div className="field"><label>Telegram bot token</label>
            <input type="password" value={cfg.telegram_bot_token} onChange={(e) => upd("telegram_bot_token", e.target.value)} /></div>
          <div className="field"><label>Telegram chat id</label>
            <input value={cfg.telegram_chat_id} onChange={(e) => upd("telegram_chat_id", e.target.value)} /></div>
        </div>

        <h2 style={{ fontSize: 14, marginTop: 10 }}>HackerOne</h2>
        <div className="repeater-grid">
          <div className="field"><label>API username (token identifier)</label>
            <input value={cfg.hackerone_username} onChange={(e) => upd("hackerone_username", e.target.value)} /></div>
          <div className="field"><label>API token</label>
            <input type="password" value={cfg.hackerone_token} onChange={(e) => upd("hackerone_token", e.target.value)} /></div>
          <div className="field"><label>Sync scopes on startup</label>
            <select value={cfg.h1_auto_sync} onChange={(e) => upd("h1_auto_sync", e.target.value)}><option value="true">on</option><option value="false">off</option></select></div>
          <div className="field"><label>Parallel scope requests (1–16)</label>
            <input value={cfg.h1_sync_concurrency} onChange={(e) => upd("h1_sync_concurrency", e.target.value)} /></div>
          <div className="field"><label>Requests per second (1–10)</label>
            <input value={cfg.h1_requests_per_second} onChange={(e) => upd("h1_requests_per_second", e.target.value)} /></div>
          <div className="field"><label>Refresh scopes older than (hours, 0 = always)</label>
            <input value={cfg.h1_scope_ttl_hours} onChange={(e) => upd("h1_scope_ttl_hours", e.target.value)} /></div>
        </div>

        <h2 style={{ fontSize: 14, marginTop: 10 }}>Automation</h2>
        <div className="repeater-grid">
          <div className="field"><label>Verify confidence threshold (%)</label>
            <input value={cfg.verify_threshold} onChange={(e) => upd("verify_threshold", e.target.value)} /></div>
          <div className="field"><label>Auto-verify findings</label>
            <select value={cfg.auto_verify} onChange={(e) => upd("auto_verify", e.target.value)}><option value="true">on</option><option value="false">off</option></select></div>
          <div className="field"><label>Auto-send confirmed to Telegram</label>
            <select value={cfg.auto_telegram} onChange={(e) => upd("auto_telegram", e.target.value)}><option value="true">on</option><option value="false">off</option></select></div>
          <div className="field"><label>Auto-submit to HackerOne (⚠ review first!)</label>
            <select value={cfg.auto_submit} onChange={(e) => upd("auto_submit", e.target.value)}><option value="false">off</option><option value="true">on</option></select></div>
          <div className="field"><label>Agent workspace shell (⚠ runs commands on this machine)</label>
            <select value={cfg.enable_shell} onChange={(e) => upd("enable_shell", e.target.value)}><option value="false">off</option><option value="true">on</option></select></div>
          <div className="field"><label>Auto test-account signup (⚠ only if the program allows it)</label>
            <select value={cfg.allow_account_provisioning} onChange={(e) => upd("allow_account_provisioning", e.target.value)}><option value="false">off</option><option value="true">on</option></select></div>
          <div className="field"><label>Max test accounts (cap)</label>
            <input value={cfg.max_test_accounts} onChange={(e) => upd("max_test_accounts", e.target.value)} /></div>
        </div>

        <details><summary>Usage, limits, appearance and MCP</summary>
        <div className="repeater-grid">{[['max_steps_per_task','Total model steps (0 = unlimited)'],['budget_usd_per_task','Task budget USD'],['max_subagents','Parallel specialists'],['subagent_max_iterations','Specialist iterations'],['max_passes','Continuation passes']].map(([key,label])=><div className="field" key={key}><label>{label}</label><input type="number" min="0" value={cfg[key]} onChange={e=>upd(key,e.target.value)}/></div>)}</div>
        <div className="field"><label>Enforce scope</label><select value={cfg.scope_enforced} onChange={e=>upd('scope_enforced',e.target.value)}><option value="true">on</option><option value="false">off</option></select></div>
        <div className="field"><label>Accent</label><input type="color" value={cfg.accent_color} onChange={e=>upd('accent_color',e.target.value)}/></div>
        <p className="muted">Use trusted MCP servers with explicit tool allowlists. Servers must enforce their own outbound scope. Local stdio also requires shell enabled.</p>
        <select value={cfg.enable_mcp} onChange={e=>upd('enable_mcp',e.target.value)}><option value="false">MCP disabled</option><option value="true">MCP enabled</option></select>
        <textarea aria-label="MCP servers JSON" rows={6} value={cfg.mcp_servers} onChange={e=>upd('mcp_servers',e.target.value)}/>
        </details>
        </fieldset>
        {test && <div role="status" className="mono" style={{ fontSize: 12, margin: "6px 0", color: test.startsWith("✓") ? "var(--accent)" : "var(--crit)" }}>{test}</div>}
        <div className="row" style={{ justifyContent: "space-between" }}>
          <button disabled={busy} onClick={() => runTest()}>Test main connection</button>
          <div className="row">
            <button onClick={onClose}>Close</button>
            <button disabled={busy} className="primary" onClick={save}>{busy ? "Working…" : saved ? "Saved ✓" : "Save"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------- Routines view ------------------------- */
export function RoutinesView({ programs }: any) {
  const [rows, setRows] = useState<any[]>([]);
  const [form, setForm] = useState({ name: "", target: "", objective: "", interval_minutes: 1440, program_id: "" });
  const load = () => api.listRoutines().then(setRows).catch(() => {});
  useEffect(() => { load(); }, []);
  const create = async () => {
    await api.createRoutine({ ...form, program_id: form.program_id ? Number(form.program_id) : null });
    setForm({ name: "", target: "", objective: "", interval_minutes: 1440, program_id: "" });
    load();
  };
  return (
    <div>
      <div className="panel" style={{ marginBottom: 14, maxHeight: "none" }}>
        <h3>New routine (recurring scan)</h3>
        <div className="body">
          <div className="repeater-grid">
            <input placeholder="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input placeholder="https://target" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} />
            <input placeholder="objective" value={form.objective} onChange={(e) => setForm({ ...form, objective: e.target.value })} />
            <input type="number" placeholder="interval minutes" value={form.interval_minutes}
              onChange={(e) => setForm({ ...form, interval_minutes: Number(e.target.value) })} />
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <select value={form.program_id} onChange={(e) => setForm({ ...form, program_id: e.target.value })}>
              <option value="">— no program —</option>
              {programs.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <button className="primary" disabled={!form.name || !form.target} onClick={create}>Create</button>
          </div>
        </div>
      </div>
      <div className="panel" style={{ maxHeight: "none" }}>
        <h3>Routines ({rows.length})</h3>
        <div className="body">
          {rows.length === 0 && <div className="empty">No routines.</div>}
          {rows.map((r) => (
            <div key={r.id} className="finding">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{r.name}</strong>
                <div className="row">
                  <span className="pill">every {r.interval_minutes}m</span>
                  <span className={"badge " + (r.enabled ? "running" : "stopped")}>{r.enabled ? "on" : "off"}</span>
                  <button onClick={() => api.toggleRoutine(r.id).then(load)}>toggle</button>
                  <button className="danger" onClick={() => api.deleteRoutine(r.id).then(load)}>del</button>
                </div>
              </div>
              <div className="mono muted" style={{ fontSize: 12 }}>{r.target} · last: {r.last_run || "never"}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------- Files view ------------------------- */
export function FilesView() {
  const [files, setFiles] = useState<any[]>([]);
  useEffect(() => { api.files().then(setFiles).catch(() => {}); }, []);
  return (
    <div className="panel" style={{ maxHeight: "none" }}>
      <h3>Agent files ({files.length})</h3>
      <div className="body">
        {files.length === 0 && <div className="empty">No artifacts yet (screenshots & downloads appear here).</div>}
        {files.map((f) => (
          <div key={f.name} className="endpoint">
            <a href={(process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000") + f.url} target="_blank" rel="noreferrer">{f.name}</a>
            <span className="muted"> · {(f.size / 1024).toFixed(1)} KB · {f.modified}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------- Overview stats strip (ExecHawk-style) ------------------------- */
function Stat({ label, value, accent }: any) {
  return (
    <div style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 8,
      padding: "8px 12px", minWidth: 92 }}>
      <div style={{ fontSize: 20, fontWeight: 700, color: accent || "var(--text)" }}>{value}</div>
      <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
    </div>
  );
}

export function OverviewStrip({ taskId, refreshKey }: any) {
  const [ov, setOv] = useState<any>(null);
  useEffect(() => { api.overview(taskId).then(setOv).catch(() => {}); }, [taskId, refreshKey]);
  if (!ov) return null;
  const dur = ov.duration_seconds != null
    ? `${Math.floor(ov.duration_seconds / 60)}m ${ov.duration_seconds % 60}s` : "—";
  const f = ov.findings || {};
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
      <Stat label="Plan steps" value={ov.plan.total} />
      <Stat label="Endpoints" value={ov.endpoints} accent="var(--accent2)" />
      <Stat label="Findings" value={f.total} accent="var(--warn)" />
      <Stat label="Confirmed" value={f.confirmed} accent="var(--accent)" />
      <Stat label="Reports" value={ov.reports.submitted} accent="var(--accent)" />
      <Stat label="Pend. addenda" value={ov.pending_addenda} />
      <Stat label="Leads" value={ov.leads} />
      <Stat label="Intel" value={ov.intel} />
      <Stat label="Memory" value={ov.memory} />
      <Stat label="Sub-agents" value={ov.subagents} />
      <Stat label="Assets" value={ov.assets ?? 0} />
      <Stat label="Graph n·e" value={`${ov.graph?.nodes ?? 0}·${ov.graph?.edges ?? 0}`} accent="var(--accent2)" />
      <Stat label="Repeater" value={ov.repeater} />
      <Stat label="Files" value={ov.files} />
      <Stat label="Duration" value={dur} />
      {ov.cost?.price_unknown&&<span className="pill">Partial cost: unknown model pricing</span>}
      {ov.cost && <Stat label="Cost" value={ov.cost.usd ? `$${ov.cost.usd.toFixed(3)}` : `${((ov.cost.tokens_in + ov.cost.tokens_out) / 1000).toFixed(1)}k tok`} accent="var(--warn)" />}
    </div>
  );
}

/* ------------------------- Ideas Queue ------------------------- */
export function IdeasQueue({ taskId, refreshKey }: any) {
  const [ideas, setIdeas] = useState<any[]>([]);
  const load = () => api.ideas(taskId).then(setIdeas).catch(() => {});
  useEffect(() => { load(); }, [taskId, refreshKey]);
  const sev: any = { 1: "sev-critical", 2: "sev-high", 3: "sev-medium", 4: "sev-low", 5: "sev-info" };
  return (
    <Panel title={`Ideas queue (${ideas.filter((i) => i.status === "queued").length} open)`}>
      {ideas.length === 0 ? <div className="empty">—</div> :
        ideas.map((i) => (
          <div key={i.id} className="endpoint" style={{ opacity: i.status === "done" || i.status === "dropped" ? 0.4 : 1 }}>
            <span className={"badge " + (sev[i.priority] || "")}>P{i.priority}</span> {i.text}
            <span className="muted"> · {i.status}</span>
          </div>
        ))}
    </Panel>
  );
}

/* ------------------------- Assets panel ------------------------- */
export function AssetsPanel({ taskId, refreshKey }: any) {
  const [assets, setAssets] = useState<any[]>([]);
  useEffect(() => { api.assets(taskId).then(setAssets).catch(() => {}); }, [taskId, refreshKey]);
  return (
    <Panel title={`Assets (${assets.length})`}>
      {assets.length === 0 ? <div className="empty">—</div> :
        assets.map((a) => (
          <div key={a.id} className="endpoint">
            <span className="kind-tag">{a.kind}</span> {a.host}
            {a.source && <span className="muted"> · {a.source}</span>}
          </div>
        ))}
    </Panel>
  );
}

/* ------------------------- Knowledge Graph panel (lightweight SVG) ------------------------- */
export function GraphPanel({ taskId, refreshKey }: any) {
  const [g, setG] = useState<any>({ nodes: [], edges: [] });
  useEffect(() => { api.graph(taskId).then(setG).catch(() => {}); }, [taskId, refreshKey]);
  const nodes = g.nodes || [];
  const edges = g.edges || [];
  // simple circular layout
  const R = 120, cx = 160, cy = 140;
  const pos: any = {};
  nodes.forEach((n: any, idx: number) => {
    const ang = (idx / Math.max(nodes.length, 1)) * 2 * Math.PI;
    pos[n.id] = { x: cx + R * Math.cos(ang), y: cy + R * Math.sin(ang) };
  });
  const kindColor: any = { host: "#4aa8ff", endpoint: "#39d98a", finding: "#ff5370", object: "#ffb454" };
  return (
    <Panel title={`Knowledge graph (${nodes.length} nodes · ${edges.length} edges)`} grow>
      {nodes.length === 0 ? <div className="empty">—</div> : (
        <svg viewBox="0 0 320 280" style={{ width: "100%", height: 260 }}>
          {edges.map((e: any, i: number) => {
            const a = pos[e.src], b = pos[e.dst];
            if (!a || !b) return null;
            return <g key={i}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#2a3346" strokeWidth={1} />
              <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2} fill="#7d8699" fontSize={7}>{e.rel}</text>
            </g>;
          })}
          {nodes.map((n: any) => {
            const p = pos[n.id]; if (!p) return null;
            return <g key={n.id}>
              <circle cx={p.x} cy={p.y} r={5} fill={kindColor[n.kind] || "#7d8699"} />
              <text x={p.x + 7} y={p.y + 3} fill="#d7dce5" fontSize={7}>{(n.label || n.key).slice(0, 22)}</text>
            </g>;
          })}
        </svg>
      )}
    </Panel>
  );
}

/* ------------------------- Health panel ------------------------- */
export function HealthPanel({ taskId, refreshKey }: any) {
  const [h, setH] = useState<any>(null);
  useEffect(() => { api.taskHealth(taskId).then(setH).catch(() => {}); }, [taskId, refreshKey]);
  if (!h) return null;
  return (
    <Panel title="Session health">
      <div className="row" style={{ gap: 16, marginBottom: 8 }}>
        <span>Tool errors: <b className={h.total_errors ? "sev-high" : "sev-low"}>{h.total_errors}</b></span>
        <span>Scope blocks: <b>{h.scope_blocks}</b></span>
      </div>
      {Object.entries(h.tool_errors || {}).map(([k, v]: any) => (
        <div key={k} className="endpoint"><span className="sev-high">{v}×</span> {k}</div>
      ))}
      {(h.warnings || []).map((w: string, i: number) => <div key={i} className="timeline-item muted">{w}</div>)}
      {h.total_errors === 0 && (h.warnings || []).length === 0 && <div className="empty">✓ healthy</div>}
    </Panel>
  );
}

/* ------------------------- Conversation / Instruct box ------------------------- */
export function ConversationBox({ taskId, running }: any) {
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const send = async () => {
    if (!text.trim()) return;
    try { await api.instruct(taskId, text); setMsg("queued — delivered after the current step"); setText(""); }
    catch (e: any) { setMsg(e.message); }
    setTimeout(() => setMsg(""), 3000);
  };
  return (
    <div className="panel" style={{ marginTop: 14, maxHeight: "none" }}>
      <h3>Instruct agent {running ? <span className="pill sev-low">running</span> : <span className="pill muted">idle</span>}</h3>
      <div className="body">
        <div className="row">
          <input placeholder="Message… (queues it; delivered when the current step finishes)"
            value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
          <button className="primary" disabled={!running} onClick={send}>Queue</button>
        </div>
        {msg && <div className="mono" style={{ fontSize: 11, color: "var(--accent)", marginTop: 6 }}>{msg}</div>}
        {!running && <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>Start a scan to steer it live.</div>}
      </div>
    </div>
  );
}

/* ------------------------- Workspace panel (agent's scripts/files) ------------------------- */
export function WorkspacePanel({ taskId, refreshKey }: any) {
  const [files, setFiles] = useState<any[]>([]);
  const [sel, setSel] = useState<string>("");
  const [content, setContent] = useState<string>("");
  useEffect(() => { api.workspace(taskId).then(setFiles).catch(() => {}); }, [taskId, refreshKey]);
  const open = async (path: string) => {
    setSel(path);
    try { const r = await api.workspaceFile(taskId, path); setContent(r.content); } catch { setContent(""); }
  };
  if (files.length === 0) return null;
  return (
    <div className="panel" style={{ marginTop: 14, maxHeight: "none" }}>
      <h3>Workspace — agent files ({files.length})</h3>
      <div className="body">
        <div className="repeater-grid">
          <div>
            {files.map((f) => (
              <div key={f.path} className="endpoint" style={{ cursor: "pointer", color: sel === f.path ? "var(--accent)" : "" }}
                onClick={() => open(f.path)}>
                {f.path} <span className="muted">· {(f.size / 1024).toFixed(1)}KB</span>
              </div>
            ))}
          </div>
          <div>
            {sel ? <pre style={{ maxHeight: 300 }}>{content}</pre> : <div className="empty">Click a file to view.</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------- Knowledge view (Memory · Skills · Pending Addenda) ------------------------- */
export function KnowledgeView() {
  const [mem, setMem] = useState<any[]>([]);
  const [skills, setSkills] = useState<any[]>([]);
  const [pending, setPending] = useState<any[]>([]);
  const [tools, setTools] = useState<any>(null);
  const [editId, setEditId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => {
    api.memory().then(setMem).catch(() => {});
    api.skills().then(setSkills).catch(() => {});
    api.pendingAddenda().then(setPending).catch(() => {});
    api.reconTools().then(setTools).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 2000); };

  return (
    <div>
      {msg && <div style={{ position: "fixed", top: 60, right: 20, background: "var(--panel)", border: "1px solid var(--accent)", borderRadius: 6, padding: "6px 12px", zIndex: 80, fontSize: 12 }}>{msg}</div>}
      <div className="panel" style={{ marginBottom: 14, maxHeight: "none" }}>
        <h3>Pending addenda — drafts to post as comments ({pending.length})</h3>
        <div className="body">
          {pending.length === 0 && <div className="empty">No pending drafts. New evidence on reported findings appears here.</div>}
          {pending.map((a) => (
            <div key={a.id} className="finding" style={{ borderLeft: "3px solid var(--warn)", paddingLeft: 10 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span><span className={"badge sev-" + a.severity}>{a.severity}</span> <strong>{a.finding_title}</strong></span>
                {a.h1_report_id && <a href={a.h1_report_url} target="_blank" rel="noreferrer" className="pill">H1 #{a.h1_report_id}</a>}
              </div>
              {editId === a.id ? (
                <textarea rows={6} value={editText} onChange={(e) => setEditText(e.target.value)} style={{ marginTop: 6 }} />
              ) : (
                <pre style={{ marginTop: 6 }}>{a.body_md}</pre>
              )}
              <div className="row" style={{ marginTop: 6 }}>
                {editId === a.id ? (
                  <>
                    <button className="primary" onClick={async () => { await api.editAddendum(a.id, { body_md: editText }); setEditId(null); load(); flash("saved"); }}>Save</button>
                    <button onClick={() => setEditId(null)}>Cancel</button>
                  </>
                ) : (
                  <>
                    <button className="primary" onClick={async () => { await copy(a.body_md); flash("copied — open the report and paste"); }}>📋 Copy</button>
                    <button onClick={() => { setEditId(a.id); setEditText(a.body_md); }}>✏️ Edit</button>
                    <button onClick={async () => { await api.addendumTelegram(a.id); load(); flash("sent to Telegram"); }}>✈️ Telegram</button>
                    <button className="danger" onClick={async () => { await api.editAddendum(a.id, { discard: true }); load(); }}>Discard</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid">
        <div className="panel" style={{ maxHeight: 460 }}>
          <h3>Memory — learned facts ({mem.length})</h3>
          <div className="body">
            {mem.length === 0 && <div className="empty">The agent stores durable facts here as it learns them.</div>}
            {mem.map((m) => (
              <div key={m.id} className="timeline-item">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span><span className="kind-tag">{m.category}</span>{m.content}</span>
                  <button onClick={async () => { await api.deleteMemory(m.id); load(); }} style={{ fontSize: 10, padding: "1px 6px" }}>×</button>
                </div>
                <div className="ts">{m.scope}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="panel" style={{ maxHeight: 460 }}>
          <h3>Skills — loaded playbooks ({skills.filter((s) => s.enabled).length}/{skills.length})</h3>
          <div className="body">
            {tools && (
              <details style={{ marginBottom: 8 }}>
                <summary className="muted" style={{ cursor: "pointer", fontSize: 12 }}>
                  🛠 Recon tools installed on this machine: {tools.installed_count}/{tools.total_checked}
                </summary>
                <div className="mono" style={{ fontSize: 11, marginTop: 4 }}>
                  <div style={{ color: "var(--accent)" }}>{tools.installed.join(", ") || "none"}</div>
                  <div className="muted" style={{ marginTop: 4 }}>missing: {tools.missing.join(", ")}</div>
                </div>
              </details>
            )}
          </div>
          <h3 style={{ borderTop: "1px solid var(--border)" }}>
            Playbooks
            <label className="pill" style={{ cursor: "pointer" }}>
              + upload
              <input type="file" multiple accept=".md,.json,.yaml,.yml,.txt" style={{ display: "none" }}
                onChange={async (e) => {
                  const fl = Array.from(e.target.files || []);
                  const files = await Promise.all(fl.map(async (f) => ({ filename: f.name, content: await f.text() })));
                  if (files.length) {
                    try {
                      const r = await api.uploadSkills(files);
                      const total = (r.added || 0) + (r.replaced || 0);
                      flash(total ? `✓ ${total} skill(s) loaded: ${(r.names || []).join(", ")}` : "no valid skills found in file");
                      load();
                    } catch (err: any) { flash("upload failed: " + err.message); }
                    e.target.value = "";  // allow re-uploading the same file
                  }
                }} />
            </label>
          </h3>
          <div className="body">
            {skills.map((s) => (
              <div key={s.name} className="timeline-item">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span><span className="kind-tag">{s.category}</span><strong>{s.name}</strong>
                    {s.is_custom && <span className="pill" style={{ marginLeft: 6 }}>custom</span>}</span>
                  <span className="row">
                    <button onClick={async () => { await api.toggleSkill(s.name); load(); }}
                      className={s.enabled ? "primary" : ""} style={{ fontSize: 10, padding: "2px 8px" }}>
                      {s.enabled ? "on" : "off"}
                    </button>
                    {s.is_custom && <button className="danger" style={{ fontSize: 10, padding: "2px 6px" }}
                      onClick={async () => { await api.deleteSkill(s.name); load(); }}>×</button>}
                  </span>
                </div>
                <details><summary className="muted" style={{ fontSize: 11, cursor: "pointer" }}>{s.summary}</summary>
                  <pre style={{ fontSize: 10 }}>{s.playbook}</pre></details>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------- Chat view ------------------------- */
export function ChatView() {
  const [mode, setMode] = useState("general");
  const [thread, setThread] = useState("general");
  const [msgs, setMsgs] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const load = (t: string) => api.chatHistory(t).then(setMsgs).catch(() => setMsgs([]));
  useEffect(() => { load(thread); }, [thread]);

  const send = async () => {
    if (!input.trim()) return;
    setBusy(true);
    const mine = input; setInput("");
    setMsgs((m) => [...m, { role: "user", content: mine }]);
    try {
      const r = await api.chatSend({ thread, mode, content: mine });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", content: "⚠️ " + e.message }]);
    }
    setBusy(false);
  };

  return (
    <div className="panel" style={{ maxHeight: "none" }}>
      <h3>
        Assistant
        <span className="row">
          <select value={mode} onChange={(e) => setMode(e.target.value)} style={{ width: 130 }}>
            <option value="general">General</option>
            <option value="triage">Triage</option>
            <option value="retest">Retest</option>
          </select>
          <input style={{ width: 160 }} value={thread} onChange={(e) => setThread(e.target.value)}
            title="thread id, e.g. general or finding:3" />
        </span>
      </h3>
      <div className="body" style={{ minHeight: 320 }}>
        {msgs.length === 0 && <div className="empty">Ask anything — recon ideas, payloads, triager replies…</div>}
        {msgs.map((m, i) => (
          <div key={i} style={{ margin: "8px 0" }}>
            <span className="kind-tag">{m.role}</span>
            <span style={{ whiteSpace: "pre-wrap" }}>{m.content}</span>
          </div>
        ))}
        <div className="row" style={{ marginTop: 10 }}>
          <input placeholder="message…" value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()} />
          <button className="primary" disabled={busy} onClick={send}>{busy ? "…" : "Send"}</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------- Finding card ------------------------- */
async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); } catch {}
}

export function FindingCard({ f, onChange }: any) {
  const [proof,setProof]=useState((f.evidence_ids||[]).join(', '));
  const [adds, setAdds] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("poc");
  const [body, setBody] = useState("");
  const [msg, setMsg] = useState("");
  const loadAdds = () => api.addenda(f.id).then(setAdds).catch(() => {});
  useEffect(() => { if (open) loadAdds(); }, [open]);

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 2500); };

  const addAddendum = async () => {
    if (!body.trim()) return;
    await api.createAddendum({ finding_id: f.id, kind, body_md: body });
    setBody(""); loadAdds();
  };
  const verify = async () => { flash("verifying…"); try { await api.verifyFinding(f.id); onChange(); flash("verified"); } catch (e: any) { flash(e.message); } };
  const submit = async () => { flash("submitting…"); try { const r = await api.submitFinding(f.id); onChange(); flash("submitted #" + r.id); } catch (e: any) { flash(e.message); } };
  const tele = async () => { flash("sending…"); try { await api.findingTelegram(f.id); flash("sent to Telegram"); } catch (e: any) { flash(e.message); } };
  const copyReport = async () => {
    try { const p = await api.findingPayload(f.id); await copy(`# ${p.title}\n\n${p.vulnerability_information}\n\n## Impact\n${p.impact}`); flash("report copied"); }
    catch (e: any) { flash(e.message); }
  };

  const relBadge: any = { bypass: "sev-critical", same: "sev-info", new: "sev-low" };
  return (
    <div className="finding">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{f.title}</strong>
        <div className="row">
          {f.weakness && <span className="pill">{f.weakness}</span>}
          {f.confidence > 0 && <span className={"pill " + (f.verified ? "sev-low" : "sev-medium")}>{f.confidence}% {f.verified ? "✓" : ""}</span>}
          {f.relation && <span className={"pill " + (relBadge[f.relation] || "")}>{f.relation}</span>}
          <span className={"badge sev-" + f.severity}>{f.severity}</span>
          <select value={f.status} onChange={(e) => api.setFindingStatus(f.id, e.target.value).then(onChange)} style={{ width: 110 }}>
            {["new", "confirmed", "duplicate", "fixed", "reported"].map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
      </div>
      <div className="muted" style={{ fontSize: 12, margin: "4px 0" }}>{f.summary}</div>
      <details><summary>Proof requests · quality {f.evidence_score||0}/100</summary><div className="row"><input aria-label="Proof request IDs" value={proof} onChange={e=>setProof(e.target.value)} placeholder="12, 15"/><button onClick={async()=>{try{const ids=proof.split(',').filter((x:string)=>x.trim()).map(Number);if(ids.some((i:number)=>!Number.isInteger(i)||i<1))throw Error('Positive request IDs required');await api.linkEvidence(f.id,ids);onChange();flash('Linked; verify again');}catch(e:any){flash(e.message);}}}>Link evidence</button></div></details>
      {f.verdict && <div className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{f.verdict}</div>}

      <div className="row" style={{ gap: 6, margin: "8px 0", flexWrap: "wrap" }}>
        <button onClick={verify}>🔍 Verify</button>
        <button onClick={copyReport}>📋 Copy report</button>
        <button onClick={tele}>✈️ Telegram</button>
        {f.h1_report_id
          ? <a href={f.h1_report_url} target="_blank" rel="noreferrer"><button className="primary">✅ #{f.h1_report_id}</button></a>
          : <button className="primary" onClick={submit}>🚀 Submit to HackerOne</button>}
        {msg && <span className="mono" style={{ fontSize: 11, color: "var(--accent)" }}>{msg}</span>}
      </div>

      <details><summary className="muted" style={{ cursor: "pointer", fontSize: 12 }}>report</summary>
        <pre>{f.report_md}</pre></details>
      <a className="muted" style={{ fontSize: 11, cursor: "pointer" }} onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} evidence / addenda ({adds.length})
      </a>
      {open && (
        <div style={{ marginTop: 6 }}>
          {adds.map((a) => (
            <div key={a.id} className="timeline-item">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span><span className="kind-tag">{a.kind}</span><span style={{ whiteSpace: "pre-wrap" }}>{a.body_md}</span></span>
                <span className="row">
                  <button onClick={async () => { const p = await api.addendumPayload(a.id); await copy(p.text); flash("comment copied"); }}>📋 Copy</button>
                  <button onClick={async () => { try { await api.addendumTelegram(a.id); flash("comment → Telegram"); } catch (e: any) { flash(e.message); } }}>✈️</button>
                </span>
              </div>
            </div>
          ))}
          <div className="row" style={{ marginTop: 6 }}>
            <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 110 }}>
              {["poc", "comment", "retest", "bypass"].map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <input placeholder="new evidence / note…" value={body} onChange={(e) => setBody(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addAddendum()} />
            <button onClick={addAddendum}>add</button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------- New task modal ------------------------- */
export function NewTaskModal({ programs, onClose, onCreated }: any) {
  const [target, setTarget] = useState("");
  const [objective, setObjective] = useState("");
  const [programId, setProgramId] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [providers, setProviders] = useState<any>({});
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api.providers().then(setProviders).catch(() => {}); }, []);

  const create = async () => {
    setBusy(true); setErr("");
    try {
      const t = await api.createTask({
        target, objective, program_id: programId ? Number(programId) : null,
        provider: provider || null, model: model || null,
      });
      onCreated(t);
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };
  const provModels = provider && providers[provider] ? providers[provider].models : [];
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New scan</h2>
        <div className="field"><label>Target URL</label>
          <input placeholder="https://app.example.com" value={target} onChange={(e) => setTarget(e.target.value)} /></div>
        <div className="field"><label>Objective (optional)</label>
          <textarea rows={2} value={objective} onChange={(e) => setObjective(e.target.value)} /></div>
        <div className="field"><label>Program (enforces scope)</label>
          <select value={programId} onChange={(e) => setProgramId(e.target.value)}>
            <option value="">— none (blocklist only) —</option>
            {programs.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select></div>
        <div className="repeater-grid">
          <div className="field"><label>Provider (optional override)</label>
            <select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }}>
              <option value="">— use default —</option>
              {Object.entries(providers).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}
            </select></div>
          <div className="field"><label>Model (optional override)</label>
            <input list="tmodels" value={model} onChange={(e) => setModel(e.target.value)} />
            <datalist id="tmodels">{provModels.map((m: string) => <option key={m} value={m} />)}</datalist></div>
        </div>
        {err && <div className="sev-high" style={{ fontSize: 12, marginBottom: 10 }}>{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !target} onClick={create}>{busy ? "Launching…" : "Launch agent"}</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------- New program modal ------------------------- */
export function NewProgramModal({ onClose, onCreated }: any) {
  const [name, setName] = useState(""); const [platform, setPlatform] = useState("manual");
  const [ptype, setPtype] = useState("bounty");
  const [scopeIn, setScopeIn] = useState(""); const [scopeOut, setScopeOut] = useState("");
  const [policy, setPolicy] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const create = async () => {
    setBusy(true); setErr("");
    try {
      await api.createProgram({
        name, platform, program_type: ptype,
        scope_in: scopeIn.split(/[\n,]/).map((s) => s.trim()).filter(Boolean),
        scope_out: scopeOut.split(/[\n,]/).map((s) => s.trim()).filter(Boolean),
        policy,
      });
      onCreated();
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Add program</h2>
        <div className="field"><label>Name</label><input value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div className="repeater-grid">
          <div className="field"><label>Platform</label>
            <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="manual">manual</option><option value="hackerone">hackerone</option>
            </select></div>
          <div className="field"><label>Type</label>
            <select value={ptype} onChange={(e) => setPtype(e.target.value)}>
              <option value="bounty">Bounty</option>
              <option value="vdp">VDP (disclosure only)</option>
              <option value="unknown">Unknown</option>
            </select></div>
        </div>
        <div className="field"><label>In-scope hosts (comma / newline). Supports *.example.com</label>
          <textarea rows={3} value={scopeIn} onChange={(e) => setScopeIn(e.target.value)} /></div>
        <div className="field"><label>Out-of-scope hosts</label>
          <textarea rows={2} value={scopeOut} onChange={(e) => setScopeOut(e.target.value)} /></div>
        <div className="field"><label>Policy notes (optional)</label>
          <textarea rows={2} value={policy} onChange={(e) => setPolicy(e.target.value)} /></div>
        {err && <div className="sev-high" style={{ fontSize: 12, marginBottom: 10 }}>{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !name} onClick={create}>Save</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------- Program icon + type badge ------------------------- */
export function ProgramIcon({ p, size = 26 }: any) {
  const colors = ["#39d98a", "#4aa8ff", "#ffb454", "#ff5370", "#a78bfa"];
  const c = colors[(p.name || "?").charCodeAt(0) % colors.length];
  if (p.icon_url) {
    return <img src={p.icon_url} alt="" style={{ width: size, height: size, borderRadius: 6, objectFit: "cover" }}
      onError={(e: any) => { e.target.style.display = "none"; }} />;
  }
  return (
    <div style={{ width: size, height: size, borderRadius: 6, background: c + "22", color: c,
      display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: size * 0.5 }}>
      {(p.name || "?").trim()[0]?.toUpperCase()}
    </div>
  );
}

const TYPE_BADGE: any = { bounty: { t: "BOUNTY", c: "sev-low" }, vdp: { t: "VDP", c: "sev-medium" }, unknown: {t: 'UNKNOWN', c: 'sev-info'} };
export function TypeBadge({ type }: any) {
  const b = TYPE_BADGE[programType(type)];
  return <span className={"badge " + b.c}>{b.t}</span>;
}

/* ------------------------- Program detail modal ------------------------- */
export function ProgramDetailModal({ program, onClose, onScan, onRetryScope }: any) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const [idents, setIdents] = useState<any[]>([]);
  const [showId, setShowId] = useState(false);
  const [nid, setNid] = useState({ label: "userA", auth_type: "cookie", header_name: "", material: "", username: "" });
  const loadIds = () => api.identities(program.id).then(setIdents).catch(() => {});
  useEffect(() => { loadIds(); }, []);
  const addId = async () => {
    await api.createIdentity({ ...nid, program_id: program.id });
    setNid({ label: "userB", auth_type: "cookie", header_name: "", material: "", username: "" });
    setShowId(false); loadIds();
  };
  const scan = async () => {
    setBusy(true); setErr("");
    try { await onScan(program); } catch (e: any) { setErr(e.message); setBusy(false); }
  };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ gap: 12, marginBottom: 12 }}>
          <ProgramIcon p={program} size={40} />
          <div>
            <h2 style={{ margin: 0 }}>{program.name}</h2>
            <div className="row" style={{ gap: 8 }}>
              <TypeBadge type={program.program_type} />
              <span className="pill">{program.platform}</span>
              {program.handle && <span className="pill mono">{program.handle}</span>}
            </div>
          </div>
        </div>
        <ScopeStatus program={program} onRetry={onRetryScope} />
        {(program.scope_assets || []).length > 0 && <details><summary>Structured scope assets ({program.scope_assets.length})</summary>
          {program.scope_assets.map((asset: any, index: number) => <div className="endpoint" key={index}>
            <b>{asset.asset_identifier}</b> · {asset.asset_type} · {asset.normalization_status}
            <div className="muted">{asset.normalization_reason}</div>
            {asset.instruction && <pre>{asset.instruction}</pre>}
          </div>)}
        </details>}
        <div className="field"><label>In scope ({(program.scope_in || []).length})</label>
          <pre style={{ maxHeight: 140 }}>{(program.scope_in || []).join("\n") || "— (scope still syncing or none)"}</pre></div>
        {(program.scope_out || []).length > 0 && (
          <div className="field"><label>Out of scope</label>
            <pre style={{ maxHeight: 80 }}>{(program.scope_out || []).join("\n")}</pre></div>
        )}
        {program.policy && (
          <div className="field"><label>Policy</label>
            <pre style={{ maxHeight: 180, whiteSpace: "pre-wrap" }}>{program.policy}</pre></div>
        )}
        {program.handle && (
          <a href={`https://hackerone.com/${program.handle}`} target="_blank" rel="noreferrer"
            className="muted" style={{ fontSize: 12 }}>↗ open on HackerOne</a>
        )}

        {/* Test identities for authenticated IDOR/BOLA testing */}
        <div className="field" style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label style={{ margin: 0 }}>Test identities for IDOR/BOLA ({idents.length})</label>
            <a onClick={() => setShowId(!showId)} style={{ cursor: "pointer", fontSize: 11 }}>+ add identity</a>
          </div>
          <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
            Add 2 test accounts (userA, userB) — the agent replays requests across them to prove BOLA. Credentials stay on your machine.
          </div>
          {idents.map((i) => (
            <div key={i.id} className="endpoint">
              <span className="pill">{i.label}</span> {i.auth_type}
              {i.username && <span className="muted"> · {i.username}</span>}
              <span className="pill" style={{ marginLeft: 6 }}>{i.source}</span>
              <span className="danger" style={{ cursor: "pointer", float: "right" }}
                onClick={async () => { await api.deleteIdentity(i.id); loadIds(); }}>×</span>
            </div>
          ))}
          {showId && (
            <div style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 8, marginTop: 6 }}>
              <div className="repeater-grid">
                <input placeholder="label (userA)" value={nid.label} onChange={(e) => setNid({ ...nid, label: e.target.value })} />
                <select value={nid.auth_type} onChange={(e) => setNid({ ...nid, auth_type: e.target.value })}>
                  <option value="cookie">cookie</option><option value="bearer">bearer token</option><option value="header">custom header</option>
                </select>
              </div>
              {nid.auth_type === "header" && <input placeholder="header name (e.g. X-Auth)" value={nid.header_name} onChange={(e) => setNid({ ...nid, header_name: e.target.value })} style={{ marginTop: 6 }} />}
              <input placeholder="value (cookie string / token)" type="password" value={nid.material} onChange={(e) => setNid({ ...nid, material: e.target.value })} style={{ marginTop: 6 }} />
              <input placeholder="username/email (optional)" value={nid.username} onChange={(e) => setNid({ ...nid, username: e.target.value })} style={{ marginTop: 6 }} />
              <div className="row" style={{ justifyContent: "flex-end", marginTop: 6 }}>
                <button onClick={() => setShowId(false)}>Cancel</button>
                <button className="primary" disabled={!nid.material} onClick={addId}>Add</button>
              </div>
            </div>
          )}
        </div>

        {err && <div className="sev-high" style={{ fontSize: 12, margin: "8px 0" }}>{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button onClick={onClose}>Close</button>
          <button className="primary" disabled={busy} onClick={scan}>▶ Scan whole program</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------- Repeater panel ------------------------- */
export function RepeaterPanel({ taskId, history, onSent }: any) {
  const [method, setMethod] = useState("GET");
  const [url, setUrl] = useState("");
  const [headers, setHeaders] = useState("{}");
  const [body, setBody] = useState("");
  const [resp, setResp] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const send = async () => {
    setBusy(true); setErr(""); setResp(null);
    try {
      let h = {};
      try { h = JSON.parse(headers || "{}"); } catch { throw new Error("Headers must be valid JSON"); }
      const r = await api.sendRepeater({ method, url, headers: h, body, task_id: taskId });
      setResp(r); onSent(r);
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div className="panel" style={{ marginTop: 14, maxHeight: "none" }}>
      <h3>Repeater</h3>
      <div className="body">
        <div className="row" style={{ marginBottom: 8 }}>
          <select style={{ width: 110 }} value={method} onChange={(e) => setMethod(e.target.value)}>
            {["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"].map((m) => <option key={m}>{m}</option>)}
          </select>
          <input placeholder="https://target/api/..." value={url} onChange={(e) => setUrl(e.target.value)} />
          <button className="primary" disabled={busy || !url} onClick={send}>{busy ? "…" : "Send"}</button>
        </div>
        <div className="repeater-grid">
          <div>
            <label className="muted" style={{ fontSize: 11 }}>Headers (JSON)</label>
            <textarea rows={4} value={headers} onChange={(e) => setHeaders(e.target.value)} />
            <label className="muted" style={{ fontSize: 11, marginTop: 6, display: "block" }}>Body</label>
            <textarea rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          <div>
            <label className="muted" style={{ fontSize: 11 }}>Response</label>
            {err && <pre style={{ color: "var(--crit)" }}>{err}</pre>}
            {resp && <pre>{`HTTP ${resp.status_code}  (${resp.duration_ms} ms)\n` +
              Object.entries(resp.response_headers).slice(0, 8).map(([k, v]) => `${k}: ${v}`).join("\n") +
              "\n\n" + resp.response_body.slice(0, 2000)}</pre>}
            {!resp && !err && <div className="empty">No request sent.</div>}
          </div>
        </div>
        {history.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <label className="muted" style={{ fontSize: 11 }}>History ({history.length})</label>
            {history.slice(0, 12).map((r: any) => (
              <div key={r.id} className="endpoint">
                <span className="method">{r.method}</span>
                <span className={r.status_code && r.status_code < 400 ? "sev-low" : "sev-high"}> {r.status_code}</span> {r.url}
                <span className="muted"> · #{r.id} · {r.duration_ms}ms</span><button onClick={async()=>{try{await api.instruct(taskId,`Review captured request #${r.id}: ${r.method} ${r.url}`);}catch(e:any){setErr(e.message);}}}>Instruct agent</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
