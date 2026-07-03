"""开发服务器启动脚本 — 热更新 + 进程生命周期管理

启动:
    uv run python scripts/dev.py                   # 2 mock + dispatcher
    uv run python scripts/dev.py --mock-only       # 仅 2 mock 引擎
    uv run python scripts/dev.py --dispatch-only    # 仅 dispatcher

特性:
    - uvicorn --reload: 代码变更自动重启, 无需手动
    - 进程组管理: SIGINT/Ctrl+C 自动清理所有子进程
    - 启动等待: 健康检查就绪后打印端点列表
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import urllib.request

MOCK1_PORT = 8001
MOCK2_PORT = 8002
DISPATCHER_PORT = 9090

GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


def wait_http(url: str, timeout: float = 20) -> bool:
    """轮询等待 HTTP 服务就绪。"""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Dev server launcher")
    parser.add_argument("--mock-only", action="store_true")
    parser.add_argument("--dispatch-only", action="store_true")
    args = parser.parse_args()

    start_mock = not args.dispatch_only
    start_dispatch = not args.mock_only

    procs: list[subprocess.Popen] = []

    def cleanup():
        print(f"\n{YELLOW}Shutting down...{RESET}")
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print(f"{GREEN}All processes stopped.{RESET}")

    signal.signal(signal.SIGINT, lambda s, f: cleanup() or sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: cleanup() or sys.exit(0))

    # ── Mock engines ──
    if start_mock:
        print(f"{BOLD}[dev] Starting mock engines...{RESET}")
        for port in [MOCK1_PORT, MOCK2_PORT]:
            p = subprocess.Popen(
                [sys.executable, "scripts/mock_engine.py",
                 f"--port={port}", "--latency=0.05", "--reload"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            procs.append(p)

        if not wait_http(f"http://127.0.0.1:{MOCK1_PORT}/health"):
            print("  Mock-1 failed to start!")
            cleanup()
            return
        if not wait_http(f"http://127.0.0.1:{MOCK2_PORT}/health"):
            print("  Mock-2 failed to start!")
            cleanup()
            return
        print(f"  {GREEN}Mock engines ready{RESET}  (ports {MOCK1_PORT}, {MOCK2_PORT}, --reload on)")

    # ── Dispatcher ──
    if start_dispatch:
        print(f"{BOLD}[dev] Starting dispatcher...{RESET}")
        p = subprocess.Popen(
            [sys.executable, "-m", "uvicorn",
             "src.dispatcher.main:create_app",
             "--factory",
             f"--port={DISPATCHER_PORT}",
             "--host=0.0.0.0",
             "--reload",
             "--reload-dir=src",
             "--reload-dir=config",
             ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(p)

        if not wait_http(f"http://127.0.0.1:{DISPATCHER_PORT}/health"):
            print("  Dispatcher failed to start!")
            cleanup()
            return
        print(f"  {GREEN}Dispatcher ready{RESET}  (port {DISPATCHER_PORT}, --reload on)")

    # ── 自动注册 mock 引擎到调度器 ──
    if start_mock and start_dispatch:
        print(f"{BOLD}[dev] Registering mock engines...{RESET}")
        for i, (iid, port) in enumerate([("mock-1", MOCK1_PORT), ("mock-2", MOCK2_PORT)], 1):
            try:
                body = json.dumps({
                    "instance_id": iid,
                    "address": f"http://127.0.0.1:{port}",
                    "model": "test-model",
                    "engine_type": "ollama",
                }).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{DISPATCHER_PORT}/admin/instances",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
                print(f"  {GREEN}Registered{RESET} {iid} → http://127.0.0.1:{port}")
            except Exception as e:
                print(f"  {YELLOW}Register {iid} warning:{RESET} {e}")

    # ── Summary ──
    print(f"\n{BOLD}{'='*50}{RESET}")
    print(f"{BOLD}  Dev server running — Ctrl+C to stop{RESET}")
    print(f"{'='*50}")
    if start_mock:
        print(f"  {CYAN}Mock-1{RESET}   http://127.0.0.1:{MOCK1_PORT}/health")
        print(f"  {CYAN}Mock-2{RESET}   http://127.0.0.1:{MOCK2_PORT}/health")
    if start_dispatch:
        print(f"  {CYAN}Dispatch{RESET} http://127.0.0.1:{DISPATCHER_PORT}/health")
        print(f"           http://127.0.0.1:{DISPATCHER_PORT}/admin/instances")
    print(f"{'='*50}\n")

    # ── Keep alive ──
    try:
        while True:
            # 检查是否有进程退出
            for p in procs:
                if p.poll() is not None:
                    print(f"{YELLOW}Process {p.pid} exited, shutting down...{RESET}")
                    cleanup()
                    return
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
