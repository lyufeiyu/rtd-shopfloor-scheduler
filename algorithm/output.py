import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from openpyxl.utils import get_column_letter
import os

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False


def plot_gantt(result, current_time, save_path=None, show=False):
    schedule = result["schedule"]
    initial_wip = result["initial_wip"]
    current_time = pd.Timestamp(current_time)

    # 只画实际有任务的机器
    used_machines = set()

    if not initial_wip.empty:
        used_machines.update(initial_wip["machine"].tolist())

    if not schedule.empty:
        used_machines.update(schedule["设备"].tolist())

    used_machines = sorted(used_machines)

    if not used_machines:
        print("没有可绘制的排产结果")
        return

    y_pos = {machine: i for i, machine in enumerate(used_machines)}

    fig_height = max(6, len(used_machines) * 0.38)
    fig, ax = plt.subplots(figsize=(18, fig_height))

    # =========================================================
    # 初始 WIP
    # =========================================================
    if not initial_wip.empty:
        for _, row in initial_wip.iterrows():
            machine = row["machine"]

            if machine not in y_pos:
                continue

            start = max(pd.Timestamp(row["start_time"]), current_time)
            finish = pd.Timestamp(row["finish_time"])

            if finish <= current_time:
                continue

            ax.barh(
                y_pos[machine],
                finish - start,
                left=start,
                height=0.65,
                alpha=0.35,
                hatch="//"
            )

    # =========================================================
    # 新排任务
    # =========================================================
    if not schedule.empty:
        for _, row in schedule.iterrows():
            machine = row["设备"]

            if machine not in y_pos:
                continue

            start = pd.Timestamp(row["开始时间"])
            finish = pd.Timestamp(row["完成时间"])

            ax.barh(
                y_pos[machine],
                finish - start,
                left=start,
                height=0.65
            )

            # 时间较长的任务才显示 job_id，避免文字挤在一起
            duration_minutes = (finish - start).total_seconds() / 60

            if duration_minutes >= 120:
                middle = start + (finish - start) / 2

                ax.text(
                    middle,
                    y_pos[machine],
                    str(row["job_id"]),
                    ha="center",
                    va="center",
                    fontsize=6
                )

    # 排产开始时间
    ax.axvline(
        current_time,
        linestyle="--",
        linewidth=1.5,
        label="排产开始时间"
    )

    ax.set_yticks(range(len(used_machines)))
    ax.set_yticklabels(used_machines, fontsize=8)
    ax.invert_yaxis()

    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%m-%d\n%H:%M")
    )

    # 自动确定 X 轴范围
    finish_times = []

    if not initial_wip.empty:
        finish_times.extend(
            pd.to_datetime(initial_wip["finish_time"]).tolist()
        )

    if not schedule.empty:
        finish_times.extend(
            pd.to_datetime(schedule["完成时间"]).tolist()
        )

    if finish_times:
        max_finish = max(finish_times)

        ax.set_xlim(
            current_time - pd.Timedelta(minutes=20),
            max_finish + pd.Timedelta(minutes=30)
        )

    ax.set_xlabel("时间")
    ax.set_ylabel("设备")
    ax.set_title("单工站排产甘特图")

    ax.grid(axis="x", linestyle="--", alpha=0.25)
    ax.legend(loc="upper right")

    plt.tight_layout()

    # =========================================================
    # 保存图片
    # =========================================================
    if save_path:
        directory = os.path.dirname(save_path)

        if directory:
            os.makedirs(directory, exist_ok=True)

        plt.savefig(
            save_path,
            dpi=200,
            bbox_inches="tight"
        )

        print(f"甘特图已保存: {save_path}")

    # 是否同时弹出图片
    if show:
        plt.show()

    plt.close(fig)



def export_schedule(result, station, output_path=None):
    if output_path is None:
        output_path = f"{station}_排产方案.xlsx"

    schedule_df = result["schedule"].copy()
    initial_wip_df = result["initial_wip"].copy()
    unscheduled_df = result["unscheduled_jobs"].copy()

    # 排产方案
    if not schedule_df.empty:
        schedule_df = schedule_df[
            [
                "job_id", "制造单号", "批次", "优先级",
                "工艺路线", "工站", "数量", "设备", "UPH",
                "开始时间", "完成时间", "加工时间(分钟)"
            ]
        ]

        # pandas Timestamp -> Python datetime
        for col in ["开始时间", "完成时间"]:
            schedule_df[col] = pd.to_datetime(
                schedule_df[col]
            ).dt.strftime("%Y-%m-%d %H:%M:%S")

    # 初始 WIP
    if not initial_wip_df.empty:
        initial_wip_df = initial_wip_df.rename(columns={
            "machine": "设备",
            "route": "工艺路线",
            "quantity": "数量",
            "start_time": "投产时间",
            "finish_time": "预计完成时间"
        })

        for col in ["投产时间", "预计完成时间"]:
            if col in initial_wip_df.columns:
                initial_wip_df[col] = pd.to_datetime(
                    initial_wip_df[col]
                ).dt.strftime("%Y-%m-%d %H:%M:%S")

    # 未排产任务
    if not unscheduled_df.empty:
        keep_cols = [
            col for col in
            ["job_id", "制造单号", "批次", "工艺路线", "下一工站", "数量"]
            if col in unscheduled_df.columns
        ]
        unscheduled_df = unscheduled_df[keep_cols]

    # 写 Excel
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        schedule_df.to_excel(writer, sheet_name="排产方案", index=False)
        initial_wip_df.to_excel(writer, sheet_name="初始WIP", index=False)
        unscheduled_df.to_excel(writer, sheet_name="未排产任务", index=False)

        for sheet_name in writer.book.sheetnames:
            ws = writer.book[sheet_name]

            # 来自上传数据的文本不允许被 Excel 解释为公式。
            for cells in ws:
                for cell in cells:
                    if cell.data_type == 'f':
                        cell.data_type = 's'

            # 冻结首行
            ws.freeze_panes = "A2"

            # 筛选
            if ws.max_row > 1:
                ws.auto_filter.ref = ws.dimensions

            # 获取表头
            headers = {
                ws.cell(1, col).value: col
                for col in range(1, ws.max_column + 1)
            }

            # 自动列宽
            for col in range(1, ws.max_column + 1):
                max_len = 0

                for row in range(1, ws.max_row + 1):
                    value = ws.cell(row, col).value
                    if value is not None:
                        max_len = max(max_len, len(str(value)))

                width = min(max(max_len + 2, 10), 30)
                ws.column_dimensions[get_column_letter(col)].width = width

            # 时间格式 + 加宽
            for name in ["开始时间", "完成时间", "投产时间", "预计完成时间"]:
                if name in headers:
                    col = headers[name]
                    ws.column_dimensions[get_column_letter(col)].width = 21

                    # for row in range(2, ws.max_row + 1):
                    #     ws.cell(row, col).number_format = "yyyy-mm-dd hh:mm:ss"

            # 加工时间保留两位小数
            if "加工时间(分钟)" in headers:
                col = headers["加工时间(分钟)"]

                for row in range(2, ws.max_row + 1):
                    ws.cell(row, col).number_format = "0.00"

    print(f"排产方案已输出: {output_path}")
