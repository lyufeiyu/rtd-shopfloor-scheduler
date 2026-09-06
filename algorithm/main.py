# main.py

import os
import sys
import time
import pandas as pd

from data_loader import load_data
from scheduler_heuristic import schedule_station as heuristic_schedule
from scheduler_sa import schedule_station as sa_schedule
from metrics import evaluate_schedule
from output import export_schedule, plot_gantt


class Tee:
    def __init__(self, *files):
        self.files = files

    def write(self, text):
        for f in self.files:
            f.write(text)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()


# =========================================================
# 1. 基本参数
# =========================================================
excel_path = "RTD_Dataset_v6.xlsx"
current_time = "2026-08-12 10:00:00"

# 可选：
# Priority_LFF_SPT
# SA
algorithm_name = "SA"

algorithms = {
    "Priority_LFF_SPT": heuristic_schedule,
    "SA": sa_schedule
}

if algorithm_name not in algorithms:
    raise ValueError(f"未知算法: {algorithm_name}")

schedule_func = algorithms[algorithm_name]

output_dir = "schedule_results"
algorithm_dir = os.path.join(output_dir, algorithm_name)
os.makedirs(algorithm_dir, exist_ok=True)


# =========================================================
# 2. 中文编码 + 保存终端日志
# =========================================================
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

console = sys.stdout
log_path = os.path.join(algorithm_dir, "运行日志.txt")
log_file = open(log_path, "w", encoding="utf-8-sig")
sys.stdout = Tee(console, log_file)

print("=" * 60)
print(f"算法: {algorithm_name}")
print(f"排产时间: {current_time}")
print("=" * 60)


# =========================================================
# 3. 读取数据
# =========================================================
(
    pending_df,
    route_equipment_map,
    station_equipment_map,
    machine_uph,
    machine_status,
    machine_unavailable,
    wip_df
) = load_data(excel_path)

stations = sorted(pending_df["下一工站"].dropna().unique().tolist())

print(f"待排工站数: {len(stations)}")
print(f"待排Job数: {len(pending_df)}")
print(f"设备数: {len(machine_uph)}")


# =========================================================
# 4. 汇总容器
# =========================================================
all_schedules = []
all_metrics = []
all_machine_metrics = []
all_unscheduled = []
all_unscheduled_reasons = []


# =========================================================
# 5. 各工站排产
# =========================================================
for station in stations:
    print("\n" + "=" * 60)
    print(f"开始排产工站: {station}")
    print("=" * 60)

    start_clock = time.time()

    result = schedule_func(
        pending_df,
        route_equipment_map,
        station_equipment_map,
        machine_uph,
        machine_status,
        machine_unavailable,
        wip_df,
        station,
        current_time
    )

    runtime = time.time() - start_clock

    if result is None:
        continue

    schedule_df = result["schedule"]
    unscheduled_df = result["unscheduled_jobs"]
    unscheduled_reasons_df = result.get("unscheduled_reasons", pd.DataFrame())

    print(f"工站设备数: {len(result['machines'])}")
    print(f"已排Job数: {len(schedule_df)}")
    print(f"未排Job数: {len(unscheduled_df)}")
    print(f"算法运行时间(秒): {runtime:.2f}")

    # SA额外信息
    if "sa_info" in result:
        print("\nSA信息:")
        for name, value in result["sa_info"].items():
            print(f"{name}: {value}")

    # -----------------------------------------------------
    # 评价指标
    # -----------------------------------------------------
    metrics_df, machine_metrics_df = evaluate_schedule(result, current_time)

    metrics_df.insert(0, "算法", algorithm_name)
    metrics_df.insert(1, "工站", station)
    metrics_df["算法运行时间(秒)"] = round(runtime, 2)

    if not machine_metrics_df.empty:
        machine_metrics_df.insert(0, "算法", algorithm_name)
        machine_metrics_df.insert(1, "工站", station)

    print("\n排产评价指标:")
    for name, value in metrics_df.iloc[0].items():
        if name not in ["算法", "工站"]:
            print(f"{name}: {value}")

    # -----------------------------------------------------
    # 未排原因
    # -----------------------------------------------------
    if not unscheduled_reasons_df.empty:
        print("\n未排原因:")
        for _, row in unscheduled_reasons_df.iterrows():
            print(f"{row['job_id']}: {row['未排原因']}")

    # -----------------------------------------------------
    # 单工站输出
    # -----------------------------------------------------
    safe_station = str(station).replace("/", "_").replace("\\", "_")

    export_schedule(result, station, output_path=os.path.join(algorithm_dir, f"{safe_station}_排产方案.xlsx"))
    plot_gantt(result, current_time, save_path=os.path.join(algorithm_dir, f"{safe_station}_甘特图.png"), show=False)

    # -----------------------------------------------------
    # 汇总
    # -----------------------------------------------------
    if not schedule_df.empty:
        temp = schedule_df.copy()
        temp.insert(0, "排产工站", station)
        all_schedules.append(temp)

    all_metrics.append(metrics_df)

    if not machine_metrics_df.empty:
        all_machine_metrics.append(machine_metrics_df)

    if not unscheduled_df.empty:
        temp = unscheduled_df.copy()
        temp.insert(0, "排产工站", station)
        all_unscheduled.append(temp)

    if not unscheduled_reasons_df.empty:
        temp = unscheduled_reasons_df.copy()
        temp.insert(0, "排产工站", station)
        all_unscheduled_reasons.append(temp)


# =========================================================
# 6. 合并全部工站
# =========================================================
total_schedule = pd.concat(all_schedules, ignore_index=True) if all_schedules else pd.DataFrame()
total_metrics = pd.concat(all_metrics, ignore_index=True,sort=False) if all_metrics else pd.DataFrame()
total_machine_metrics = pd.concat(all_machine_metrics, ignore_index=True) if all_machine_metrics else pd.DataFrame()
total_unscheduled = pd.concat(all_unscheduled, ignore_index=True) if all_unscheduled else pd.DataFrame()
total_unscheduled_reasons = pd.concat(all_unscheduled_reasons, ignore_index=True) if all_unscheduled_reasons else pd.DataFrame()


# =========================================================
# 7. 时间转字符串
# =========================================================
if not total_schedule.empty:
    for col in ["开始时间", "完成时间"]:
        if col in total_schedule.columns:
            total_schedule[col] = pd.to_datetime(total_schedule[col]).dt.strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# 8. 保存评价指标
# =========================================================
metrics_path = os.path.join(algorithm_dir, "评价指标.csv")
total_metrics.to_csv(metrics_path, index=False, encoding="utf-8-sig")


# =========================================================
# 9. 保存全部工站汇总
# =========================================================
summary_path = os.path.join(algorithm_dir, "全部工站排产汇总.xlsx")

with pd.ExcelWriter(summary_path, engine="openpyxl") as writer:
    total_schedule.to_excel(writer, sheet_name="全部排产", index=False)
    total_metrics.to_excel(writer, sheet_name="评价指标", index=False)
    total_machine_metrics.to_excel(writer, sheet_name="设备指标", index=False)
    total_unscheduled.to_excel(writer, sheet_name="未排产任务", index=False)
    total_unscheduled_reasons.to_excel(writer, sheet_name="未排原因", index=False)


# =========================================================
# 10. 最终输出
# =========================================================
print("\n" + "=" * 60)
print("全部工站排产完成")
print("=" * 60)
print(f"算法: {algorithm_name}")
print(f"总已排Job数: {len(total_schedule)}")
print(f"总未排Job数: {len(total_unscheduled)}")
print(f"总算法运行时间(秒): {total_metrics['算法运行时间(秒)'].sum():.2f}" if not total_metrics.empty else "总算法运行时间(秒): 0")
print(f"运行日志: {log_path}")
print(f"评价指标: {metrics_path}")
print(f"汇总文件: {summary_path}")
print("=" * 60)


# =========================================================
# 11. 恢复终端
# =========================================================
sys.stdout = console
log_file.close()