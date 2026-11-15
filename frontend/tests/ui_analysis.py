"""Production browser regression: analysis coverage, paging and specialist reports."""
import asyncio
import json
import os
import re
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, expect

APP=os.environ.get('UI_TEST_URL','http://127.0.0.1:3135')
API=os.environ.get('UI_TEST_API','http://localhost:8000')


async def run():
    target='https://fixture.example.test'
    aid='a'*32;offsets=[];errors=[]
    manifest={'analysis_id':aid,'kind':'javascript','url':target+'/static/main.js',
        'coverage':{'status':'partial','file_status':'partial','files_total':3,'files_analyzed':2,'contracts_symbolic':25},
        'totals':{'files':3,'request_shapes':25,'endpoints':25,'possible_secrets':0,'warnings':1}}
    shapes=[{'url':'/api/fixture/'+str(i),'method':'PUT','body':{'name':{'$expression':'form.name'}}} for i in range(25)]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=await browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        async def route(route):
            url=route.request.url
            if url.startswith(APP+'/'):return await route.continue_()
            if not url.startswith(API+'/'):return await route.abort()
            path=urlparse(url).path;status=200;data=[]
            headers={'Access-Control-Allow-Origin':APP,'Access-Control-Allow-Credentials':'true',
                'Access-Control-Allow-Headers':'Content-Type','Access-Control-Allow-Methods':'GET, OPTIONS'}
            if route.request.method=='OPTIONS':return await route.fulfill(status=204,headers=headers)
            if path=='/api/health':data={'ok':True,'has_key':True,'provider':'custom','model':'fixture','tools':35,'skills':27,'scope_enforced':True}
            elif path=='/api/tasks':data=[{'id':1,'target':target,'objective':'Offline fixture','status':'paused','provider':'custom','model':'fixture'}]
            elif path=='/api/programs/sync_h1/status':data={'status':'idle'}
            elif path=='/api/tasks/1/subagents':data=[{'id':1,'name':'JS fixture','status':'partial','summary':'Full report '+'x'*4500+' REPORT_TAIL',
                'coverage':{'stop_reason':'iteration_limit','analyses':[manifest],'tool_errors':[]}}]
            elif path=='/api/tasks/1/specs':data=[{'id':1,'url':target+'/api/items','method':'PUT','shape':{'review_required':True}}]
            elif path=='/api/tasks/1/analyses':data=[manifest]
            elif path=='/api/tasks/1/analyses/'+aid:
                q=parse_qs(urlparse(url).query);section=q.get('section',['request_shapes'])[0];offset=int(q.get('offset',['0'])[0])
                rows=shapes if section=='request_shapes' else ([{'warning':'Fixture chunk deferred'}] if section=='warnings' else [{'url':target+'/one.js','status':'parsed'}])
                if section=='request_shapes':offsets.append(offset)
                data={'analysis_id':aid,'section':section,'offset':offset,'total':len(rows),'items':rows[offset:offset+10],
                      'next_offset':offset+10 if offset+10<len(rows) else None}
            elif path=='/api/tasks/1/graph':data={'nodes':[],'edges':[]}
            elif path=='/api/tasks/1/overview':status=404;data={'detail':'Fixture overview omitted'}
            await route.fulfill(status=status,headers=headers,json=data)
        await page.route('**/*',route)
        await page.route_web_socket(API.replace('http','ws',1)+'/ws',lambda ws:None)
        await page.goto(APP,wait_until='networkidle')
        await page.locator('.sidebar').get_by_text(target,exact=True).click()
        await page.get_by_text('JS fixture · partial',exact=True).click()
        await expect(page.get_by_text(re.compile('Full report'))).to_contain_text('REPORT_TAIL')
        await expect(page.get_by_text('Stop: iteration_limit',exact=False)).to_be_visible()
        await page.get_by_role('tab',name='Assets',exact=True).click()
        await page.locator('summary').filter(has_text='javascript · partial').click()
        await expect(page.get_by_text('File coverage: partial',exact=False)).to_be_visible()
        await page.get_by_role('button',name='request_shapes (25)',exact=True).click()
        await expect(page.get_by_text('request_shapes: 1–10 / 25',exact=True)).to_be_visible()
        await page.get_by_role('button',name='Next analysis page').click()
        await expect(page.get_by_text('request_shapes: 11–20 / 25',exact=True)).to_be_visible()
        await page.get_by_role('button',name='Next analysis page').click()
        await expect(page.get_by_text('request_shapes: 21–25 / 25',exact=True)).to_be_visible()
        await expect(page.get_by_role('button',name='Next analysis page')).to_have_count(0)
        await page.get_by_role('button',name='warnings (1)',exact=True).click()
        await expect(page.get_by_text('Fixture chunk deferred',exact=False)).to_be_visible()
        await page.screenshot(path=os.environ.get('UI_ANALYSIS_SCREENSHOT','/tmp/reconhawk-analysis.png'),full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.locator('.main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'),'Mobile analysis view overflows'
        await page.screenshot(path=os.environ.get('UI_ANALYSIS_MOBILE_SCREENSHOT','/tmp/reconhawk-analysis-mobile.png'),full_page=True)
        assert offsets==[0,10,20],offsets
        assert not errors,errors
        await browser.close()
        print(json.dumps({'analysis_pages':offsets,'partial_status':'visible','specialist_report':'full tail retained','warnings':'visible','mobile':True,'page_errors':errors}))

asyncio.run(run())
