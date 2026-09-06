# metrics.py

import pandas as pd


def evaluate_schedule(result, start_time):
    start_time = pd.Timestamp(start_time)
    schedule = result["schedule"].copy()
    initial_wip = result["initial_wip"].copy()
    machines = result["machines"]
    unscheduled = result["unscheduled_jobs"]

    if schedule.empty:
        summary = pd.DataFrame([{
            "Makespan(小时)": 0,
            "平均等待时间(分钟)": 0,
            "最大等待时间(分钟)": 0,
            "平均流动时间(分钟)": 0,
            "平均加工时间(分钟)": 0,
            "设备平均利用率(%)": 0,
            "已排Job数": 0,
            "未排Job数": len(unscheduled),
            "未排率(%)": 100 if len(unscheduled) else 0,
            "Throughput(Job/小时)": 0
        }])
        return summary, pd.DataFrame()

    schedule["开始时间"] = pd.to_datetime(schedule["开始时间"])
    schedule["完成时间"] = pd.to_datetime(schedule["完成时间"])

    # ---------------------------------------------------------
    # 1. Makespan
    # 从排产开始时刻到最后一个新排 Job 完成
    # ---------------------------------------------------------
    end_time = schedule["完成时间"].max()
    makespan_minutes = (end_time - start_time).total_seconds() / 60
    makespan_hours = makespan_minutes / 60

    # ---------------------------------------------------------
    # 2. 等待时间
    # 当前定义：从 RTD 快照时刻到 Job 真正开始加工
    # ---------------------------------------------------------
    schedule["等待时间(分钟)"] = (
        schedule["开始时间"] - start_time
    ).dt.total_seconds() / 60

    avg_waiting = schedule["等待时间(分钟)"].mean()
    max_waiting = schedule["等待时间(分钟)"].max()

    # ---------------------------------------------------------
    # 3. Flow Time
    # 从当前快照到 Job 完工
    # ---------------------------------------------------------
    schedule["流动时间(分钟)"] = (
        schedule["完成时间"] - start_time
    ).dt.total_seconds() / 60

    avg_flow_time = schedule["流动时间(分钟)"].mean()
    avg_processing_time = schedule["加工时间(分钟)"].mean()

    # ---------------------------------------------------------
    # 4. 设备利用率
    # 初始 WIP + 新排任务都算设备占用
    # ---------------------------------------------------------
    machine_metrics = []

    for machine in machines:
        intervals = []

        # 初始 WIP：只计算 start_time 之后仍然占用机器的部分
        if not initial_wip.empty:
            machine_wip = initial_wip[initial_wip["machine"] == machine]

            for _, row in machine_wip.iterrows():
                start = max(pd.Timestamp(row["start_time"]), start_time)
                finish = min(pd.Timestamp(row["finish_time"]), end_time)

                if finish > start:
                    intervals.append((start, finish))

        # 新排任务
        machine_schedule = schedule[schedule["设备"] == machine]

        for _, row in machine_schedule.iterrows():
            intervals.append((row["开始时间"], row["完成时间"]))

        # 合并重叠区间，避免重复计算设备占用
        intervals.sort(key=lambda x: x[0])
        merged = []

        for start, finish in intervals:
            if not merged or start > merged[-1][1]:
                merged.append([start, finish])
            else:
                merged[-1][1] = max(merged[-1][1], finish)

        busy_minutes = sum(
            (finish - start).total_seconds() / 60
            for start, finish in merged
        )

        utilization = busy_minutes / makespan_minutes * 100 if makespan_minutes > 0 else 0

        machine_metrics.append({
            "设备": machine,
            "忙碌时间(分钟)": round(busy_minutes, 2),
            "利用率(%)": round(utilization, 2),
            "新排Job数": len(machine_schedule)
        })

    machine_metrics_df = pd.DataFrame(machine_metrics)

    avg_utilization = (
        machine_metrics_df["利用率(%)"].mean()
        if not machine_metrics_df.empty else 0
    )

    # ---------------------------------------------------------
    # 5. 排产数量 / 未排率 / Throughput
    # ---------------------------------------------------------
    scheduled_count = len(schedule)
    unscheduled_count = len(unscheduled)
    total_count = scheduled_count + unscheduled_count

    unscheduled_rate = unscheduled_count / total_count * 100 if total_count else 0
    throughput = scheduled_count / makespan_hours if makespan_hours > 0 else 0
    throughput_qty = schedule["数量"].sum() / makespan_hours if makespan_hours > 0 else 0

    # ---------------------------------------------------------
    # 6. 汇总
    # ---------------------------------------------------------
    summary = pd.DataFrame([{
        "Makespan(小时)": round(makespan_hours, 2),
        "平均等待时间(分钟)": round(avg_waiting, 2),
        "最大等待时间(分钟)": round(max_waiting, 2),
        "平均流动时间(分钟)": round(avg_flow_time, 2),
        "平均加工时间(分钟)": round(avg_processing_time, 2),
        "设备平均利用率(%)": round(avg_utilization, 2),
        "已排Job数": scheduled_count,
        "未排Job数": unscheduled_count,
        "未排率(%)": round(unscheduled_rate, 2),
        "Throughput(Job/小时)": round(throughput, 2),
        "Throughput(数量/小时)": round(throughput_qty, 2),
    }])

    return summary, machine_metrics_df