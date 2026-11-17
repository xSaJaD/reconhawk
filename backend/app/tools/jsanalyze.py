"""Complete bounded JS downloads, chunk discovery, contracts and coverage manifests."""
from __future__ import annotations
import asyncio
import hashlib
import re
from urllib.parse import urljoin, urlsplit
from ..guard import request
from .. import jsast
from ..shapes import store, unresolved
from . import analysis_store, workspace

MAX_JS_BYTES = 16_000_000
MAX_TOTAL_BYTES = 48_000_000
MAX_FILES = 12
ENDPOINT_RE = re.compile(r'''["'`](/[a-zA-Z0-9_\-./?=&%]{1,250}|https?://[^\s"'`<>]{1,400})["'`]''')
SECRET_RE = re.compile(r'''(?i)(api[_-]?key|secret|token|authorization|bearer|aws_access|client_secret)["'`\s:=,]{1,6}["'`]?([A-Za-z0-9_\-.]{12,})''')
KNOWN_KEYS = {
    'google_browser_key': re.compile(r'AIza[0-9A-Za-z_-]{35}'),
    'aws_access_key_id': re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}'),
    'private_key_header': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github_token': re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),
    'jwt_literal': re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'),
}
PLACEHOLDERS = {'access_token', 'refresh_token', 'authorization', 'client_secret', 'user_access_token',
                'your_api_key', 'undefined', 'undefined:undefined', 'not-needed'}


async def fetch_js(url, timeout=30.0, max_chars=None, max_bytes=MAX_JS_BYTES):
    # Legacy character bounds reject oversized text; never return a silent prefix.
    if not 1 <= max_bytes <= MAX_JS_BYTES:
        raise ValueError('JavaScript byte budget exhausted')
    response = await request('GET', url, timeout=timeout, max_bytes=max_bytes)
    response.raise_for_status()
    if max_chars is not None and len(response.text) > max_chars:
        raise ValueError('JavaScript exceeds requested character bound; no partial analysis was returned')
    return response.text


def analyze(source):
    endpoints = sorted({m.group(1) for m in ENDPOINT_RE.finditer(source)
                        if m.group(1) not in ('/', '//') and not m.group(1).endswith(('.png', '.jpg', '.svg', '.css', '.woff', '.gif'))})
    candidates, seen = [], set()
    for label, pattern in KNOWN_KEYS.items():
        for match in pattern.finditer(source):
            value = match.group(0)
            seen.add(value)
            candidates.append({'label': label, 'value_preview': value[:6] + '…' + value[-4:],
                               'offset': match.start(), 'classification': 'public_client_identifier' if label == 'google_browser_key' else 'unverified_candidate',
                               'verified': False})
    for match in SECRET_RE.finditer(source):
        value = match.group(2)
        if value in seen or value.lower() in PLACEHOLDERS or value.upper() == value and '_' in value:
            continue
        if not any(c.isdigit() for c in value) or len(set(value)) < 8:
            continue
        seen.add(value)
        candidates.append({'label': match.group(1), 'value_preview': value[:4] + '…' + value[-4:],
                           'offset': match.start(), 'classification': 'unverified_candidate', 'verified': False})
    return {'endpoints': endpoints, 'interesting_endpoints': [x for x in endpoints if any(
        k in x.lower() for k in ('api', 'v1', 'v2', 'graphql', 'admin', 'user', 'auth', 'upload', 'token', 'internal'))],
        'possible_secrets': candidates, 'endpoint_count': len(endpoints)}


def discover_chunks(source, url):
    """Explicit JS references plus the common webpack numeric hash-map runtime.

    Derived URLs still pass the scoped transport. Arbitrary code is never executed.
    """
    found = {urljoin(url, m.group(1)) for m in re.finditer(
        r'''["'`]((?:https?://|/|\./|\.\./)[^\s"'`<>]{1,350}\.js(?:\?[^\s"'`<>]*)?)["'`]''', source)
        if '.chunk.js' in m.group(1) or '/static/js/' in m.group(1)}
    found.update(urljoin(url, m.group(1)) for m in re.finditer(
        r'''\bimport\s*(?:\(\s*|[^;]{0,100}\bfrom\s*)["']([^"']+\.js(?:\?[^"']*)?)["']''', source))
    runtime = source[:40_000]
    prefix = re.search(r'''["']([^"']*(?:static/js|assets)/)["']''', runtime)
    public = re.search(r'''\.p\s*=\s*["']([^"']*)["']''', runtime)
    if prefix:
        root = urljoin(url, public.group(1) if public else '/')
        for table in re.finditer(r'''\{((?:(?:\d+|["']\d+["'])\s*:\s*["'][a-f0-9]{6,64}["']\s*,?\s*)+)\}\[\w+\]\s*\+\s*["']\.chunk\.js''', runtime):
            for key, value in re.findall(r'''["']?(\d+)["']?\s*:\s*["']([a-f0-9]{6,64})["']''', table.group(1)):
                found.add(urljoin(root, prefix.group(1) + key + '.' + value + '.chunk.js'))
    found.discard(url)
    return sorted(x for x in found if urlsplit(x).scheme in ('http', 'https'))


async def analyze_bundle(task_id, url, include_chunks=True, should_stop=None):
    pending, visited, files, shapes, endpoints, candidates, warnings = [url], set(), [], [], set(), [], []
    total_bytes = 0
    while pending and len(visited) < MAX_FILES:
        if should_stop and should_stop():
            warnings.append('Analysis stopped by the operator; remaining files were not requested.')
            break
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        item = {'url': current, 'status': 'pending'}
        files.append(item)
        try:
            source = await fetch_js(current, max_bytes=min(MAX_JS_BYTES, MAX_TOTAL_BYTES - total_bytes))
            size = len(source.encode())
            total_bytes += size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError('Total JavaScript analysis byte budget exceeded')
            digest = hashlib.sha256(current.encode()).hexdigest()[:10] + '-' + hashlib.sha256(source.encode()).hexdigest()[:20]
            path = f'analysis/sources/{digest}.js'
            workspace.write_file(task_id, path, source)
            basic = analyze(source)
            ast_result = await asyncio.to_thread(jsast.analyze, source)
            for shape in ast_result['request_shapes']:
                shape['source'], shape['source_path'] = current, path
            await store(task_id, ast_result['request_shapes'], current)
            shapes.extend(ast_result['request_shapes'])
            endpoints.update(basic['endpoints'])
            candidates.extend({**x, 'source': current} for x in basic['possible_secrets'])
            item.update(status=ast_result['parse_status'], bytes=size, bytes_read=size,
                        analyzed_bytes=size if ast_result['parse_status'] == 'parsed' else 0,
                        nodes_visited=ast_result['nodes_visited'],
                        sha256=hashlib.sha256(source.encode()).hexdigest(), source_path=path,
                        shapes=len(ast_result['request_shapes']), warnings=ast_result['warnings'])
            warnings.extend(f'{current}: {w}' for w in ast_result['warnings'])
            if include_chunks:
                pending.extend(x for x in discover_chunks(source, current) if x not in visited and x not in pending)
        except Exception as exc:
            item.update(status='blocked' if 'scope' in type(exc).__name__.lower() else 'error', error=str(exc)[:600])
            warnings.append(f'{current}: {item["error"]}')
    for current in dict.fromkeys(pending):
        if current not in visited:
            files.append({'url': current, 'status': 'deferred', 'error': 'Analysis stopped or file-count budget reached'})
    if pending:
        warnings.append('Some discovered files were deferred by a stop request or the file-count budget.')
    failed = sum(f['status'] != 'parsed' for f in files)
    symbolic = sum(any(unresolved(s.get(k)) for k in ('url', 'method', 'headers', 'body', 'params')) for s in shapes)
    coverage = {'file_status': 'partial' if failed else 'complete', 'files_total': len(files),
                'files_analyzed': len(files) - failed, 'bytes_analyzed': sum(f.get('analyzed_bytes', 0) for f in files),
                'contracts_total': len(shapes), 'contracts_symbolic': symbolic,
                'resolution_status': 'partial' if symbolic else 'resolved',
                'status': 'partial' if failed or symbolic else 'complete',
                'limits': {'file_bytes': MAX_JS_BYTES, 'total_bytes': MAX_TOTAL_BYTES, 'files': MAX_FILES},
                'note': 'Complete file parsing is not proof of complete application coverage or a vulnerability.'}
    return analysis_store.save(task_id, 'javascript', url, {'files': files, 'request_shapes': shapes,
        'endpoints': sorted(endpoints), 'possible_secrets': candidates, 'warnings': warnings, 'coverage': coverage})


async def fetch_and_analyze(url):
    source = await fetch_js(url)
    return {**analyze(source), 'url': url, 'bytes': len(source.encode()), 'truncated': False}
