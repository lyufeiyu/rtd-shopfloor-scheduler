# scheduler_sa.py

import time
import random
import numpy as np
import pandas as pd

from decoder import decode_schedule


# Score = Makespan + 0.5 * AvgWaiting + 0.2 * WorkloadStd + 100000 * UnscheduledCount

def schedule_station(
    pending_df,
    route_equipment_map,
    station_equipment_map,
    machine_uph,
    machine_status,
    machine_unavailable,
    wip_df,
    station,
    current_time,
    initial_temperature=1000,
    cooling_rate=0.95,
    min_temperature=1,
    iterations_per_temp=30,
    early_stop_iterations=500,
    max_seconds=5,
    random_seed=42
):
    random.seed(random_seed)
    np.random.seed(random_seed)

    start_clock = time.time()
    current_time = pd.Timestamp(current_time)

    jobs = pending_df[pending_df["下一工站"] == station].copy().reset_index(drop=True)

    if jobs.empty:
        print(f"{station} 没有待排 Job")
        return None

    machines = station_equipment_map.get(station, [])
    machines = [m for m in machines if m in machine_uph and machine_uph[m] > 0]

    if not machines:
        return decode_schedule(pending_df, route_equipment_map, station_equipment_map,
            machine_uph, machine_status, machine_unavailable, wip_df, station,
            current_time, list(range(len(jobs))))

    # =========================================================
    # 1. Priority + LFF + SPT 生成初始顺序
    # =========================================================
    initial_data = []

    for i, job in jobs.iterrows():
        allowed = job.get('_allowed', route_equipment_map.get((job["工艺路线"], station), []))
        valid = [m for m in allowed if m in machines]

        flexibility = len(valid)
        best_process_time = min(job["数量"] / machine_uph[m] * 60 for m in valid) if valid else np.inf

        initial_data.append((i, job["优先级"], flexibility, best_process_time))

    initial_data.sort(key=lambda x: (-x[1], x[2], x[3]))
    current_solution = [x[0] for x in initial_data]

    # =========================================================
    # 2. 评价函数
    # =========================================================
    def evaluate(solution):
        result = decode_schedule(
            pending_df,
            route_equipment_map,
            station_equipment_map,
            machine_uph,
            machine_status,
            machine_unavailable,
            wip_df,
            station,
            current_time,
            solution
        )

        schedule = result["schedule"]

        if schedule.empty:
            return 1e12, result

        makespan = (schedule["完成时间"].max() - current_time).total_seconds() / 60
        avg_wait = ((schedule["开始时间"] - current_time).dt.total_seconds() / 60).mean()

        workload = schedule.groupby("设备")["加工时间(分钟)"].sum().reindex(result["machines"], fill_value=0)
        workload_std = workload.std(ddof=0)

        unscheduled_count = len(result["unscheduled_jobs"])

        score = makespan + 0.5 * avg_wait + 0.2 * workload_std + 100000 * unscheduled_count

        return score, result

    # =========================================================
    # 3. 初始解
    # =========================================================
    current_score, current_result = evaluate(current_solution)

    best_solution = current_solution.copy()
    best_score = current_score
    best_result = current_result

    temperature = initial_temperature

    iteration_count = 0
    no_improve_count = 0
    stop_reason = "温度达到下限"
    stop = False

    # =========================================================
    # 4. 模拟退火
    # =========================================================
    while temperature > min_temperature and not stop:
        for _ in range(iterations_per_temp):

            # -------------------------------------------------
            # 时间限制
            # -------------------------------------------------
            if time.time() - start_clock >= max_seconds:
                stop_reason = f"达到时间限制 {max_seconds} 秒"
                stop = True
                break

            new_solution = current_solution.copy()

            # -------------------------------------------------
            # 只能调整相同优先级内部的 Job
            # -------------------------------------------------
            priority_groups = {}

            for pos, job_index in enumerate(new_solution):
                priority = jobs.iloc[job_index]["优先级"]
                priority_groups.setdefault(priority, []).append(pos)

            movable_groups = [positions for positions in priority_groups.values() if len(positions) >= 2]

            if not movable_groups:
                stop_reason = "没有可调整的同优先级Job"
                stop = True
                break

            positions = random.choice(movable_groups)
            p1, p2 = random.sample(positions, 2)

            # 70% Swap，30% Insertion
            if random.random() < 0.7:
                new_solution[p1], new_solution[p2] = new_solution[p2], new_solution[p1]
            else:
                value = new_solution.pop(p1)

                if p1 < p2:
                    p2 -= 1

                new_solution.insert(p2, value)

            # -------------------------------------------------
            # 评价新方案
            # -------------------------------------------------
            new_score, new_result = evaluate(new_solution)
            delta = new_score - current_score

            # 更优直接接受，更差按概率接受
            if delta <= 0 or random.random() < np.exp(-delta / temperature):
                current_solution = new_solution
                current_score = new_score
                current_result = new_result

            # -------------------------------------------------
            # 更新全局最优
            # -------------------------------------------------
            if current_score < best_score - 1e-9:
                best_solution = current_solution.copy()
                best_score = current_score
                best_result = current_result
                no_improve_count = 0
            else:
                no_improve_count += 1

            iteration_count += 1

            # -------------------------------------------------
            # 早停
            # -------------------------------------------------
            if no_improve_count >= early_stop_iterations:
                stop_reason = f"连续 {early_stop_iterations} 次迭代最优解未改善"
                stop = True
                break

        temperature *= cooling_rate

    # =========================================================
    # 5. SA运行信息
    # =========================================================
    runtime = time.time() - start_clock

    best_result["sa_info"] = {
        "best_score": round(best_score, 2),
        "iterations": iteration_count,
        "无改善迭代次数": no_improve_count,
        "initial_temperature": initial_temperature,
        "cooling_rate": cooling_rate,
        "early_stop_iterations": early_stop_iterations,
        "max_seconds": max_seconds,
        "最终温度": round(temperature, 4),
        "停止原因": stop_reason,
        "运行时间(秒)": round(runtime, 2)
    }

    best_result["job_order"] = best_solution

    print(f"SA Best Score: {best_score:.2f}")
    print(f"SA迭代次数: {iteration_count}")
    print(f"SA停止原因: {stop_reason}")
    print(f"SA运行时间: {runtime:.2f} 秒")

    return best_result
