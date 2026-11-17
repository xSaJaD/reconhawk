"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { api, WS_URL } from "../lib/api";
import {
  Panel, parseBody, SettingsModal, RoutinesView, FilesView, ChatView, KnowledgeView,
  RepeaterPanel, FindingCard, NewTaskModal, NewProgramModal, OverviewStrip, WorkspacePanel,
  IdeasQueue, AssetsPanel, GraphPanel, HealthPanel, ConversationBox,
  ProgramIcon, TypeBadge, ProgramDetailModal, TimelineItem,
} from "./panels";

import {Access,Specs,Tools} from './extra';
import {ScopeStatus} from './scope-status';
import {programType, syncIsActive, syncSummary} from '../lib/programs';
function ts(s?: string) { try { return s ? new Date(s).toLocaleTimeString() : ""; } catch { return ""; } }

export default function Page() {
  const [tab,setTab]=useState('Overview');
  const [login,setLogin]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{const handler=()=>setLogin(true);window.addEventListener('reconhawk-auth',handler);return()=>window.removeEventListener('reconhawk-auth',handler);},[]);
  const act=async(fn:()=>Promise<any>)=>{try{setError('');await fn();}catch(e:any){setError(e.message);}};
  const [health, setHealth] = useState<any>(null);
  const [view, setView] = useState("console");
  const [tasks, setTasks] = useState<any[]>([]);
  const [programs, setPrograms] = useState<any[]>([]);
  const [notifs, setNotifs] = useState<any[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [wsOn, setWsOn] = useState(false);
  const [h1Msg, setH1Msg] = useState("");
  const [scopeSync, setScopeSync] = useState<any>(null);
  const [syncEpoch, setSyncEpoch] = useState(0);
  const [importingH1, setImportingH1] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  const [progTab, setProgTab] = useState("all");
  const [progSearch, setProgSearch] = useState("");
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
  const refreshPrograms = useCallback(async () => {
    try { setPrograms(await api.listPrograms()); setConnectionError(''); }
    catch (error: any) { setConnectionError('Cannot load programs from the backend: ' + error.message); }
  }, []);
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
    let ws: WebSocket; let retry: any; let disposed=false;
    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => setWsOn(true);
      ws.onclose = () => { setWsOn(false); if(!disposed) retry = setTimeout(connect, 2000); };
      ws.onmessage = (m) => {
        let ev:any;try{ev=JSON.parse(m.data);}catch{return;}
        if (ev.kind === "notification") { refreshNotifs(); return; }
        if (ev.kind === "status") refreshTasks();
        if (ev.task_id !== selRef.current) return;
        const body = parseBody(ev.body);
        if (ev.kind === "plan") setPlan(body.steps || []);
        else if (ev.kind === "idea") setIdeas((p) => [...p, ev]);
        else if (ev.kind === "endpoint") setEndpoints((p) => [{ id: Math.random(), ...body }, ...p]);
        else if (ev.kind === "lead") setLeads((p) => [{ id: Math.random(), title: ev.title, ...body }, ...p]);
        else if (ev.kind === "subagent") api.subagents(ev.task_id).then(setSubagents).catch(()=>{});
        else if (ev.kind === "intel") setIntel((p) => [{ id: Math.random(), ...body }, ...p]);
        else if (ev.kind === "finding") api.findings(ev.task_id).then(setFindings).catch(() => {});
        else if (ev.kind === "repeater") api.repeater(ev.task_id).then(setRepeater).catch(() => {});
        if (TL_KINDS.includes(ev.kind)) setTimeline((p) => [...p.slice(-400), ev]);
      };
    };
    connect();
    return () => { disposed=true; clearTimeout(retry); ws && ws.close(); };
  }, [refreshTasks, refreshNotifs, login]);

  useEffect(() => {
    if (login) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const status = await api.h1SyncStatus();
        if (disposed) return;
        setScopeSync(status); setH1Msg(syncSummary(status));
        await refreshPrograms();
        if (!disposed && (syncIsActive(status.status) || status.importing)) timer = setTimeout(poll, 2000);
      } catch (error: any) {
        if (!disposed) {
          setH1Msg('Scope sync status unavailable; retrying: ' + error.message);
          timer = setTimeout(poll, 5000);
        }
      }
    };
    poll();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [refreshPrograms, login, syncEpoch]);

  const syncH1 = async () => {
    if (importingH1 || syncIsActive(scopeSync?.status)) return;
    setImportingH1(true);
    setH1Msg("syncing HackerOne…");
    try {
      await api.syncH1();
      await refreshPrograms();
      setSyncEpoch(value => value + 1);
    } catch (e: any) {
      setH1Msg(e.message);
    } finally { setImportingH1(false); }
  };

  const retryScope = async (id: number) => {
    try {
      await api.retryScope(id); await refreshPrograms();
      setSyncEpoch(value => value + 1);
    } catch (error: any) { setH1Msg(error.message); }
  };

  const current = tasks.find((t) => t.id === sel);
  const unread = notifs.filter((n) => !n.read).length;
  const navBtn = (k: string, label: string) => (
    <button onClick={() => setView(k)} style={view === k ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}}>{label}</button>
  );

  return (
    <>
      <div className="topbar">
        <div className="logo">Recon<span>Hawk</span> <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>v1.7.2</span></div>
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

      {connectionError && <div role="alert" className="connection-error">{connectionError}</div>}
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
              <button onClick={syncH1} disabled={importingH1 || syncIsActive(scopeSync?.status)} style={{ fontSize: 11 }} title="Pull programs + scope from HackerOne">{importingH1 ? 'Importing…' : '⟳ H1'}</button>
              <a onClick={() => setShowProg(true)} style={{ cursor: "pointer", fontSize: 11 }}>+ add</a>
            </span>
          </div>
          <div className="row" style={{ gap: 4, padding: "0 12px 6px", flexWrap: "wrap" }}>
            {[["all", "All"], ["bounty", "Bounty"], ["vdp", "VDP"], ["unknown", "Unknown"]].map(([k, lbl]) => (
              <button key={k} onClick={() => setProgTab(k)}
                style={{ fontSize: 10, padding: "2px 8px",
                  ...(progTab === k ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}) }}>
                {lbl} {k !== "all" && <span className="muted">{programs.filter((p) => programType(p.program_type) === k).length}</span>}
              </button>
            ))}
          </div>
          <div style={{ padding: "0 12px 6px" }}>
            <input aria-label="Search programs" value={progSearch} onChange={(e) => setProgSearch(e.target.value)}
              placeholder="🔍 Search programs…" style={{ width: "100%", fontSize: 12, padding: "4px 8px" }} />
          </div>
          {h1Msg && <div className="t-meta" style={{ padding: "0 16px", color: "var(--accent)" }}>{h1Msg}</div>}
          {programs.filter((p) => (progTab === "all" || programType(p.program_type) === progTab) && (progSearch.trim() === "" || ((p.name || "") + " " + (p.handle || "")).toLowerCase().includes(progSearch.trim().toLowerCase()))).length === 0 &&
            <div className="empty">No {progTab === "all" ? "" : progTab.toUpperCase() + " "}programs.</div>}
          {programs.filter((p) => (progTab === "all" || programType(p.program_type) === progTab) && (progSearch.trim() === "" || ((p.name || "") + " " + (p.handle || "")).toLowerCase().includes(progSearch.trim().toLowerCase()))).map((p) => (
            <div key={p.id} className="task-item" style={{ cursor: "pointer" }} onClick={() => setProgDetail(p)}>
              <div className="row" style={{ gap: 8 }}>
                <ProgramIcon p={p} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="t-target" style={{ fontSize: 13 }}>{p.name}</span>
                    <TypeBadge type={p.program_type} />
                  </div>
                  <div className="t-meta mono">{p.scope_sync?.web_asset_count ?? (p.scope_in || []).length} web assets · {p.platform}</div>
                  <ScopeStatus program={p} onRetry={retryScope} />
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
                  {current.status==='running'&&<><button onClick={()=>act(()=>api.setEffort(current.id,'max'))}>Deep hunt</button><button className="danger" onClick={()=>act(()=>api.stopTask(current.id))}>Stop</button></>}
{!['running','queued'].includes(current.status)&&<button onClick={()=>act(async()=>{await api.resumeTask(current.id);await refreshTasks();})}>Resume</button>}
                </div>
              </div>


              <div role="tablist" className="session-tabs">{['Overview','Conversation','Assets','Findings','Reports','Activity','Health','Files','Tools','Repeater'].map(name=><button role="tab" aria-selected={tab===name} className={tab===name?'primary':''} key={name} onClick={()=>setTab(name)}>{name}</button>)}</div>
              {error&&<p role="alert">{error}</p>}
              <div role="tabpanel" key={current.id+'-'+tab}>
              {tab==='Overview'&&<><OverviewStrip taskId={current.id} refreshKey={timeline.length}/><div className="grid grid3">
                <Panel title="Plan">{plan.map((x,i)=><p key={i}>{i+1}. {x}</p>)}</Panel>
                <IdeasQueue taskId={current.id} refreshKey={timeline.length}/>
                <Panel title="Specialists">{subagents.map(x=><p key={x.id}>{x.name} · {x.status} · {x.summary}</p>)}</Panel>
                <Panel title="Leads">{leads.map(x=><p key={x.id}>#{x.id} {x.title} · {x.status}</p>)}</Panel></div></>}
              {tab==='Conversation'&&<><Panel title="Agent conversation">{timeline.filter(e=>parseBody(e.body)?.text).map((e,i)=><pre key={i}>{parseBody(e.body).text}</pre>)}</Panel><ConversationBox taskId={current.id} running={current.status==='running'}/></>}
              {tab==='Assets'&&<><div className="grid"><AssetsPanel taskId={current.id} refreshKey={timeline.length}/><GraphPanel taskId={current.id} refreshKey={timeline.length}/></div><Panel title="Endpoints">{endpoints.map(x=><p key={x.id}>{x.method} {x.url}</p>)}</Panel><Specs taskId={current.id}/></>}
              {['Findings','Reports'].includes(tab)&&<Panel title={tab}>{findings.filter(f=>tab!=='Reports'||f.report_md).map(f=><FindingCard key={f.id} f={f} onChange={()=>api.findings(current.id).then(setFindings)}/>)}</Panel>}
              {tab==='Activity'&&<Panel title="Activity">{timeline.length===0?<div className="empty">Waiting…</div>:timeline.slice().reverse().map((e,i)=><TimelineItem key={e.id??i} e={e} />)}</Panel>}
              {tab==='Health'&&<HealthPanel taskId={current.id} refreshKey={timeline.length}/>}
              {tab==='Files'&&<WorkspacePanel taskId={current.id} refreshKey={timeline.length}/>}
              {tab==='Tools'&&<Tools/>}
              {tab==='Repeater'&&<RepeaterPanel taskId={current.id} history={repeater} onSent={(x:any)=>setRepeater(p=>[x,...p])}/>}
              </div>
            </>
          )}
        </div>
      </div>

      {login&&<Access done={()=>{setLogin(false);refreshTasks();refreshPrograms();refreshNotifs();api.health().then(setHealth);}}/>}
      {showTask && <NewTaskModal programs={programs} onClose={() => setShowTask(false)}
        onCreated={async (t: any) => { setShowTask(false); await refreshTasks(); loadTask(t.id); }} />}
      {showProg && <NewProgramModal onClose={() => setShowProg(false)}
        onCreated={async () => { setShowProg(false); await refreshPrograms(); }} />}
      {showSettings && <SettingsModal onClose={() => { setShowSettings(false); api.health().then(setHealth); }} />}
      {progDetail && <ProgramDetailModal program={programs.find(p => p.id === progDetail.id) || progDetail} onClose={() => setProgDetail(null)} onRetryScope={retryScope}
        onScan={async (p: any) => {
          const t = await api.scanProgram(p.id);
          setProgDetail(null); await refreshTasks(); loadTask(t.id);
        }} />}
    </>
  );
}
