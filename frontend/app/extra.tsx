"use client";
import {useEffect,useState} from 'react';
import {api} from '../lib/api';

export function Access({done}:any){
 const [token,setToken]=useState('');const [error,setError]=useState('');
 return <div className="modal-bg"><div className="modal"><h2>Connect to ReconHawk</h2><p>Enter the server access token.</p><input type="password" aria-label="Access token" value={token} onChange={e=>setToken(e.target.value)}/><button onClick={async()=>{try{await api.login(token);done();}catch(e:any){setError(e.message);}}}>Connect</button><p role="alert">{error}</p></div></div>;
}
export function Specs({taskId}:any){
 const [rows,setRows]=useState<any[]>([]);
 const [analyses,setAnalyses]=useState<any[]>([]);const [page,setPage]=useState<any>(null);const [error,setError]=useState('');
 useEffect(()=>{let active=true;const load=async()=>{try{const [specs,reports]=await Promise.all([api.specs(taskId),api.analyses(taskId)]);if(active){setRows(specs);setAnalyses(reports);setError('');}}catch(e:any){if(active)setError(e.message);}};load();const timer=setInterval(load,5000);return()=>{active=false;clearInterval(timer);};},[taskId]);
 const read=async(id:string,section:string,offset=0)=>{try{setPage(await api.analysisPage(taskId,id,section,offset));setError('');}catch(e:any){setError(e.message);}};
 return <div className="panel"><h3>API request shapes ({rows.length})</h3><div className="body">
 {error&&<p role="alert">{error}</p>}
 {analyses.map(a=><details key={a.analysis_id}><summary style={{overflowWrap:'anywhere'}}>{a.kind} · {a.coverage?.status||'partial'} · {a.coverage?.files_analyzed||0}/{a.coverage?.files_total||0} files · {a.url}</summary>
 <p>File coverage: {a.coverage?.file_status||'partial'} · Symbolic contracts: {a.coverage?.contracts_symbolic||0}. Static analysis requires review.</p>
 <div style={{display:'flex',flexWrap:'wrap',gap:4}}>{['files','request_shapes','endpoints','possible_secrets','warnings'].map(section=><button key={section} onClick={()=>read(a.analysis_id,section)}>{section} ({a.totals?.[section]||0})</button>)}</div></details>)}
 {page&&<div><p>{page.section}: {page.total?page.offset+1:0}–{page.offset+page.items.length} / {page.total}</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(page.items,null,2)}</pre>{page.next_offset!==null&&<button onClick={()=>read(page.analysis_id,page.section,page.next_offset)}>Next analysis page</button>}</div>}
 {rows.length===0&&<p>No static shapes recovered yet. Check analysis coverage and unresolved runtime values.</p>}{rows.map(r=><details key={r.id}><summary>#{r.id} {r.method} {r.url}{r.shape?.review_required?' · review required':''}</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(r.shape,null,2)}</pre></details>)}</div></div>;
}
export function Tools(){
 const [data,setData]=useState<any>({});const [message,setMessage]=useState('');
 useEffect(()=>{api.mcpServers().then(setData).catch(()=>{});},[]);
 return <div className="panel"><h3>MCP tools</h3><div className="body"><pre>{JSON.stringify(data,null,2)}</pre><button onClick={async()=>{try{const r=await api.testMcp();setMessage(JSON.stringify(r,null,2));setData(await api.mcpServers());}catch(e:any){setMessage(e.message);}}}>Test configured servers</button><pre>{message}</pre></div></div>;
}
