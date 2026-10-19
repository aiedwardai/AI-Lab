# 回环只读监控器（设计说明 · 尚未实装）

> 本目录**不包含**可运行的监控器代码。它记录设计意图，便于后续落地或对齐参考仓库 `qwen3.8-27b-apple-silicon-agent` 的 `monitor.py`。

## 设计目标

一个 **stable、dependency-free、session-scoped** 的会话监控器：

- 绑定 `127.0.0.1`（绝不暴露到其他接口）。
- **只读**：只 tail 推理服务日志、查 `/v1/models`，**绝不**启停或代理推理服务。
- 计算缓存效率、加权 prefill/decode、MTP 接受率等可观测指标，供本地仪表盘展示。

## ⚠️ 关键约束（落地前必须确认）

参考仓库的 `monitor.py` 解析的是 **mlx-serve 专属日志格式**（如 `[spec-stats] mode=mtp`、`[hot-cache]`、`<- N+M tokens`）。本栈使用的是自研 `mtplx`，日志格式**不一定相同**——若直接套用，正则抓不到对应行会导致统计全 0。

**落地步骤**：
1. 先 `tail` 你实际的 MTPLX 日志，确认其日志格式（缓存 / prefill / decode / MTP 字段长什么样）。
2. 据此重写解析正则（其余框架——回环绑定、`/api/state`、会话级只读——可直接复用）。
3. 再补一个纯 stdlib 的 `monitor.py` 与本说明互换。

> 在确认日志格式前，本目录保持为空设计文档，不发布假代码。
