# 第二大脑（Obsidian `brain` vault）— Smart Connections + Copilot

> 目标：在本地 MTPLX（`127.0.0.1:8000`，27B 聊天 + 4B 嵌入）之上，把 `~/Documents/obsidian/brain` 变成可语义检索、可对话的第二大脑。
> 适用：macOS / Obsidian 已装 / `brain` vault 已建 / MTPLX 已在线。

## 一、版本事实（决定选型）

| 组件 | 当前状态 | 对本案含义 |
| --- | --- | --- |
| **Smart Connections 核心（免费）** | 自带**本地嵌入模型**，零配置即可语义检索；不校验外部 API | 免费即可满足"语义检索" |
| **Smart Chat** | 已移至 **Smart Chat Pro（付费）** | 免费核心版不能"聊 vault" |
| **Copilot for Obsidian（免费）** | 支持 `Custom OpenAI` / BYOK 端点，可做 vault QA | 免费实现"笔记对话"的最佳选择 |

→ 第二大脑 = **Smart Connections（检索）+ Copilot（对话）** 两个免费插件的组合。

## 二、组件分工（一个能力一个主负责人）

- **Smart Connections** → 语义检索 / 相关笔记推荐 / 全库向量索引。
- **Copilot** → 侧边栏与 vault 对话，基于笔记上下文回答（RAG）。
- 两者**共用同一后端** `http://127.0.0.1:8000/v1`，不重复知识库、不各维护一套地址。

统一参数（全文复用）：
- Base URL：`http://127.0.0.1:8000/v1`
- API Key：读取 `~/.mtplx/api-key` 的内容（MTPLX 校验此 key，必须填真值）
- 聊天模型：`mtplx-qwen38-27b-optimized-quality`
- 嵌入模型：`Qwen3-Embedding-4B-4bit-DWQ`（维度 2560，插件通常自动识别）

## 三、安装（社区插件）

Obsidian → 设置 → 第三方插件 → 关闭安全模式 → 浏览 → 分别安装 `Smart Connections` 与 `Copilot`。

## 四、Copilot 配置（vault 对话，免费）

`设置 → Copilot → Model Settings`：

| UI 字段 | 填写值 |
| --- | --- |
| Model Provider | `Custom OpenAI`（或 v4 的 **BYOK** 标签） |
| Base URL | `http://127.0.0.1:8000/v1` |
| Model | `mtplx-qwen38-27b-optimized-quality` |
| API Key | `~/.mtplx/api-key` 的内容 |
| Embeddings Model | `Qwen3-Embedding-4B-4bit-DWQ` |

对话时使用 **Vault QA / Chat with vault** 模式，Copilot 会先嵌入并检索当前 vault 再回答。

## 五、Smart Connections 配置

- **默认（推荐先跑通）**：启用后 Smart Connections 用**内置本地嵌入模型**自动索引 vault，无需填任何 Base URL / Key。
- **指定 MTPLX 的 Qwen3-Embedding-4B（可选）**：`设置 → Smart Connections → Smart Environment → Embeddings → OpenAI Compatible`：Base URL `http://127.0.0.1:8000/v1`、API Key 同上、Embedding Model `Qwen3-Embedding-4B-4bit-DWQ`、Dimensions `2560`。
- 首次索引：`Ctrl/Cmd+P` → `Smart Connections: Build Index`。

## 六、验收

```bash
APIKEY=$(cat ~/.mtplx/api-key)
curl -s --max-time 15 -H "Authorization: Bearer $APIKEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3-Embedding-4B-4bit-DWQ","input":"测试向量"}' \
  http://127.0.0.1:8000/v1/embeddings | head -c 200
# 期望：返回 embedding 数组且向量长度 2560
```
- Smart Connections 侧边栏能列出与当前笔记相关的历史笔记；
- Copilot Vault QA 的回答能正确引用 `brain` vault 中的具体内容；
- 全程无外部网络请求（全部走 `127.0.0.1:8000`）。

## 七、安全要点

- API Key 可能以明文存于 `.obsidian/plugins/*/data.json`，**不要把 `.obsidian` 同步到公开仓库**。
- 所有端点均为 `127.0.0.1`（回环），不要改成 `0.0.0.0`。
- 嵌入索引存于 vault 内 `.smart-env/`，随 vault 同步即可，无需跨设备重算。

## 八、跨区同步（进阶，可选）

GitHub 仓库作为 vault 的**心形中间节点（canonical hub）**，多节点（硅谷 Hermes / 香港 MBP / 大陆 Windows / 香港 Mac Studio）经 Git 最终与它对齐；Mac Studio 额外承担本地模型算力，经 **Tailscale** 零信任网状 VPN 仅对内网暴露。详见 `AI实验室部署设计与安全审计.md` 与 `第二大脑-跨区同步与本地模型方案.md` 的本地副本。

> **方案 Y（已定）**：聊天留在 Obsidian 内由 Copilot 承担 vault 对话；Smart Connections 负责"相关笔记"语义检索；Open WebUI 仅作通用对话工作台（不启用 vault RAG），避免双索引与同步缺口。
