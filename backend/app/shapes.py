"""Static contracts: retain symbolic expressions and source evidence for review."""
import json
from urllib.parse import urljoin
from sqlalchemy import select
from .db import SessionLocal, EndpointSpec, Task
from .jsast import analyze


def extract(source):
    return analyze(source)['request_shapes']


def unresolved(value):
    if isinstance(value, dict):
        return any(str(k).startswith('$') for k in value) or any(unresolved(v) for v in value.values())
    return isinstance(value, list) and any(unresolved(v) for v in value)


async def store(tid, shapes, url):
    async with SessionLocal() as session:
        task = await session.get(Task, tid)
        document_url = task.target if task else url
        rows = (await session.execute(select(EndpointSpec).where(
            EndpointSpec.task_id == tid, EndpointSpec.source == url))).scalars().all()
        existing = {(r.url, r.method): r for r in rows}
        for shape in shapes:
            if not isinstance(shape['url'], str) or not isinstance(shape['method'], str):
                continue
            # Relative fetch URLs resolve against the document, not the JS/CDN file.
            target, method = urljoin(document_url, shape['url']), shape['method'].upper()
            if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'):
                continue
            key = (target, method)
            row = existing.get(key)
            if row is None:
                row = EndpointSpec(task_id=tid, url=target, method=method, source=url)
                session.add(row)
                existing[key] = row
                value = {**shape, 'variants': []}
            else:
                value = json.loads(row.shape or '{}')
            variants = value.setdefault('variants', [])
            if shape not in variants:
                variants.append(shape)
            value['review_required'] = bool(value.get('review_required') or shape.get('review_required') or len(variants) > 1)
            row.shape = json.dumps(value)
        await session.commit()


async def capture(tid, source, url):
    shapes = extract(source)
    await store(tid, shapes, url)
    return shapes
