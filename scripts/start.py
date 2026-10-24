"""Start the local API and durable worker together. Ctrl-C stops both cleanly."""
import os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    os.chdir(ROOT)
    port=os.environ.get('LEARNLOOP_PORT','8000')
    if not (ROOT/'dist/index.html').exists():
        raise SystemExit('Run npm ci && npm run build before starting.')
    children=[]
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        children.append(subprocess.Popen([sys.executable,'-m','server.worker'],start_new_session=True))
        children.append(subprocess.Popen([sys.executable,'-m','uvicorn','server.app:app','--host','127.0.0.1','--port',port],start_new_session=True))
        print(f'Learnloop: http://127.0.0.1:{port}/',flush=True)
        while all(p.poll() is None for p in children):time.sleep(.5)
        if any(p.poll() not in (None,0) for p in children):print('A service exited. Check the preceding error.',file=sys.stderr)
    except KeyboardInterrupt:pass
    finally:
        for p in children:
            if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
        for p in children:
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()

if __name__=='__main__':main()
