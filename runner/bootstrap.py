"""Fixed entrypoint. Return the bounded JUnit report before tmpfs is unmounted."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
from threading import Thread
import base64
import os
import stat
import subprocess

server=ThreadingHTTPServer(('127.0.0.1',8765),partial(SimpleHTTPRequestHandler,directory='/workspace/site'))
Thread(target=server.serve_forever,daemon=True).start()
result=subprocess.run(['python','-m','pytest','/workspace/test_exercise.py','-q','-p','no:cacheprovider','--junitxml=/tmp/report.xml','--basetemp=/tmp/pytest'])
server.shutdown()
report=b''
try:
    fd=os.open('/tmp/report.xml',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        if stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raw=f.read(16385)
            if len(raw)<=16384:report=raw
except OSError:pass
print('\nLEARNLOOP_REPORT_V1:'+base64.b64encode(report).decode(),flush=True)
raise SystemExit(result.returncode if result.returncode >= 0 else 128-result.returncode)
