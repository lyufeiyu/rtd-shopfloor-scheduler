# RTD 车间排产产品原型

这是一个前后端分离的本地产品粗糙版原型。浏览器只展示数据概况、设备状态、排产结果和甘特图.算法源码与后台日志不通过页面暴露。

## 一键启动（推荐）

在 Finder 中双击项目根目录的 `启动RTD.command`。它会自动启动本地服务并打开浏览器；用户不需要区分前端和后端。

关闭时双击 `停止RTD.command`。运行日志仍由系统写入 `storage/logs/`，正常使用不需要查看。

如果 macOS 第一次阻止脚本运行，可以右键文件选择“打开”，或在终端执行：

```bash
RTD_ALGORITHM_PYTHON=/Users/feiyulv/miniconda3/bin/python \
  /Users/feiyulv/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python \
backend/server.py --port 8765
```

启动器默认使用本机兼容环境；如果默认端口被占用，会自动顺延到下一个端口并打开正确页面。部署到其他环境时，建议使用同一个 Python 环境安装 `pandas`、`numpy`、`openpyxl`、`matplotlib` 后直接运行 `启动RTD.command`。

## 使用流程

1. 上传 `.xlsx` 数据集，服务端校验文件类型、大小、必要工作表和关键列。
2. 查看设备、WIP、待排批次和数据提示；可按工站选择范围，并选择“快速生成”或“均衡优化”。
3. 提交排产任务。后台异步执行，页面只显示任务状态；完成后可查看设备分配、未排原因、工站甘特图并下载工作簿或压缩包。

后台日志保存在 `storage/logs/`，每次任务还有独立 worker 日志；前端没有日志查看入口。上传原文件和生成结果保存在 `storage/datasets/`，不应提交到 Git。

## 接口契约

- `POST /api/datasets`：上传 Excel，返回数据集编号、快照、表统计、工站和 warnings。
- `POST /api/jobs`：提交 `{dataset_id, strategy, station}`，返回排产任务编号。
- `GET /api/jobs/{id}`：查询 `queued/running/succeeded/failed` 状态。
- `GET /api/jobs/{id}/result`：读取结果摘要、设备分配、未排原因和文件清单。
- `GET /api/jobs/{id}/files/{name}`：下载 Excel、PNG 或 ZIP 结果。

前端只依赖这些业务接口；算法代码、worker 日志和原始内部对象不属于公开接口。

## 当前数据口径

本次实际校验的 `algorithm/RTD_Dataset_v6.xlsx` 有 10 张表、907 台去重设备、319 条 WIP、916 条待排任务。数据中没有单独的“工站-设备表”，系统会从“5-工艺路线-设备表”重建工站映射并给出提示；`DB-72` 两条设备记录互相冲突，因此被标记为“数据冲突”并禁用。无下一工站的 669 条流转记录、与 WIP 重叠的 275 条记录不会重复排产。排不下的任务仍会输出明确原因。

浏览器每次打开都是一个新的“本次上传会话”：服务端不会自动把历史数据集加载到页面，必须重新上传并通过格式检查后，设备、WIP、批次等统计才会出现。停止服务后，页面会在心跳检查失败时清空当前数据并提示重新启动。

## 三人协作安排

| 角色 | 负责目录 | 交付物 |
|---|---|---|
| A：前端产品 | `frontend/index.html`、`frontend/app.js`、`frontend/style.css` | 上传、数据概况、设备台账、排产状态、结果表、甘特图和下载；不接触算法源码 |
| B：服务端平台 | `backend/server.py`、`storage/` 结构、部署文档 | 上传校验、SQLite 任务状态、异步 worker、日志、结果下载和安全边界 |
| C：数据与算法 | `algorithm/product_data.py`、`product_runner.py`、`decoder.py`、调度器 | Excel 解析、约束过滤、快速/均衡调度、结果校验、Excel/PNG/ZIP 输出 |

三人的合并顺序是：C 先稳定输入输出和命令行；B 固定接口并接入 C；A 只按接口联调页面。三人共同遵守同一条边界：上传返回 dataset 元数据，提交返回 job，前端通过 job 状态轮询，结果通过 result/files 获取。这样可以分别开发、最后拼接，不需要把算法代码复制到浏览器。

## 验收

运行 `node backend/check-product.cjs` 可进行一次产品级 smoke test，覆盖上传、设备台账、指定 WF 工站排产、结果页和浏览器错误检查。`RTD_V3` 目录保持不改动；`frontend/preview-*.png` 已删除，当前页面使用独立的暖纸色/炭黑/铜橙视觉系统。
