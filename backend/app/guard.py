"""Context-local scope and DNS-pinned HTTP transport for all built-in research tools."""
import asyncio
import ipaddress
import socket
from contextvars import ContextVar
from urllib.parse import urljoin
import httpx
from .scope import check_url, ScopeError

policy = ContextVar('research_scope', default=([], []))

def addresses(host):
    try:
        values = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise ScopeError('DNS resolution failed') from exc
    if not values:
        raise ScopeError('No DNS addresses')
    for value in values:
        ip = ipaddress.ip_address(value)
        mapped = getattr(ip, 'ipv4_mapped', None)
        if not ip.is_global or ip.is_multicast or (mapped and not mapped.is_global):
            raise ScopeError('Non-public destination blocked')
    return values

async def request(method, url, *, headers=None, content=None, timeout=25, max_bytes=8000000, follow_redirects=True):
    headers = httpx.Headers(headers or {})
    headers.pop('host', None)
    headers['Accept-Encoding'] = 'identity'
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        for _ in range(11):
            await asyncio.to_thread(check_url, url, *policy.get())
            original = httpx.URL(url)
            ips = await asyncio.to_thread(addresses, original.host)
            client.cookies.clear()
            wire_headers = httpx.Headers(headers)
            wire_headers['Host'] = original.netloc.decode()
            async with client.stream(method, original.copy_with(host=ips[0]), headers=wire_headers,
                                     content=content, extensions={'sni_hostname': original.host}) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError('Response size limit exceeded')
                    chunks.append(chunk)
                result_headers = httpx.Headers(response.headers)
                for name in ('content-encoding', 'content-length', 'transfer-encoding'):
                    result_headers.pop(name, None)
                result = httpx.Response(response.status_code, headers=result_headers, content=b''.join(chunks), request=httpx.Request(method, original))
            if not follow_redirects or result.status_code not in (301,302,303,307,308) or 'location' not in result.headers:
                return result
            target = httpx.URL(urljoin(str(original), result.headers['location']))
            if (target.scheme,target.host,target.port) != (original.scheme,original.host,original.port):
                headers = httpx.Headers({k:v for k,v in headers.items() if k in ('accept','accept-encoding','content-type','user-agent')})
            if result.status_code == 303 or (method.upper() == 'POST' and result.status_code in (301,302)):
                method,content='GET',None
                headers.pop('content-length',None)
            url=str(target)
    raise ScopeError('Redirect limit exceeded')
