"""Evidence review must never approve a newer or edited finding with an old result."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app import verify
from app.db import Base, Task, Finding, RepeaterRequest

POSITIVE = {'confidence': 95, 'verdict': 'confirmed', 'checks': {'reproducible': True, 'impact_proven': True, 'redacted': True}}


class VerificationRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='verification-')
        self.engine = create_async_engine('sqlite+aiosqlite:///' + str(Path(self.tmp.name) / 'test.db'))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.patches = [patch('app.verify.SessionLocal', self.sessions),
                        patch('app.verify.settings_store.llm_config', AsyncMock(return_value={'provider': 'custom', 'model': 'fixture'})),
                        patch('app.verify.settings_store.get_all', AsyncMock(return_value={})),
                        patch('app.verify.settings_store.get', AsyncMock(return_value='80'))]
        for p in self.patches: p.start()
        async with self.sessions() as s:
            t = Task(target='https://example.test', provider='custom', model='fixture')
            s.add(t); await s.flush()
            proof = RepeaterRequest(task_id=t.id, url='https://example.test/api/owned', status_code=200,
                request_headers=json.dumps({'Authorization': '[identity:userB]', 'X-Api-Key': 'SECRET_TEST_ONLY'}),
                request_body='{"password":"SECRET_PASSWORD"}', response_headers='{"Content-Type":"application/json"}',
                response_body='owned-canary')
            s.add(proof); await s.flush()
            f = Finding(task_id=t.id, title='Fixture', report_md='Original report', evidence_ids=json.dumps([proof.id]))
            s.add(f); await s.commit()
            self.fid, self.rid = f.id, proof.id

    async def asyncTearDown(self):
        for p in reversed(self.patches): p.stop()
        await self.engine.dispose(); self.tmp.cleanup()

    async def review(self, model):
        with patch('app.verify.build_llm', return_value=model):
            return await verify.verify_finding(self.fid, on_usage=AsyncMock())

    async def test_prompt_retains_proof_ids_identity_and_redacts_credentials(self):
        model = SimpleNamespace(complete=AsyncMock(return_value=json.dumps(POSITIVE)))
        result = await self.review(model)
        self.assertTrue(result['verified'])
        prompt = model.complete.call_args.args[1]
        self.assertIn(f'Request #{self.rid}', prompt)
        self.assertIn('[identity:userB]', prompt)
        self.assertIn('Content-Type', prompt)
        self.assertNotIn('SECRET_TEST_ONLY', prompt)
        self.assertNotIn('SECRET_PASSWORD', prompt)

    async def test_relinked_evidence_rejects_stale_review(self):
        async def complete(*args, **kwargs):
            async with self.sessions() as s:
                f = await s.get(Finding, self.fid); f.evidence_ids='[]'; f.verified=False; await s.commit()
            return json.dumps(POSITIVE)
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.review(SimpleNamespace(complete=complete))
        async with self.sessions() as s:
            f = await s.get(Finding, self.fid)
            self.assertFalse(f.verified); self.assertEqual(f.evidence_ids, '[]')

    async def test_report_edit_rejects_stale_review(self):
        async def complete(*args, **kwargs):
            async with self.sessions() as s:
                f=await s.get(Finding, self.fid); f.report_md='Changed report'; await s.commit()
            return json.dumps(POSITIVE)
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.review(SimpleNamespace(complete=complete))

    async def test_old_positive_cannot_overwrite_newer_negative_review(self):
        started, finish = asyncio.Event(), asyncio.Event()
        calls = 0
        async def complete(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set(); await finish.wait()
                return json.dumps(POSITIVE)
            return json.dumps({'confidence': 10, 'verdict': 'false_positive', 'checks': {}})
        model = SimpleNamespace(complete=complete)
        with patch('app.verify.build_llm', return_value=model):
            old = asyncio.create_task(verify.verify_finding(self.fid, on_usage=AsyncMock()))
            await started.wait()
            latest = await verify.verify_finding(self.fid, on_usage=AsyncMock())
            self.assertFalse(latest['verified'])
            finish.set()
            with self.assertRaisesRegex(ValueError, 'changed'):
                await old
        async with self.sessions() as s:
            f=await s.get(Finding,self.fid)
            self.assertFalse(f.verified); self.assertEqual(f.confidence,10)

    async def test_header_only_proof_is_not_discarded(self):
        async with self.sessions() as s:
            r=await s.get(RepeaterRequest,self.rid);r.response_body='';await s.commit()
        result=await self.review(SimpleNamespace(complete=AsyncMock(return_value=json.dumps(POSITIVE))))
        self.assertTrue(result['verified'])
