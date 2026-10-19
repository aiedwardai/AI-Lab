# Coding-Lean 画像（可选 · 接本栈 `:8000`）

> 本目录是**可选**组件：把 [Qwen Code](https://github.com/QwenLM/qwen-code) 编码 Agent 接到本栈的 `http://127.0.0.1:8000/v1` 端点，并套用参考仓库 `qwen3.8-27b-apple-silicon-agent` 的 **Coding-Lean** 思路（精简工具 schema、关掉 MCP 开销）。
>
> 本栈默认客户端是 Open WebUI + Obsidian；**仅当你想用 Qwen Code 做本地编码 Agent 时才需要本目录**。

## 它做了什么

- `baseUrl` 指向本栈的 `http://127.0.0.1:8000/v1`（而非参考仓库的 `11235`）。
- `model` 改为本栈 advertise 的 id `mtplx-qwen38-27b-optimized-quality`。
- `tools.disabled` 关掉 25 个非核心工具（agent / artifact / cron / web_fetch / 全部 MCP 等），`toolSearch.enabled=false` → 每请求工具 schema 从约 18.2K 降到约 5.3K token，在 64GB 跑 27B 时减轻 KV / 首 token 压力。

## 安装

```bash
# 假设 Qwen Code 画像目录为 ~/.qwen-coding-lean/
cp settings.example.json ~/.qwen-coding-lean/settings.json
```

## ⚠️ 与参考仓库的关键差异

1. **api-key 必须填真值**：本栈服务端带 api-key 鉴权，所以 `OPENAI_API_KEY` 必须填 `~/.mtplx/api-key` 里的**真实值**，**不能用**参考仓库的 `local` 占位。
2. **Qwen Code 版本敏感**：画像针对 Qwen Code 特定版本；升级后请重验字段。
3. **MTPLX 上下文**：`contextWindowSize: 81920` 是规划值；请以你实际部署的 MTPLX 上下文为准。

> 如果你只想要 Open WebUI / Obsidian，**可以忽略本目录**。
