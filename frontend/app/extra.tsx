"use client";
import {useEffect,useState} from 'react';
import {api} from '../lib/api';

export function Access({done}:any){
 const [token,setToken]=useState('');const [error,setError]=useState('');
 return <div className="modal-bg"><div className="modal"><h2>Connect to ReconHawk</h2><p>Enter the server access token.</p><input type="password" aria-label="Access token" value={token} onChange={e=>setToken(e.target.value)}/><button onClick={async()=>{try{await api.login(token);done();}catch(e:any){setError(e.message);}}}>Connect</button><p role="alert">{error}</p></div></div>;
}
export function Specs({taskId}:any){
 const [rows,setRows]=useState<any[]>([]);
 useEffect(()=>{api.specs(taskId).then(setRows).catch(()=>{});},[taskId]);
 return <div className="panel"><h3>API request shapes ({rows.length})</h3><div className="body">{rows.length===0&&<p>No static shapes recovered yet.</p>}{rows.map(r=><details key={r.id}><summary>#{r.id} {r.method} {r.url}</summary><pre>{JSON.stringify(r.shape,null,2)}</pre></details>)}</div></div>;
}
export function Tools(){
 const [data,setData]=useState<any>({});const [message,setMessage]=useState('');
 useEffect(()=>{api.mcpServers().then(setData).catch(()=>{});},[]);
 return <div className="panel"><h3>MCP tools</h3><div className="body"><pre>{JSON.stringify(data,null,2)}</pre><button onClick={async()=>{try{const r=await api.testMcp();setMessage(JSON.stringify(r,null,2));setData(await api.mcpServers());}catch(e:any){setMessage(e.message);}}}>Test configured servers</button><pre>{message}</pre></div></div>;
}
