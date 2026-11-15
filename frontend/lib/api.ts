export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export const WS_URL = API_BASE.replace(/^http/, "ws") + "/ws";

async function j(path: string, opts: RequestInit = {}) {
  const res = await fetch(API_BASE + path, {
    credentials:"include",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {if(res.status===401&&typeof window!=='undefined')window.dispatchEvent(new Event('reconhawk-auth'));throw new Error((await res.text())||res.statusText);}
  return res.json();
}

export const api = {
  login:(token:string)=>j('/api/session',{method:'POST',body:JSON.stringify({token})}),
  resumeTask:(id:number)=>j(`/api/tasks/${id}/resume`,{method:'POST'}),
  setEffort:(id:number,effort:string)=>j(`/api/tasks/${id}/effort`,{method:'POST',body:JSON.stringify({effort})}),
  specs:(id:number)=>j(`/api/tasks/${id}/specs`),
  mcpServers:()=>j('/api/mcp/servers'),
  testMcp:()=>j('/api/mcp/test',{method:'POST'}),
  linkEvidence:(id:number,evidence_ids:number[])=>j(`/api/findings/${id}/evidence`,{method:'PUT',body:JSON.stringify({evidence_ids})}),
  health: () => j("/api/health"),
  providers: () => j("/api/providers"),
  getSettings: () => j("/api/settings"),
  putSettings: (b: any) => j("/api/settings", { method: "PUT", body: JSON.stringify(b) }),
  testProvider: (b: any) => j("/api/settings/test", { method: "POST", body: JSON.stringify(b) }),

  listPrograms: () => j("/api/programs"),
  createProgram: (b: any) => j("/api/programs", { method: "POST", body: JSON.stringify(b) }),
  updateProgram: (id: number, b: any) => j(`/api/programs/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  deleteProgram: (id: number) => j(`/api/programs/${id}`, { method: "DELETE" }),
  syncH1: () => j("/api/programs/sync_h1", { method: "POST" }),
  h1SyncStatus: () => j('/api/programs/sync_h1/status'),
  retryScope: (id: number) => j(`/api/programs/${id}/sync_scope`, {method: 'POST'}),
  scanProgram: (id: number) => j(`/api/programs/${id}/scan`, { method: "POST" }),

  listTasks: () => j("/api/tasks"),
  createTask: (b: any) => j("/api/tasks", { method: "POST", body: JSON.stringify(b) }),
  stopTask: (id: number) => j(`/api/tasks/${id}/stop`, { method: "POST" }),
  deleteTask: (id: number) => j(`/api/tasks/${id}`, { method: "DELETE" }),
  events: (id: number, kind?: string) => j(`/api/tasks/${id}/events${kind ? `?kind=${kind}` : ""}`),
  endpoints: (id: number) => j(`/api/tasks/${id}/endpoints`),
  leads: (id: number) => j(`/api/tasks/${id}/leads`),
  subagents: (id: number) => j(`/api/tasks/${id}/subagents`),
  findings: (id: number) => j(`/api/tasks/${id}/findings`),
  repeater: (id: number) => j(`/api/tasks/${id}/repeater`),

  setFindingStatus: (fid: number, status: string) =>
    j(`/api/findings/${fid}/status`, { method: "PUT", body: JSON.stringify({ status }) }),
  verifyFinding: (fid: number) => j(`/api/findings/${fid}/verify`, { method: "POST" }),
  submitFinding: (fid: number) => j(`/api/findings/${fid}/submit`, { method: "POST" }),
  findingPayload: (fid: number) => j(`/api/findings/${fid}/h1_payload`),
  findingTelegram: (fid: number) => j(`/api/findings/${fid}/telegram`, { method: "POST" }),
  addenda: (fid: number) => j(`/api/findings/${fid}/addenda`),
  createAddendum: (b: any) => j("/api/addenda", { method: "POST", body: JSON.stringify(b) }),
  addendumPayload: (aid: number) => j(`/api/addenda/${aid}/payload`),
  addendumTelegram: (aid: number) => j(`/api/addenda/${aid}/telegram`, { method: "POST" }),

  sendRepeater: (b: any) => j("/api/repeater/send", { method: "POST", body: JSON.stringify(b) }),

  listRoutines: () => j("/api/routines"),
  createRoutine: (b: any) => j("/api/routines", { method: "POST", body: JSON.stringify(b) }),
  toggleRoutine: (id: number) => j(`/api/routines/${id}/toggle`, { method: "PUT" }),
  deleteRoutine: (id: number) => j(`/api/routines/${id}`, { method: "DELETE" }),

  notifications: () => j("/api/notifications"),
  markRead: () => j("/api/notifications/read", { method: "POST" }),

  chatHistory: (thread: string) => j(`/api/chat/${encodeURIComponent(thread)}`),
  chatSend: (b: any) => j("/api/chat", { method: "POST", body: JSON.stringify(b) }),

  files: () => j("/api/files"),

  memory: () => j("/api/memory"),
  deleteMemory: (id: number) => j(`/api/memory/${id}`, { method: "DELETE" }),
  skills: () => j("/api/skills"),
  toggleSkill: (name: string) => j(`/api/skills/${encodeURIComponent(name)}/toggle`, { method: "PUT" }),
  taskIntel: (id: number) => j(`/api/tasks/${id}/intel`),
  ideas: (id: number) => j(`/api/tasks/${id}/ideas`),
  updateIdea: (id: number, b: any) => j(`/api/ideas/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  taskHealth: (id: number) => j(`/api/tasks/${id}/health`),
  graph: (id: number) => j(`/api/tasks/${id}/graph`),
  assets: (id: number) => j(`/api/tasks/${id}/assets`),
  instruct: (id: number, text: string) => j(`/api/tasks/${id}/instruct`, { method: "POST", body: JSON.stringify({ text }) }),
  uploadSkills: (files: any[]) => j("/api/skills/upload", { method: "POST", body: JSON.stringify({ files }) }),
  deleteSkill: (name: string) => j(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  overview: (id: number) => j(`/api/tasks/${id}/overview`),
  workspace: (id: number) => j(`/api/tasks/${id}/workspace`),
  workspaceFile: (id: number, path: string) => j(`/api/tasks/${id}/workspace/file?path=${encodeURIComponent(path)}`),
  pendingAddenda: () => j("/api/addenda/pending"),
  reconTools: () => j("/api/tools"),
  identities: (programId?: number) => j(`/api/identities${programId != null ? `?program_id=${programId}` : ""}`),
  createIdentity: (b: any) => j("/api/identities", { method: "POST", body: JSON.stringify(b) }),
  deleteIdentity: (id: number) => j(`/api/identities/${id}`, { method: "DELETE" }),
  editAddendum: (id: number, b: any) => j(`/api/addenda/${id}`, { method: "PUT", body: JSON.stringify(b) }),
};
