# 安全与隐私

> 设计依据：`AI实验室部署设计与安全审计.md`、`PDF摄取方案与安全审计.md`、`7x24运行实施手册.md`。
> 遵循「先架构图 → 再安全审计 → 后编码」；本文件是安全基线。

## 一、安全基线结论

总体风险**可控**，核心面（回环绑定、`~/.mtplx/api-key` 权限 `600`、本地零出境）已具备；**最需盯防**的是：容器端口误映射 `0.0.0.0` 与提示注入。

## 二、逐项审计

### 2.1 身份鉴权
- MTPLX 启用 `--api-key`，密钥落盘 `~/.mtplx/api-key`（权限 `600`，仅本机可读）。
- 前端（Obsidian / Open WebUI）均配置同一 key；key 仅从文件读取，不进任何 plist / 容器环境变量明文；前端配置文件权限收紧为 `600`。

### 2.2 访问控制（高危点）
- MTPLX `--host 127.0.0.1`（仅回环）+ macOS 防火墙开启。
- **风险**：Open WebUI 容器若把端口映射到 `0.0.0.0` 或发布到局域网，会直接暴露模型服务与 key。
- **措施**：容器仅映射 `127.0.0.1:PORT:8080`；Obsidian 仅连 `127.0.0.1:8000`；远程访问一律走 Tailscale，**绝不**把 8000 / Open WebUI 端口直接开放到公网或 `0.0.0.0`。
- **中继（方案 E）**：中继只绑 OrbStack `bridge100` 私有桥接 IP（不在 `en0`/Wi-Fi/局域网监听），即使探测失败回退 `0.0.0.0` 也应配合 pf 收紧，否则 8000 会暴露到局域网。

### 2.3 输入校验 / 提示注入（中危）
- 第二大脑 / PDF-RAG 会把 vault 与 PDF 内容作为上下文喂给模型。若来源含"指令式文本"（网页/邮件/文档），可能触发提示注入。
- **措施**：RAG 上下文与系统指令严格分离；检索前端只做"检索+总结+问答"，**不授予**写文件/执行命令/调用外部 API 的权限。

### 2.4 敏感数据边界
- 本地模型不联网，笔记与对话零出境。
- **风险**：若 Open WebUI 同时挂了云端模型，敏感笔记可能被发往第三方；`brain` vault 可能含健康/财务/企业文件。
- **措施**：默认只用本地 27B；云端模型按需单独配置且明确标注；高敏资料分 vault / 分目录并单独设权。

### 2.5 第三方依赖安全
- **HF 镜像**：官方源不可达时用 `HF_ENDPOINT=https://hf-mirror.com` 绕行，权重仍来自官方命名空间（`Youssofal/`、`mlx-community/`），供应链完整性不变；官方源恢复后删除该行即回归。
- **容器镜像**：Open WebUI 必须用官方镜像并校验 digest（`ghcr.io/open-webui/open-webui:main` 的 arm64 版本），不从不明第三方构建。
- **Python 包**：在隔离 venv 内安装，不污染系统环境。

### 2.6 容器安全
- 以非 root 运行容器；限制内存（64GB 大部分须留给模型）；`brain` vault 以只读挂载；不挂载 `~/.mtplx/api-key` 之外的敏感目录。

### 2.7 OCR 进程安全（arm64 原生）
- PaddleOCR 改为 **arm64 原生进程内调用**（取消原 x86 容器方案），无对外 HTTP 端口，攻击面显著缩小。
- 必须显式关闭三个预处理模型（`use_doc_orientation_classify` / `use_doc_unwarping` / `use_textline_orientation`），否则常驻内存 40GB+ 会挤占 27B 模型预算。

### 2.8 持久化安全（launchd）
- plist 权限 `644` 且**不含密钥明文**（key 由脚本运行时从文件读取）。
- 日志（`request-log-*.jsonl` 可能含请求内容）权限收紧为 `600` 并做轮转；launchd 用 `StandardOutPath/StandardErrorPath` 接管。
- `KeepAlive` 保活，但首次接入需先停掉当前"会话子进程"版，避免端口 8000 冲突导致启动失败 + 重启风暴。

### 2.9 远程访问（Tailscale）
- 需要远程访问时，仅经 Tailscale 零信任网状 VPN；模型 API 只监听 Tailscale 接口，绝不公网开放。
- GitHub 细粒度 PAT 仅授予仓库 contents 读写，存于系统钥匙串 / Obsidian Git，不落明文，季度轮换。

## 三、必须不提交清单

- `~/.mtplx/api-key` 及任何密钥文件
- `*.log`、`request-log-*.jsonl` 等运行时日志
- `01_models/`、`02_data/`（含 `*.pdf`）、`searchable/` 等权重与原始资料
- `.obsidian/` 插件配置（可能含明文 key）
- 项目内部 `rule.md`

详见仓库根目录 `.gitignore`。
