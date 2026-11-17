"""Real ASGI routing for skill names containing slashes and URL metacharacters."""
import tempfile
import unittest
from contextlib import ExitStack
from urllib.parse import quote
import json

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, skills_seed
from app.db import Base, Skill, Setting


class SkillRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = tempfile.TemporaryDirectory(prefix='rh-skill-routes-')
        self.engine = create_async_engine('sqlite+aiosqlite:///' + self.storage.name + '/test.db')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        from unittest.mock import patch
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(main, 'SessionLocal', self.sessions))
        self.patches.enter_context(patch.object(skills_seed, 'SessionLocal', self.sessions))
        self.patches.enter_context(patch.object(main.settings, 'api_token', ''))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://localhost')

    async def asyncTearDown(self):
        await self.http.aclose()
        self.patches.close()
        await self.engine.dispose()
        self.storage.cleanup()

    async def seed(self, name):
        async with self.sessions() as session:
            session.add(Skill(name=name, category='custom', playbook='original', is_custom=True,
                              source='custom', enabled=True))
            await session.commit()

    async def test_delete_slash_name_from_legacy_frontend(self):
        name = 'IDOR / BOLA (object-level authorization)'
        await self.seed(name)
        response = await self.http.delete('/api/skills/' + quote(name, safe=''))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.http.get('/api/skills')).json(), [])

    async def test_query_name_preserves_url_characters_through_all_actions(self):
        name = 'صلاحيات / OAuth?tenant=1#fragment/%2F/toggle'
        await self.seed(name)
        response = await self.http.put('/api/skills', params={'name': name}, json={'playbook': 'edited'})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.http.post('/api/skills/toggle', params={'name': name})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['enabled'])
        row, = (await self.http.get('/api/skills')).json()
        self.assertEqual((row['name'], row['playbook']), (name, 'edited'))
        response = await self.http.delete('/api/skills', params={'name': name})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_legacy_edit_and_toggle_accept_slashes(self):
        name = 'OAuth / OIDC'
        await self.seed(name)
        path = '/api/skills/' + quote(name, safe='')
        self.assertEqual((await self.http.put(path, json={'summary': 'changed'})).status_code, 200)
        response = await self.http.put(path + '/toggle')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['enabled'])

    async def test_deleted_builtin_stays_deleted_after_restart_seed(self):
        await skills_seed.seed_skills()
        name = skills_seed.DEFAULT_SKILLS[0][0]
        self.assertIn('/', name)
        response = await self.http.delete('/api/skills', params={'name': name})
        self.assertEqual(response.status_code, 200, response.text)
        await skills_seed.seed_skills()
        async with self.sessions() as session:
            self.assertIsNone(await session.get(Skill, name))
            marker = await session.get(Setting, skills_seed._DELETED_KEY)
            self.assertIn(name, json.loads(marker.value))

    async def test_missing_name_does_not_delete_other_skills(self):
        await self.seed('Keep me')
        self.assertEqual((await self.http.delete('/api/skills')).status_code, 422)
        response = await self.http.delete('/api/skills', params={'name': 'Missing / Skill'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len((await self.http.get('/api/skills')).json()), 1)

    async def test_deleting_builtin_does_not_break_settings_save(self):
        from unittest.mock import patch
        await skills_seed.seed_skills()
        name=skills_seed.DEFAULT_SKILLS[0][0]
        self.assertEqual((await self.http.delete('/api/skills',params={'name':name})).status_code,200)
        with patch.object(main.settings_store,'SessionLocal',self.sessions):
            response=await self.http.get('/api/settings')
            self.assertNotIn(skills_seed._DELETED_KEY,response.json())
            saved=await self.http.put('/api/settings',json=response.json())
            self.assertEqual(saved.status_code,200,saved.text)
        async with self.sessions() as session:
            marker=await session.get(Setting,skills_seed._DELETED_KEY)
            self.assertIn(name,json.loads(marker.value))
