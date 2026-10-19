# MTPLX — Apple Silicon Local AI Lab Stack

A **privacy-first local AI lab** reference deployment for Apple Silicon (macOS / arm64): a self-built `mtplx` inference server hosts both **Qwen3.8-27B** (generation) and **Qwen3-Embedding-4B** (embedding) in a **single OpenAI-compatible instance**, and a **loopback TCP relay** lets Open WebUI (in an OrbStack container), local Obsidian (second-brain RAG), and the PDF ingestion pipeline share the same backend.

> This repository contains **configuration examples, scripts, and small tools only** — no model weights, caches, logs, credentials, or the `mtplx` binary itself.

## Reference Configuration

The only machine tested in practice is a **Mac Studio (Mac17,14) / Apple M5 Max 18-core / 64GB unified memory**. Other hardware is untested. This is a reference config, not a hardware-compatibility or performance guarantee.

| Component | Reference Setting |
| --- | --- |
| Generation model | `Youssofal/Qwen3.8-27B-MTPLX-Optimized-Quality` |
| Embedding model | `mlx-community/Qwen3-Embedding-4B-4bit-DWQ` |
| Runtime | `mtplx` 2.12.2 (self-built, venv-managed) |
| Clients | Open WebUI (OrbStack container) + Obsidian (Smart Connections / Copilot) |
| Precision | 8-bit (generation) / 4-bit DWQ (embedding) |
| Context | 81,920 tokens (planned) |
| Inference endpoint | `http://127.0.0.1:8000/v1` (OpenAI-compatible, api-key auth) |
| Container bridge | TCP relay bound only to the OrbStack `bridge100` private IP |

## What's Included

- `scripts/mtplx-serve.sh.example` — single-instance `mtplx serve` daemon template (generation + embedding in one process, loopback-only, api-key on disk with `600` perms).
- `scripts/verify-mtplx.sh.example` — one-shot full-chain acceptance for both chat and embeddings.
- `scripts/mtplx-relay.py` — pure-stdlib TCP relay forwarding container-side `host.docker.internal` traffic to the loopback-only MTPLX; zero third-party deps.
- `scripts/ingest.py` + `scripts/ocr_bench.py` + `scripts/requirements-pdf.txt` — dual-track PDF ingestion (PyMuPDF direct extract + native PaddleOCR) pipeline and tuning benchmark.
- `docs/` — architecture, security & privacy, Open WebUI integration, second brain (Obsidian RAG), PDF-RAG, image/video generation, troubleshooting, reproduction.
- `profiles/coding-lean-openwebui/` — **optional**: a Coding-Lean profile example wiring the Qwen Code coding agent to this stack's `:8000` endpoint.
- `benchmarks/RESULTS.md` — OCR tuning measurements and 27B throughput planning estimates.
- `monitor/` — **implemented** loopback read-only monitor (pure stdlib, loopback-only, read-only).
- `CONTRIBUTING.md` — contribution guidelines.

## Quick Start

1. Install and verify `mtplx` (self-built runtime, install into an isolated venv per its instructions). Reference runtime: `mtplx 2.12.2`.
2. Pull weights (official source, or `HF_ENDPOINT=https://hf-mirror.com` mirror; the official source is unreachable in this environment, so the mirror is used — supply chain unchanged):
   - `Youssofal/Qwen3.8-27B-MTPLX-Optimized-Quality`
   - `mlx-community/Qwen3-Embedding-4B-4bit-DWQ`
3. Copy and edit the launch script, filling in your venv absolute path:
   ```bash
   cp scripts/mtplx-serve.sh.example scripts/mtplx-serve.sh
   # edit VENV_PATH / MODEL paths, then:
   bash scripts/mtplx-serve.sh
   ```
4. Accept the stack: `bash scripts/verify-mtplx.sh.example`.
5. Open WebUI: deploy the container via the OrbStack steps in `docs/OPENWEBUI-INTEGRATION.md`; it reaches MTPLX through the loopback relay.

## Architecture (summary)

Two access paths converge on the single loopback-only `mtplx` backend:

- **Native clients** (Obsidian, PDF pipeline, terminal tools) talk to `127.0.0.1:8000/v1` directly.
- **Container clients** (Open WebUI) cannot reach loopback directly, so `mtplx-relay.py` binds the OrbStack `bridge100` private IP and transparently forwards to `127.0.0.1:8000`, with zero firewall changes and minimal exposure.

See `docs/ARCHITECTURE.md` for the full topology and sequence diagrams.

## Security & Privacy

- **Loopback-only**: MTPLX never binds a LAN/WAN interface; the relay exposes only a private bridge IP.
- **API-key auth**: real credential on disk with `600` perms; never committed.
- **No data leaves the machine**: all inference, embeddings, and RAG are local.
- **Read-only monitor**: `monitor/monitor.py` only tails logs and queries `/v1/models`; it never controls the inference server.
- Secrets checklist: logs, PDFs, weights, api-key, and `rule.md` are excluded from the repo.

See `docs/SECURITY-PRIVACY.md` for the full audit.

## How This Differs From the Reference Repo

We share the same root (Qwen3.8-27B on Apple Silicon + MLX, loopback-only) but differ in positioning:

- **This stack** = a multi-client local AI lab: general chat (Open WebUI) + Obsidian knowledge-base RAG + container bridge + mandatory api-key auth + in-process embedding.
- **Reference repo** `qwen3.8-27b-apple-silicon-agent` = a single-machine coding-agent pipeline: upstream `mlx-serve` + Qwen Code + Native MTP speedup.

What we can directly borrow from it: the Coding-Lean profile (point its `baseUrl` at `:8000`), the loopback read-only monitor, and Native MTP acceleration.

## License

[MIT](./LICENSE) — configuration examples, scripts, and docs only.
