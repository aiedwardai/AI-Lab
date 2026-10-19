# 复现与持久化（launchd）

> 整理自参考机已落地步骤（`2026-10-15 工作小结`）与 `7x24运行实施手册.md`。
> 本文件给出**可复现的命令与模板**；涉及 `sudo`、系统服务、网络变更的命令，须逐项由用户确认后再执行。

## 一、实施阶段（参考机已 PASS）

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| Phase 0 | 环境核查（M5 Max / 64GB / arm64） | PASS |
| Phase 1 | 隔离 venv 装 `mtplx 2.12.2` | PASS |
| Phase 2 | `mtplx pull` 27B(30GB)+4B，三接口验证（models/embeddings/chat） | PASS |
| Phase A | MTPLX 由 launchd 常驻（仅回环） | PASS |
| Phase C | 纯 Python 中继 → Open WebUI 0.12.2 容器 → 端到端对话 200 | PASS |
| Phase B | Obsidian Smart Connections + Copilot 接本地端点 | 配置完成 |

## 二、MTPLX 守护进程（launchd）

先复制并编辑 `scripts/mtplx-serve.sh.example`，填入你的 venv 绝对路径，再托管：

```bash
cp scripts/mtplx-serve.sh.example scripts/mtplx-serve.sh
# 编辑 MTRLX_BIN / GEN_MODEL / EMB_MODEL
```

`~/Library/LaunchAgents/com.user.mtplx.plist`（**模板，请改绝对路径**）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.mtplx</string>
    <key>ProgramArguments</key>
    <array>
        <string>/ABS/PATH/TO/scripts/mtplx-serve.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/你的用户名/.mtplx/logs/mtplx.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/你的用户名/.mtplx/logs/mtplx.err.log</string>
</dict>
</plist>
```

加载与验证（在真实 Terminal.app 执行）：

```bash
launchctl load ~/Library/LaunchAgents/com.user.mtplx.plist
launchctl list | grep com.user.mtplx        # 状态码 0 或有效 PID
lsof -nP -iTCP:8000 -sTCP:LISTEN            # 应看到 127.0.0.1:8000
curl -s -H "Authorization: Bearer $(cat ~/.mtplx/api-key)" http://127.0.0.1:8000/v1/models
```

## 三、TCP 中继（launchd）

`~/Library/LaunchAgents/com.user.mtplx-relay.plist`（**模板**）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.mtplx-relay</string>
    <key>ProgramArguments</key>
    <array>
        <string>/ABS/PATH/TO/your/python3</string>
        <string>/ABS/PATH/TO/scripts/mtplx-relay.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MTPLX_RELAY_BIND_IP</key>
        <string>192.168.139.3</string><!-- 改为 ifconfig bridge100 的真实 IP -->
        <key>MTPLX_RELAY_PORT</key>
        <string>8000</string>
        <key>MTPLX_BACKEND_PORT</key>
        <string>8000</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/你的用户名/.mtplx/logs/relay.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/你的用户名/.mtplx/logs/relay.err.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.user.mtplx-relay.plist
lsof -nP -iTCP:8000 -sTCP:LISTEN   # 应看到 <bridge100 IP>:8000（而非 127.0.0.1）
```

> 中继已增强为「绑定失败自动重试、不崩溃」：桥接接口晚于中继启动时也会自动接管。

## 四、验证闭环

每项变更后立即记录：**实施前**现状+批准目标+备份位置；**实施后**逐字段比对+错误日志+只读探针；**判定** PASS / PARTIAL / FAIL（PARTIAL/FAIL 即暂停下一项）；**留痕**脱敏输出摘要，不得记录凭据。

## 五、回退

```bash
launchctl unload ~/Library/LaunchAgents/com.user.mtplx.plist
launchctl unload ~/Library/LaunchAgents/com.user.mtplx-relay.plist
rm -rf ~/.mtplx/models/<模型目录>     # 删除权重释放磁盘
```
