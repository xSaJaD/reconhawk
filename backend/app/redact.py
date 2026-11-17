import re
secret=re.compile(r'(?i)(authorization|cookie|set-cookie|api[-_]?key|access[-_]?token|refresh[-_]?token|password|secret)')
def headers(values):
    return {k:('[REDACTED]' if secret.search(k) and not str(v).startswith('[identity:') else v) for k,v in values.items()}
def text(value):
    return re.sub(r'(?i)(["\'](?:password|access_token|refresh_token|api_key|client_secret)["\']\s*:\s*["\'])[^"\']*',r'\1[REDACTED]',value or '')
def value(data):
    if isinstance(data,dict): return {k:'[REDACTED]' if secret.search(str(k)) else value(v) for k,v in data.items()}
    if isinstance(data,list): return [value(v) for v in data]
    return text(data) if isinstance(data,str) else data
