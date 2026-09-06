"""产品数据契约：检查上传工作簿并规范化，绝不静默丢失待排任务。"""
import json
import math
import re
import pandas as pd

REQUIRED = {
    '3-设备表': ['设备名','设备产能UPH','设备状态'],
    '5-工艺路线-设备表': ['工艺路线','工站','设备'],
    '6-WIP在制表': ['制造单号','批次','设备','工站','工艺路线','投产时间','数量'],
    '7-优先级': ['制造单号','优先级'],
    '8-制造单批次流转表': ['制造单号','批次','数量','工艺路线','下一工站'],
}
BLOCKED = {'故障','维修','维修中','停机','不可用','未知','数据冲突'}

class DatasetError(ValueError):
    """可公开的数据错误，不携带服务器路径。"""

def text(value):
    if pd.isna(value): return ''
    if isinstance(value,(int,float)) and float(value).is_integer(): return str(int(value))
    return str(value).strip()

def periods(value):
    value=text(value)
    if not value: return []
    pattern=r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*[-~至]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)'
    matches=re.findall(pattern,value)
    if not matches or re.sub(r'[\s,，;；]+','',re.sub(pattern,'',value)):
        raise DatasetError('维修时间段无法完整识别，请使用 YYYY-MM-DD HH:MM - YYYY-MM-DD HH:MM。')
    result=[]
    for a,b in matches:
        start,end=pd.Timestamp(a),pd.Timestamp(b)
        if end<=start: raise DatasetError('维修结束时间必须晚于开始时间。')
        result.append((start,end))
    return sorted(result)

def safe_records(frame):
    return json.loads(frame.to_json(orient='records',date_format='iso',force_ascii=False))

def load_product(path):
    warnings=[]
    with pd.ExcelFile(path) as book:
        missing=set(REQUIRED)-set(book.sheet_names)
        if missing: raise DatasetError('缺少工作表：'+'、'.join(sorted(missing)))
        tables={name:pd.read_excel(book,name) for name in book.sheet_names}
    for name,cols in REQUIRED.items():
        absent=set(cols)-set(tables[name].columns)
        if absent: raise DatasetError(name+' 缺少列：'+'、'.join(sorted(absent)))
    if any(len(t)>30000 for t in tables.values()): raise DatasetError('单张工作表最多支持 30,000 行。')
    snapshots=set()
    for table in tables.values():
        for column in table.columns:
            match=re.search(r'设定导出时间[：:]\s*(\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)',str(column))
            if match: snapshots.add(pd.Timestamp(match.group(1)))
    if len(snapshots)!=1: raise DatasetError('表头需提供一致的“设定导出时间：YYYY/MM/DD HH:MM”。')
    snapshot=next(iter(snapshots))
    machine_df=tables['3-设备表'].copy()
    machine_df['设备名']=machine_df['设备名'].map(text)
    machine_df=machine_df[machine_df['设备名']!='']
    large_col=next((c for c in machine_df if str(c).startswith('不能做4.0')),None)
    double_col=next((c for c in machine_df if str(c).startswith('不能做双芯')),None)
    down_col=next((c for c in machine_df if '维修时间段' in str(c) or '不可用时间段' in str(c)),None)
    if not large_col or not double_col: raise DatasetError('设备表缺少大芯片或双芯限制列。')
    machines={}
    for name,rows in machine_df.groupby('设备名',sort=False):
        cols=['设备产能UPH','设备状态',large_col,double_col]+([down_col] if down_col else [])
        conflict=len(rows[cols].astype(str).drop_duplicates())>1
        row=rows.iloc[0]; uph=pd.to_numeric(row['设备产能UPH'],errors='coerce')
        valid=pd.notna(uph) and math.isfinite(float(uph)) and float(uph)>0
        status=text(row['设备状态']) or '未知'
        if status not in {'生产中','空闲'}|BLOCKED:
            print(f'[数据校验-设备状态] {name}: 状态值"{text(row["设备状态"])}"不在已知枚举中（生产中/空闲/故障/维修/维修中/停机/不可用），本次禁用。',flush=True)
            warnings.append(f'{name} 状态未识别，本次禁用。'); status='未知'
        if conflict:
            dup_cols=[]
            for c in cols:
                vals=rows[c].astype(str).unique()
                if len(vals)>1: dup_cols.append(f'{c}({" / ".join(vals)})')
            print(f'[数据校验-设备冲突] {name}: 在3-设备表中出现{len(rows)}条记录，以下字段值不一致：{"；".join(dup_cols)}。已禁用，请核对主数据。',flush=True)
            warnings.append(f'{name} 存在冲突设备记录，已禁用，请核对主数据。');status='数据冲突'
        if not valid: warnings.append(f'{name} 缺少正数 UPH，禁止分配。')
        for col in [large_col,double_col]:
            if text(row[col]) not in {'0','1'}: raise DatasetError(f'{name} 的芯片限制必须为 0 或 1。')
        machines[name]=dict(id=name,status=status,uph=float(uph) if valid else 0,
            noLarge=text(row[large_col])=='1',noDouble=text(row[double_col])=='1',
            downtimes=periods(row[down_col]) if down_col and not conflict else [],stations=[])
    if not machines: raise DatasetError('设备表没有有效设备名。')
    routes={}; route_df=tables['5-工艺路线-设备表'].copy()
    route_df['工艺路线']=route_df['工艺路线'].ffill();unknown=set()
    for _,row in route_df.iterrows():
        route,station=text(row['工艺路线']),text(row['工站'])
        if not route or not station: continue
        names=[n.strip() for n in re.split(r'[,，;；\r\n]+',text(row['设备'])) if n.strip()]
        routes.setdefault((route,station),set()).update(names)
        unknown.update(n for n in names if n not in machines)
    routes={k:sorted(v) for k,v in routes.items()};station_map={}
    for (_,station),names in routes.items(): station_map.setdefault(station,set()).update(n for n in names if n in machines)
    for station,names in station_map.items():
        for name in names: machines[name]['stations'].append(station)
    if unknown:
        print(f'[数据校验-未登记设备] 工艺路线-设备表引用了{len(unknown)}个未在3-设备表中登记的设备名：{", ".join(sorted(unknown))}。已排除这些候选。',flush=True)
        warnings.append(f'路线引用了 {len(unknown)} 个未登记设备名，已排除这些候选。')
    wip=tables['6-WIP在制表'].copy();flow=tables['8-制造单批次流转表'].copy()
    for frame,name in [(wip,'WIP'),(flow,'批次流转')]:
        for c in ['制造单号','批次','工艺路线']: frame[c]=frame[c].map(text)
        frame['job_id']=frame['制造单号']+'#'+frame['批次']
        if frame.duplicated(['制造单号','批次']).any(): raise DatasetError(name+'存在重复制造单号与批次，请核对并去重。')
    flow['下一工站']=flow['下一工站'].map(text)
    completed=int((flow['下一工站']=='').sum());flow=flow[flow['下一工站']!=''].copy()
    overlap=int(flow['job_id'].isin(wip['job_id']).sum());flow=flow[~flow['job_id'].isin(wip['job_id'])].copy()
    for frame,name in [(wip,'WIP'),(flow,'待排批次')]:
        frame['数量']=pd.to_numeric(frame['数量'],errors='coerce')
        if (~frame['数量'].map(lambda x:pd.notna(x) and math.isfinite(float(x)) and x>0)).any() or (frame[['制造单号','批次','工艺路线']]=='').any().any():
            raise DatasetError(name+'存在空标识、空工艺或非正数量。')
    wip['设备']=wip['设备'].map(text);wip['投产时间']=pd.to_datetime(wip['投产时间'],format='mixed',errors='coerce')
    if wip['投产时间'].isna().any() or (wip['设备']=='').any(): raise DatasetError('WIP 有空设备或无效投产时间。')
    unregistered_wip_records=[]
    for _,w in wip.iterrows():
        device=w['设备'];route=w['工艺路线'];station=w['工站']
        route_devices=routes.get((route,station),[])
        if device not in route_devices:
            unregistered_wip_records.append(f'{device}(工艺路线={route},工站={station})')
    if unregistered_wip_records:
        print(f'[数据校验-WIP未登记设备] WIP在制表中以下{len(unregistered_wip_records)}条记录的设备未在5-工艺路线-设备表中对应(工艺路线,工站)下找到：{"；".join(unregistered_wip_records)}。保留异常记录，不映射为可派设备。',flush=True)
        warnings.append('部分 WIP 设备未登记，保留异常记录，不映射为可派设备。')
    priority=tables['7-优先级'].copy();priority['制造单号']=priority['制造单号'].map(text)
    priority['优先级']=pd.to_numeric(priority['优先级'],errors='coerce')
    if not priority['优先级'].map(lambda x:pd.notna(x) and math.isfinite(float(x))).all(): raise DatasetError('优先级必须为有限数值。')
    priority_map=priority.groupby('制造单号')['优先级'].max()
    flow['优先级']=flow['制造单号'].map(priority_map)
    missing_pri_mask=flow['优先级'].isna()
    flow['优先级']=flow['优先级'].fillna(0)
    if missing_pri_mask.any():
        missing_orders=sorted(flow.loc[missing_pri_mask,'制造单号'].unique())
        print(f'[数据校验-优先级缺失] 以下{len(missing_orders)}个制造单号在7-优先级表中未找到，优先级按0处理：{", ".join(missing_orders)}',flush=True)
    warnings.append('优先级沿用当前算法：数字越大越紧急，缺失值按 0 处理。')
    orders=tables.get('2-制造单',pd.DataFrame());products=tables.get('4-产品表',pd.DataFrame())
    order_map={};size_map={}
    if {'制造单号','成品编码','是否双芯'}.issubset(orders.columns):
        order_map={text(r['制造单号']):r for _,r in orders.iterrows()}
    if {'产品代码','芯片尺寸'}.issubset(products.columns):
        for _,r in products.iterrows():
            sizes=[float(v)/(1000 if u.lower() in {'um','μm'} else 1) for v,u in re.findall(r'(\d+(?:\.\d+)?)\s*(mm|um|μm)',text(r['芯片尺寸']),re.I)]
            if sizes: size_map[text(r['产品代码'])]=max(size_map.get(text(r['产品代码']),0),max(sizes))
    allowed_rows=[];reasons=[];unknown_chips=0
    for _,row in flow.iterrows():
        order=order_map.get(row['制造单号'])
        size=size_map.get(text(order['成品编码'])) if order is not None else None
        double=text(order['是否双芯']) in {'是','1','True'} if order is not None else None
        if size is None:
            unknown_chips+=1
            if order is None:
                print(f'[数据校验-芯片尺寸] {row["job_id"]}: 制造单号"{row["制造单号"]}"未在2-制造单表中找到，无法核验芯片尺寸，禁止分配到限制大芯片的设备。',flush=True)
            else:
                product_code=text(order['成品编码'])
                if product_code not in size_map:
                    print(f'[数据校验-芯片尺寸] {row["job_id"]}: 成品编码"{product_code}"未在4-产品表中找到芯片尺寸，无法核验，禁止分配到限制大芯片的设备。',flush=True)
        allowed=[];excluded={}
        for name in routes.get((row['工艺路线'],row['下一工站']),[]):
            m=machines.get(name);reason=''
            if m is None: reason='设备未登记'
            elif name not in station_map.get(row['下一工站'],set()): reason='设备不属于该工站'
            elif m['status'] in BLOCKED: reason='设备'+m['status']
            elif m['uph']<=0: reason='UPH 无效'
            elif m['noLarge'] and (size is None or size>4): reason='芯片尺寸不满足或无法核验'
            elif m['noDouble'] and (double is None or double): reason='双芯限制不满足或无法核验'
            if reason: excluded[name]=reason
            else: allowed.append(name)
        allowed_rows.append(allowed);reasons.append(excluded)
    flow['_allowed']=allowed_rows;flow['_excluded']=reasons
    if unknown_chips: warnings.append(f'{unknown_chips} 个批次无法核验芯片尺寸，禁止分配到限制大芯片的设备。')
    flow=flow.sort_values(['优先级','job_id'],ascending=[False,True]).reset_index(drop=True)
    return dict(pending=flow,wip=wip,machines=machines,routes=routes,station_map={s:sorted(v) for s,v in station_map.items()},snapshot=snapshot,warnings=warnings,
        sheets=[dict(name=n,rows=len(t)) for n,t in tables.items()],counts=dict(machines=len(machines),pending=len(flow),wip=len(wip),completedExcluded=completed,wipExcluded=overlap))

def scheduler_args(data):
    m=data['machines']
    return (data['pending'],data['routes'],data['station_map'],{n:x['uph'] for n,x in m.items()},
        {n:x['status'] for n,x in m.items()},{n:list(x['downtimes']) for n,x in m.items()},data['wip'])