# 排障

> 整理自参考机（M5 Max / 64GB）实测踩坑与 7×24 运维手册。每条给出「现象 → 根因 → 处理」。

## 一、部署期常见坑

| # | 现象 | 根因 | 处理 |
| --- | --- | --- | --- |
| 1 | `launchctl load` 报 `5: Input/output error` | 沙箱是非 GUI bootstrap 域，用户级 job 写/提交系统性不可用 | 移交用户在**真实 Terminal.app** 执行 `launchctl load`；会话内用后台进程临时托管 |
| 2 | `sudo ... operation not permitted` | 内核级 root 调用在沙箱不可达 | 需 root 的命令（如 `pmset`）如实告知用户在本地终端跑；只做只读核查 + 闭环验证 |
| 3 | 想用 `socat` 做中继却装不上 | 本机**无任何包管理器**（brew / macports / nix 均无） | 改为**纯 Python stdlib（`socket`+`threading`）全双工转发**（`scripts/mtplx-relay.py`），零外部依赖 |
| 4 | `huggingface.co` 拉权重 `HTTP 000` | 官方源本环境不可达 | 改 `HF_ENDPOINT=https://hf-mirror.com`（同一官方命名空间权重，供应链不变）；官方源恢复后删除该行 |
| 5 | 容器内连不到仅回环 MTPLX | `host.docker.internal` 解析到宿主桥接私有 IP，直连 `127.0.0.1` 够不到仅回环服务 | 选**方案 E 主机侧中继**（绑 `bridge100` 私有 IP → 转发 `127.0.0.1`），而非改 `--host 0.0.0.0` |
| 6 | 中继起不来：`Cannot assign requested address` | `host.docker.internal` 解析到 `0.250.250.254`（OrbStack 魔术网关地址，**不存在于宿主接口、不可绑**） | 中继要绑的是宿主 **`bridge100` 真实 IP**（`ifconfig bridge100`）；脚本已内置自动探测兜底 |
| 7 | Open WebUI 注册报 405 | 0.12 把 auth 挂在 `/api/v1/auths`，不是 `/api/auth/register` | 用 `POST /api/v1/auths/signup`（首用户自动 admin 并关公开注册） |
| 8 | 27B 返回 `content` 为空 | Qwen3 带 reasoning，思考占满 `max_tokens` 预算 | 给足 `max_tokens`（如 256+）即正常；这是 reasoning 占位，非故障 |
| 9 | 端口冲突导致 MTPLX 崩溃循环 | 会话内手动实例已占 8000，再 `launchctl load` 后 port in use | 切 launchd 前**先停会话内手动实例**（`TaskStop` / `pkill -f mtplx`），腾出端口 |
| 10 | `bash mtplx-serve.sh` + `| tee` 在 launchd 下裂出孤儿 pipe | 脚本派生进程族，`tee` 在沙箱裂出 toybox tee | plist 用 `StandardOutPath`/`StandardErrorPath` 接管日志 + `KeepAlive` + 脚本 `exec` 单进程 |
| 11 | Copilot v4 旧 Custom OpenAI 路径作废 | 新入口 = **BYOK** 标签（免费）；vault 语义检索改由 Miyo 承担 | 按 BYOK 接 MTPLX（Base=`127.0.0.1:8000/v1`，Key 必填，Model ID 手填，Enable CORS 兜底） |
| 12 | Smart Connections 自定义嵌入被收进 Pro | 免费核心版自带本地嵌入、零配置 | 先跑默认本地嵌入做"相关笔记"，再决定是否走自定义 MTPLX 嵌入 |

## 二、运行期检查

```bash
# 1. MTPLX 是否仅回环监听 8000
lsof -nP -iTCP:8000 -sTCP:LISTEN        # 应看到 127.0.0.1:8000（而非 *:8000 / 0.0.0.0）

# 2. 中继是否绑在桥接私有 IP（而非 0.0.0.0）
lsof -nP -iTCP:8000 -sTCP:LISTEN        # 中继应显示 <bridge100 IP>:8000

# 3. 探活 + 双模型验收
bash scripts/verify-mtplx.sh.example

# 4. 桥接 IP 是否变化（中继起不来时查）
ifconfig bridge100 | awk '/inet /{print $2}'
```

## 三、回退

```bash
# 停 MTPLX（launchd）
launchctl unload ~/Library/LaunchAgents/com.user.mtplx.plist
# 彻底清理（删除权重释放磁盘）
rm -rf ~/.mtplx/models/<模型目录>

# 停中继
launchctl unload ~/Library/LaunchAgents/com.user.mtplx-relay.plist
```

## 四、7×24 运维要点（摘）

- **电源**：固定供电桌面机可 `sudo pmset -c sleep 0`（系统常醒，显示器仍 10 分钟熄屏）；笔记本另评合盖策略。
- **断电恢复**：`sudo pmset -c autorestart 1`（需 UPS + 用户批准）；**不要拔电源做暴力测试**。
- **FileVault**：当前若关闭，离线物理窃取风险高；开启后重启可能卡解锁界面需人工——与安全/可用性权衡，默认不自动登录。
- **备份**：配置/数据加密备份到另一故障域；不把密码/令牌/私钥/恢复密钥放入普通文本备份。
- **告警**：至少覆盖服务连续失败、任务积压/超时、主机离线、磁盘低余量、备份失败、UPS 异常；告警不得含秘密。
- **变更闭环**：实施前记录现状+批准目标+回滚条件；实施后逐字段比对；PASS/PARTIAL/FAIL 判定，PARTIAL/FAIL 即暂停下一项。

详见 `7x24运行实施手册.md`（本地副本，未纳入本仓库）与 `docs/REPRODUCTION.md`。
