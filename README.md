# 桌面小伴侣 · Desk Companion

基于 **Waveshare ESP32-S3-RLCD-4.2** 的桌面便签与新闻屏，配套 Windows 托盘程序，通过 BLE 通信。PC 平时只显示托盘图标，按快捷键才打开输入或截图工具。

## 功能

- 划选文字静默发送；托盘附近的小输入框支持文字及 Windows 语音输入。
- 框选截图：纯文本完整转录，聊天界面按语境整理，保留分项与简单表格。
- 便签和新闻支持翻页、置顶、删除及在 PC 打开，设备 Flash 持久保存。
- Hacker News + GDELT 每源 10 分钟刷新、错开 5 分钟；默认共 20 条。
- 新闻接口、正文、相关报道检索共用可配置代理；OpenAI 兼容 API 可生成 60–120 汉字简讯。
- AI 提议提醒时间，由用户确认；同一分钟自动合并，设备到点定位便签并闪屏，断连仍可提醒。
- 自定义显示主题、字号、行距、底部提示；显示日期时间、蓝牙与电池图标；PC 约每 4 小时校时。

## 快速开始

PC 需要 Windows 10/11、蓝牙适配器和 Python 3.12–3.14（包含 Tk）。固件针对 ESP-IDF 5.3.2、ESP32-S3、16 MB Flash / 8 MB PSRAM。

```powershell
.\setup-pc.ps1
.\启动小伴侣.bat
```

设备需要先运行本项目固件，详见[固件编译与备份](docs/development.md)。右键托盘 → 设置；蓝牙地址留空时自动发现 DeskCompanion，存在多个设备时可填写目标地址。已有本地配置会保留。

| 操作 | 快捷键 |
| --- | --- |
| 发送选中文字 | Ctrl+Alt+J |
| 打开输入框 | Ctrl+Alt+空格 |
| 框选截图识别 | Ctrl+Alt+K（需验证图片 API） |
| 给最后一条已送达便签设置提醒 | Ctrl+Alt+L |

设备按键：BOOT 下一条、KEY 上一条，双击 KEY 切换模式，双击 BOOT 在 PC 打开，长按 BOOT 删除，长按 KEY 置顶。PWR 保留硬件电源功能。

新闻代理位于“设置 → 新闻”：自动（系统/直连）、自定义代理或不使用代理。自定义填写如 `http://127.0.0.1:7897` 的 HTTP/HTTPS 代理地址；自定义模式不会在失败后自动绕过代理。

## 目录

```text
pc/                    Windows 托盘、BLE、新闻、截图与提醒
device/               ESP-IDF 固件与第三方显示组件
tests/                PC 单元测试及显式运行的硬件验收
tools/                固件编译、备份与刷写脚本
docs/                 使用、开发、许可说明
LICENSES/             第三方授权及字体例外文本
.github/workflows/    Windows 自动测试
```

详细说明：[使用指南](docs/usage.md) · [开发与刷写](docs/development.md) · [贡献指南](CONTRIBUTING.md)。

`data/`、`logs/`、`backups/`、早期测试固件和构建输出保留在本机并由 Git 忽略。请勿把便签、截图、密钥、Flash 备份或个人配置放入 Issue / PR。

## License

项目原创代码采用 **GPL-3.0-only**，全文见 [LICENSE](LICENSE)。第三方驱动、U8g2 与字体继续适用各自原始许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

GPLv3 允许商业使用；分发衍生版本须按许可向接收者提供相应源码，私人使用和内部修改不要求公开。选择理由与 MIT / Apache-2.0 的比较见[许可说明](docs/licensing.md)。本仓库不对微雪硬件设计、商标或第三方新闻内容授予权利。
