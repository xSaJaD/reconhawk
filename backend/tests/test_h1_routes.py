"""Exercise the actual HTTP contract, queue and serialized program state offline."""
import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import h1_sync, main
from app.db import Base, Program
from app.integrations.hackerone import HackerOneError


class HackerOneRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = tempfile.TemporaryDirectory(prefix='reconhawk-h1-http-')
        self.engine = create_async_engine('sqlite+aiosqlite:///' + self.storage.name + '/test.db')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.fake = SimpleNamespace(list_programs=AsyncMock(return_value=[{'attributes': {
            'handle': 'owned', 'name': 'Owned fixture', 'offers_bounties': True}}]),
            get_scopes=AsyncMock(return_value=[{'id': 'asset-1', 'attributes': {
                'asset_identifier': 'https://owned.example.test/api/', 'asset_type': 'URL',
                'eligible_for_submission': True, 'instruction': 'Owned test data only'}}]))
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(main, 'SessionLocal', self.sessions))
        self.patches.enter_context(patch.object(h1_sync, 'SessionLocal', self.sessions))
        self.patches.enter_context(patch.object(main.settings, 'api_token', ''))
        self.patches.enter_context(patch.object(h1_sync, '_state', {
            'status': 'idle', 'total': 0, 'completed': 0, 'succeeded': 0, 'failed': 0}))
        self.patches.enter_context(patch.object(main.settings_store, 'get_all',
                                               AsyncMock(return_value={})))
        self.patches.enter_context(patch.object(main, 'HackerOneClient', return_value=self.fake))
        self.patches.enter_context(patch.object(h1_sync, 'HackerOneClient', return_value=self.fake))
        self.patches.enter_context(patch.object(h1_sync.notify, 'push', AsyncMock()))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://localhost')

    async def asyncTearDown(self):
        await h1_sync.stop()
        await self.http.aclose()
        self.patches.close()
        await self.engine.dispose()
        self.storage.cleanup()

    async def seed(self, platform='hackerone'):
        async with self.sessions() as session:
            row = Program(name='Existing', platform=platform, handle='owned', scope_in='["old.example.test"]')
            session.add(row)
            await session.commit()
            return row.id

    async def test_import_job_and_program_response_agree(self):
        response = await self.http.post('/api/programs/sync_h1')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()['added'], response.json()['scopes']), (1, 'queued'))
        await h1_sync._task
        status = (await self.http.get('/api/programs/sync_h1/status')).json()
        self.assertEqual((status['status'], status['succeeded'], status['importing']), ('complete', 1, False))
        program, = (await self.http.get('/api/programs')).json()
        self.assertEqual(program['program_type'], 'bounty')
        self.assertEqual(program['scope_in'], ['https://owned.example.test/api/'])
        self.assertEqual(program['scope_sync']['status'], 'synced')
        self.assertEqual(program['scope_assets'][0]['instruction'], 'Owned test data only')
        self.assertIsNotNone(program['scope_sync']['synced_at'])

    async def test_failed_retry_is_visible_and_keeps_previous_scope(self):
        pid = await self.seed()
        self.fake.get_scopes.side_effect = HackerOneError('Access denied (403)', status_code=403)
        response = await self.http.post(f'/api/programs/{pid}/sync_scope')
        self.assertEqual(response.status_code, 200)
        await h1_sync._task
        program, = (await self.http.get('/api/programs')).json()
        self.assertEqual(program['scope_in'], ['old.example.test'])
        self.assertEqual(program['scope_sync']['status'], 'error')
        self.assertIn('403', program['scope_sync']['error'])
        status = (await self.http.get('/api/programs/sync_h1/status')).json()
        self.assertEqual((status['status'], status['failed']), ('partial', 1))

    async def test_missing_and_manual_programs_cannot_queue_h1_work(self):
        self.assertEqual((await self.http.post('/api/programs/999/sync_scope')).status_code, 404)
        pid = await self.seed(platform='manual')
        self.assertEqual((await self.http.post(f'/api/programs/{pid}/sync_scope')).status_code, 400)
        self.fake.get_scopes.assert_not_awaited()

    async def test_in_progress_job_rejects_duplicate_requests(self):
        pid = await self.seed()
        started, finish = asyncio.Event(), asyncio.Event()
        async def pending(_):
            started.set()
            await finish.wait()
            return []
        self.fake.get_scopes.side_effect = pending
        self.assertEqual((await self.http.post(f'/api/programs/{pid}/sync_scope')).status_code, 200)
        await asyncio.wait_for(started.wait(), 5)
        self.assertEqual((await self.http.post('/api/programs/sync_h1')).status_code, 409)
        self.assertEqual((await self.http.post(f'/api/programs/{pid}/sync_scope')).status_code, 409)
        self.fake.list_programs.assert_not_awaited()
        finish.set()
        await h1_sync._task
