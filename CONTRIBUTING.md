# 参与开发

请使用临时目录和合成数据测试，不使用个人账号、真实聊天、健康数据库或生产设备绑定作为测试夹具。

提交前运行 Python 测试、前端构建和隐私检查。涉及消息发送的测试应注入假发送器；涉及模型的测试应使用假响应。配置、数据迁移和版本恢复需要覆盖失败与并发分支。

前端源码在 `desktop/dashboard-src/`，通过 `tools/build-dashboard.py` 生成 `desktop/console.html`。提交前同时更新两者。

修改手机部分时保留 Gadgetbridge 的许可证和版权声明。不要提交构建出的 APK、签名材料、`local.properties` 或设备导出。
