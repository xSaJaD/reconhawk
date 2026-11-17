import os,tempfile
_tmp=tempfile.TemporaryDirectory()
os.environ['DATABASE_URL']='sqlite+aiosqlite:///'+_tmp.name+'/test.db'
os.environ['ARTIFACTS_DIR']=_tmp.name+'/artifacts'
os.environ['ANTHROPIC_API_KEY']=''
import asyncio,json,socket,ipaddress,unittest
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
import httpx
from sqlalchemy import select
from app import db,identities,settings_store,usage,verify,scheduler
from app.config import settings
from app.scope import check_url,ScopeError,load_scope
from app.guard import request,policy
from app.agent.runner import AgentRun,start_task,ACTIVE,STOP_FLAGS,INBOX
from app.main import app
from app.shapes import extract,unresolved

Client=httpx.AsyncClient
def dns(host,*a,**k):
    try: ip=str(ipaddress.ip_address(host))
    except ValueError: ip='93.184.216.34'
    return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,0))]

class ReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        settings.api_token='';settings.scope_enforced=True
        async with db.engine.begin() as c: await c.run_sync(db.Base.metadata.drop_all)
        await db.init_db()
        async with db.SessionLocal() as s:
            a,b=db.Program(name='A',scope_in='["example.test"]'),db.Program(name='B',scope_in='["other.test"]')
            s.add_all([a,b]);await s.flush()
            self.pid,self.other_pid=a.id,b.id
            t,u=db.Task(target='https://example.test',program_id=a.id),db.Task(target='https://other.test',program_id=b.id)
            s.add_all([t,u]);await s.commit();self.tid,self.other_tid=t.id,u.id
    async def asyncTearDown(self):
        await scheduler.stop();ACTIVE.clear();STOP_FLAGS.clear();INBOX.clear();await db.engine.dispose()
    async def test_identity_isolation(self):
        async with db.SessionLocal() as s:
            s.add(db.Identity(label='A',program_id=self.other_pid,material='TEST'));await s.commit()
        self.assertIsNone(await identities.get_by_label(self.tid,'A'))
    async def test_identity_global_fallback(self):
        async with db.SessionLocal() as s:
            s.add(db.Identity(label='A',material='GLOBAL'));await s.commit()
        self.assertEqual((await identities.get_by_label(self.tid,'A')).material,'GLOBAL')
    async def test_scope_private(self):
        with patch('app.scope.socket.getaddrinfo',side_effect=dns):
            for url in ('http://127.0.0.1','http://10.0.0.1','http://[::1]','file:///etc/passwd','https://u:p@example.test'):
                with self.subTest(url=url),self.assertRaises(ScopeError): check_url(url,[],[])
    async def test_scope_dns_failure(self):
        with patch('app.scope.socket.getaddrinfo',side_effect=socket.gaierror()),self.assertRaises(ScopeError): check_url('https://bad.test',[],[])
    async def test_scope_allowlist(self):
        with patch('app.scope.socket.getaddrinfo',side_effect=dns):
            check_url('https://api.example.test',['*.example.test'],[])
            with self.assertRaises(ScopeError):check_url('https://other.test',['*.example.test'],[])
    async def test_empty_program_rejected(self):
        with self.assertRaises(ScopeError):load_scope(SimpleNamespace(scope_in='[]',scope_out='[]'))
    async def test_redirect_private_blocked(self):
        calls=[]
        def mock(req): calls.append(req);return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
        with patch('app.scope.socket.getaddrinfo',side_effect=dns),patch('app.guard.httpx.AsyncClient',side_effect=lambda **kw:Client(transport=httpx.MockTransport(mock),**kw)):
            with self.assertRaises(ScopeError):await request('GET','https://example.test')
        self.assertEqual(len(calls),1);self.assertEqual(calls[0].url.host,'93.184.216.34')
    async def test_redirect_scope_blocked(self):
        token=policy.set((['example.test'],[]))
        try:
            with patch('app.scope.socket.getaddrinfo',side_effect=dns),patch('app.guard.httpx.AsyncClient',side_effect=lambda **kw:Client(transport=httpx.MockTransport(lambda req:httpx.Response(302,headers={'location':'https://other.test'})),**kw)):
                with self.assertRaises(ScopeError):await request('GET','https://example.test')
        finally:policy.reset(token)
    async def test_redirect_strips_credentials(self):
        calls=[]
        def mock(req):
            calls.append(req)
            return httpx.Response(302,headers={'location':'https://other.test'}) if len(calls)==1 else httpx.Response(200,text='ok')
        with patch('app.scope.socket.getaddrinfo',side_effect=dns),patch('app.guard.httpx.AsyncClient',side_effect=lambda **kw:Client(transport=httpx.MockTransport(mock),**kw)):
            await request('GET','https://example.test',headers={'Authorization':'Bearer TEST','Cookie':'TEST','X-Key':'TEST'})
        self.assertNotIn('authorization',calls[1].headers);self.assertNotIn('cookie',calls[1].headers)
    async def test_bounded_body(self):
        with patch('app.scope.socket.getaddrinfo',side_effect=dns),patch('app.guard.httpx.AsyncClient',side_effect=lambda **kw:Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,text='x'*100)),**kw)):
            with self.assertRaises(ValueError):await request('GET','https://example.test',max_bytes=5)
    async def test_gzip(self):
        import gzip
        with patch('app.scope.socket.getaddrinfo',side_effect=dns),patch('app.guard.httpx.AsyncClient',side_effect=lambda **kw:Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=gzip.compress(b'hello'),headers={'content-encoding':'gzip'})),**kw)):
            self.assertEqual((await request('GET','https://example.test')).text,'hello')
    async def test_shell_gate(self):
        with patch('app.tools.workspace.run_command',new_callable=AsyncMock) as call:
            _,error=await AgentRun(self.tid).dispatch('run_command',{'command':'echo test'})
            self.assertTrue(error);call.assert_not_awaited()
    async def test_idea_isolation(self):
        async with db.SessionLocal() as s:
            idea=db.Idea(task_id=self.other_tid,text='private');s.add(idea);await s.commit()
        _,error=await AgentRun(self.tid).dispatch('resolve_idea',{'idea_id':idea.id});self.assertTrue(error)
    async def test_actual_model_accounting(self):
        await asyncio.gather(usage.record(self.tid,'openai','gpt-4o',1000,1000),usage.record(self.tid,'openai','gpt-4o-mini',1000,1000))
        async with db.SessionLocal() as s:
            t=await s.get(db.Task,self.tid);self.assertEqual(t.tokens_in,2000);self.assertAlmostEqual(t.cost_usd,.01325)
    async def test_unknown_price(self):
        await usage.record(self.tid,'custom','local-model',5,5)
        async with db.SessionLocal() as s:self.assertTrue((await s.get(db.Task,self.tid)).unknown_price)
    async def test_limits(self):
        for values in ({'max_subagents':'-1'},{'max_test_accounts':'99'},{'budget_usd_per_task':'NaN'}):
            with self.assertRaises(ValueError):await settings_store.set_many(values)
    async def test_runtime_scope_setting(self):
        await settings_store.set_many({'scope_enforced':'false'});self.assertFalse(settings.scope_enforced)
    async def test_ast(self):
        shapes=extract('fetch("/api/x",{method:"POST",body:JSON.stringify({id:user.id}),headers:{"X-Test":"yes"}});axios.post("/api/y",{ok:true})')
        self.assertEqual(len(shapes),2);self.assertTrue(unresolved(shapes[0]['body']));self.assertFalse(unresolved(shapes[1]['body']))
    async def test_probe_symbolic_rejected(self):
        async with db.SessionLocal() as s:
            spec=db.EndpointSpec(task_id=self.tid,url='https://example.test',method='POST',shape='{"body":{"id":{"$expression":"user.id"}}}');s.add(spec);await s.commit()
        _,error=await AgentRun(self.tid).dispatch('probe_spec',{'spec_id':spec.id,'confirm_write':True});self.assertTrue(error)
    async def test_no_unlinked_verification(self):
        async with db.SessionLocal() as s:
            f=db.Finding(task_id=self.tid,title='Assertions');s.add(f);await s.commit()
        model=SimpleNamespace(complete=AsyncMock(return_value='{"confidence":100,"verdict":"confirmed","checks":{"reproducible":true,"impact_proven":true}}'))
        with patch('app.verify.build_llm',return_value=model):self.assertFalse((await verify.verify_finding(f.id))['verified'])
    async def test_auth(self):
        async with Client(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as c:
            self.assertEqual((await c.get('/api/health')).status_code,200)
            settings.api_token='TEST_ONLY'
            self.assertEqual((await c.get('/api/health')).status_code,401)
            self.assertEqual((await c.post('/api/session',json={'token':'TEST_ONLY'})).status_code,200)
            self.assertEqual((await c.get('/api/health')).status_code,200)
            self.assertEqual((await c.get('/api/health',headers={'Origin':'https://evil.test'})).status_code,401)
    async def test_scheduler_persisted_timezone(self):
        async with db.SessionLocal() as s:s.add(db.Routine(name='Recent',target='https://example.test',last_run=db.now()));await s.commit()
        with patch('app.scheduler.start_task',new_callable=AsyncMock) as call:await scheduler._tick();call.assert_not_awaited()
    async def test_resume_policy_and_checkpoint(self):
        messages=[{'role':'user','content':'saved'}]
        async with db.SessionLocal() as s:
            t=await s.get(db.Task,self.tid);t.provider='custom';t.model='local';t.checkpoint=json.dumps({'provider':'custom','model':'local','messages':messages})
            p=await s.get(db.Program,self.pid);p.policy='No account changes';await s.commit()
        model=SimpleNamespace(agent_loop=AsyncMock(return_value='done'))
        with patch('app.agent.runner.build_llm',return_value=model),patch('app.agent.runner.notify.push',new_callable=AsyncMock),patch('app.tools.browser.BrowserSession.start',new_callable=AsyncMock):await start_task(self.tid)
        self.assertEqual(model.agent_loop.call_args.kwargs['initial_messages'],messages)
        self.assertIn('No account changes',model.agent_loop.call_args.kwargs['system'])
    async def test_resume_duplicate(self):
        async with db.SessionLocal() as s:t=await s.get(db.Task,self.tid);t.status='stopped';await s.commit()
        with patch('app.main.start_task',new_callable=AsyncMock),patch('app.scope.socket.getaddrinfo',side_effect=dns):
            async with Client(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as c:
                self.assertEqual((await c.post(f'/api/tasks/{self.tid}/resume')).status_code,200)
                self.assertEqual((await c.post(f'/api/tasks/{self.tid}/resume')).status_code,409)
    async def test_mcp_scope(self):
        from app.mcp_client import Manager
        m=Manager();token=policy.set((['example.test'],[]))
        try:
            with patch('app.scope.socket.getaddrinfo',side_effect=dns),self.assertRaises(ScopeError):await m.call('missing',{'url':'https://other.test'})
        finally:policy.reset(token)
    async def test_real_mcp_transport(self):
        import sys
        from pathlib import Path
        from app.mcp_client import Manager,STATUS
        m=Manager()
        try:
            await m.connect({'enable_mcp':'true','enable_shell':'true','mcp_servers':json.dumps([{'name':'fixture','transport':'stdio','command':sys.executable,'args':[str(Path(__file__).with_name('mcp_fixture.py'))],'tools':['inspect_fixture']}])})
            self.assertEqual(len(m.tools),1,STATUS)
            result,error=await m.call('mcp__fixture__inspect_fixture',{'label':'offline'})
            self.assertFalse(error);self.assertIn('fixture:offline',result)
        finally:await m.close()
    async def test_linked_verification(self):
        async with db.SessionLocal() as s:
            proof=db.RepeaterRequest(task_id=self.tid,url='https://example.test/proof',status_code=200,response_body='PROOF_MARKER')
            other=db.RepeaterRequest(task_id=self.tid,url='https://example.test/other',status_code=200,response_body='UNRELATED_MARKER')
            s.add_all([proof,other]);await s.flush()
            f=db.Finding(task_id=self.tid,title='Test',evidence_ids=json.dumps([proof.id]));s.add(f);await s.commit()
        model=SimpleNamespace(complete=AsyncMock(return_value='{"confidence":95,"verdict":"confirmed","checks":{"reproducible":true,"impact_proven":true,"redacted":true}}'))
        with patch('app.verify.build_llm',return_value=model):self.assertTrue((await verify.verify_finding(f.id))['verified'])
        prompt=model.complete.call_args.args[1]
        self.assertIn('PROOF_MARKER',prompt);self.assertNotIn('UNRELATED_MARKER',prompt)
    async def test_browser_scope(self):
        from app.tools.browser import BrowserSession
        b=BrowserSession(self.tid);b.scope_in=['example.test']
        route=SimpleNamespace(request=SimpleNamespace(method='GET',url='https://other.test',all_headers=AsyncMock(return_value={}),post_data_buffer=None),abort=AsyncMock(),fulfill=AsyncMock())
        with patch('app.scope.socket.getaddrinfo',side_effect=dns):await b._route(route)
        route.abort.assert_awaited_once();route.fulfill.assert_not_awaited()

if __name__=='__main__':unittest.main()
