# 电脑与独立桌面

## 环境与配置

使用 Windows 10/11、Python 3.12 和系统 .NET Framework C# 编译器。Node.js 用于检查看板 JavaScript。微信自动化依赖具体微信版本与 `wechatauto-replica` 的兼容情况。

按根目录 README 创建虚拟环境，安装依赖，并复制 `.env.example` 和 `desktop/bot-config.example.json`。所有相对文件路径以仓库根目录为基准；密钥可使用 `${ENV_NAME}` 引用，不需要写入公开配置。

| 配置 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 主聊天与日程模型 |
| `MIMO_API_KEY` | 辅助文字模型 |
| `DASHSCOPE_API_KEY` | 按需启用的图片、视觉和语音服务 |
| `WECHAT_PRIVATE_CHAT` | 私聊对象，多个对象用逗号分隔 |
| `WECHAT_GROUP_CHATS` | 群名，多个群用逗号分隔 |
| `HEALTH_PUSH_TOKEN` | 自己生成并与手机一致的健康令牌 |
| `DATA_DIR` | 本机数据目录；留空使用仓库内 `data/` |
| `FFMPEG_BIN`、`FFPROBE_BIN`、`SILK_DECODER` | 自己安装的音视频工具 |

模型名称、地址和功能开关在本地 JSON 中设置，需使用服务商当前提供且账号有权调用的模型。示例配置为空聊天列表，关闭健康、图片、视觉、语音、主动消息和日程；需要的功能由使用者自行启用。`--check-config` 仅做本地检查，不验证余额或远程模型可用性，也不连接微信。

默认人格文件是 `personas/group-friend.md`，可改为 `personas/furina.md`。个人提示词放在被忽略的 `personas/local/`。参考图放在被忽略的 `assets/refs/`，再配置本地 `image.refs`。

## 构建与首次登录

```powershell
python tools/build-dashboard.py
.\tools\build-desktop.ps1
.\desktop\isolated-desktop\WeChatDesktop.exe
```

首次使用先在启动器连接本机子会话，执行隔离检查，然后启动微信并登录机器人账号，完成只读检查与本地账号绑定后再启动机器人。绑定文件只留在本机。

此功能依赖 Windows 本机子会话组件，不同 Windows 版本和系统策略可能不支持。若预检报告会话服务被禁用，可在理解本机改动后，由管理员运行 `desktop/isolated-desktop/Enable-LocalChildSession.ps1`。脚本仅恢复三个本机会话服务，要求外部远程桌面处于关闭状态，不修改防火墙。不要为了使用此功能开放公网远程桌面。

微信窗口、输入与鼠标自动化发生在独立会话中。父会话注销会结束子会话；锁屏、微信升级或掉线后应查看预检和诊断状态。构建通过不能代替具体电脑上的隔离检查。

## 日常一键打开

首次登录、绑定和预检通过后，运行：

```powershell
.\desktop\isolated-desktop\start-suite.ps1
```

启动控制器复用已有会话与机器人，在后台准备子桌面，打开默认浏览器的 `http://127.0.0.1:8765/console`，并把独立桌面窗口收起到托盘。首次登录、身份不符或预检失败会提示处理。

可给构建产生的 `desktop/isolated-desktop/OpenCompanion.exe` 创建桌面快捷方式，目标和“起始位置”均指向自己的安装目录。快捷方式不随源码分发。`COMPANION_PYTHON`、`COMPANION_PWSH` 可覆盖本机运行时位置。

## 诊断与数据

群不回复时，在消息诊断中依次检查收到、触发、生成、发送。模型页面显示真实配置、错误与耗时；原始输入和服务商实际返回的文本可能包含个人内容，勿直接上传故障日志。

日程页提供重新生成、剩余时间重排、锁定和版本恢复。重试会产生新的任务状态，失败信息可在看板检查。日程是角色的虚构活动计划。

浏览器看板仅允许本机访问，健康推送入口单独使用令牌。关闭浏览器不会停止机器人，可通过独立桌面托盘查看运行状态。迁移或删除本地数据前自行备份；公开仓库不携带运行历史、健康记录、设备绑定或密钥。
