"""读取现有 RTD 数据，生成前端离线快照；不修改源项目。"""
import json
import sys
from pathlib import Path

SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/Users/feiyulv/Desktop/排产项目/RTD算法')
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
import pandas as pd
import rtd_neural_scheduler as scheduler

tables = scheduler.read_tables(SOURCE / 'RTD_Dataset_v6.xlsx')
snapshot = scheduler.parse_snapshot_time(tables['6-WIP在制表'].columns)
machines = scheduler.build_machine_info(tables['3-设备表'], snapshot)
routes, invalid_routes = scheduler.build_route_map(tables['5-工艺路线-设备表'], machines)
states, invalid_wip = scheduler.build_initial_state(tables['6-WIP在制表'], machines, snapshot)
tasks = scheduler.build_tasks(tables, None)
chips = scheduler.build_chip_size_map(tables['4-产品表'])
station_map = {}
for _, row in tables['工站-设备表'].iterrows():
    name = scheduler.normalize_id(row['设备名'])
    station_map.setdefault(name, set()).add(str(row['工站']).strip())

def records(frame):
    return json.loads(frame.to_json(orient='records', date_format='iso', force_ascii=False))

equipment = []
for name, machine in machines.items():
    equipment.append(dict(id=name, stations=sorted(station_map.get(name, {'未归属'})),
        status=machine.status, uph=machine.uph, noLarge=machine.cannot_large_chip,
        noDouble=machine.cannot_double_chip, downtimes=machine.downtimes,
        ready=states[name].ready_hour, lastRoute=states[name].last_route,
        wip=states[name].wip_count))
jobs = []
for _, task in tasks.iterrows():
    feasible, facts = scheduler.feasible_candidates(task, routes, machines, chips)
    due = task['目标交期']
    due_hour = 720.0 if pd.isna(due) else ((pd.Timestamp(due).normalize() + pd.Timedelta(days=1)) - snapshot).total_seconds()/3600
    station, route = str(task['下一工站']), str(task['工艺路线'])
    jobs.append(dict(id=str(task['任务ID']), station=station, route=route,
        quantity=float(task['数量']), priority=float(task['优先级']), due=due_hour,
        candidates=routes.get((route, station), []), feasible=feasible,
        large=bool(facts['大芯片']), double=bool(facts['双芯'])))
output = SOURCE / 'schedule_results' / 'Neural_MLP'
data = dict(snapshot=str(snapshot), machines=equipment, jobs=jobs,
    wip=records(tables['6-WIP在制表']),
    plan=records(pd.read_csv(output/'schedule.csv')),
    metrics=json.loads((output/'metrics.json').read_text()),
    quality=dict(invalidRoutes=len(invalid_routes), invalidWip=len(invalid_wip)))
target = Path(__file__).parent/'data.js'
target.write_text('window.RTD_DATA = '+json.dumps(data, ensure_ascii=False, allow_nan=False)+';\n', encoding='utf-8')
print(f'已导出 {len(equipment)} 台设备、{len(jobs)} 个任务、{len(data["plan"])} 条历史计划。')
