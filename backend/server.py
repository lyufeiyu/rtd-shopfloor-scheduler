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

TABLE_DEFS={
    '设备表':{
        'label':'设备表 (3-设备表)',
        'description':'导入设备信息，包含设备名称、产能、状态、芯片限制和维修时间段。',
        'columns':[
            {'name':'设备名','type':'字符串','required':True,'hint':'设备唯一标识名称，不能为空（也支持"设备名称"）'},
            {'name':'设备产能UPH','type':'正数','required':True,'hint':'设备每小时产能，必须大于0'},
            {'name':'设备状态','type':'字符串','required':True,'hint':'可选值：生产中、空闲、故障、维修、维修中、停机、不可用'},
            {'name':'不能做4.0以上芯片','type':'0或1','required':True,'hint':'0:否，1:是。仅允许0或1（列名可带后缀如"(0:否；1：是)"）'},
            {'name':'不能做双芯片','type':'0或1','required':True,'hint':'0:否，1:是。仅允许0或1（列名可带后缀如"(0:否；1：是)"）'},
            {'name':'设备维修时间段','type':'字符串','required':False,'hint':'格式：YYYY-MM-DD HH:MM - YYYY-MM-DD HH:MM，多个时间段用逗号分隔'},
        ]
    },
    '制造单表':{
        'label':'制造单表 (2-制造单)',
        'description':'导入制造单信息，包含制造单号、客户订单号、产品型号、产品类型、芯片型号、封装形式、发料日期、下单数、入库良品、完成状态、周期码、成品编码、差异数、是否双芯。',
        'columns':[
            {'name':'制造单号','type':'字符串','required':True,'hint':'制造单唯一标识，不能为空'},
            {'name':'客户订单号','type':'字符串','required':True,'hint':'关联客户订单号，不能为空'},
            {'name':'晶圆批号','type':'字符串','required':False,'hint':'晶圆批次编号'},
            {'name':'产品型号','type':'字符串','required':True,'hint':'产品型号，不能为空'},
            {'name':'产品类型','type':'字符串','required':False,'hint':'产品类型'},
            {'name':'芯片型号','type':'字符串','required':True,'hint':'芯片型号，不能为空'},
            {'name':'封装形式','type':'字符串','required':True,'hint':'封装形式，不能为空'},
            {'name':'发料日期','type':'字符串','required':True,'hint':'发料日期，不能为空'},
            {'name':'下单数','type':'正整数','required':True,'hint':'下单数量，必须大于0'},
            {'name':'入库良品','type':'数字','required':True,'hint':'入库良品数量'},
            {'name':'完成状态','type':'字符串','required':True,'hint':'完成状态，不能为空'},
            {'name':'周期码','type':'字符串','required':False,'hint':'周期码'},
            {'name':'成品编码','type':'字符串','required':True,'hint':'成品编码，用于关联产品表获取芯片尺寸'},
            {'name':'差异数','type':'数字','required':False,'hint':'差异数量'},
            {'name':'是否双芯','type':'字符串','required':False,'hint':'可选值：是/否/1/0/True/False'},
        ]
    },
    '产品表':{
        'label':'产品表 (4-产品表)',
        'description':'导入产品信息，包含产品代码、产品型号、芯片尺寸、框架和线径。',
        'columns':[
            {'name':'产品代码','type':'字符串','required':True,'hint':'产品唯一标识代码，不能为空'},
            {'name':'产品型号','type':'字符串','required':False,'hint':'产品型号'},
            {'name':'芯片尺寸','type':'字符串','required':False,'hint':'格式示例：5.0mm、1200um、6.5mm 等'},
            {'name':'框架','type':'字符串','required':False,'hint':'框架信息'},
            {'name':'线径','type':'字符串','required':False,'hint':'线径信息'},
        ]
    },
    '工艺路线设备表':{
        'label':'工艺路线-设备表 (5-工艺路线-设备表)',
        'description':'导入工艺路线与设备的对应关系，包含工艺路线、工站、车间、排序和可用设备。',
        'columns':[
            {'name':'工艺路线','type':'字符串','required':True,'hint':'工艺路线名称，不能为空'},
            {'name':'工站','type':'字符串','required':True,'hint':'工站名称，不能为空'},
            {'name':'车间','type':'字符串','required':False,'hint':'车间名称'},
            {'name':'排序','type':'数字','required':False,'hint':'工站排序号'},
            {'name':'设备','type':'字符串','required':False,'hint':'设备名称，多个设备用逗号分隔（可暂时为空）'},
        ]
    },
    'WIP在制表':{
        'label':'WIP在制表 (6-WIP在制表)',
        'description':'导入在制品信息，包含客户代码、制造单号、批次、产品代码、产品型号、工艺路线、工站、设备、数量和投产时间。',
        'columns':[
            {'name':'客户代码','type':'字符串','required':False,'hint':'客户代码'},
            {'name':'制造单号','type':'字符串','required':True,'hint':'制造单号，不能为空'},
            {'name':'批次','type':'字符串','required':True,'hint':'批次号，不能为空，与制造单号组合唯一'},
            {'name':'产品代码','type':'字符串','required':False,'hint':'产品代码，可通过制造单表关联查询'},
            {'name':'产品型号','type':'字符串','required':False,'hint':'产品型号，可通过制造单表关联查询'},
            {'name':'工艺路线','type':'字符串','required':True,'hint':'工艺路线名称，不能为空'},
            {'name':'工站','type':'字符串','required':True,'hint':'当前所在工站，不能为空'},
            {'name':'设备','type':'字符串','required':True,'hint':'当前加工设备名称，不能为空'},
            {'name':'数量','type':'正整数','required':True,'hint':'在制数量，必须大于0'},
            {'name':'投产时间','type':'日期时间','required':True,'hint':'格式：YYYY-MM-DD HH:MM 或 YYYY/MM/DD HH:MM'},
        ]
    },
    '优先级表':{
        'label':'优先级表 (7-优先级)',
        'description':'导入制造单优先级信息，数字越大越紧急。',
        'columns':[
            {'name':'客户代码','type':'字符串','required':False,'hint':'客户代码'},
            {'name':'制造单号','type':'字符串','required':True,'hint':'制造单号，不能为空'},
            {'name':'优先级','type':'数字','required':True,'hint':'优先级数值，数字越大越紧急，必须为有限数值'},
        ]
    },
    '批次流转表':{
        'label':'批次流转表 (8-制造单批次流转表)',
        'description':'导入制造单批次流转信息，包含制造单号、批次、数量、工艺路线、已完成工站和下一工站。',
        'columns':[
            {'name':'制造单号','type':'字符串','required':True,'hint':'制造单号，不能为空'},
            {'name':'批次','type':'字符串','required':True,'hint':'批次号，不能为空'},
            {'name':'数量','type':'正整数','required':True,'hint':'批次数量，必须大于0'},
            {'name':'工艺路线','type':'字符串','required':True,'hint':'工艺路线名称，不能为空'},
            {'name':'已完成工站','type':'字符串','required':False,'hint':'已完成工站名称，可以为空'},
            {'name':'下一工站','type':'字符串','required':False,'hint':'下一工站名称，为空表示已完成'},
        ]
    },
    '订单表':{
        'label':'订单表 (1-订单表)',
        'description':'导入订单信息，包含客户代码、客户订单号、下单日期和目标交期。',
        'columns':[
            {'name':'客户代码','type':'字符串','required':True,'hint':'客户代码，不能为空'},
            {'name':'客户订单号','type':'字符串','required':True,'hint':'客户订单号，不能为空'},
            {'name':'下单日期','type':'日期时间','required':True,'hint':'格式：YYYY-MM-DD 或 YYYY/MM/DD'},
            {'name':'目标交期','type':'日期时间','required':True,'hint':'格式：YYYY-MM-DD 或 YYYY/MM/DD'},
        ]
    },
}
# STATIC={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/format.css':'format.css'}
# POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rtd-job')
# STATIC.update({'/workspace.css':'workspace.css','/assistant.js':'assistant.js'})
STATIC={
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/format.css': ('format.css', 'text/css; charset=utf-8'),
    '/workspace.css': ('workspace.css', 'text/css; charset=utf-8'),
    '/assistant.js': ('assistant.js', 'application/javascript; charset=utf-8')
}
POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rtd-job')
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

def validate_single_table(filepath,table_type):
    import pandas as pd
    import math
    if table_type not in TABLE_DEFS:
        raise ValueError(f'未知的表类型：{table_type}')
    definition=TABLE_DEFS[table_type]
    try:
        df=pd.read_excel(filepath)
    except Exception as e:
        raise ValueError(f'无法读取 Excel 文件：{str(e)}')
    if df.empty:
        raise ValueError('表格为空，请检查文件内容。')
    if len(df)>30000:
        raise ValueError('单张工作表最多支持 30,000 行。')
    col_map={}
    for col_def in definition['columns']:
        expected=col_def['name']
        if expected in df.columns:
            col_map[expected]=expected
            continue
        found=None
        if expected=='设备名':
            for alias in ['设备名称']:
                if alias in df.columns:
                    found=alias;break
        elif expected in ('不能做4.0以上芯片','不能做双芯片'):
            for col in df.columns:
                if str(col).startswith(expected):
                    found=col;break
        elif expected=='设备维修时间段':
            for col in df.columns:
                if '维修时间段' in str(col) or '不可用时间段' in str(col):
                    found=col;break
        elif expected=='客户订单号':
            for alias in ['订单号']:
                if alias in df.columns:
                    found=alias;break
        elif expected=='发料日期':
            for alias in ['发料日期/是否发料']:
                if alias in df.columns:
                    found=alias;break
        if found:
            col_map[expected]=found
    missing=[c['name'] for c in definition['columns'] if c['required'] and c['name'] not in col_map]
    if missing:
        raise ValueError(f'缺少必需列：{"、".join(missing)}')
    for col_def in definition['columns']:
        col_name=col_def['name']
        if col_name not in col_map:
            continue
        actual_col=col_map[col_name]
        col_type=col_def['type']
        for idx,val in df[actual_col].items():
            if pd.isna(val) or (isinstance(val,str) and val.strip()==''):
                if col_def['required']:
                    raise ValueError(f'第{idx+2}行"{actual_col}"列不能为空。')
                continue
            if col_type in ('0或1',):
                try:
                    v=int(float(str(val).strip()))
                    if v not in (0,1):
                        raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"必须为0或1。')
                except (ValueError,TypeError):
                    raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"必须为0或1。')
            elif col_type in ('正数','正整数'):
                try:
                    v=float(str(val).strip())
                    if not (math.isfinite(v) and v>0):
                        raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"必须为正数。')
                    if col_type=='正整数' and v!=int(v):
                        raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"必须为正整数。')
                except (ValueError,TypeError):
                    raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"必须为{"正整数" if col_type=="正整数" else "正数"}。')
            elif col_type=='数字':
                try:
                    v=float(str(val).strip())
                    if not math.isfinite(v):
                        raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"必须为有限数值。')
                except (ValueError,TypeError):
                    raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"必须为数值。')
            elif col_type=='日期时间':
                try:
                    pd.to_datetime(val,format='mixed',errors='raise')
                except Exception:
                    raise ValueError(f'第{idx+2}行"{actual_col}"列的值"{val}"无法识别为日期时间。')
    records=json.loads(df.to_json(orient='records',date_format='iso',force_ascii=False))
    return dict(columns=list(df.columns),rows=len(df),records=records)

SHEET_MAPPING={
    '1-订单表':'订单表','2-制造单':'制造单表','3-设备表':'设备表',
    '4-产品表':'产品表','5-工艺路线-设备表':'工艺路线设备表',
    '6-WIP在制表':'WIP在制表','7-优先级':'优先级表',
    '8-制造单批次流转表':'批次流转表'
}

def extract_tables_from_dataset(dataset_id):
    dataset_dir=STORAGE/'datasets'/dataset_id
    xlsx_path=dataset_dir/'input.xlsx'
    if not xlsx_path.exists():
        raise ValueError('数据集文件不存在。')
    import pandas as pd
    tables_dir=STORAGE/'tables'
    tables_dir.mkdir(parents=True,exist_ok=True)
    results=[]
    with pd.ExcelFile(xlsx_path) as book:
        for sheet_name,table_type in SHEET_MAPPING.items():
            if sheet_name not in book.sheet_names:
                continue
            df=pd.read_excel(book,sheet_name)
            if df.empty:
                continue
            tid=uuid.uuid4().hex
            records=json.loads(df.to_json(orient='records',date_format='iso',force_ascii=False))
            data=dict(columns=list(df.columns),rows=len(df),records=records,definition=TABLE_DEFS[table_type])
            (tables_dir/tid).mkdir(parents=True,exist_ok=True)
            (tables_dir/tid/'data.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            write('DELETE FROM tables WHERE type=? AND source=?',(table_type,'自动提取'))
            write('INSERT INTO tables VALUES (?,?,?,?,?,?,?)',(tid,table_type,dataset_id,'自动提取',len(df),now(),now()))
            results.append(dict(id=tid,type=table_type,label=TABLE_DEFS[table_type]['label'],rows=len(df),source='from_unified',dataset_id=dataset_id))
    return results

def assemble_dataset_from_tables():
    tables_dir=STORAGE/'tables'
    reverse_mapping={v:k for k,v in SHEET_MAPPING.items()}
    tables=query('SELECT * FROM tables ORDER BY created_at DESC')
    used_types=set()
    dfs={}
    for row in tables:
        ttype=row['type']
        if ttype in used_types: continue
        used_types.add(ttype)
        data_path=tables_dir/row['id']/'data.json'
        if not data_path.exists(): continue
        data=read_json(data_path)
        if not data.get('records'): continue
        import pandas as pd
        df=pd.DataFrame(data['records'])
        sheet_name=reverse_mapping.get(ttype)
        if sheet_name:
            dfs[sheet_name]=df
    if not dfs:
        raise ValueError('没有已导入的数据表，请先上传至少一张数据表。')
    dataset_id=uuid.uuid4().hex
    dataset_dir=STORAGE/'datasets'/dataset_id
    dataset_dir.mkdir(parents=True)
    xlsx_path=dataset_dir/'input.xlsx'
    with pd.ExcelWriter(xlsx_path,engine='openpyxl') as writer:
        for sheet_name,df in dfs.items():
            df.to_excel(writer,sheet_name=sheet_name,index=False)
    filename='从数据表组装.xlsx'
    write('INSERT INTO datasets VALUES (?,?,?,?)',(dataset_id,filename,now(),xlsx_path.stat().st_size))
    try:
        import subprocess,os
        env=os.environ.copy()
        env['PYTHONIOENCODING']='utf-8'
        code=subprocess.run([sys.executable,str(SCRIPT_DIR/'validate.py'),str(xlsx_path),str(dataset_dir)],capture_output=True,text=True,timeout=45,env=env).returncode
        if code:
            error=read_json(dataset_dir/'error.json')['message'] if (dataset_dir/'error.json').exists() else '工作簿无法解析，请检查 Excel 文件和日期字段。'
            logging.warning('dataset_assemble_rejected id=%s error=%s',dataset_id,error)
            import shutil
            shutil.rmtree(dataset_dir)
            write('DELETE FROM datasets WHERE id=?',(dataset_id,))
            raise ValueError(error)
    except ValueError: raise
    except Exception:
        logging.exception('assemble_validate_failed id=%s',dataset_id)
    try:
        extracted=extract_tables_from_dataset(dataset_id)
        logging.info('assemble_extract_tables id=%s count=%s',dataset_id,len(extracted))
    except Exception:
        logging.exception('assemble_extract_failed id=%s',dataset_id)
    return public_dataset(query('SELECT * FROM datasets WHERE id=?',(dataset_id,),one=True))

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
        # if path in STATIC:
        #     file=ROOT/'frontend'/STATIC[path]
        #     return self.send_bytes(file.read_bytes(),mimetypes.guess_type(file)[0] or 'text/plain')
        if path in STATIC:
            name, ctype = STATIC[path]
            file = ROOT / 'frontend' / name
            return self.send_bytes(file.read_bytes(), ctype)
        if path=='/api/health': return self.json(dict(status='ok'))
        if path=='/api/datasets': return self.json(dict(items=[public_dataset(r) for r in query('SELECT * FROM datasets ORDER BY created_at DESC')]))
        if path=='/api/jobs': return self.json(dict(items=[public_job(r) for r in query('SELECT * FROM jobs ORDER BY created_at DESC')]))
        if path=='/api/tables':
            items=[]
            for r in query('SELECT * FROM tables ORDER BY created_at DESC'):
                d=dict(r)
                d['rows']=d.pop('rows_count',0)
                items.append(d)
            return self.json(dict(items=items))
        if path=='/api/tables/defs':
            return self.json(dict(defs={k:{'label':v['label'],'description':v['description'],'columns':v['columns']} for k,v in TABLE_DEFS.items()}))
        match=re.fullmatch(r'/api/tables/([a-f0-9]{32})',path)
        if match:
            row=query('SELECT * FROM tables WHERE id=?',(match[1],),one=True)
            if not row: return self.error('数据表不存在。',404)
            data=read_json(STORAGE/'tables'/row['id']/'data.json')
            return self.json(dict(**row,**data))
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
        host=self.headers.get('Host','');origin=self.headers.get('Origin')
        if host not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'} or (origin and origin not in {'http://'+host}):
            return self.error('不允许跨站提交请求。',403)
        try:
            path=urlsplit(self.path).path
            if path=='/api/datasets': return self.upload()
            if path=='/api/jobs': return self.create_job()
            if path=='/api/tables': return self.upload_table()
            if path=='/api/tables/from-unified': return self.extract_unified()
            if path=='/api/tables/assemble': return self.assemble_dataset()
            return self.error('接口不存在。',404)
        except (ValueError,json.JSONDecodeError) as exc: self.error(str(exc))
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            logging.exception('post_failed');self.error('请求未完成，请稍后重试。',500)
    def do_DELETE(self):
        try:
            path=urlsplit(self.path).path
            match=re.fullmatch(r'/api/tables/([a-f0-9]{32})',path)
            if match:
                row=query('SELECT * FROM tables WHERE id=?',(match[1],),one=True)
                if not row: return self.error('数据表不存在。',404)
                import shutil
                table_type=row['type']
                for r in query('SELECT * FROM tables WHERE type=?',(table_type,)):
                    table_dir=STORAGE/'tables'/r['id']
                    if table_dir.exists(): shutil.rmtree(table_dir)
                write('DELETE FROM tables WHERE type=?',(table_type,))
                self.json(dict(status='deleted'))
            else:
                self.error('接口不存在。',404)
        except (ValueError,json.JSONDecodeError) as exc: self.error(str(exc))
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception:
            logging.exception('delete_failed');self.error('删除未完成，请稍后重试。',500)
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
            try:
                extracted=extract_tables_from_dataset(dataset_id)
                logging.info('dataset_extract_tables id=%s count=%s',dataset_id,len(extracted))
            except Exception:
                logging.exception('extract_tables_failed id=%s',dataset_id)
            self.json(public_dataset(query('SELECT * FROM datasets WHERE id=?',(dataset_id,),one=True)),201)
        finally: UPLOAD_LOCK.release()
    def upload_table(self):
        if not UPLOAD_LOCK.acquire(blocking=False): return self.error('正在处理另一个上传，请稍后重试。',429)
        try:
            table_type=unquote(self.headers.get('X-Table-Type',''))
            filename=Path(unquote(self.headers.get('X-Upload-Name',''))).name
            if not filename.lower().endswith('.xlsx') or len(filename)>180: raise ValueError('仅支持 .xlsx 文件。')
            if table_type not in TABLE_DEFS:
                return self.error(f'未知的表类型：{table_type}，支持的类型：{", ".join(TABLE_DEFS.keys())}',400)
            data=self.body(MAX_UPLOAD);tid=uuid.uuid4().hex
            tables_dir=STORAGE/'tables';tables_dir.mkdir(parents=True,exist_ok=True)
            table_dir=tables_dir/tid;table_dir.mkdir(parents=True)
            file=table_dir/'input.xlsx';file.write_bytes(data)
            check_workbook(file)
            result=validate_single_table(file,table_type)
            data_obj=dict(columns=result['columns'],rows=result['rows'],records=result['records'],definition=TABLE_DEFS[table_type])
            (table_dir/'data.json').write_text(json.dumps(data_obj,ensure_ascii=False),encoding='utf-8')
            write('DELETE FROM tables WHERE type=? AND source=?',(table_type,'手动导入'))
            write('INSERT INTO tables VALUES (?,?,?,?,?,?,?)',(tid,table_type,'','手动导入',result['rows'],now(),now()))
            logging.info('table_upload id=%s type=%s rows=%s',tid,table_type,result['rows'])
            self.json(dict(id=tid,type=table_type,label=TABLE_DEFS[table_type]['label'],rows=result['rows'],source='manual'),201)
        finally: UPLOAD_LOCK.release()
    def extract_unified(self):
        payload=json.loads(self.body(8192))
        if not isinstance(payload,dict):
            raise ValueError('参数格式错误。')
        dataset_id=payload.get('dataset_id')
        if not isinstance(dataset_id,str) or not re.fullmatch('[a-f0-9]{32}',dataset_id):
            raise ValueError('请先选择数据集。')
        row=query('SELECT * FROM datasets WHERE id=?',(dataset_id,),one=True)
        if not row: return self.error('数据集不存在。',404)
        try:
            results=extract_tables_from_dataset(dataset_id)
            self.json(dict(status='ok',items=results))
        except Exception as e:
            self.error(str(e))
    def assemble_dataset(self):
        try:
            result=assemble_dataset_from_tables()
            self.json(result,201)
        except Exception as e:
            self.error(str(e))
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
        conn.execute('CREATE TABLE IF NOT EXISTS tables (id TEXT PRIMARY KEY, type TEXT, dataset_id TEXT, source TEXT, rows_count INTEGER, created_at TEXT, updated_at TEXT)')
        conn.execute("UPDATE jobs SET status='failed', finished_at=?, error='服务重启中断了排产，请重新提交。' WHERE status IN ('queued','running')",(now(),))
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'RTD 已启动：http://127.0.0.1:{args.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close();POOL.shutdown(wait=True)

if __name__=='__main__':main()