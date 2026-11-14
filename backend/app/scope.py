from __future__ import annotations

import fnmatch
import ipaddress
import json
import socket
from urllib.parse import urlparse

from .config import settings


class ScopeError(Exception):
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
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return True
    return False


def host_in_patterns(host: str, patterns: list[str]) -> bool:
    for p in patterns:
        p = p.strip().lower()
        if not p:
            continue
        # normalize URL-ish patterns down to a host/glob
        p_host = _host_of(p) if "://" in p else p
        if fnmatch.fnmatch(host, p_host) or host == p_host:
            return True
        # wildcard domain form "*.example.com" also matches the apex
        if p_host.startswith("*.") and host == p_host[2:]:
            return True
    return False


def check_url(url: str, scope_in: list[str], scope_out: list[str]) -> None:
    """Raise ScopeError if the URL must not be touched."""
    host = _host_of(url)
    if not host:
        raise ScopeError(f"Could not parse host from URL: {url}")

    if _is_private(host):
        raise ScopeError(f"Blocked internal/unsafe host: {host}")

    if not settings.scope_enforced:
        return

    if scope_out and host_in_patterns(host, scope_out):
        raise ScopeError(f"Host {host} is explicitly OUT of scope")

    if scope_in:
        if not host_in_patterns(host, scope_in):
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
        si, so = [], []
    return si, so
