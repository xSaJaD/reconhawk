"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { api, WS_URL } from "../lib/api";
import {
  Panel, parseBody, SettingsModal, RoutinesView, FilesView, ChatView, KnowledgeView,
  RepeaterPanel, FindingCard, NewTaskModal, NewProgramModal, OverviewStrip, WorkspacePanel,
  IdeasQueue, AssetsPanel, GraphPanel, HealthPanel, ConversationBox,
  ProgramIcon, TypeBadge, ProgramDetailModal,
} from "./panels";

function ts(s?: string) { try { return s ? new Date(s).toLocaleTimeString() : ""; } catch { return ""; } }

export default function Page() {
  const [health, setHealth] = useState<any>(null);
  const [view, setView] = useState("console");
  const [tasks, setTasks] = useState<any[]>([]);
  const [programs, setPrograms] = useState<any[]>([]);
  const [notifs, setNotifs] = useState<any[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [wsOn, setWsOn] = useState(false);
  const [h1Msg, setH1Msg] = useState("");
  const [progTab, setProgTab] = useState("all");
  const [progDetail, setProgDetail] = useState<any>(null);
  const [showTask, setShowTask] = useState(false);
  const [showProg, setShowProg] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showBell, setShowBell] = useState(false);

  const [plan, setPlan] = useState<string[]>([]);
  const [ideas, setIdeas] = useState<any[]>([]);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [endpoints, setEndpoints] = useState<any[]>([]);
  const [leads, setLeads] = useState<any[]>([]);
  const [subagents, setSubagents] = useState<any[]>([]);
  const [findings, setFindings] = useState<any[]>([]);
  const [repeater, setRepeater] = useState<any[]>([]);
  const [intel, setIntel] = useState<any[]>([]);

  const selRef = useRef<number | null>(null); selRef.current = sel;
  // All fetches are guarded so a transient backend hiccup (e.g. during startup)
  // never throws an unhandled rejection into the dev error overlay.
  const refreshTasks = useCallback(async () => { try { setTasks(await api.listTasks()); } catch {} }, []);
  const refreshPrograms = useCallback(async () => { try { setPrograms(await api.listPrograms()); } catch {} }, []);
  const refreshNotifs = useCallback(async () => { try { setNotifs(await api.notifications()); } catch {} }, []);

  const TL_KINDS = ["log", "tool_call", "status", "finding", "endpoint", "repeater", "screenshot", "lead", "subagent", "addendum", "memory", "intel", "file", "command"];

  const loadTask = useCallback(async (id: number) => {
    setSel(id); setView("console");
    let evs: any[] = [], eps: any[] = [], ld: any[] = [], sa: any[] = [], fs: any[] = [], rp: any[] = [], it: any[] = [];
    try {
      [evs, eps, ld, sa, fs, rp, it] = await Promise.all([
        api.events(id), api.endpoints(id), api.leads(id), api.subagents(id), api.findings(id), api.repeater(id), api.taskIntel(id),
      ]);
    } catch { return; }
    setIntel(it);
    const plans = evs.filter((e: any) => e.kind === "plan");
    setPlan(plans.length ? (parseBody(plans[plans.length - 1].body).steps || []) : []);
    setIdeas(evs.filter((e: any) => e.kind === "idea"));
    setTimeline(evs.filter((e: any) => TL_KINDS.includes(e.kind)));
    setEndpoints(eps); setLeads(ld); setSubagents(sa); setFindings(fs); setRepeater(rp);
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    refreshTasks(); refreshPrograms(); refreshNotifs();
  }, [refreshTasks, refreshPrograms, refreshNotifs]);

  useEffect(() => {
    let ws: WebSocket; let retry: any;
    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => setWsOn(true);
      ws.onclose = () => { setWsOn(false); retry = setTimeout(connect, 2000); };
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.kind === "notification") { refreshNotifs(); return; }
        if (ev.kind === "status") refreshTasks();
        if (ev.task_id !== selRef.current) return;
        const body = parseBody(ev.body);
        if (ev.kind === "plan") setPlan(body.steps || []);
        else if (ev.kind === "idea") setIdeas((p) => [...p, ev]);
        else if (ev.kind === "endpoint") setEndpoints((p) => [{ id: Math.random(), ...body }, ...p]);
        else if (ev.kind === "lead") setLeads((p) => [{ id: Math.random(), title: ev.title, ...body }, ...p]);
        else if (ev.kind === "subagent") setSubagents((p) => [{ id: Math.random(), ...body }, ...p]);
        else if (ev.kind === "intel") setIntel((p) => [{ id: Math.random(), ...body }, ...p]);
        else if (ev.kind === "finding") api.findings(ev.task_id).then(setFindings).catch(() => {});
        else if (ev.kind === "repeater") api.repeater(ev.task_id).then(setRepeater).catch(() => {});
        if (TL_KINDS.includes(ev.kind)) setTimeline((p) => [...p.slice(-400), ev]);
      };
    };
    connect();
    return () => { clearTimeout(retry); ws && ws.close(); };
  }, [refreshTasks, refreshNotifs]);

  const syncH1 = async () => {
    setH1Msg("syncing HackerOne…");
    try {
      const r = await api.syncH1();
      await refreshPrograms();
      setH1Msg(`${r.total} program(s) · fetching scope…`);
      // Scopes arrive in the background — refresh a few times to show them filling in.
      [4000, 10000, 20000, 35000].forEach((ms) => setTimeout(refreshPrograms, ms));
      setTimeout(() => setH1Msg(""), 40000);
    } catch (e: any) {
      setH1Msg(e.message);
      setTimeout(() => setH1Msg(""), 8000);
    }
  };

  const current = tasks.find((t) => t.id === sel);
  const unread = notifs.filter((n) => !n.read).length;
  const navBtn = (k: string, label: string) => (
    <button onClick={() => setView(k)} style={view === k ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}}>{label}</button>
  );

  return (
    <>
      <div className="topbar">
        <div className="logo">Recon<span>Hawk</span> <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>v1.6</span></div>
        {navBtn("console", "Console")}
        {navBtn("knowledge", "Knowledge")}
        {navBtn("routines", "Routines")}
        {navBtn("files", "Files")}
        {navBtn("chat", "Assistant")}
        <div style={{ flex: 1 }} />
        <span className="row" style={{ gap: 6 }}>
          <span className={"dot " + (wsOn ? "on" : "off")} />
          <span className="muted" style={{ fontSize: 12 }}>{wsOn ? "live" : "offline"}</span>
        </span>
        {health && <span className="pill mono">tools {health.tools} · skills {health.skills} · scope {health.scope_enforced ? "on" : "off"}{health.shell ? " · shell" : ""}</span>}
        <span className="pill">{health?.provider}/{health?.model}</span>
        <div style={{ position: "relative" }}>
          <button onClick={() => { setShowBell(!showBell); if (!showBell) api.markRead().then(refreshNotifs); }}>
            🔔{unread > 0 ? ` ${unread}` : ""}
          </button>
          {showBell && (
            <div className="modal" style={{ position: "absolute", right: 0, top: 36, width: 360, zIndex: 60, maxHeight: 420 }}>
              <strong style={{ fontSize: 13 }}>Notifications</strong>
              {notifs.length === 0 && <div className="empty">none</div>}
              {notifs.map((n) => (
                <div key={n.id} className="timeline-item">
                  <span className={"kind-tag sev-" + (n.level === "critical" ? "critical" : n.level === "warn" ? "medium" : "low")}>{n.level}</span>
                  {n.title}<div className="ts">{n.body} · {ts(n.created_at)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
        <button onClick={() => setShowSettings(true)}>⚙ Settings</button>
      </div>

      {health && health.has_key === false && (
        <div style={{ background: "#3a2a12", color: "var(--warn)", padding: "8px 18px",
          fontSize: 13, borderBottom: "1px solid var(--border)" }}>
          ⚠️ No API key set for <b>{health.provider}</b>. Scans and the assistant won't run until you add one —{" "}
          <a style={{ cursor: "pointer", textDecoration: "underline" }} onClick={() => setShowSettings(true)}>open Settings</a>.
        </div>
      )}

      <div className="layout">
        <div className="sidebar">
          <div className="row" style={{ justifyContent: "space-between", padding: "12px 14px" }}>
            <strong style={{ fontSize: 13 }}>Tasks</strong>
            <button className="primary" onClick={() => setShowTask(true)}>+ New scan</button>
          </div>
          {tasks.length === 0 && <div className="empty">No scans yet.</div>}
          {tasks.map((t) => (
            <div key={t.id} className={"task-item" + (t.id === sel ? " active" : "")} onClick={() => loadTask(t.id)}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="t-target">{t.target}</span>
                <span className="row" style={{ gap: 6 }}>
                  <span className={"badge " + t.status}>{t.status}</span>
                  <span title="Delete scan" className="muted" style={{ cursor: "pointer", fontSize: 14 }}
                    onClick={async (e) => {
                      e.stopPropagation();
                      if (!confirm(`Delete scan #${t.id} (${t.target})? This removes all its data.`)) return;
                      await api.deleteTask(t.id); if (sel === t.id) setSel(null); refreshTasks();
                    }}>🗑</span>
                </span>
              </div>
              <div className="t-meta">#{t.id} · {t.provider}/{t.model || "—"} · {ts(t.created_at)}</div>
            </div>
          ))}
          <div className="section-title row" style={{ justifyContent: "space-between" }}>
            <span>Programs</span>
            <span className="row" style={{ gap: 8 }}>
              <a onClick={syncH1} style={{ cursor: "pointer", fontSize: 11 }} title="Pull programs + scope from HackerOne">⟳ H1</a>
              <a onClick={() => setShowProg(true)} style={{ cursor: "pointer", fontSize: 11 }}>+ add</a>
            </span>
          </div>
          <div className="row" style={{ gap: 4, padding: "0 12px 6px", flexWrap: "wrap" }}>
            {[["all", "All"], ["bbp", "BBP"], ["bounty", "Bounty"], ["vdp", "VDP"]].map(([k, lbl]) => (
              <button key={k} onClick={() => setProgTab(k)}
                style={{ fontSize: 10, padding: "2px 8px",
                  ...(progTab === k ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}) }}>
                {lbl} {k !== "all" && <span className="muted">{programs.filter((p) => p.program_type === k).length}</span>}
              </button>
            ))}
          </div>
          {h1Msg && <div className="t-meta" style={{ padding: "0 16px", color: "var(--accent)" }}>{h1Msg}</div>}
          {programs.filter((p) => progTab === "all" || p.program_type === progTab).length === 0 &&
            <div className="empty">No {progTab === "all" ? "" : progTab.toUpperCase() + " "}programs.</div>}
          {programs.filter((p) => progTab === "all" || p.program_type === progTab).map((p) => (
            <div key={p.id} className="task-item" style={{ cursor: "pointer" }} onClick={() => setProgDetail(p)}>
              <div className="row" style={{ gap: 8 }}>
                <ProgramIcon p={p} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="t-target" style={{ fontSize: 13 }}>{p.name}</span>
                    <TypeBadge type={p.program_type} />
                  </div>
                  <div className="t-meta mono">{(p.scope_in || []).length} assets · {p.platform}</div>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="main">
          {view === "knowledge" && <KnowledgeView />}
          {view === "routines" && <RoutinesView programs={programs} />}
          {view === "files" && <FilesView />}
          {view === "chat" && <ChatView />}
          {view === "console" && !current && <div className="empty" style={{ marginTop: 60 }}>Select or start a scan.</div>}
          {view === "console" && current && (
            <>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
                <div>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>{current.target}</div>
                  <div className="muted" style={{ fontSize: 12 }}>{current.objective || "Enumerate attack surface"} · {current.provider}/{current.model}</div>
                </div>
                <div className="row">
                  <span className={"badge " + current.status}>{current.status}</span>
                  {current.status === "running" && <button className="danger" onClick={() => api.stopTask(current.id)}>Stop</button>}
                </div>
              </div>

              <OverviewStrip taskId={current.id} refreshKey={timeline.length} />

              <div className="grid grid3">
                <Panel title="Plan">
                  {plan.length === 0 ? <div className="empty">No plan yet.</div> :
                    plan.map((s, i) => <div key={i} className="plan-step"><span className="muted">{i + 1}.</span> {s}</div>)}
                </Panel>
                <IdeasQueue taskId={current.id} refreshKey={timeline.length} />
                <Panel title={`Sub-agents (${subagents.length})`}>
                  {subagents.length === 0 ? <div className="empty">—</div> :
                    subagents.map((a, i) => (
                      <div key={a.id ?? i} className="plan-step">
                        <strong>{a.name}</strong> <span className="muted">· {a.role}</span>
                        {a.summary && <div className="muted" style={{ fontSize: 12 }}>{a.summary}</div>}
                      </div>
                    ))}
                </Panel>
              </div>

              <div className="grid grid3" style={{ marginTop: 14 }}>
                <Panel title={`Endpoints (${endpoints.length})`}>
                  {endpoints.length === 0 ? <div className="empty">—</div> :
                    endpoints.map((e, i) => (
                      <div key={e.id ?? i} className="endpoint">
                        <span className="method">{e.method || "GET"}</span> {e.url}
                        {e.source && <span className="muted"> · {e.source}</span>}
                      </div>
                    ))}
                </Panel>
                <Panel title={`Leads (${leads.length})`}>
                  {leads.length === 0 ? <div className="empty">—</div> :
                    leads.map((l, i) => (
                      <div key={l.id ?? i} className="endpoint">
                        <span className={"badge sev-" + (l.severity_guess || "info")}>{l.severity_guess}</span> {l.title}
                        {l.rationale && <div className="muted" style={{ fontSize: 11 }}>{l.rationale}</div>}
                      </div>
                    ))}
                </Panel>
                <Panel title={`Extracted intel (${intel.length})`}>
                  {intel.length === 0 ? <div className="empty">—</div> :
                    intel.map((i, idx) => (
                      <div key={i.id ?? idx} className="endpoint">
                        <span className="kind-tag">{i.kind}</span> {i.value}
                        {i.source && <span className="muted"> · {i.source}</span>}
                      </div>
                    ))}
                </Panel>
                <Panel title="Live timeline" grow>
                  {timeline.length === 0 ? <div className="empty">Waiting…</div> :
                    timeline.slice().reverse().map((e, i) => {
                      const b = parseBody(e.body);
                      return (
                        <div key={i} className="timeline-item">
                          <span className="kind-tag">{e.kind}</span>
                          {e.title || (b && b.text) || (b && b.tool) || ""}
                          {e.kind === "tool_call" && b?.input && <span className="muted mono"> {JSON.stringify(b.input).slice(0, 100)}</span>}
                          <div className="ts">{ts(e.created_at)}</div>
                        </div>
                      );
                    })}
                </Panel>
              </div>

              <div className="grid grid3" style={{ marginTop: 14 }}>
                <AssetsPanel taskId={current.id} refreshKey={timeline.length} />
                <HealthPanel taskId={current.id} refreshKey={timeline.length} />
                <GraphPanel taskId={current.id} refreshKey={timeline.length} />
              </div>

              <ConversationBox taskId={current.id} running={current.status === "running"} />

              <div className="panel" style={{ marginTop: 14, maxHeight: "none" }}>
                <h3>Reports / Findings ({findings.length})</h3>
                <div className="body">
                  {findings.length === 0 ? <div className="empty">No findings recorded.</div> :
                    findings.map((f) => <FindingCard key={f.id} f={f} onChange={() => api.findings(current.id).then(setFindings)} />)}
                </div>
              </div>

              <WorkspacePanel taskId={current.id} refreshKey={timeline.length} />
              <RepeaterPanel taskId={current.id} history={repeater} onSent={(r: any) => setRepeater((p) => [r, ...p])} />
            </>
          )}
        </div>
      </div>

      {showTask && <NewTaskModal programs={programs} onClose={() => setShowTask(false)}
        onCreated={async (t: any) => { setShowTask(false); await refreshTasks(); loadTask(t.id); }} />}
      {showProg && <NewProgramModal onClose={() => setShowProg(false)}
        onCreated={async () => { setShowProg(false); await refreshPrograms(); }} />}
      {showSettings && <SettingsModal onClose={() => { setShowSettings(false); api.health().then(setHealth); }} />}
      {progDetail && <ProgramDetailModal program={progDetail} onClose={() => setProgDetail(null)}
        onScan={async (p: any) => {
          const t = await api.scanProgram(p.id);
          setProgDetail(null); await refreshTasks(); loadTask(t.id);
        }} />}
    </>
  );
}
