"""Explicit proxy selection avoids ambiguous IPv6 NO_PROXY parsing in HTTPX."""
import fnmatch
import os
from urllib.parse import urlparse
import httpx

def client(endpoint,timeout):
    u=urlparse(endpoint)
    host=u.hostname or ''
    no_proxy=os.environ.get('NO_PROXY',os.environ.get('no_proxy',''))
    bypass=host in ('127.0.0.1','localhost','::1')
    for pattern in no_proxy.split(','):
        pattern=pattern.strip().strip('[]')
        if pattern and (pattern=='*' or host==pattern or host.endswith('.'+pattern.lstrip('.')) or fnmatch.fnmatch(host,pattern)):
            bypass=True
    proxy=None if bypass else os.environ.get(u.scheme.upper()+'_PROXY',os.environ.get(u.scheme+'_proxy',os.environ.get('ALL_PROXY',os.environ.get('all_proxy'))))
    return httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False,proxy=proxy)
