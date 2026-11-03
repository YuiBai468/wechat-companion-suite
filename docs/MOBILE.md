# 手机、Gadgetbridge 与小米手环

`mobile/gadgetbridge/` 包含定制 Android 应用的完整跟踪源码，保留上游版权、AGPL-3.0-or-later 和美术资源许可。本仓库不包含手机备份、配对密钥、数据库、APK、调试密钥库或个人设备信息。

## 构建

安装 Android Studio 或命令行 Android SDK，以及适用于当前 Gradle/Android 插件的 JDK。建议 JDK 21；应用源码的 Java 兼容级别为 17。仓库当前使用 Android API 37.0，具体版本以 `app/build.gradle`、`gradle/libs.versions.toml` 和 Gradle wrapper 为准。

```powershell
Set-Location mobile/gadgetbridge
# JAVA_HOME 和 ANDROID_HOME 指向自己安装的 JDK、SDK
.\gradlew.bat :app:assembleMainlineDebug --no-daemon --console=plain
```

Linux 可用 `./gradlew`。输出在 `app/build/outputs/apk/` 下。首次构建会下载依赖，需能访问构建文件中列出的官方仓库和 JitPack。调试构建会使用本机新生成的调试签名；不要提交签名文件。仓库提供手动触发的 Android 构建工作流作为独立检查入口，不分发预签名 APK。

公开仓库重新建立干净历史，不能使用原私有仓库提交数量推断手机安装版本。首次安装与覆盖更新前检查 applicationId、versionCode 和签名是否匹配。签名不同不能直接覆盖原应用；先通过应用支持的方式备份自己的数据，再决定迁移方法。本次开源没有操作任何已配对手机。

## 健康推送设置

1. 用自己的手机和手环完成 Gadgetbridge 配对及授权。
2. 电脑本地 JSON 中启用 `health.enabled`，并设置非空 `HEALTH_PUSH_TOKEN`。
3. 在手机的健康推送设置中填写电脑的局域网地址，例如 `http://YOUR_PC_LAN_IP:8765/health`。
4. 手机填入同一个令牌，再启用推送。不要使用 `127.0.0.1` 指向另一台电脑。
5. 打开电脑的健康看板确认上报、真实蓝牙连接状态、电量和测量时间。

默认推送关闭，地址和令牌为空。令牌输入框使用密码显示方式。推送为 HTTP JSON，令牌通过 `X-Token` 发送；在可信私有网络中使用。不要把控制台暴露到公网。网络或防火墙放行由使用者按自己的环境配置，源码不会自动开放防火墙。

连接与电量来源于真实 Gadgetbridge 设备状态变化，空闲时有约 120 秒心跳；不会发送蓝牙 MAC 地址。心率、步数、睡眠、压力、血氧等能力取决于设备、权限和实际测量，缺失指标不能当作零值。连接失败和历史测量过期是不同状态。

健康页是个人记录展示，不用于诊断或医疗决策。若断线，先查看手机蓝牙连接、后台运行限制和端点配置，再查看电脑接收日志。分享日志前去除聊天、健康测量、令牌和设备名称。

## 来源与构建范围

上游通用组件及其测试夹具随源码保留，不代表作者个人数据。`.gitmodules` 保留上游可选组件声明；主应用构建是否使用某项由 Gradle 工程决定，不要求复制原作者工作目录。实际验证状态见 [发布验证](RELEASE_VALIDATION.md)。
