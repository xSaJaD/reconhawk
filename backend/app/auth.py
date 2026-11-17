import hmac
import ipaddress
from urllib.parse import urlparse
from .config import settings

def origin_ok(connection):
    origin=connection.headers.get('origin')
    return not origin or origin in settings.cors_origins.split(',')

def allowed(connection):
    if not origin_ok(connection):
        return False
    if settings.api_token:
        token=connection.headers.get('authorization','').removeprefix('Bearer ') or connection.cookies.get('reconhawk_session','')
        return hmac.compare_digest(token,settings.api_token)
    try:
        return ipaddress.ip_address(connection.client.host).is_loopback and urlparse('http://'+connection.headers.get('host','')).hostname in ('localhost','127.0.0.1','::1')
    except (ValueError,AttributeError):
        return False
