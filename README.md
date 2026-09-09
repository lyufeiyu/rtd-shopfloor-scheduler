# RTD 车间排产系统

新版界面、排产进度、助手用法与 LLM 接入边界见 [智能排产工作台说明](docs/智能排产工作台.md)。

RTD 是一个在本机运行的车间排产系统。启动后通过浏览器访问，支持上传 `.xlsx` 数据集、查看设备与任务概况、执行快速或均衡排产，并下载 Excel、图片和压缩包结果。

## 首次安装

需要 Python 3.9 或更高版本。建议从 Python 官网安装，并在 Windows 安装界面勾选 **Add Python to PATH**。

在项目根目录安装依赖：

```powershell
# Windows PowerShell
py -3 -m pip install -r requirements.txt
# 如果系统没有 py 启动器，也可使用：python -m pip install -r requirements.txt
```

```bash
# macOS 终端
python3 -m pip install -r requirements.txt
```

## Windows

- 启动：双击 `启动RTD-Windows.cmd`
- 停止：双击 `停止RTD-Windows.cmd`

双击启动入口后，后台服务会隐藏运行，服务就绪后直接打开浏览器页面，适合现场展示。`.cmd` 自身可能出现极短的系统窗口闪烁，这是 Windows 启动批处理时的正常现象；启动失败时会保留提示窗口并指向错误日志。

如果演示时希望完全不出现控制台窗口，也可以直接双击 `start-rtd-windows.vbs`；它与 `.cmd` 使用同一套启动逻辑。

也可以在项目根目录打开 PowerShell：

```powershell
.\启动RTD-Windows.ps1
.\停止RTD-Windows.ps1
```

脚本会自动选择 Python、检查依赖、从端口 `8765` 启动服务，并打开浏览器。如果端口已被其他程序占用，会顺延选择可用端口。

需要指定 Python 或端口时：

```powershell
$env:RTD_SERVICE_PYTHON = 'C:\Python312\python.exe'
$env:RTD_ALGORITHM_PYTHON = 'C:\Python312\python.exe'
$env:RTD_PORT = '9000'
.\启动RTD-Windows.ps1
```

## macOS

- 启动：双击 `启动RTD-macOS.command`
- 停止：双击 `停止RTD-macOS.command`

原来的未带平台名称的 `.command` 启动和停止脚本仍然保留。新增的带 `macOS` 后缀版本与 Windows 入口在名称上明确区分，建议后续使用新名称。

macOS 第一次运行时如果系统拦截，请在 Finder 中右键脚本并选择“打开”。也可以先赋予执行权限：

```bash
chmod +x 启动RTD-macOS.command 停止RTD-macOS.command
./启动RTD-macOS.command
```

## 使用流程

1. 启动成功后，浏览器会打开 RTD 页面；默认地址为 `http://127.0.0.1:8765`。
2. 上传符合系统格式要求的 `.xlsx` 数据集。
3. 查看设备、WIP、待排批次和数据提示，按需选择工站。
4. 选择“快速生成”或“均衡优化”，提交排产任务。
5. 等待任务完成后查看设备分配、未排原因和甘特图，并下载结果文件。
6. 使用对应平台的停止脚本关闭后台服务。

服务仅监听本机地址 `127.0.0.1`，不会直接向局域网或互联网开放。日志位于 `storage/logs/`；Windows 启动器的标准错误日志为 `storage/logs/launcher-windows-error.log`。

## 本地数据与 GitHub

`storage/` 是每台电脑独立的运行数据目录，其中包含上传的生产数据、SQLite 状态库、排产结果和日志。该目录可能包含企业生产数据，且数据库记录必须与 `datasets/`、`runs/` 中的文件保持一致，因此**不应上传到 GitHub**。

拉取或克隆代码后，首次启动会自动创建空的 `storage/`。需要迁移历史数据时，应先停止 RTD，再通过可信的内部存储介质整体复制 `storage/`，不要只复制 `state.sqlite`。如果数据库中存在记录但对应文件缺失，服务会跳过这些失效记录，并在页面顶部显示恢复提示。

更详细的接口、数据口径和验收说明见 [frontend/README.md](frontend/README.md)。
