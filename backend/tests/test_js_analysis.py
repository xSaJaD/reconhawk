"""Offline regression corpus for static analysis, full files, maps and coverage."""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import jsast, shapes, main
from app.db import Base, Task, EndpointSpec
from app.tools import jsanalyze, sourcemap, analysis_store, workspace


class StaticContracts(unittest.TestCase):
    def contracts(self, source):
        return jsast.analyze(source)['request_shapes']

    def test_alias_and_local_request_wrapper(self):
        result = self.contracts('const client=require("axios");function get(path,p){return client.get("/api/"+path,{params:p})}get("items",{page:1});')
        found = next(x for x in result if x['url'] == '/api/items')
        self.assertEqual(found['params'], {'page': 1})
        self.assertTrue(found['review_required'])
        self.assertIn('caller_range', found)

    def test_fetch_config_callsite_follows_body_and_response(self):
        result = self.contracts('function ok(r){return {rows:r.data}}function send(e){return {fetchConfig:{path:"/api/submit",method:"PUT",body:JSON.stringify([e]),success:ok}}}let body={name:form.name,metadata:{kind:form.kind}};send(body);')
        found = next(x for x in result if x['evidence_kind'] == 'local_wrapper')
        self.assertEqual(found['method'], 'PUT')
        self.assertIn('[].metadata.kind', found['body_keys'])
        self.assertIn('r.data', found['response_handlers']['success']['$callback'])
        self.assertTrue(shapes.unresolved(found['body']))

    def test_exported_action_creator_is_explicitly_inferred(self):
        result = self.contracts('function send(e){return {fetchConfig:{path:"/api/login",method:"POST",body:JSON.stringify(e)}}}lib.d(exports,"postLogin",function(){return send});actions.postLogin({email:form.phone,password:form.password});')
        found = next(x for x in result if x['evidence_kind'] == 'export_name_inference')
        self.assertEqual(set(found['body']), {'email', 'password'})
        self.assertTrue(found['review_required'])

    def test_scope_shadowing_and_uninitialized_local(self):
        result = self.contracts('var item={wrong:1};function f(){var item;item={right:1};fetch("/a",{body:JSON.stringify(item)})} {let item={block:1};fetch("/b",{body:JSON.stringify(item)})}fetch("/c",{body:JSON.stringify(item)});')
        by_url = {x['url']: x for x in result}
        self.assertEqual(by_url['/a']['body'], {'right': 1})
        self.assertEqual(by_url['/b']['body'], {'block': 1})
        self.assertEqual(by_url['/c']['body'], {'wrong': 1})

    def test_unknown_object_get_is_not_axios(self):
        self.assertEqual(self.contracts('const store={};store.get("/not-a-request");const fake=foo("axios");fake.post("/nope",{});'), [])
        self.assertEqual(self.contracts('function f(require,window){const fake=require("axios");fake.post("/nope",{});window.fetch("/nope");}'), [])

    def test_sparse_webpack_module_identity(self):
        source = '(function(m){ })([function(a,b,r){var c=r(3),q=r.n(c);q.a.get("/api/items");},,,function(e){var s={};s.Axios=1;s.CancelToken=2;s.isCancel=3;e.exports=s;}]);'
        result = self.contracts(source)
        self.assertEqual(result[0]['url'], '/api/items')
        self.assertEqual(result[0]['transport'], 'axios')

    def test_native_fetch_alias_and_callback(self):
        result = self.contracts('import net from "cross-fetch";net("/api/items").then(function(r){return r.json()});')
        self.assertEqual(result[0]['url'], '/api/items')
        self.assertIn('r.json()', result[0]['response_handlers']['then'][0]['$callback'])

    def test_axios_instance_base_and_headers_are_retained(self):
        result=self.contracts('import axios from "axios";const api=axios.create({baseURL:"/api",headers:{"X-Client":"fixture"}});api.get("/items",{headers:{"X-Call":"example"}});')
        self.assertEqual(result[0]['url'],'/api/items')
        self.assertEqual(result[0]['headers'],{'X-Client':'fixture','X-Call':'example'})

    def test_runtime_fetch_options_do_not_default_to_get(self):
        result=self.contracts('fetch("/api/x",runtimeOptions);')
        self.assertTrue(shapes.unresolved(result[0]['method']))

    def test_redux_response_reads_are_attached_to_declared_contract(self):
        result=self.contracts('function ok(r){return {type:"GOT_DATA",data:r}}function get(){return {fetchConfig:{path:"/api/x",method:"GET",success:ok}}}function reducer(state,a){switch(a.type){case "GOT_DATA":return {loginData:{data:a.data}}}}view.props.globalData.loginData.data.token;')
        usage=result[0]['response_usage'][0]
        self.assertEqual(usage['action'],'GOT_DATA')
        self.assertIn('loginData.data.token',usage['observed_state_reads'])

    def test_unknown_branch_does_not_guess_a_method(self):
        result = self.contracts('var verb="GET";if(runtimeFlag){verb="DELETE"}fetch("/api/item",{method:verb});')
        self.assertIsInstance(result[0]['method'], dict)
        self.assertTrue(shapes.unresolved(result[0]['method']))

    def test_branch_evaluation_does_not_use_python_boolean_equality(self):
        source='let path="/safe";[1].includes(true)&&(path="/wrong");fetch(path);let method="GET";if(1===true){method="DELETE"}fetch("/api/a",{method});'
        result=self.contracts(source)
        self.assertEqual(result[0]['url'],'/safe')
        self.assertEqual(result[1]['method'],'GET')

    def test_known_allowlist_changes_wrapper_url_only_when_it_matches(self):
        source='const urls=["/public"];function request(path){var u="/api/"+path;urls.includes(path)&&(u=path);axios.get(u)}request("/public");request("private");'
        urls = [x['url'] for x in self.contracts(source)]
        self.assertIn('/public', urls)
        self.assertIn('/api/private', urls)

    def test_query_names_from_symbolic_concatenation(self):
        result=self.contracts('function route(p,c){return {fetchConfig:{path:"/api/page",method:"GET",queryParams:"page="+p+"&country="+c}}}')
        self.assertEqual(result[0]['query_keys'], ['country', 'page'])

    def test_nested_conditional_fields_are_preserved_without_faking_values(self):
        result=self.contracts('let body={metadata:{kind:"test"}};if(runtimeUser){body.metadata.user_profile_id=runtimeUser.id}fetch("/api/x",{body:JSON.stringify(body)});')
        self.assertIn('metadata.user_profile_id',result[0]['body_keys'])
        self.assertEqual(result[0]['body']['metadata']['kind'],'test')
        self.assertTrue(shapes.unresolved(result[0]['body']))

    def test_symbolic_expression_and_syntax_limits_are_visible(self):
        parsed=jsast.analyze('fetch("/api/a",{body:unknown(' + 'x,'*400 + 'last)})')
        self.assertTrue(parsed['request_shapes'][0]['body']['$expression_truncated'])
        self.assertEqual(jsast.analyze('fetch("/api/x", {')['parse_status'], 'partial')
        with patch.object(jsast,'MAX_NODES',5):
            self.assertIn('AST node limit', ' '.join(jsast.analyze('let a=1;fetch("/x");')['warnings']))

    def test_placeholder_is_not_a_leaked_secret(self):
        for text in ['const TOKEN="access_token";', 'var SECRET="client_secret";', 'const API_KEY="YOUR_API_KEY";']:
            self.assertEqual(jsanalyze.analyze(text)['possible_secrets'], [])

    def test_browser_key_beyond_old_limit_is_classified_and_redacted(self):
        key='AIza'+'aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3wX'
        source='/*'+'x'*410000+'*/;define(obj,"API_KEY","'+key+'");fetch("/late");'
        candidate=jsanalyze.analyze(source)['possible_secrets'][0]
        self.assertEqual(candidate['classification'],'public_client_identifier')
        self.assertNotIn(key, candidate['value_preview'])
        self.assertFalse(candidate['verified'])
        self.assertIn('/late',[x['url'] for x in self.contracts(source)])

    def test_common_chunk_runtime_and_literal_import(self):
        source='o.p="/";function chunk(e){return "partner/static/js/"+e+"."+{2:"abcdef12",3:"abcdef13"}[e]+".chunk.js"}import("./lazy.js");'
        urls=jsanalyze.discover_chunks(source,'https://example.test/partner/static/js/main.js')
        self.assertIn('https://example.test/partner/static/js/2.abcdef12.chunk.js',urls)
        self.assertIn('https://example.test/partner/static/js/lazy.js',urls)
        self.assertNotIn('https://outside.test/tracker.js',jsanalyze.discover_chunks('var analytics="https://outside.test/tracker.js"','https://example.test/main.js'))


class AnalysisPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rh-static-')
        self.engine=create_async_engine('sqlite+aiosqlite:///'+self.temp.name+'/test.db')
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        async with self.sessions() as s:
            task=Task(target='https://example.test/app/');s.add(task);await s.commit();self.tid=task.id
        self.patches=[patch.object(shapes,'SessionLocal',self.sessions),patch.object(main,'SessionLocal',self.sessions),
                      patch.object(main.settings,'api_token',''),patch.object(workspace.settings,'artifacts_dir',self.temp.name+'/artifacts')]
        for p in self.patches:p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):p.stop()
        await self.engine.dispose();self.temp.cleanup()

    def response(self,text,url='https://example.test/main.js',status=200):
        return httpx.Response(status,text=text,request=httpx.Request('GET',url))

    async def test_full_download_not_a_prefix_and_legacy_bound_rejects(self):
        source='/*'+'x'*410000+'*/;fetch("/late");'
        with patch.object(jsanalyze,'request',AsyncMock(return_value=self.response(source))) as call:
            self.assertEqual(await jsanalyze.fetch_js('https://example.test/main.js'),source)
            self.assertEqual(call.call_args.args[0],'GET')
            with self.assertRaises(ValueError):await jsanalyze.fetch_js('https://example.test/main.js',max_chars=400000)

    async def test_bundle_discovers_chunks_and_saves_all_results(self):
        sources={'https://example.test/main.js':'import("./one.js");fetch("/api/a");',
                 'https://example.test/one.js':'fetch("/api/b");'}
        async def fetch(url,**kwargs):return sources[url]
        with patch.object(jsanalyze,'fetch_js',side_effect=fetch):
            manifest=await jsanalyze.analyze_bundle(self.tid,'https://example.test/main.js')
        self.assertEqual(manifest['coverage']['files_analyzed'],2)
        self.assertEqual(manifest['coverage']['file_status'],'complete')
        page=analysis_store.read(self.tid,manifest['analysis_id'])
        self.assertEqual({x['url'] for x in page['items']},{'/api/a','/api/b'})
        async with self.sessions() as session:
            self.assertEqual(len((await session.execute(select(EndpointSpec))).scalars().all()),2)

    async def test_failed_chunk_and_budget_are_partial(self):
        async def fetch(url,**kwargs):
            if url.endswith('main.js'):return 'import("./missing.js");fetch("/api/a");'
            raise ValueError('Fixture blocked destination')
        with patch.object(jsanalyze,'fetch_js',side_effect=fetch):
            manifest=await jsanalyze.analyze_bundle(self.tid,'https://example.test/main.js')
        self.assertEqual(manifest['coverage']['file_status'],'partial')
        self.assertEqual(manifest['coverage']['files_analyzed'],1)
        with patch.object(jsanalyze,'MAX_FILES',1),patch.object(jsanalyze,'fetch_js',side_effect=fetch):
            manifest=await jsanalyze.analyze_bundle(self.tid,'https://example.test/main.js')
        files=analysis_store.read(self.tid,manifest['analysis_id'],'files')['items']
        self.assertEqual(files[-1]['status'],'deferred')

    async def test_stop_prevents_followup_asset_requests(self):
        stopped=asyncio.Event()
        async def fetch(url,**kwargs):
            stopped.set();return 'import("./never-requested.js");fetch("/api/a");'
        with patch.object(jsanalyze,'fetch_js',side_effect=fetch) as call:
            manifest=await jsanalyze.analyze_bundle(self.tid,'https://example.test/main.js',should_stop=stopped.is_set)
        self.assertEqual(call.await_count,1)
        self.assertEqual(manifest['coverage']['file_status'],'partial')

    async def test_source_map_limit_counts_all_deferred_sources(self):
        data={'version':3,'sources':['a.js','b.js','c.js'],'sourcesContent':['fetch("/a")','fetch("/b")','fetch("/c")']}
        with patch.object(sourcemap,'MAX_SOURCES',1),patch.object(sourcemap,'_fetch',AsyncMock(return_value=json.dumps(data))):
            result=await sourcemap.reconstruct(self.tid,'https://example.test/main.js.map')
        self.assertEqual(result['coverage']['files_total'],3)
        self.assertEqual(result['coverage']['files_analyzed'],1)
        self.assertEqual(result['coverage']['file_status'],'partial')

    async def test_relative_contract_uses_document_not_bundle_directory(self):
        await shapes.capture(self.tid,'fetch("items")','https://cdn.example.test/static/main.js')
        async with self.sessions() as session:
            spec=(await session.execute(select(EndpointSpec))).scalars().one()
            self.assertEqual(spec.url,'https://example.test/app/items')

    async def test_alternative_contracts_are_preserved(self):
        await shapes.capture(self.tid,'axios.post("/api/items",{a:1});axios.post("/api/items",{b:2});','https://example.test/main.js')
        async with self.sessions() as session:
            spec=(await session.execute(select(EndpointSpec))).scalars().one()
            self.assertEqual(len(json.loads(spec.shape)['variants']),2)
            self.assertTrue(json.loads(spec.shape)['review_required'])

    async def test_large_valid_map_is_not_truncated(self):
        raw=json.dumps({'version':3,'sources':['../source.js'],'sourcesContent':['/*'+'x'*3_000_000+'*/;fetch("/api/late");']})
        with patch.object(sourcemap,'request',AsyncMock(return_value=self.response(raw))) as call:
            result=await sourcemap.reconstruct(self.tid,'https://example.test/main.js.map')
        self.assertNotIn('error',result)
        self.assertEqual(result['files_recovered'],1)
        self.assertEqual(result['request_shapes'][0]['url'],'/api/late')
        self.assertEqual(call.call_args.args[0],'GET')
        self.assertNotIn('..',result['files'][0]['source_path'])

    async def test_missing_map_content_and_external_sections_are_partial(self):
        for data in [{'version':3,'sources':['missing.js']},{'version':3,'sections':[{'url':'https://outside.test/map'}]}]:
            with patch.object(sourcemap,'_fetch',AsyncMock(return_value=json.dumps(data))):
                result=await sourcemap.reconstruct(self.tid,'https://example.test/main.js.map')
            self.assertEqual(result['coverage']['file_status'],'partial')

    async def test_inline_indexed_source_map(self):
        data={'version':3,'sections':[{'offset':{'line':0,'column':0},'map':{'version':3,'sources':['a.js'],'sourcesContent':['fetch("/inline");']}}]}
        url='data:application/json;base64,'+base64.b64encode(json.dumps(data).encode()).decode()
        with patch.object(sourcemap,'request',AsyncMock()) as network:
            result=await sourcemap.reconstruct(self.tid,url)
            network.assert_not_awaited()
        self.assertEqual(result['request_shapes'][0]['url'],'/inline')
        self.assertEqual(result['coverage']['file_status'],'complete')

    async def test_paginated_results_and_cross_task_isolation(self):
        manifest=analysis_store.save(self.tid,'javascript','https://example.test/main.js',{'request_shapes':[{'n':i} for i in range(25)],'coverage':{'status':'complete'}})
        first=analysis_store.read(self.tid,manifest['analysis_id'],limit=10)
        self.assertEqual((len(first['items']),first['total'],first['next_offset']),(10,25,10))
        self.assertEqual(analysis_store.read(self.tid,manifest['analysis_id'],offset=20)['next_offset'],None)
        with self.assertRaises(FileNotFoundError):analysis_store.read(self.tid+1,manifest['analysis_id'])
        with self.assertRaises(ValueError):analysis_store.read(self.tid,'../escape')
        with self.assertRaises(ValueError):workspace.read_page(self.tid,'../../escape')

    async def test_large_result_item_is_explicit_and_file_pages_are_complete(self):
        manifest=analysis_store.save(self.tid,'javascript','https://example.test/large.js',{'request_shapes':[{'huge':'x'*50000}]})
        page=analysis_store.read(self.tid,manifest['analysis_id'])
        self.assertTrue(page['items'][0]['oversized_item'])
        path=manifest['artifact_path'];data=b'';offset=0
        while True:
            p=workspace.read_page(self.tid,path,offset,10000)
            data+=p['text'].encode()
            if p['next_offset'] is None:break
            offset=p['next_offset']
        self.assertEqual(len(json.loads(data)['request_shapes'][0]['huge']),50000)

    async def test_analysis_http_pages_and_scope_are_task_local(self):
        manifest=analysis_store.save(self.tid,'javascript','https://example.test/main.js',{'request_shapes':[{'url':'/a'}]})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://localhost') as client:
            self.assertEqual((await client.get(f'/api/tasks/{self.tid}/analyses')).status_code,200)
            response=await client.get(f'/api/tasks/{self.tid}/analyses/{manifest["analysis_id"]}')
            self.assertEqual(response.json()['items'][0]['url'],'/a')
            self.assertEqual((await client.get(f'/api/tasks/{self.tid+1}/analyses')).status_code,404)

    async def test_utf8_file_pages_preserve_text_across_boundaries(self):
        original='مرحبا بالعالم 🌍' * 15
        workspace.write_file(self.tid,'unicode.txt',original)
        offset=0;result=''
        while True:
            page=workspace.read_page(self.tid,'unicode.txt',offset,5)
            result+=page['text']
            if page['next_offset'] is None:break
            self.assertGreater(page['next_offset'],offset)
            offset=page['next_offset']
        self.assertEqual(result,original)
