# MTPLX — Apple Silicon 本地 AI 实验室栈

一套面向 Apple Silicon（macOS / arm64）的**隐私优先本地 AI 实验室**部署参考：用自研 `mtplx` 推理服务在**单实例**内同时托管 **Qwen3.8-27B 生成模型**与 **Qwen3-Embedding-4B 嵌入模型**（OpenAI 兼容），并通过**回环 TCP 中继**让 OrbStack 容器内的 Open WebUI、本机 Obsidian（第二大脑 RAG）、以及 PDF 摄取管线共用同一后端。

> 本仓库仅包含**配置示例、脚本与小工具**，不含模型权重、缓存、日志、凭据，也不含 `mtplx` 二进制本身。

## 参考配置

唯一实测参考机为 **Mac Studio (Mac17,14) / Apple M5 Max 18 核 / 64GB 统一内存**。其他硬件未经测试。这是参考配置，不是硬件兼容性或性能承诺。

| 组件 | 参考设置 |
| --- | --- |
| 生成模型 | `Youssofal/Qwen3.8-27B-MTPLX-Optimized-Quality` |
| 嵌入模型 | `mlx-community/Qwen3-Embedding-4B-4bit-DWQ` |
| 运行时 | `mtplx` 2.12.2（自研，venv 托管） |
| 客户端 | Open WebUI（OrbStack 容器） + Obsidian（Smart Connections / Copilot） |
| 精度 | 8-bit（生成） / 4-bit DWQ（嵌入） |
| 上下文 | 81,920 tokens（规划） |
| 推理端点 | `http://127.0.0.1:8000/v1`（OpenAI 兼容，api-key 鉴权） |
| 容器桥接 | 仅绑 OrbStack `bridge100` 私有 IP 的 TCP 中继 |

## 包含内容

- `scripts/mtplx-serve.sh.example` — `mtplx serve` 单实例守护进程模板（生成 + 嵌入同进程，仅回环，api-key 落盘 `600`）。
- `scripts/verify-mtplx.sh.example` — 双模型（chat + embeddings）全链路一键验收。
- `scripts/mtplx-relay.py` — 纯标准库 TCP 中继，把容器侧 `host.docker.internal` 流量转发到仅回环的 MTPLX；零第三方依赖。
- `scripts/ingest.py` + `scripts/ocr_bench.py` + `scripts/requirements-pdf.txt` — PDF 双轨摄取（PyMuPDF 直抽 + 原生 PaddleOCR）管线与调优基准。
- `docs/` — 架构、安全与隐私、Open WebUI 接入、第二大脑（Obsidian RAG）、PDF-RAG、图像/视频生成、排障、复现。
- `profiles/coding-lean-openwebui/` — **可选**：把 Qwen Code 编码 Agent 接到本栈 `:8000` 端点的 Coding-Lean 画像示例。
- `benchmarks/RESULTS.md` — OCR 调优实测与 27B 吞吐规划估算。
- `monitor/` — 回环只读监控器设计说明（尚未实装，见内文）。

## 快速开始

1. 安装并确认 `mtplx` 可用（自研运行时，按其官方说明装进隔离 venv）。参考运行时 `mtplx 2.12.2`。
2. 拉取权重（官方源，或 `HF_ENDPOINT=https://hf-mirror.com` 镜像；本环境官方源不可达，用镜像绕行，供应链不变）：
   - `Youssofal/Qwen3.8-27B-MTPLX-Optimized-Quality`
   - `mlx-community/Qwen3-Embedding-4B-4bit-DWQ`
3. 复制并编辑启动脚本，填入你的 venv 绝对路径：
   ```bash
   cp scripts/mtplx-serve.sh.example scripts/mtplx-serve.sh
   # 编辑 MTRLX_BIN 等变量
   bash scripts/mtplx-serve.sh        # 前台运行；生产用 launchd 托管（见 docs/REPRODUCTION.md）
   ```
4. 验收双模型：
   ```bash
   cp scripts/verify-mtplx.sh.example scripts/verify-mtplx.sh
   bash scripts/verify-mtplx.sh
   ```
5. （可选）让容器内的 Open WebUI 访问仅回环的 MTPLX —— 见 `docs/OPENWEBUI-INTEGRATION.md`（方案 E 中继）。

> **MTP 加速提示**：本栈当前**未启用** Native MTP。参考仓库 `qwen3.8-27b-apple-silicon-agent` 用上游 `mlx-serve` 开了 MTP（短码 ~3.5× 解码提速）。是否支持取决于 `mtplx` 是否暴露 `--mtp/--pld` 且权重含 MTP 资产；本仓库不做该假设。

## 性能说明

在参考机上，27B 生成模型的**规划吞吐估算**约为 **18–25 tok/s**（Q4_K_M 量级，非实测基准，取决于 `mtplx` 实际实现与量化）。OCR 调优为**实测**：扫描书 `fast` 档约 **1.35s/页**、峰值内存 ~1.8GB（见 `benchmarks/RESULTS.md`）。这些数字与参考仓库的 MTP 短码基准（18.9→65.9 tok/s）**不可直接比较**。

> 注意：27B 为 **Qwen3（带 reasoning 思考过程）**，思考会占用 `max_tokens` 预算；若 `max_tokens` 太小（如 64），`content` 可能被思考占满而显空。给足 256+ 即可正常出字（实测定位）。

## 文档

- [架构](docs/ARCHITECTURE.md)
- [安全与隐私](docs/SECURITY-PRIVACY.md)
- [Open WebUI 接入](docs/OPENWEBUI-INTEGRATION.md)
- [第二大脑（Obsidian RAG）](docs/SECOND-BRAIN.md)
- [PDF-RAG 摄取](docs/PDF-RAG.md)
- [图像 / 视频生成](docs/IMAGE-VIDEO.md)
- [排障](docs/TROUBLESHOOTING.md)
- [复现与持久化](docs/REPRODUCTION.md)

## 已落地验证（参考机）

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| Phase 0–2 | 环境核查 + venv 装 `mtplx 2.12.2` + 拉取 27B/4B + 三接口（models/embeddings/chat）验证 | PASS |
| Phase A | MTPLX 由 launchd 常驻，仅回环 | PASS |
| Phase C | 纯 Python 中继 → Open WebUI 0.12.2 容器 → 端到端对话 200 | PASS |
| Phase B | Obsidian Smart Connections + Copilot 接本地端点 | 配置完成 |

## 与参考仓库的差异

本栈对标 `qwen3.8-27b-apple-silicon-agent` 的**文档与组织模式**，但定位不同：参考仓库是「单机编码 Agent 精装流水线」（上游 `mlx-serve` + Qwen Code + Native MTP）；本栈是「多客户端本地 AI 实验室」（自研 `mtplx` + Open WebUI + Obsidian 第二大脑 + PDF-RAG，强制 api-key 鉴权与容器穿透中继）。可直接借鉴参考仓库的三项能力：Coding-Lean 画像（已给出 `:8000` 适配示例）、回环只读监控、Native MTP 提速。

## 许可证

[MIT](LICENSE)
