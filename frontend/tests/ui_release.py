"""Browser checks against a running UI, with every API/provider/H1 request mocked.

Start Next with NEXT_PUBLIC_API_BASE=http://127.0.0.1:9135 on port3135, then run
this file with the backend virtualenv's Python. No real integration is contacted.
"""
import asyncio
import copy
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.llm.base import PROVIDERS
from app.settings_store import DEFAULTS

APP = os.environ.get('UI_TEST_URL', 'http://127.0.0.1:3135')
API = os.environ.get('UI_TEST_API', 'http://127.0.0.1:9135')


async def run():
    cfg = {**DEFAULTS, 'anthropic_api_key': '••••••1111', 'openai_api_key': '••••••2222',
           'custom_api_key': '••••••3333', 'llm_provider': 'anthropic'}
    def program(pid, name, category, status='never'):
        return {'id': pid, 'name': name, 'platform': 'hackerone', 'handle': f'fixture-{pid}',
                'program_type': category, 'scope_in': [], 'scope_out': [], 'policy': 'Owned fixtures only',
                'scope_sync': {'status': status, 'error': '', 'warnings': [], 'web_asset_count': 0}}
    programs = [program(1, 'Paid Alpha', 'bounty'), program(2, 'Legacy Paid', 'bbp'),
                program(3, 'Disclosure Program', 'vdp'), program(4, 'Unknown Program', 'unknown'),
                program(5, 'Failed Program', 'bounty', 'error')]
    programs[-1]['scope_sync']['error'] = 'Fixture: HackerOne denied access (403)'
    state = {'active': False, 'polls': 0, 'poll_failures': 0, 'retry': [], 'tests': [], 'saved': [], 'fail_save': False}
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = await browser.new_page(viewport={'width': 1440, 'height': 1040})
        page.on('pageerror', lambda error: errors.append(str(error)))
        async def route_all(route):
            url = route.request.url
            if url.startswith(APP + '/'):
                return await route.continue_()
            if not url.startswith(API + '/'):
                return await route.abort()
            path = urlparse(url).path
            method = route.request.method
            headers = {'Access-Control-Allow-Origin': APP, 'Access-Control-Allow-Credentials': 'true',
                       'Access-Control-Allow-Headers': 'Content-Type, Authorization',
                       'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS'}
            result, status = {}, 200
            if method == 'OPTIONS':
                return await route.fulfill(status=204, headers=headers)
            if path == '/api/providers': result = PROVIDERS
            elif path == '/api/settings':
                if method == 'PUT':
                    body = route.request.post_data_json
                    state['saved'].append(copy.deepcopy(body))
                    if state['fail_save']:
                        status, result = 500, {'detail': 'Fixture save failure'}
                        state['fail_save'] = False
                    else:
                        cfg.update(body); result = cfg
                else: result = cfg
            elif path == '/api/settings/test':
                state['tests'].append(route.request.post_data_json)
                result = {'ok': True, 'reply': 'FIXTURE_OK'}
            elif path == '/api/health': result = {'ok': True, 'has_key': True, 'provider': cfg['llm_provider'], 'model': cfg['llm_model'], 'tools': 35, 'skills': 21, 'scope_enforced': True}
            elif path == '/api/programs': result = programs
            elif path == '/api/programs/sync_h1': state['active'] = True; state['polls'] = 0; result = {'total': 26}
            elif path == '/api/programs/sync_h1/status':
                if state['active']:
                    state['polls'] += 1
                    if state['polls'] == 3:
                        state['poll_failures'] += 1
                        return await route.fulfill(status=503, headers=headers, json={'detail': 'Fixture transient interruption'})
                    if state['polls'] >= 23:
                        state['active'] = False
                        programs[0]['scope_in'] = ['https://paid.example.test/api/']
                        programs[0]['scope_sync'].update(status='synced', web_asset_count=1)
                    result = {'status': 'running' if state['active'] else 'complete', 'total': 26,
                              'completed': min(state['polls'], 26) if state['active'] else 26,
                              'succeeded': min(state['polls'], 26) if state['active'] else 26, 'failed': 0}
                else: result = {'status': 'idle'}
            elif path.endswith('/sync_scope'):
                pid = int(path.split('/')[-2]); state['retry'].append(pid)
                programs[pid - 1]['scope_sync'].update(status='synced', error='', web_asset_count=1)
                programs[pid - 1]['scope_in'] = ['https://retry.example.test/']
                result = {'status': 'complete', 'total': 1, 'completed': 1, 'succeeded': 1, 'failed': 0}
            elif path in ('/api/tasks', '/api/notifications', '/api/identities'): result = []
            elif path == '/api/notifications/read': result = {'ok': True}
            else: status, result = 404, {'detail': 'Unmocked endpoint ' + path}
            await route.fulfill(status=status, headers=headers, json=result)
        await page.route('**/*', route_all)
        await page.route_web_socket(API.replace('http', 'ws', 1) + '/ws', lambda ws: None)
        await page.clock.install()
        await page.goto(APP, wait_until='networkidle')
        await page.get_by_role('button', name=re.compile('^Bounty')).click()
        await expect(page.get_by_text('Paid Alpha', exact=True)).to_be_visible()
        await expect(page.get_by_text('Legacy Paid', exact=True)).to_be_visible()
        await expect(page.get_by_text('Disclosure Program', exact=True)).to_have_count(0)
        await page.get_by_role('button', name=re.compile('^Unknown')).click()
        await expect(page.get_by_text('Unknown Program', exact=True)).to_be_visible()
        await expect(page.get_by_text('Legacy Paid', exact=True)).to_have_count(0)
        await page.get_by_role('button', name=re.compile('^All')).click()

        await page.get_by_role('button', name='⚙ Settings').click()
        await expect(page.get_by_label('Anthropic (Claude) API key', exact=True)).to_be_visible()
        await expect(page.get_by_label('Custom base URL', exact=True)).to_have_count(0)
        await expect(page.locator('#main-effort')).to_be_visible()
        await page.locator('#main-provider').select_option('custom')
        await expect(page.get_by_label('Custom base URL', exact=True)).to_be_visible()
        await expect(page.get_by_label('Anthropic (Claude) API key', exact=True)).to_have_count(0)
        await expect(page.locator('#main-effort')).to_have_count(0)
        await page.locator('#main-model').fill('fixture-custom-model')
        await page.locator('#main-provider').select_option('openai')
        await expect(page.locator('#main-model')).not_to_have_value('fixture-custom-model')
        await page.locator('#main-provider').select_option('custom')
        await expect(page.locator('#main-model')).to_have_value('fixture-custom-model')
        await page.locator('#worker-enabled').check()
        await expect(page.locator('#worker-api-key')).to_have_count(0)
        await page.locator('#worker-provider').select_option('openai')
        await expect(page.get_by_label('OpenAI API key', exact=True)).to_be_visible()
        await page.locator('#worker-model').fill('fixture-worker-model')
        await page.get_by_role('button', name='Test worker connection', exact=True).click()
        await expect(page.get_by_role('status')).to_contain_text('Worker: FIXTURE_OK')
        assert state['tests'][-1] == {'provider': 'openai', 'model': 'fixture-worker-model'}
        await page.get_by_role('button', name='Test main connection', exact=True).click()
        await expect(page.get_by_role('status')).to_contain_text('Main: FIXTURE_OK')
        assert state['tests'][-1] == {'provider': 'custom', 'model': 'fixture-custom-model'}
        assert state['saved'][-1]['anthropic_api_key'] == '••••••1111'
        await page.locator('#worker-enabled').uncheck()
        state['fail_save'] = True
        await page.get_by_role('button', name='Save', exact=True).click()
        await expect(page.get_by_role('status')).to_contain_text('Fixture save failure')
        await expect(page.get_by_role('button', name='Save', exact=True)).to_be_enabled()
        await page.get_by_role('button', name='Save', exact=True).click()
        await expect(page.get_by_role('button', name='Saved ✓', exact=True)).to_be_visible()
        assert state['saved'][-1]['worker_provider'] == '' and state['saved'][-1]['worker_model'] == ''
        await page.get_by_role('button', name='Close', exact=True).click()

        await page.get_by_role('button', name='⟳ H1', exact=True).click()
        for _ in range(28):
            await page.clock.run_for(2100)
            await page.wait_for_timeout(60)
        assert state['polls'] >= 23, state['polls']
        assert state['poll_failures'] == 1
        await expect(page.get_by_text('complete: 26/26 scopes · 26 synced · 0 failed', exact=True)).to_be_visible()
        await page.get_by_text('Paid Alpha', exact=True).click()
        await expect(page.locator('.modal').get_by_text('https://paid.example.test/api/', exact=True)).to_be_visible()
        await page.get_by_role('button', name='Close', exact=True).click()
        await page.get_by_text('Failed Program', exact=True).click()
        await expect(page.locator('.modal').get_by_role('alert')).to_contain_text('403')
        await page.locator('.modal').get_by_role('button', name='Retry scope for Failed Program').click()
        await expect(page.locator('.modal').get_by_text('https://retry.example.test/', exact=True)).to_be_visible()
        assert state['retry'] == [5]
        await page.get_by_role('button', name='Close', exact=True).click()
        await page.screenshot(path=os.environ.get('UI_SCREENSHOT', '/workspace/work/reconhawk-171.png'), full_page=True)
        await page.set_viewport_size({'width': 390, 'height': 844})
        await expect(page.get_by_role('button', name='⚙ Settings')).to_be_visible()
        await page.get_by_role('button', name='⚙ Settings').click()
        await expect(page.locator('#main-provider')).to_be_visible()
        await expect(page.locator('#main-base-url')).to_be_visible()
        assert await page.locator('.modal').evaluate('(element) => element.scrollWidth <= element.clientWidth + 1'), 'Settings overflows mobile width'
        await page.screenshot(path=os.environ.get('UI_MOBILE_SCREENSHOT', '/workspace/work/reconhawk-171-mobile.png'), full_page=True)
        assert not errors, errors
        await browser.close()
        print(json.dumps({'provider_fields': 'isolated', 'worker_tests': 'correct model', 'masked_keys': 'preserved',
                          'failed_save': 'visible and recoverable', 'paid_and_legacy_bbp': 'visible',
                          'unknown': 'separate', 'scope_poll_virtual_seconds': 28 * 2.1,
                          'scope_status_error': 'recovered after transient 503',
                          'scope_retry': state['retry'], 'mobile': True, 'page_errors': errors}))

asyncio.run(run())
