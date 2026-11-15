"""Bounded complete source maps, including inline and embedded indexed maps."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote_to_bytes
from ..guard import request
from .. import jsast
from ..shapes import store, unresolved
from . import analysis_store, workspace

MAX_MAP_BYTES = 24_000_000
MAX_SOURCES = 2000


async def _fetch(url, timeout=40.0, max_chars=None):
    if url.startswith('data:'):
        if len(url) > MAX_MAP_BYTES * 4 // 3 + 200:
            raise ValueError('Inline source map exceeds size bound')
        meta, encoded = url.split(',', 1)
        if not meta.startswith(('data:application/json', 'data:application/octet-stream')):
            raise ValueError('Unsupported inline source map type')
        body = base64.b64decode(encoded, validate=True) if ';base64' in meta else unquote_to_bytes(encoded)
        if len(body) > MAX_MAP_BYTES:
            raise ValueError('Inline source map exceeds size bound')
        text = body.decode('utf-8-sig')
    else:
        response = await request('GET', url, timeout=timeout, max_bytes=MAX_MAP_BYTES)
        response.raise_for_status()
        text = response.content.decode('utf-8-sig')
    if max_chars is not None and len(text) > max_chars:
        raise ValueError('Source map exceeds requested bound; JSON was not truncated')
    return text


async def find_map_url(js_url, js_text=None):
    if js_text is None:
        from .jsanalyze import fetch_js
        js_text = await fetch_js(js_url)
    match = re.search(r'(?://[#@]|/\*[#@])\s*sourceMappingURL=([^\s*]+)', js_text)
    if match:
        reference = match.group(1).strip()
        return reference if reference.startswith('data:') else urljoin(js_url, reference)
    parsed = urlsplit(js_url)
    return urlunsplit(parsed._replace(path=parsed.path + '.map', fragment=''))


async def reconstruct(task_id, map_url, should_stop=None):
    files, shapes, endpoints, auth_signals, warnings = [], [], set(), set(), []
    try:
        if should_stop and should_stop():
            raise ValueError('Analysis stopped before source-map request')
        raw = await _fetch(map_url)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('version') != 3:
            raise ValueError('Expected a version-3 source map object')
        key = hashlib.sha256((map_url + hashlib.sha256(raw.encode()).hexdigest()).encode()).hexdigest()[:20]
        workspace.write_file(task_id, f'analysis/maps/{key}.json', raw)
        queue, source_count, depth = [(data, 0)], 0, 0
        while queue:
            current, depth = queue.pop(0)
            if depth > 16:
                warnings.append('Indexed source map nesting limit reached')
                continue
            if not isinstance(current, dict):
                raise ValueError('Malformed indexed source map')
            if 'sections' in current:
                if not isinstance(current['sections'], list):
                    raise ValueError('Malformed source map sections')
                for section in current['sections']:
                    if not isinstance(section, dict):
                        raise ValueError('Malformed source map section')
                    if 'map' in section:
                        queue.append((section['map'], depth + 1))
                    else:
                        warnings.append('External indexed-map section is deferred; no unreviewed URL was fetched')
                continue
            sources, contents = current.get('sources', []), current.get('sourcesContent', [])
            if not isinstance(sources, list) or not isinstance(contents, list):
                raise ValueError('Malformed sources or sourcesContent')
            source_count += len(sources)
            for i, source in enumerate(sources):
                if should_stop and should_stop():
                    warnings.append('Analysis stopped; remaining embedded sources are deferred')
                    break
                if len(files) >= MAX_SOURCES:
                    warnings.append('Source map file-count budget reached; remaining sources were not analyzed')
                    break
                record = {'source': str(source), 'status': 'missing_content'}
                files.append(record)
                if i >= len(contents) or not isinstance(contents[i], str):
                    warnings.append(f'Missing embedded source content: {str(source)[:160]}')
                    continue
                body = contents[i]
                name = re.sub(r'[^A-Za-z0-9_.-]', '_', str(source).split('/')[-1])[-80:] or 'source.js'
                path = f'srcmap/{key}/{len(files):05d}-{name}'
                workspace.write_file(task_id, path, body)
                result = await asyncio.to_thread(jsast.analyze, body)
                from .jsanalyze import analyze as literals
                endpoints.update(literals(body)['endpoints'])
                source_url = (map_url if not map_url.startswith('data:') else 'inline-source-map') + '#' + str(source)
                for shape in result['request_shapes']:
                    shape.update(source=source_url, source_path=path, review_required=True)
                await store(task_id, result['request_shapes'], source_url)
                shapes.extend(result['request_shapes'])
                auth_signals.update(m.group(0).lower() for m in re.finditer(r'(?i)authorization|bearer|access[_-]?token|cookie', body))
                record.update(status=result['parse_status'], source_path=path, bytes=len(body.encode()),
                              analyzed_bytes=len(body.encode()) if result['parse_status'] == 'parsed' else 0,
                              warnings=result['warnings'])
                warnings.extend(result['warnings'])
        recovered = sum('source_path' in x for x in files)
        complete = source_count == len(files) and all(x['status'] == 'parsed' for x in files) and not warnings
        if not files:
            warnings.append('No embedded sources were available for analysis')
            complete = False
        return {'map_url': '(inline)' if map_url.startswith('data:') else map_url,
                'files_recovered': recovered, 'total_sources': source_count, 'files': files,
                'request_shapes': shapes, 'endpoints': sorted(endpoints), 'auth_signals': sorted(auth_signals),
                'warnings': list(dict.fromkeys(warnings)), 'workspace_dir': f'srcmap/{key}/',
                'coverage': {'file_status': 'complete' if complete else 'partial',
                             'files_total': source_count, 'files_analyzed': sum(x['status'] == 'parsed' for x in files),
                             'files_recovered': recovered,
                             'bytes_analyzed': sum(x.get('analyzed_bytes', 0) for x in files),
                             'contracts_total': len(shapes),
                             'contracts_symbolic': sum(any(unresolved(s.get(k)) for k in ('url', 'method', 'body', 'headers', 'params')) for s in shapes),
                             'status': 'complete' if complete and not any(unresolved(s.get(k)) for s in shapes for k in ('url', 'method', 'body', 'headers', 'params')) else 'partial'}}
    except Exception as exc:
        return {'error': str(exc)[:600], 'map_url': '(inline)' if map_url.startswith('data:') else map_url,
                'files': files, 'request_shapes': shapes, 'endpoints': sorted(endpoints),
                'warnings': [*warnings, str(exc)[:600]], 'coverage': {'file_status': 'partial', 'status': 'partial'}}


async def analyze(task_id, js_url, should_stop=None):
    try:
        map_url = await find_map_url(js_url)
        result = await reconstruct(task_id, map_url, should_stop=should_stop)
    except Exception as exc:
        result = {'files': [], 'request_shapes': [], 'endpoints': [], 'warnings': [str(exc)[:600]],
                  'coverage': {'file_status': 'partial', 'status': 'partial'}}
    return analysis_store.save(task_id, 'source_map', js_url, result)
