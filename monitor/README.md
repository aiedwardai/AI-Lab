# 回环只读监控器（已实装）

一个 **stable、dependency-free、session-scoped** 的本地监控器，用于观察自研 `mtplx` 推理服务的运行状态。代码见 [`monitor.py`](./monitor.py)。

## 设计原则

- **仅绑定回环地址**（默认 `127.0.0.1`）。若 `QWEN3MON_HOST` 被设为非回环地址，进程直接 `SystemExit` 退出，绝不把监控面暴露到局域网或公网。
- **只读**：只做两件事——读取 MTPLX 的日志文件（`tail`）、向 `/v1/models` 发 `GET` 请求。它**绝不能**启动、停止、重启或代理推理服务，也不写入任何文件（除自身运行日志到 stderr 外）。
- **零第三方依赖**：仅用 Python 标准库，干净环境直接跑。

## 安全姿态（对齐项目安全基线）

- 调用 `/v1/models` 时从 `MTPLX_API_KEY_FILE` 读取 api-key 并以 `Bearer` 头发送；该文件权限应为 `600`，密钥不落库、不回显、不进入任何响应体。
- 所有 HTTP 响应均为服务端构造的聚合数据，不反射任何外部输入，无命令注入、无路径穿越。
- `/api/state` 不含任何密钥或权重路径。

## 用法

```bash
# 可选：自定义端口 / 推理端点 / 日志路径 / api-key 路径
export QWEN3MON_PORT=11236
export MTPLX_BASE_URL="http://127.0.0.1:8000/v1"
export MTPLX_API_KEY_FILE="$HOME/.mtplx/api-key"
export MTPLX_LOG_FILE="/path/to/mtplx.log"   # 不设置则只显示模型状态

python3 monitor/monitor.py
```

启动后：

- 仪表盘：`http://127.0.0.1:11236/`
- 状态 JSON：`http://127.0.0.1:11236/api/state`（含已加载模型、日志错误数、最近 N 行）
- 健康检查：`http://127.0.0.1:11236/healthz`

## 已知约束（诚实说明）

参考仓库 `qwen3.8-27b-apple-silicon-agent` 的 `monitor.py` 解析的是 **mlx-serve 专属日志格式**（如 `[spec-stats] mode=mtp`、`[hot-cache]`、`<- N+M tokens`）。本栈使用自研 `mtplx`，日志格式尚未在本仓固化，因此本监控器采用**通用解析策略**：仅做错误关键字匹配与最近行缓存，不假设特定字段。

若后续 MTPLX 输出结构化（如 JSON）日志，可在 `LogTailer._ingest` 中扩展抽取缓存 / prefill / decode / MTP 等指标，而不影响「只读 + 回环」语义。模型探针已预留 `mtp_loaded` 字段——当 `mtplx` 的 `/v1/models` 返回该标志时会自动展示。
