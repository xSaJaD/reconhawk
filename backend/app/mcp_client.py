import asyncio
import json
import re
from contextlib import AsyncExitStack
from .scope import check_url
from .guard import policy

STATUS={}

def validate(servers):
    if not isinstance(servers,list) or len(servers)>10: raise ValueError('At most ten MCP servers')
    names=set()
    for s in servers:
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,20}',s.get('name','')) or s['name'] in names: raise ValueError('Unique MCP names required')
        names.add(s['name'])
        if s.get('transport') not in ('stdio','http','sse') or not isinstance(s.get('tools'),list): raise ValueError('Transport and explicit tool allowlist required')
        if 'env' in s or 'headers' in s: raise ValueError('Keep server secrets in its environment')

class Manager:
    def __init__(self): self.stack=AsyncExitStack();self.tools=[];self.registry={}
    async def connect(self,cfg):
        if cfg.get('enable_mcp')!='true': return
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamablehttp_client
        servers=json.loads(cfg.get('mcp_servers','[]'));validate(servers)
        for s in servers:
            try:
                if s['transport']=='stdio':
                    if cfg.get('enable_shell')!='true': raise ValueError('stdio requires shell enabled')
                    transport=stdio_client(StdioServerParameters(command=s['command'],args=s.get('args',[])))
                else: transport=(sse_client if s['transport']=='sse' else streamablehttp_client)(s['url'])
                streams=await self.stack.enter_async_context(transport)
                session=await self.stack.enter_async_context(ClientSession(streams[0],streams[1]))
                await asyncio.wait_for(session.initialize(),20)
                listing=await asyncio.wait_for(session.list_tools(),20)
                names=[]
                for tool in listing.tools:
                    name=f"mcp__{s['name']}__{tool.name}"
                    if tool.name not in s['tools'] or len(name)>64 or not re.fullmatch(r'[a-zA-Z0-9_-]+',name): continue
                    self.registry[name]=(session,tool.name);names.append(name)
                    self.tools.append({'name':name,'description':tool.description or '', 'input_schema':tool.inputSchema})
                STATUS[s['name']]={'tools':names,'connected':True}
            except Exception as e: STATUS[s['name']]={'error':str(e)[:300],'connected':False}
    async def call(self,name,args):
        def inspect(v,key=''):
            if isinstance(v,dict):
                for k,x in v.items(): inspect(x,k)
            elif isinstance(v,list):
                for x in v: inspect(x,key)
            elif isinstance(v,str):
                for url in re.findall(r'https?://[^\s<>"\']+',v): check_url(url,*policy.get())
                if key in ('host','hostname','domain','target','url') and v: check_url(v,*policy.get())
        inspect(args)
        if name not in self.registry: raise ValueError('Tool not allowed')
        session,tool=self.registry[name]
        result=await asyncio.wait_for(session.call_tool(tool,args),60)
        return result.model_dump_json()[:20000],bool(result.isError)
    async def close(self):
        await self.stack.aclose()
        for name in self.registry:
            if name.split('__')[1] in STATUS: STATUS[name.split('__')[1]]['connected']=False
