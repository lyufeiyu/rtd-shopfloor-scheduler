"""后端唯一算法入口：validate / run。stdout 与 traceback 由服务端记录。"""
import argparse
import json
import os
from pathlib import Path
import time
import zipfile

import pandas as pd
from product_data import DatasetError, REQUIRED, load_product, scheduler_args, safe_records
from decoder import decode_schedule, initial_occupancy, SCHEDULE_COLUMNS


def dump(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, default=str), encoding='utf-8')


def progress(out, percent, stage, message):
    temporary=out/'progress.tmp'
    dump(temporary,dict(percent=percent,stage=stage,message=message))
    temporary.replace(out/'progress.json')


def metadata(data):
    warning_count=len(data['warnings'])
    return dict(snapshot=str(data['snapshot']), counts=data['counts'], warnings=data['warnings'],
        sheets=data['sheets'], stations=sorted(set(data['station_map'])|set(data['pending']['下一工站'])),
        formatCheck=dict(status='warning' if warning_count else 'passed', warningCount=warning_count,
            checkedSheets=len(data['sheets']), requiredSheets=list(REQUIRED)))


def dashboard(data):
    args=scheduler_args(data)
    ready, initial=initial_occupancy(data['wip'],args[3],data['snapshot'])
    machines=[]
    for name,m in data['machines'].items():
        rows=data['wip'][data['wip']['设备']==name]
        machines.append({**m, 'ready':str(ready[name]), 'wipCount':len(rows),
            'route':str(rows.iloc[-1]['工艺路线']) if len(rows) else '',
            'downtimes':[[str(a),str(b)] for a,b in m['downtimes']]})
    jobs=[]
    for _,j in data['pending'].iterrows():
        jobs.append(dict(id=j['job_id'],station=j['下一工站'],route=j['工艺路线'],quantity=float(j['数量']),
            priority=float(j['优先级']),candidates=j['_allowed'],excluded=j['_excluded']))
    return dict(**metadata(data), machines=machines, jobs=jobs, wip=safe_records(data['wip']),
        initialWip=safe_records(initial), schedule=[], unscheduled=[])


def validate_result(data, schedule, unscheduled, occupancy):
    ids=set(data['pending']['job_id']); selected=set(schedule['job_id']); rejected=set(unscheduled['job_id'])
    if selected & rejected or selected|rejected != ids or len(schedule)+len(unscheduled)!=len(ids):
        raise RuntimeError('任务守恒校验失败')
    jobs=data['pending'].set_index('job_id')
    for _,row in schedule.iterrows():
        if row['设备'] not in jobs.loc[row['job_id'],'_allowed']:
            raise RuntimeError('设备硬约束校验失败')
        if row['完成时间']<=row['开始时间'] or row['开始时间']<data['snapshot']:
            raise RuntimeError('任务时间顺序校验失败')
        for a,b in data['machines'][row['设备']]['downtimes']:
            if row['开始时间']<b and row['完成时间']>a: raise RuntimeError('维修时间冲突')
        existing=occupancy[occupancy['machine']==row['设备']]
        if any(row['开始时间']<w.finish_time and row['完成时间']>w.start_time for w in existing.itertuples()):
            raise RuntimeError('WIP 占用冲突')
    for _,rows in schedule.groupby('设备'):
        rows=rows.sort_values('开始时间')
        if any(a>b for a,b in zip(rows['完成时间'].iloc[:-1],rows['开始时间'].iloc[1:])):
            raise RuntimeError('跨工站设备时间重叠')


def run(data, out, strategy, station):
    # 工站依次排产，已分配的共用设备时段作为后续工站的不可用窗口。
    started=time.monotonic();args=list(scheduler_args(data))
    if station:
        data={**data,'pending':data['pending'][data['pending']['下一工站']==station].copy()}
        args[0]=data['pending']
    stations=sorted(set(data['pending']['下一工站']))
    out.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('MPLBACKEND','Agg')
    os.environ.setdefault('MPLCONFIGDIR',str(out/'plot-cache'))
    from output import export_schedule, plot_gantt
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    fonts={f.name for f in font_manager.fontManager.ttflist}
    plt.rcParams['font.sans-serif']=[next((f for f in ['SimHei','Hiragino Sans GB','Heiti SC','Noto Sans CJK SC','Microsoft YaHei'] if f in fonts),'DejaVu Sans')]
    from scheduler_sa import schedule_station as sa
    from metrics import evaluate_schedule
    schedules=[];unassigned=[];stats=[];files=[]
    _, initial=initial_occupancy(data['wip'],args[3],data['snapshot'])
    for idx,name in enumerate(stations):
        progress(out,10+int(75*idx/max(1,len(stations))),'scheduling',f'正在安排 {name} 工站（{idx+1}/{len(stations)}），计算设备分配并生成工站文件')
        print(f'工站 {idx+1}/{len(stations)} {name}',flush=True)
        jobs=data['pending'][data['pending']['下一工站']==name].reset_index(drop=True)
        if strategy=='balanced':
            result=sa(*args,name,data['snapshot'],max_seconds=1.0,early_stop_iterations=40)
        else:
            order=sorted(range(len(jobs)),key=lambda i:(-jobs.iloc[i]['优先级'],len(jobs.iloc[i]['_allowed']),
                min((jobs.iloc[i]['数量']/args[3][m] for m in jobs.iloc[i]['_allowed']),default=float('inf'))))
            result=decode_schedule(*args,name,data['snapshot'],order)
        schedules.append(result['schedule'])
        un=result['unscheduled_jobs'].copy()
        reason_map={r['job_id']:r['未排原因'] for r in result['unscheduled_reasons'].to_dict('records')}
        un['未排原因']=un['job_id'].map(reason_map).fillna('无满足约束的设备')
        unassigned.append(un)
        metric,_=evaluate_schedule(result,data['snapshot']);metric.insert(0,'工站',name);stats.append(metric)
        # 所有文件采用服务器生成的编号，工站名称不能成为路径。
        stem=f'station-{idx+1:03d}'
        export_schedule(result,name,str(out/f'{stem}.xlsx'))
        plot_gantt(result,data['snapshot'],str(out/f'{stem}.png'))
        files.append(dict(id=f'{stem}.xlsx',name=f'{name}_排产方案.xlsx',kind='xlsx',station=name))
        if (out/f'{stem}.png').exists(): files.append(dict(id=f'{stem}.png',name=f'{name}_甘特图.png',kind='png',station=name))
        for _,row in result['schedule'].iterrows():
            args[5].setdefault(row['设备'],[]).append((row['开始时间'],row['完成时间']))
    schedule=pd.concat(schedules,ignore_index=True) if schedules else pd.DataFrame(columns=SCHEDULE_COLUMNS)
    unscheduled=pd.concat(unassigned,ignore_index=True) if unassigned else pd.DataFrame(columns=['job_id','制造单号','批次','工艺路线','下一工站','数量','未排原因'])
    progress(out,85,'checking','正在核验设备资格、维修窗口、在制占用与任务守恒')
    validate_result(data,schedule,unscheduled,initial)
    metrics=pd.concat(stats,ignore_index=True) if stats else pd.DataFrame()
    # 复用工程原有 Excel 导出方式，所有空结果也保留表头。
    progress(out,92,'exporting','正在生成汇总工作簿、结果摘要与下载包')
    with pd.ExcelWriter(out/'summary.xlsx',engine='openpyxl') as writer:
        schedule.to_excel(writer,sheet_name='全部排产',index=False)
        initial.to_excel(writer,sheet_name='初始WIP',index=False)
        unscheduled.drop(columns=['_allowed','_excluded'],errors='ignore').to_excel(writer,sheet_name='未排产任务',index=False)
        metrics.to_excel(writer,sheet_name='评价指标',index=False)
        for sheet in writer.book:
            sheet.freeze_panes='A2'
            # 上传文本仅作为文本输出，避免被 Excel 当公式执行。
            for row in sheet:
                for cell in row:
                    if cell.data_type=='f': cell.data_type='s'
    files.insert(0,dict(id='summary.xlsx',name='全部工站排产汇总.xlsx',kind='xlsx',station=''))
    result=dashboard(data)
    result.update(schedule=safe_records(schedule),unscheduled=safe_records(unscheduled.drop(columns=['_allowed','_excluded'],errors='ignore')),
        metrics=safe_records(metrics),files=files,summary=dict(scheduled=len(schedule),unscheduled=len(unscheduled),
        machinesUsed=int(schedule['设备'].nunique()),duration=round(time.monotonic()-started,2),violations=0,strategy=strategy))
    dump(out/'result.json',result)
    with zipfile.ZipFile(out/'results.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for f in files: archive.write(out/f['id'],f['id'])
        archive.writestr('文件索引.json',json.dumps(files,ensure_ascii=False))
    print(json.dumps(result['summary'],ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['validate','run']);parser.add_argument('dataset');parser.add_argument('output')
    parser.add_argument('--strategy',choices=['quick','balanced'],default='quick');parser.add_argument('--station',default='')
    cli=parser.parse_args();out=Path(cli.output);out.mkdir(parents=True,exist_ok=True)
    try:
        if cli.action=='run': progress(out,5,'loading','正在读取生产数据与设备约束')
        data=load_product(cli.dataset)
        if cli.action=='validate': dump(out/'metadata.json',metadata(data));dump(out/'dashboard.json',dashboard(data))
        else: run(data,out,cli.strategy,cli.station)
    except DatasetError as exc:
        dump(out/'error.json',dict(message=str(exc)))
        raise

if __name__=='__main__': main()
