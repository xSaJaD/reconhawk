"""Specialist completion must reflect tool errors, coverage and iteration limits."""
import asyncio
import json
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.db import Base, Task, SubAgent
from app.agent import subagent


class SpecialistCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage=tempfile.TemporaryDirectory(prefix='rh-specialist-')
        self.engine=create_async_engine('sqlite+aiosqlite:///'+self.storage.name+'/test.db')
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        async with self.sessions() as s:
            task=Task(target='https://example.test');s.add(task);await s.commit();tid=task.id
        self.parent=SimpleNamespace(task_id=tid,target='https://example.test',policy='Fixture only',
            _provider_name='custom',_model_name='fixture',effort='high',stop=asyncio.Event(),
            subagent_semaphore=asyncio.Semaphore(1),_on_usage=AsyncMock(),dispatch=AsyncMock())
        self.model=SimpleNamespace(last_stop_reason='model_finished',agent_loop=AsyncMock(return_value='Complete report'))
        self.patches=ExitStack()
        for target,attr,val in [(subagent,'SessionLocal',self.sessions),(subagent,'build_llm',lambda *a,**kw:self.model),
            (subagent.settings_store,'llm_config',AsyncMock(return_value={'effort':'high'})),
            (subagent.settings_store,'get_all',AsyncMock(return_value={})),
            (subagent.settings_store,'get',AsyncMock(return_value='14')),
            (subagent.bus,'emit',AsyncMock())]:self.patches.enter_context(patch.object(target,attr,val))

    async def asyncTearDown(self):
        self.patches.close();await self.engine.dispose();self.storage.cleanup()

    async def row(self):
        async with self.sessions() as session:
            return (await session.execute(select(SubAgent))).scalars().one()

    def setup_analysis(self,status='complete',text='report'):
        manifest={'analysis_id':'a'*32,'coverage':{'status':status,'file_status':'complete'}}
        self.parent.dispatch.return_value=(json.dumps(manifest),False)
        async def loop(**kw):
            await kw['dispatch']('analyze_js',{'url':'https://example.test/main.js'})
            return text
        self.model.agent_loop.side_effect=loop

    async def test_js_without_analysis_is_partial(self):
        await subagent.SubAgentRun(self.parent,'js','js_analyst','Read JS').run()
        self.assertEqual((await self.row()).status,'partial')

    async def test_complete_static_manifest_and_full_report_are_preserved(self):
        self.setup_analysis(text='Full report '+'x'*6000)
        await subagent.SubAgentRun(self.parent,'js','js_analyst','Read JS').run()
        row=await self.row()
        self.assertEqual(row.status,'done');self.assertGreater(len(row.summary),6000)
        self.assertEqual(json.loads(row.analysis_json)['stop_reason'],'model_finished')

    async def test_symbolic_manifest_is_partial(self):
        self.setup_analysis('partial')
        await subagent.SubAgentRun(self.parent,'js','js_analyst','Read JS').run()
        self.assertEqual((await self.row()).status,'partial')

    async def test_recon_role_cannot_hide_partial_js_analysis(self):
        self.setup_analysis('partial')
        await subagent.SubAgentRun(self.parent,'recon JS','recon','Read JS').run()
        self.assertEqual((await self.row()).status,'partial')

    async def test_iteration_cap_is_partial_even_when_text_says_done(self):
        self.setup_analysis();self.model.last_stop_reason='iteration_limit'
        await subagent.SubAgentRun(self.parent,'js','js_analyst','Read JS').run()
        self.assertEqual((await self.row()).status,'partial')

    async def test_write_requests_are_blocked_and_reported(self):
        async def loop(**kw):
            text,error=await kw['dispatch']('http_request',{'url':'https://example.test/submit','method':'POST'})
            self.assertTrue(error);self.assertIn('read-only',text)
            return 'Done'
        self.model.agent_loop.side_effect=loop
        await subagent.SubAgentRun(self.parent,'js','js_analyst','Read JS').run()
        self.parent.dispatch.assert_not_awaited()
        self.assertEqual(len(json.loads((await self.row()).analysis_json)['tool_errors']),1)

    async def test_cancelled_specialist_is_persisted_as_paused(self):
        self.model.agent_loop.side_effect=asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await subagent.SubAgentRun(self.parent,'js','js_analyst','Read JS').run()
        self.assertEqual((await self.row()).status,'paused')

    def test_js_tool_catalog_includes_paged_reads_but_no_shell_or_recursion(self):
        names={x['name'] for x in subagent._tools_for('js_analyst')}
        self.assertTrue({'read_file','read_analysis','list_analyses','analyze_source_map','list_specs'}<=names)
        self.assertFalse({'spawn_subagents','spawn_subagent','run_command','write_file','probe_spec'}&names)
