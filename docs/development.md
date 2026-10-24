# 开发与设备更新

## PC 程序

在 Windows 安装 Python 3.12–3.14，包含 Tk，执行 `setup-pc.ps1`，再运行 `启动小伴侣.bat`。依赖为 bleak、pystray 和 Pillow；模型请求使用标准库，不依赖特定厂商 SDK。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
```

这些单元测试不连接实体板卡、不访问新闻或大模型服务。`.github/workflows/pc-tests.yml` 在 Windows 上运行相同测试。

`pc/core.py` 管理本地缓存及发送队列，`pc/bridge.py` 负责 BLE、新闻和简讯线程，`pc/news_network.py` 管理新闻代理。截图与 API 能力验证位于 `pc/ai.py`，独立取词进程位于 `pc/selection.py`。

本地配置保存在 `data/state.json`；API 密钥以当前 Windows 用户的 DPAPI 加密保存在 `data/news-api-key.dpapi`。不要将这两个文件或完整 Flash 备份上传到仓库。删除 `.venv` 不会移除便签或密钥。

## ESP-IDF 固件

使用 ESP-IDF **v5.3.2** 和 ESP32-S3 工具链，目标板为 16 MB Flash / 8 MB PSRAM 的 ESP32-S3-RLCD-4.2。在已激活的 ESP-IDF PowerShell 中：

```powershell
cd device
idf.py -B "$env:TEMP\desk-companion-idf-build" build
```

或使用仓库脚本，它会将产物复制到 `device/dist/`：

```powershell
.\tools\build-device.ps1
# SDK 不在用户目录的 esp/ 时，可指定位置：
.\tools\build-device.ps1 -EspRoot 'D:\esp'
```

脚本优先使用已设置的 `IDF_PATH`、`IDF_TOOLS_PATH`、`IDF_PYTHON_ENV_PATH`。本机备用路径按 `~/esp/esp-idf` 与 `~/esp/tools/python_env/idf5.3_py3.12_env` 布局解析。使用 SDK 激活脚本设置好 CMake、Ninja、编译器路径后，可使用其他安装布局。

构建目录放在系统临时目录，避免旧工具链处理中文构建路径时出错。源码与配置均在本仓库，第三方来源和修改说明见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 先备份，再刷写

先退出托盘程序，释放 BLE/串口；通过设备管理器确认 COM 口。以下用 COM6 举例，需换成实际串口。

```powershell
.\tools\backup-device.ps1 -Port COM6
.\tools\build-device.ps1
.\tools\flash-companion.ps1 -Port COM6
```

备份脚本读取完整 16 MB Flash，保存 `backups/original-full-16MB.bin` 和 SHA256 清单。已有完整备份只校验、不覆盖。Flash 备份可能含设备数据和凭证，整个 `backups/` 由 Git 忽略。非标准 SDK Python 路径可用 `-Python '路径\python.exe'` 指定。

正常更新写入 bootloader、分区表和应用，保留 NVS 及专用便签存储区。设备数据使用独立键：原始便签布局、`news_v2`、`display_v1`、`reminders_v1`。现有固件未启用 Secure Boot 或 Flash Encryption，可用同一套开源工具安装修改后的固件。

恢复命令 `tools/flash-companion.ps1 -RestoreOriginal` 会恢复备份时的应用及存储区，**移除备份以后的小伴侣数据**。执行前另存当前数据；不要把它当作普通更新命令。该命令的应用回滚范围为从 Flash 起始的 2 MB，正常刷写会检查应用大小不超出此范围。

## 实机验收

`tests/*_device.py`、`tests/display_reboot.py` 等脚本会实际操作硬件，须手动运行，默认使用本地设置中的蓝牙地址或自动发现 DeskCompanion。可用 `DESK_COMPANION_ADDRESS` 指定目标；同时存在多个设备时应明确指定。

```powershell
$env:DESK_COMPANION_ADDRESS = 'AA:BB:CC:DD:EE:FF'
.\.venv\Scripts\python.exe -m tests.display_device
.\.venv\Scripts\python.exe -m tests.clock_reminder_device
```

部分重启测试使用 COM6，请先核对脚本与串口。验收结果和完整记录默认保存在忽略的 `logs/` 中，不作为公开报告提交；`docs/images/` 单独保留经过筛选的合成输入图与临时测试便签帧缓冲，来源见该目录说明。硬件脚本会保留用户数据，并在没有空位或存在用户提醒时拒绝覆盖测试。

## Git 与发布

GitHub 源码仓库为 https://github.com/fanzipei/desk-companion ，默认分支为 `main`。提交与推送前先检查 `git status`、暂存内容与忽略规则，确保未包含本机数据或凭证。

发布固件二进制时应保留 LICENSE、NOTICE 和适用第三方声明，并依据所包含组件的条款提供对应源码、构建脚本等材料。根目录 Apache-2.0 不替换第三方字体或依赖的授权，见 [许可说明](licensing.md)。
