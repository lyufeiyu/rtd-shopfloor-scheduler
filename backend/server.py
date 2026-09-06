"""RTD 本地产品服务。仅开放显式静态文件和业务 API，算法与日志不提供 HTTP 访问。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from urllib.parse import unquote, urlsplit, quote
import zipfile

ROOT=Path(__file__).resolve().parents[1]
STORAGE=ROOT/'storage'
MAX_UPLOAD=20*1024*1024
STATIC={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/format.css':'format.css'}
POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rtd-job')
STATIC.update({'/workspace.css':'workspace.css','/assistant.js':'assistant.js'})
UPLOAD_LOCK=threading.BoundedSemaphore(1)
JOB_LOCK=threading.Lock()

def algorithm_python():
    """后台子进程使用含排产绘图库的解释器，服务进程仍使用自身 SQLite 正常的解释器。"""
    configured=os.environ.get('RTD_ALGORITHM_PYTHON')
    candidates=[configured] if configured else []
    candidates += [sys.executable, '/Users/feiyulv/miniconda3/bin/python']
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return sys.executable

def now(): return datetime.now(timezone.utc).isoformat()
def public_job(row):
    row=dict(row)
    status=row['status']
    progress=dict(percent=0,stage='queued',message='任务已进入队列，等待计算资源')
    if status in ('running','failed'):
        progress=dict(percent=2,stage='loading',message='正在准备排产环境')
        try: progress=read_json(STORAGE/'runs'/row['id']/'progress.json')
        except (OSError,ValueError): pass
    if status=='succeeded': progress=dict(percent=100,stage='done',message='方案与下载文件已就绪')
    if status=='failed': progress['message']=row.get('error') or '排产未完成，请重新提交'
    row['progress']=progress
    return row
def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def db():
    connection=sqlite3.connect(STORAGE/'state.sqlite',timeout=20)
    connection.row_factory=sqlite3.Row
    return connection
def query(sql,params=(),one=False):
    with db() as conn:
        rows=conn.execute(sql,params).fetchall()
    return (dict(rows[0]) if rows else None) if one else [dict(r) for r in rows]
def write(sql,params=()):
    with db() as conn: conn.execute(sql,params)
def public_dataset(row):
    meta=read_json(STORAGE/'datasets'/row['id']/'metadata.json')
    return {**meta,**row}
def child(action,dataset,out,extra,timeout):
    out.mkdir(parents=True,exist_ok=True)
    with (out/'worker.log').open('ab') as log:
        return subprocess.run([algorithm_python(),'-B',str(ROOT/'algorithm'/'product_runner.py'),action,
            str(dataset),str(out),*extra],cwd=str(ROOT/'algorithm'),stdout=log,stderr=subprocess.STDOUT,
            timeout=timeout,check=False).returncode

def execute_job(job_id):
    row=query('SELECT * FROM jobs WHERE id=?',(job_id,),one=True)
    out=STORAGE/'runs'/job_id
    write("UPDATE jobs SET status='running', started_at=? WHERE id=?",(now(),job_id))
    logging.info('job_started id=%s dataset=%s strategy=%s station=%s',job_id,row['dataset_id'],row['strategy'],row['station'])
    try:
        code=child('run',STORAGE/'datasets'/row['dataset_id']/'input.xlsx',out,
            ['--strategy',row['strategy'],'--station',row['station']],180)
        if code: raise RuntimeError(f'worker exit {code}')
        read_json(out/'result.json')
        write("UPDATE jobs SET status='succeeded', finished_at=? WHERE id=?",(now(),job_id))
        logging.info('job_succeeded id=%s',job_id)
    except Exception:
        logging.exception('job_failed id=%s',job_id)
        write("UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?",(now(),'本次排产未完成，请重试或联系管理员并提供任务编号。',job_id))

def check_workbook(path):
    try:
        with zipfile.ZipFile(path) as archive:
            files=archive.infolist()
            if len(files)>3000 or sum(f.file_size for f in files)>100*1024*1024:
                raise ValueError('工作簿解压后过大，请拆分数据。')
            if '[Content_Types].xml' not in archive.namelist() or 'xl/workbook.xml' not in archive.namelist():
                raise ValueError('请选择有效的 .xlsx 工作簿。')
            if any('vbaProject' in f.filename for f in files): raise ValueError('不支持包含宏的工作簿。')
    except zipfile.BadZipFile as exc: raise ValueError('文件不是有效的 Excel 工作簿。') from exc

class Handler(BaseHTTPRequestHandler):
    server_version='RTD'
    def log_message(self,fmt,*args): logging.info('http %s',fmt%args)
    def send_bytes(self,data,content_type='application/json; charset=utf-8',status=200,download=None):
        self.send_response(status);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        if download: self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(download,safe=''))
        self.end_headers();self.wfile.write(data)
    def json(self,payload,status=200): self.send_bytes(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode(),status=status)
    def error(self,message,status=400): self.json(dict(error=dict(message=message)),status)
    def do_GET(self):
        try: self.get()
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            logging.exception('get_failed');self.error('服务暂时无法读取数据，请稍后重试。',500)
    def get(self):
        path=urlsplit(self.path).path
        if path in STATIC:
            file=ROOT/'frontend'/STATIC[path]
            return self.send_bytes(file.read_bytes(),mimetypes.guess_type(file)[0] or 'text/plain')
        if path=='/api/health': return self.json(dict(status='ok'))
        if path=='/api/datasets': return self.json(dict(items=[public_dataset(r) for r in query('SELECT * FROM datasets ORDER BY created_at DESC')]))
        if path=='/api/jobs': return self.json(dict(items=[public_job(r) for r in query('SELECT * FROM jobs ORDER BY created_at DESC')]))
        match=re.fullmatch(r'/api/datasets/([a-f0-9]{32})/dashboard',path)
        if match:
            row=query('SELECT * FROM datasets WHERE id=?',(match[1],),one=True)
            if not row: return self.error('数据集不存在。',404)
            return self.json(read_json(STORAGE/'datasets'/row['id']/'dashboard.json'))
        match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/(result|files/([a-z0-9.-]+)))?',path)
        if match:
            row=query('SELECT * FROM jobs WHERE id=?',(match[1],),one=True)
            if not row: return self.error('任务不存在。',404)
            if not match[2]: return self.json(public_job(row))
            if row['status']!='succeeded': return self.error('排产结果尚未就绪。',409)
            out=STORAGE/'runs'/row['id'];result=read_json(out/'result.json')
            if match[2]=='result': return self.json(result)
            files={f['id']:f['name'] for f in result['files']};files['results.zip']='排产结果包.zip'
            name=match[3]
            if name not in files: return self.error('文件不存在。',404)
            file=out/name
            return self.send_bytes(file.read_bytes(),mimetypes.guess_type(file)[0] or 'application/octet-stream',download=files[name])
        self.error('页面或资源不存在。',404)
    def do_POST(self):
        # 同源请求；不允许任意网页向本地服务提交任务。
        host=self.headers.get('Host','');origin=self.headers.get('Origin')
        if host not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'} or (origin and origin not in {'http://'+host}):
            return self.error('不允许跨站提交请求。',403)
        try:
            path=urlsplit(self.path).path
            if path=='/api/datasets': return self.upload()
            if path=='/api/jobs': return self.create_job()
            return self.error('接口不存在。',404)
        except (ValueError,json.JSONDecodeError) as exc: self.error(str(exc))
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            logging.exception('post_failed');self.error('请求未完成，请稍后重试。',500)
    def body(self,limit):
        length=int(self.headers.get('Content-Length','0'))
        if length<=0 or length>limit: raise ValueError(f'请求内容为空或超过 {limit//1024//1024 or 1} MB 限制。')
        self.connection.settimeout(30)
        data=self.rfile.read(length)
        if len(data)!=length: raise ValueError('上传中断，请重新选择文件。')
        return data
    def upload(self):
        if not UPLOAD_LOCK.acquire(blocking=False): return self.error('正在校验另一个数据集，请稍后重试。',429)
        try:
            filename=Path(unquote(self.headers.get('X-Upload-Name',''))).name
            if not filename.lower().endswith('.xlsx') or len(filename)>180: raise ValueError('仅支持 .xlsx 文件。')
            data=self.body(MAX_UPLOAD);dataset_id=uuid.uuid4().hex
            directory=STORAGE/'datasets'/dataset_id;directory.mkdir(parents=True)
            file=directory/'input.xlsx';file.write_bytes(data)
            check_workbook(file)
            logging.info('dataset_upload id=%s bytes=%s',dataset_id,len(data))
            code=child('validate',file,directory,[],45)
            if code:
                error=read_json(directory/'error.json')['message'] if (directory/'error.json').exists() else '工作簿无法解析，请检查 Excel 文件和日期字段。'
                logging.warning('dataset_rejected id=%s error=%s',dataset_id,error)
                return self.error(error,422)
            write('INSERT INTO datasets VALUES (?,?,?,?)',(dataset_id,filename,now(),len(data)))
            self.json(public_dataset(query('SELECT * FROM datasets WHERE id=?',(dataset_id,),one=True)),201)
        finally: UPLOAD_LOCK.release()
    def create_job(self):
        payload=json.loads(self.body(8192))
        if not isinstance(payload,dict): raise ValueError('排产参数格式错误。')
        dataset_id=payload.get('dataset_id');strategy=payload.get('strategy','quick');station=payload.get('station','')
        if not isinstance(dataset_id,str) or not re.fullmatch('[a-f0-9]{32}',dataset_id): raise ValueError('请先选择数据集。')
        if strategy not in ['quick','balanced'] or not isinstance(station,str): raise ValueError('排产参数无效。')
        row=query('SELECT * FROM datasets WHERE id=?',(dataset_id,),one=True)
        if not row: return self.error('数据集不存在。',404)
        meta=public_dataset(row)
        if station and station not in meta['stations']: raise ValueError('工站不在当前数据集中。')
        with JOB_LOCK:
            if len(query("SELECT id FROM jobs WHERE status IN ('queued','running')"))>=8: return self.error('排产队列已满，请稍后再试。',429)
            job_id=uuid.uuid4().hex
            write('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',(job_id,dataset_id,strategy,station,'queued',now(),None,None,None))
            POOL.submit(execute_job,job_id)
        self.json(query('SELECT * FROM jobs WHERE id=?',(job_id,),one=True),202)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--storage',type=Path,default=STORAGE)
    args=parser.parse_args();globals()['STORAGE']=args.storage.resolve();STORAGE.mkdir(parents=True,exist_ok=True)
    (STORAGE/'logs').mkdir(exist_ok=True)
    handler=RotatingFileHandler(STORAGE/'logs'/'service.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')
    with db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, filename TEXT, created_at TEXT, size INTEGER)')
        conn.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, dataset_id TEXT, strategy TEXT, station TEXT, status TEXT, created_at TEXT, started_at TEXT, finished_at TEXT, error TEXT)')
        conn.execute("UPDATE jobs SET status='failed', finished_at=?, error='服务重启中断了排产，请重新提交。' WHERE status IN ('queued','running')",(now(),))
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'RTD 已启动：http://127.0.0.1:{args.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close();POOL.shutdown(wait=True)

if __name__=='__main__':main()
