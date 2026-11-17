from __future__ import annotations

import fnmatch
import ipaddress
import json
import socket
import posixpath
import re
from urllib.parse import urlparse, unquote, parse_qsl

from .config import settings


class ScopeError(ValueError):
    pass


def _host_of(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    return (urlparse(url).hostname or "").lower()


def _is_private(host: str) -> bool:
    """Block SSRF-style targets (internal IPs, cloud metadata, loopback)."""
    if host in settings.blocklist():
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not addr.is_global or addr.is_multicast:
            return True
    return False


def host_in_patterns(host: str, patterns: list[str]) -> bool:
    for p in patterns:
        p = p.strip().lower()
        if not p:
            continue
        # normalize URL-ish patterns down to a host/glob
        p_host = _host_of(p)
        if fnmatch.fnmatch(host, p_host) or host == p_host:
            return True
        # wildcard domain form "*.example.com" also matches the apex
        if p_host.startswith("*.") and host == p_host[2:]:
            return True
    return False


def url_in_patterns(url: str, patterns: list[str]) -> bool:
    """A URL rule keeps its scheme, port, path and query restrictions.

    Exact paths match exactly; trailing '/' rules include descendants; explicit
    '*' path globs are supported. Plain host rules retain host-only semantics.
    """
    target = urlparse(url if '://' in url else 'https://' + url)
    for pattern in patterns:
        raw = pattern.strip()
        if not raw:
            continue
        rule = urlparse(raw if '://' in raw else 'https://' + raw)
        if not host_in_patterns((target.hostname or '').lower(), [raw]):
            continue
        try:
            target_port = target.port or (443 if target.scheme == 'https' else 80)
            if '://' in raw:
                # An http:// scope entry also permits https:// (a safe upgrade; bug-bounty
                # scopes routinely list http:// loosely to mean the host). https stays strict.
                scheme_ok = rule.scheme == target.scheme or (rule.scheme == 'http' and target.scheme == 'https')
                if not scheme_ok:
                    continue
                # Only enforce an explicit, non-default port from the rule.
                if rule.port is not None and rule.port != target_port:
                    continue
            if '://' not in raw and rule.port is not None and rule.port != target_port:
                continue
        except ValueError:
            raise ScopeError('Invalid scope URL port')
        if rule.path and rule.path != '/':
            # Reject ambiguous separators/dot encoding when enforcing path rules.
            if re.search(r'%2f|%5c|%2e|%25|%00', target.path, re.IGNORECASE) or any(c in target.path for c in ('\\', ';', '\x00')):
                continue
            requested = posixpath.normpath(unquote(target.path or '/'))
            expected = posixpath.normpath(unquote(rule.path))
            if '*' in rule.path:
                if not fnmatch.fnmatchcase(unquote(target.path), unquote(rule.path)):
                    continue
            elif rule.path.endswith('/'):
                if requested != expected and not requested.startswith(expected.rstrip('/') + '/'):
                    continue
            elif requested != expected:
                continue
        if rule.query and sorted(parse_qsl(rule.query, keep_blank_values=True)) != sorted(parse_qsl(target.query, keep_blank_values=True)):
            continue
        return True
    return False


def check_url(url: str, scope_in: list[str], scope_out: list[str]) -> None:
    """Raise ScopeError if the URL must not be touched."""
    parsed = urlparse(url if '://' in url else 'https://' + url)
    if parsed.scheme not in ('http','https') or parsed.username or parsed.password:
        raise ScopeError('Only HTTP(S) URLs without embedded credentials are allowed')
    try:
        parsed.port
    except ValueError as exc:
        raise ScopeError('Invalid URL port') from exc
    host = _host_of(url)
    if not host:
        raise ScopeError(f"Could not parse host from URL: {url}")

    if _is_private(host):
        raise ScopeError(f"Blocked internal/unsafe host: {host}")

    if not settings.scope_enforced:
        return

    path_rules = [p for p in scope_in + scope_out if host_in_patterns(host, [p]) and
                  urlparse(p if '://' in p else 'https://' + p).path not in ('', '/')]
    if path_rules and (re.search(r'%2f|%5c|%2e|%25|%00', parsed.path, re.IGNORECASE) or
                       any(c in parsed.path for c in ('\\', ';', '\x00'))):
        raise ScopeError('Ambiguous URL encoding cannot be used with path-restricted scope')

    if scope_out and url_in_patterns(url, scope_out):
        raise ScopeError(f"Host {host} is explicitly OUT of scope")

    if scope_in:
        if not url_in_patterns(url, scope_in):
            raise ScopeError(
                f"Host {host} is not in the program scope: {scope_in}"
            )
    # If no in-scope list is configured, we only enforce the blocklist/out list.


def load_scope(program) -> tuple[list[str], list[str]]:
    if program is None:
        return [], []
    try:
        si = json.loads(program.scope_in or "[]")
        so = json.loads(program.scope_out or "[]")
    except json.JSONDecodeError:
        raise ScopeError('Malformed scope')
    if not isinstance(si,list) or not isinstance(so,list) or not si:
        raise ScopeError('Program requires a non-empty scope allowlist')
    return si, so
