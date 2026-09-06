# decoder.py

import numpy as np
import pandas as pd

SCHEDULE_COLUMNS = ['job_id','制造单号','批次','优先级','工艺路线','工站','数量','设备','UPH','开始时间','完成时间','加工时间(分钟)']


def initial_occupancy(wip_df, machine_uph, current_time):
    """对仍出现在快照中的 WIP 保守保留余量，同设备按投产时间串行占用。"""
    ready = {m: current_time for m in machine_uph}
    rows = []
    for _, wip in wip_df.sort_values('投产时间').iterrows():
        machine = wip['设备']
        uph = machine_uph.get(machine, 0)
        if uph <= 0: continue
        duration = float(wip['数量']) / uph * 60
        elapsed = max(0, (current_time-wip['投产时间']).total_seconds()/60)
        remaining = max(duration-elapsed, duration*0.1)
        start = max(current_time, ready[machine], wip['投产时间'])
        finish = start + pd.Timedelta(minutes=remaining)
        ready[machine] = finish
        rows.append(dict(machine=machine, route=wip['工艺路线'], quantity=wip['数量'], start_time=start, finish_time=finish))
    return ready, pd.DataFrame(rows, columns=['machine','route','quantity','start_time','finish_time'])


def decode_schedule(
    pending_df,
    route_equipment_map,
    station_equipment_map,
    machine_uph,
    machine_status,
    machine_unavailable,
    wip_df,
    station,
    current_time,
    job_order
):
    current_time = pd.Timestamp(current_time)

    jobs = pending_df[pending_df["下一工站"] == station].copy().reset_index(drop=True)

    if jobs.empty:
        return None

    if 'job_id' not in jobs:
        jobs["job_id"] = [f"{row['制造单号']}#{row['批次']}" for _, row in jobs.iterrows()]

    machines = station_equipment_map.get(station, [])
    machines = list(dict.fromkeys(m for m in machines if m in machine_uph and machine_uph[m] > 0))

    machine_index = {machine: j for j, machine in enumerate(machines)}

    # 基础可加工矩阵 + 加工时间矩阵
    base_assignment = np.zeros((len(jobs), len(machines)), dtype=int)
    processing_time = np.full((len(jobs), len(machines)), np.inf)

    for i, job in jobs.iterrows():
        allowed = job.get('_allowed', route_equipment_map.get((job["工艺路线"], station), []))

        for machine in allowed:
            if machine not in machine_index:
                continue

            j = machine_index[machine]
            base_assignment[i, j] = 1
            processing_time[i, j] = job["数量"] / machine_uph[machine] * 60

    # 每台机器最早可用时间
    all_ready, all_wip = initial_occupancy(wip_df, machine_uph, current_time)
    machine_ready = {machine: all_ready[machine] for machine in machines}
    initial_wip = all_wip[all_wip['machine'].isin(machines)].to_dict('records')

    # 故障 / 维修
    blocked_status = {"故障", "维修", "维修中", "停机", "不可用", "未知", "数据冲突"}
    disabled_machines = set()

    for machine in machines:
        status = machine_status.get(machine, "")
        periods = sorted(machine_unavailable.get(machine, []), key=lambda x: x[0])

        # 当前正在维修
        active = [(start, end) for start, end in periods if start <= current_time < end]

        if active:
            machine_ready[machine] = max(machine_ready[machine], max(end for _, end in active))

        # 当前状态就是故障/维修
        if status in blocked_status:
            # 任意未来维护窗口不等于当前故障的已确认恢复时间。
            disabled_machines.add(machine)

    # 初始可分配矩阵
    initial_assignment = np.zeros_like(base_assignment)

    for i in range(len(jobs)):
        for j, machine in enumerate(machines):
            if base_assignment[i, j] == 0 or machine in disabled_machines or machine_ready[machine] > current_time:
                continue

            process_minutes = processing_time[i, j]
            finish = current_time + pd.Timedelta(minutes=float(process_minutes))
            conflict = any(current_time < end and finish > start for start, end in machine_unavailable.get(machine, []))

            if not conflict:
                initial_assignment[i, j] = 1

    schedule = []
    unscheduled = []

    # 按 SA 给定的 Job 顺序解码
    for i in job_order:
        job = jobs.iloc[i]
        candidates = []

        for j, machine in enumerate(machines):
            if base_assignment[i, j] == 0 or machine in disabled_machines:
                continue

            process_minutes = float(processing_time[i, j])
            start = max(current_time, machine_ready[machine])
            periods = sorted(machine_unavailable.get(machine, []), key=lambda x: x[0])

            # 找最早不会撞维修窗口的开始时间
            while True:
                finish = start + pd.Timedelta(minutes=process_minutes)
                conflict = None

                for unavailable_start, unavailable_end in periods:
                    if start < unavailable_end and finish > unavailable_start:
                        conflict = (unavailable_start, unavailable_end)
                        break

                if conflict is None:
                    break

                start = conflict[1]

            finish = start + pd.Timedelta(minutes=process_minutes)

            # ECT：最早完成时间
            candidates.append((finish, start, process_minutes, j))

        if not candidates:
            unscheduled.append(i)
            continue

        finish, start, process_minutes, machine_idx = min(candidates, key=lambda x: (x[0], x[1], x[2]))
        machine = machines[machine_idx]

        schedule.append({
            "job_id": job["job_id"],
            "制造单号": job["制造单号"],
            "批次": job["批次"],
            "优先级": job["优先级"],
            "工艺路线": job["工艺路线"],
            "工站": station,
            "数量": job["数量"],
            "设备": machine,
            "UPH": machine_uph[machine],
            "开始时间": start,
            "完成时间": finish,
            "加工时间(分钟)": round(process_minutes, 2)
        })

        machine_ready[machine] = finish

    schedule_df = pd.DataFrame(schedule, columns=SCHEDULE_COLUMNS)
    unscheduled_df = jobs.iloc[sorted(unscheduled)].copy()

    initial_assignment_df = pd.DataFrame(initial_assignment, index=jobs["job_id"], columns=machines)
    processing_time_df = pd.DataFrame(processing_time, index=jobs["job_id"], columns=machines)

    unscheduled_reasons = []

    for i in unscheduled:
        job = jobs.iloc[i]
        allowed = route_equipment_map.get((job["工艺路线"], station), [])
        valid = [m for m in allowed if m in machines]
        disabled = [m for m in valid if m in disabled_machines]

        if not allowed:
            reason = "工艺路线-工站没有配置设备"
        elif not valid:
            reason = "配置设备不在工站设备表或缺少UPH"
        elif len(disabled) == len(valid):
            reason = "可加工设备均故障/维修且无恢复时间"
        elif job.get('_excluded'):
            reason = '；'.join(sorted(set(job['_excluded'].values())))
        else:
            reason = "其他设备约束"

        unscheduled_reasons.append({
            "job_id": job["job_id"],
            "工艺路线": job["工艺路线"],
            "工站": station,
            "工艺路线配置设备": ",".join(allowed),
            "有效设备": ",".join(valid),
            "未排原因": reason
        })

    return {
        "jobs": jobs,
        "machines": machines,
        "initial_wip": pd.DataFrame(initial_wip, columns=['machine','route','quantity','start_time','finish_time']),
        "initial_assignment_matrix": initial_assignment_df,
        "processing_time_matrix": processing_time_df,
        "schedule": schedule_df,
        "unscheduled_jobs": unscheduled_df,
        "unscheduled_reasons": pd.DataFrame(unscheduled_reasons),
        "machine_busy_until": machine_ready,
        "disabled_machines": disabled_machines
    }
