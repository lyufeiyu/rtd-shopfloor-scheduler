"""Isolated API smoke test, using a temporary store and a real WF schedule."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory(prefix='rtd-progress-') as folder:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with open(Path(folder)/'server.log','wb') as log:
            process=subprocess.Popen([sys.executable,'-B',str(ROOT/'backend/server.py'),'--port',str(port),'--storage',folder],stdout=log,stderr=log)
            def request(path,data=None,headers=None):
                req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,headers=headers or {})
                try:
                    with urllib.request.urlopen(req,timeout=60) as response: return json.load(response)
                except urllib.error.HTTPError as exc:
                    print(exc.read().decode('utf-8'),flush=True)
                    for worker in Path(folder).rglob('worker.log'): print(worker.read_text(encoding='utf-8',errors='replace'),flush=True)
                    raise
            try:
                for _ in range(50):
                    try: request('/api/health');break
                    except OSError: time.sleep(.2)
                dataset=request('/api/datasets',(ROOT/'data/RTD_Dataset_v6.xlsx').read_bytes(),{'X-Upload-Name':'test.xlsx','Content-Type':'application/octet-stream'})
                job=request('/api/jobs',json.dumps(dict(dataset_id=dataset['id'],station='WF',strategy='quick')).encode(),{'Content-Type':'application/json'})
                observed=[]
                deadline=time.monotonic()+90
                while time.monotonic()<deadline:
                    state=request('/api/jobs/'+job['id'])
                    p=state['progress']
                    if not observed or observed[-1]!=p['percent']:
                        observed.append(p['percent']);print(json.dumps(p,ensure_ascii=False),flush=True)
                    if state['status'] in ('succeeded','failed'): break
                    time.sleep(.1)
                assert state['status']=='succeeded',state
                assert observed==sorted(observed) and observed[-1]==100,observed
                assert any(0<p<100 for p in observed),observed
                result=request('/api/jobs/'+job['id']+'/result')
                assert result['summary']['violations']==0
                assert result['summary']['scheduled']+result['summary']['unscheduled']>0
                print(json.dumps(result['summary'],ensure_ascii=False),flush=True)
            finally:
                process.terminate();process.wait(timeout=10)

if __name__=='__main__': main()
