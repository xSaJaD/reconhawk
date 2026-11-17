"""Task-local, paginated static-analysis artifacts; no external network access."""
import hashlib
import json
import re
from . import workspace

SECTIONS = {'request_shapes', 'files', 'endpoints', 'possible_secrets', 'warnings', 'auth_signals'}


def save(task_id, kind, url, result):
    analysis_id = hashlib.sha256((kind + ':' + url).encode()).hexdigest()[:32]
    result = {**result, 'analysis_id': analysis_id, 'kind': kind, 'url': url}
    path = f'analysis/{analysis_id}/result.json'
    workspace.write_file(task_id, path, json.dumps(result, ensure_ascii=False))
    manifest = {k: result[k] for k in ('analysis_id', 'kind', 'url', 'coverage', 'warnings') if k in result}
    manifest['artifact_path'] = path
    manifest['totals'] = {k: len(result.get(k, [])) for k in SECTIONS}
    manifest['warnings'] = result.get('warnings', [])[:10]
    manifest['warnings_preview_truncated'] = len(result.get('warnings', [])) > 10
    workspace.write_file(task_id, f'analysis/{analysis_id}/manifest.json', json.dumps(manifest, ensure_ascii=False))
    return manifest


def list_results(task_id):
    results = []
    for item in workspace.list_files(task_id):
        if re.fullmatch(r'analysis/[a-f0-9]{32}/manifest\.json', item['path']):
            with open(workspace._safe_path(task_id, item['path']), encoding='utf-8') as file:
                results.append(json.load(file))
    return results


def read(task_id, analysis_id, section='request_shapes', offset=0, limit=20):
    if not re.fullmatch('[a-f0-9]{32}', analysis_id) or section not in SECTIONS:
        raise ValueError('Invalid analysis ID or section')
    offset, limit = int(offset), int(limit)
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError('Invalid analysis page')
    with open(workspace._safe_path(task_id, f'analysis/{analysis_id}/result.json'), encoding='utf-8') as file:
        result = json.load(file)
    rows = result.get(section, [])
    items, consumed, size = [], 0, 0
    for row in rows[offset:offset + limit]:
        item_bytes = len(json.dumps(row, ensure_ascii=False).encode())
        if items and size + item_bytes > 48000:
            break
        if item_bytes > 48000:
            row = {'oversized_item': True, 'index': offset + consumed, 'bytes': item_bytes,
                   'artifact_path': f'analysis/{analysis_id}/result.json',
                   'note': 'Use read_file byte pages to inspect this full item in the saved result.'}
        items.append(row)
        consumed += 1
        size += item_bytes
    end = offset + consumed
    return {'analysis_id': analysis_id, 'section': section, 'items': items,
            'offset': offset, 'total': len(rows), 'next_offset': end if end < len(rows) else None,
            'coverage': result.get('coverage', {})}
