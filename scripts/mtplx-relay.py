#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MTPLX 回环中继（纯标准库实现，无需 socat / brew / 任何第三方依赖）。

职责：
    在 <绑定IP>:RELAY_PORT 监听 TCP，把连接透明双向转发到 127.0.0.1:BACKEND_PORT。
    MTPLX 自身始终 --host 127.0.0.1（回环），本中继只负责把「容器侧」的访问
    搬运到回环地址，从而让 OrbStack 容器内的 Open WebUI 经 host.docker.internal
    访问仅回环的 MTPLX。

安全要点：
    - 绑定 IP 由环境变量 MTPLX_RELAY_BIND_IP 指定（应为 OrbStack 桥接私有 IP，
      即容器内 host.docker.internal 解析到的宿主 IP）。
    - 该 IP 属于 OrbStack 内部子网，不在 en0/Wi-Fi/局域网/公网上监听，暴露面最小。
    - 中继不存储任何 api-key，只是透明搬运 TCP；MTPLX 末端的 api-key 鉴权仍生效。
    - 未指定绑定 IP 时，脚本尝试自动探测 OrbStack 桥接 IP；探测失败才回退 0.0.0.0
      并明确告警（此时需配合防火墙收紧，否则 8000 会暴露到局域网）。

用法（由 launchd plist 调用，或手动测试）：
    MTPLX_RELAY_BIND_IP=127.0.0.1 MTPLX_RELAY_PORT=18000 python3 mtplx-relay.py
"""

import os
import socket
import subprocess
import threading
import time

# —— 配置（均可用环境变量覆盖）——
RELAY_PORT = int(os.environ.get("MTPLX_RELAY_PORT", "8000"))
MTRLX_HOST = "127.0.0.1"                      # MTPLX 回环地址（固定不变）
MTRLX_PORT = int(os.environ.get("MTPLX_BACKEND_PORT", "8000"))
BIND_IP = os.environ.get("MTPLX_RELAY_BIND_IP", "").strip()


def detect_orbstack_bridge_ip():
    """探测 OrbStack 桥接私有 IP（host.docker.internal 在容器内解析到的宿主 IP）。"""
    # 方法 1：orbctl show（OrbStack 自带命令行）
    try:
        out = subprocess.run(
            ["orbctl", "show"], capture_output=True, text=True, timeout=5
        ).stdout
        for line in out.splitlines():
            low = line.lower()
            if "address" in low or "ip" in low:
                for token in line.replace(":", " ").split():
                    if token.count(".") == 3 and not token.startswith("127."):
                        return token
    except Exception:
        pass
    # 方法 2：/etc/hosts 中 host.docker.internal 的宿主机解析
    try:
        with open("/etc/hosts", encoding="utf-8") as fh:
            for line in fh:
                if "host.docker.internal" in line:
                    candidate = line.split()[0].strip()
                    if candidate.count(".") == 3:
                        return candidate
    except Exception:
        pass
    return ""


def _pump(src: socket.socket, dst: socket.socket):
    """把 src 收到的数据全部转发给 dst，任一方关闭即结束本方向。"""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def _handle_client(client: socket.socket):
    """为每个入站连接建立到 MTPLX 的 upstream 并双向转发。"""
    try:
        upstream = socket.create_connection((MTRLX_HOST, MTRLX_PORT), timeout=10)
    except OSError as exc:
        # 后端不可达：直接关掉客户端，launchd 的 KeepAlive 会保证中继本身存活
        try:
            client.close()
        except OSError:
            pass
        print(f"[relay] upstream 连接失败（MTPLX 未起？）: {exc}", flush=True)
        return
    # 两个方向各开一个线程，保证全双工
    t1 = threading.Thread(target=_pump, args=(client, upstream), daemon=True)
    t2 = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
    t1.start()
    t2.start()


def main():
    bind = BIND_IP or detect_orbstack_bridge_ip()
    if not bind:
        print(
            "[relay][WARN] 未探测到桥接 IP，回退绑定 0.0.0.0 —— "
            "请确保已用防火墙限制 8000 仅 lo0+OrbStack 桥接接口，否则将暴露到局域网！",
            flush=True,
        )
        bind = "0.0.0.0"

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # 绑定失败时持续重试：桥接接口可能晚于中继启动（OrbStack 重启/网络切换），
    # 不退出、不崩，等接口就绪后自动接管。
    while True:
        try:
            server.bind((bind, RELAY_PORT))
            break
        except OSError as exc:
            print(
                f"[relay] 绑定 {bind}:{RELAY_PORT} 失败（接口/IP 可能尚未就绪），"
                f"10s 后重试: {exc}",
                flush=True,
            )
            time.sleep(10)
    server.listen(128)
    print(
        f"[relay] 已启动：监听 {bind}:{RELAY_PORT} -> {MTRLX_HOST}:{MTRLX_PORT}",
        flush=True,
    )

    while True:
        try:
            client, peer = server.accept()
        except OSError:
            time.sleep(1)
            continue
        threading.Thread(target=_handle_client, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
