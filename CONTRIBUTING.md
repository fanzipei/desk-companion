# 贡献指南

PC 端需要 Windows 和 Python 3.12–3.14，设备端使用 ESP-IDF 5.3.2。安装、测试与刷写步骤见 [docs/development.md](docs/development.md)。

修改代码后运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
```

修改设备绘制、按钮或存储逻辑时，额外构建固件，并选择相关实机验收；没有设备时说明验证限制。请勿让测试覆盖用户便签、新闻、显示设置或提醒。

PR 描述应说明解决的问题、最终行为和验证结果。界面保持托盘优先，普通划选发送不得弹出主窗口或通知。新闻和 API 请求失败时应保留已有内容。

请勿提交 data、logs、backups、API 密钥、个人设备地址、截图或完整 Flash 镜像。引入或修改第三方代码时保留原版权和许可，更新 THIRD_PARTY_NOTICES.md。对原创代码的贡献按本项目 Apache-2.0 提交。
