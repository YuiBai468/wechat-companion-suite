# 看板源码

在仓库根目录执行 `python tools/build-dashboard.py`，生成 `desktop/console.html`。Node.js 用于检查内联 JavaScript 的语法。

- `dashboard-redesign.html`：基础页面及原有内容。
- `dashboard-v2-*`：运动健康与逐模型原始调用。
- `life-retry.*`：异步重新生成日程。
- `companion-upgrade-*`：芙宁娜、消息诊断、记忆与承诺、日程锁定/局部重排/恢复、模型统计。

生成页面不依赖 CDN、外部字体或聊天工作目录。后端接口不可用时显示缺失或连接错误，不使用演示数据冒充真实记录。
