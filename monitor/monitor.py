#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MTPLX 回环只读监控器（monitor）。

设计原则（与参考仓库 monitor.py 一致，并适配本 MTPLX 栈）：

1. 仅绑定回环地址（127.0.0.1 / ::1 / localhost）。若 QWEN3MON_HOST 被设为非回环地址，
   直接 SystemExit 退出，绝不把监控面暴露到局域网或公网。
2. 只读：本监控器只做两件事——读取 MTPLX 的日志文件（tail）、向 MTPLX 的
   ``/v1/models`` 发 GET 请求。它**绝不能**启动、停止、重启或代理推理服务，
   也不能修改任何文件（除自身运行日志写入 stderr 外）。
3. 零第三方依赖：仅用 Python 标准库，便于在干净环境直接运行。

安全审计摘要（对应项目 rule.md 的"编码前安全审计"要求）：
- 鉴权：调用 /v1/models 时从 MTPLX_API_KEY_FILE 读取 api-key 并以 Bearer 头发送；
  该文件权限应为 600，密钥不落库、不回显、不进入任何响应体。
- 输入校验：所有 HTTP 响应均为服务端构造的静态/聚合数据，不反射任何外部输入，
  无命令注入、无路径穿越（日志路径由环境变量显式指定，不做字符串拼接）。
- 暴露面：仅一个只读 HTTP 端口，且强制回环；/api/state 不含任何密钥或权重路径。
- 自保：后台线程全部为守护线程，主线程退出即整体退出；日志解析异常被兜底捕获，
  不会因一行脏数据导致监控器崩溃。
"""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# 配置（全部来自环境变量，带安全默认值）
# ---------------------------------------------------------------------------
HOST = os.environ.get("QWEN3MON_HOST", "127.0.0.1")
try:
    PORT = int(os.environ.get("QWEN3MON_PORT", "11236"))
except ValueError:
    PORT = 11236

MTPLX_BASE_URL = os.environ.get(
    "MTPLX_BASE_URL", "http://127.0.0.1:8000/v1"
).rstrip("/")
API_KEY_FILE = os.environ.get(
    "MTPLX_API_KEY_FILE", os.path.expanduser("~/.mtplx/api-key")
)
LOG_FILE = os.environ.get("MTPLX_LOG_FILE", "")
try:
    TAIL_LINES = int(os.environ.get("QWEN3MON_TAIL_LINES", "50"))
except ValueError:
    TAIL_LINES = 50

# 安全边界：仅允许回环地址。参考仓库同样强制 127.0.0.1。
_LOOPBACK = ("127.0.0.1", "::1", "localhost")
if HOST not in _LOOPBACK:
    sys.stderr.write(
        "[FATAL] QWEN3MON_HOST 必须为回环地址（127.0.0.1 / ::1 / localhost），"
        "收到: %s\n" % HOST
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# 日志 tailer：后台线程跟随日志文件，做轻量聚合（行数 / 错误数 / 最近 N 行）
# ---------------------------------------------------------------------------
class LogTailer:
    """只读跟随 MTPLX 日志文件，聚合行数与错误数。

    由于 MTPLX 的具体日志格式尚未在本仓固化，这里采用**通用**解析策略：
    仅做错误关键字匹配与最近行缓存，不假设特定字段。若后续 MTPLX 输出
    结构化（如 JSON）日志，可在 ``_ingest`` 中扩展抽取，而不影响只读语义。
    """

    # 错误关键字（不区分大小写），命中即计入错误计数。
    _ERR_RE = re.compile(
        r"\b(error|exception|traceback|failed|fatal|panic)\b", re.IGNORECASE
    )

    def __init__(self, path: str, max_lines: int = 50) -> None:
        self.path = path
        self.max_lines = max_lines
        self.lines: list[str] = []
        self.lines_seen = 0
        self.error_count = 0
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    def start(self) -> None:
        """启动后台跟随线程（若未配置日志文件则静默跳过）。"""
        if not self.path:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # 文件可能尚未生成，循环等待其出现；任何异常都被兜底，避免后台线程崩溃。
        while not self._stop.is_set():
            try:
                self._follow()
            except FileNotFoundError:
                time.sleep(2)
            except OSError as exc:
                sys.stderr.write("[warn] tailer open: %s\n" % exc)
                time.sleep(5)
            except Exception as exc:  # noqa: BLE001 - 守护线程兜底
                sys.stderr.write("[warn] tailer: %s\n" % exc)
                time.sleep(5)

    def _follow(self) -> None:
        # errors='replace' 保证脏字节不会让读取中断；只读打开，绝不写入。
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, os.SEEK_END)
            while not self._stop.is_set():
                line = fh.readline()
                if not line:
                    time.sleep(0.3)
                    continue
                self._ingest(line.rstrip("\n"))

    def _ingest(self, line: str) -> None:
        with self.lock:
            self.lines_seen += 1
            if self._ERR_RE.search(line):
                self.error_count += 1
            self.lines.append(line)
            if len(self.lines) > self.max_lines:
                self.lines = self.lines[-self.max_lines:]

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "path": self.path or None,
                "lines_seen": self.lines_seen,
                "error_count": self.error_count,
                "last_lines": list(self.lines),
            }


# ---------------------------------------------------------------------------
# 模型探针：带鉴权地查询 /v1/models，短缓存避免反复打推理服务
# ---------------------------------------------------------------------------
class ModelProbe:
    """只读查询 MTPLX 的 /v1/models 端点。"""

    def __init__(self, base_url: str, key_file: str) -> None:
        self.base_url = base_url
        self.key_file = key_file
        self._cache: "dict | None" = None
        self._cache_ts = 0.0
        self._ttl = 5.0  # 秒

    def _read_key(self) -> str:
        try:
            with open(self.key_file, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def fetch(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._cache is not None and now - self._cache_ts < self._ttl:
            return self._cache

        result = {"reachable": False, "models": [], "error": None}
        key = self._read_key()
        req = Request(self.base_url + "/models", method="GET")
        if key:
            req.add_header("Authorization", "Bearer " + key)
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("data", []) if isinstance(data, dict) else []
                result["models"] = [
                    {
                        "id": m.get("id"),
                        "owned_by": m.get("owned_by"),
                        # 若服务端返回 Native MTP 标志则展示，否则为 None（不臆造）。
                        "mtp_loaded": m.get("mtp_loaded"),
                    }
                    for m in models
                ]
                result["reachable"] = True
        except (URLError, HTTPError, ValueError, OSError) as exc:
            # 服务不可达是常见状态，记错误字符串但不抛出，保证监控器自身可用。
            result["error"] = str(exc)

        self._cache = result
        self._cache_ts = now
        return result


# ---------------------------------------------------------------------------
# HTTP 层：仅暴露只读端点
# ---------------------------------------------------------------------------
STARTED_AT = time.time()
tailer: "LogTailer | None" = None
probe: "ModelProbe | None" = None


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_state() -> dict:
    """聚合当前监控状态，供 /api/state 与仪表盘共用。"""
    uptime = time.time() - STARTED_AT
    models = probe.fetch() if probe else {"reachable": False, "models": [], "error": "probe 未初始化"}
    log = tailer.snapshot() if tailer else {"path": None, "lines_seen": 0, "error_count": 0, "last_lines": []}
    return {
        "service": "mtplx-loopback-monitor",
        "host": HOST,
        "port": PORT,
        "started_at": datetime.fromtimestamp(STARTED_AT, tz=timezone.utc).isoformat(),
        "uptime_seconds": round(uptime, 1),
        "config": {
            "mtplx_base_url": MTPLX_BASE_URL,
            "log_file": LOG_FILE or None,
            "api_key_file": API_KEY_FILE,
        },
        "models": models,
        "log": log,
        "note": "只读监控：不控制推理服务，仅 tail 日志与查询 /v1/models。",
    }


def render_dashboard() -> str:
    state = build_state()
    models = state["models"].get("models", [])
    if models:
        model_rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                _html_escape(str(m.get("id"))),
                _html_escape(str(m.get("owned_by") or "-")),
                _html_escape(str(m.get("mtp_loaded"))),
            )
            for m in models
        )
    else:
        model_rows = "<tr><td colspan='3'>（无 / 不可达：%s）</td></tr>" % _html_escape(
            str(state["models"].get("error") or "no models")
        )

    log_lines = state["log"].get("last_lines") or ["（未配置日志文件或暂无输出）"]
    log_html = "\n".join(_html_escape(ln) for ln in log_lines)

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>MTPLX 回环只读监控</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#222}
 h1{font-size:1.4rem} table{border-collapse:collapse;width:100%;margin:.5rem 0}
 th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;font-size:.9rem}
 th{background:#f5f5f5} .ok{color:#1a7f37} .bad{color:#cf222e}
 pre{background:#0d1117;color:#c9d1d9;padding:1rem;border-radius:6px;overflow:auto;max-height:20rem}
 .meta{color:#666;font-size:.85rem}
</style></head><body>
<h1>MTPLX 回环只读监控</h1>
<p class="meta">服务：%(service)s ｜ 绑定：%(host)s:%(port)d ｜ 运行：%(uptime).0f s ｜ 只读，不控制推理服务</p>
<h2>已加载模型</h2>
<table><tr><th>id</th><th>owned_by</th><th>mtp_loaded</th></tr>%(rows)s</table>
<h2>日志（最近 %(tail)d 行，错误数：%(err)d）</h2>
<pre>%(log)s</pre>
<p class="meta">/api/state 返回 JSON；本页每刷新一次重新聚合。</p>
</body></html>
""" % {
        "service": _html_escape(state["service"]),
        "host": _html_escape(HOST),
        "port": PORT,
        "uptime": state["uptime_seconds"],
        "rows": model_rows,
        "tail": TAIL_LINES,
        "err": state["log"].get("error_count", 0),
        "log": log_html,
    }


class Handler(BaseHTTPRequestHandler):
    # 抑制默认向 stderr 刷请求行，改由我们统一格式输出。
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[http] " + (fmt % args) + "\n")

    def _send(self, body: bytes, content_type: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(render_dashboard().encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            payload = json.dumps(build_state(), ensure_ascii=False, indent=2).encode("utf-8")
            self._send(payload, "application/json; charset=utf-8")
        elif self.path == "/healthz":
            self._send(b"ok", "text/plain; charset=utf-8")
        else:
            self._send(
                json.dumps({"error": "not found"}, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                code=404,
            )


def main() -> None:
    global tailer, probe
    tailer = LogTailer(LOG_FILE, TAIL_LINES)
    tailer.start()
    probe = ModelProbe(MTPLX_BASE_URL, API_KEY_FILE)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write(
        "[info] monitor 监听 http://%s:%d（仅回环，只读）\n" % (HOST, PORT)
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if tailer:
            tailer.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
