"""端到端演示 — 注册实例→发送请求→熔断演示→查看指标→重启恢复。

启动方式：uv run python scripts/demo.py
需要先在另一个终端启动 mock 引擎：uv run python scripts/mock_engine.py --port 8001
"""

import asyncio
import subprocess
import sys
import time

import httpx

MOCK_PORT = 8001
MOCK_PORT2 = 8002
DISPATCHER_PORT = 9090

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def ok(msg: str) -> str:
    return f"{GREEN}{msg}{RESET}"


def fail(msg: str) -> str:
    return f"{RED}{msg}{RESET}"


def info(msg: str) -> str:
    return f"{YELLOW}{msg}{RESET}"


async def wait_for_service(url: str, timeout: float = 30) -> bool:
    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(url, timeout=2)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
    return False


async def main():
    # ── 启动服务 ──
    print("[1/7] 启动 Mock 引擎...")
    mock = subprocess.Popen([sys.executable, "scripts/mock_engine.py", f"--port={MOCK_PORT}"])
    mock2 = subprocess.Popen([sys.executable, "scripts/mock_engine.py", f"--port={MOCK_PORT2}"])
    if not await wait_for_service(f"http://localhost:{MOCK_PORT}/health"):
        print(fail("  ❌ Mock 引擎 1 启动失败"))
        return
    if not await wait_for_service(f"http://localhost:{MOCK_PORT2}/health"):
        print(fail("  ❌ Mock 引擎 2 启动失败"))
        return
    print(ok("  ✅ 2 个 Mock 引擎就绪"))

    print("[2/7] 启动 LLM Dispatcher...")
    disp_cmd = [
        sys.executable, "-m", "uvicorn", "src.dispatcher.main:create_app",
        "--factory", f"--port={DISPATCHER_PORT}",
    ]
    disp = subprocess.Popen(disp_cmd)
    if not await wait_for_service(f"http://localhost:{DISPATCHER_PORT}/health"):
        print(fail("  ❌ 调度服务启动失败"))
        return
    print(ok("  ✅ 调度服务就绪"))

    async with httpx.AsyncClient() as client:
        # ── 注册实例 ──
        print("[3/7] 注册 2 个实例...")
        for inst_id, port in [("mock-1", MOCK_PORT), ("mock-2", MOCK_PORT2)]:
            r = await client.post(f"http://localhost:{DISPATCHER_PORT}/admin/instances", json={
                "instance_id": inst_id, "address": f"http://localhost:{port}",
                "model": "test-model", "engine_type": "ollama",
            })
            print(f"  {ok('✅') if r.status_code == 200 else fail('❌')} {inst_id}")

        # ── 发送推理请求 ──
        print("[4/7] 发送推理请求...")
        for i in range(3):
            r = await client.post(f"http://localhost:{DISPATCHER_PORT}/v1/chat/completions", json={
                "model": "test-model", "messages": [{"role": "user", "content": f"Hello {i}"}],
            })
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                port = MOCK_PORT if str(MOCK_PORT) in content else MOCK_PORT2
                print(f"  {ok('✅')} 响应来自端口 {port}")
            else:
                print(f"{fail('❌')} HTTP {r.status_code}")

        # ── 熔断演示 ──
        print("[5/7] 熔断演示: 设置 mock-1 返回 500...")
        await client.post(f"http://localhost:{MOCK_PORT}/admin/fail/20")

        print("  发送 6 个请求触发熔断...")
        for i in range(6):
            r = await client.post(f"http://localhost:{DISPATCHER_PORT}/v1/chat/completions", json={
                "model": "test-model", "messages": [{"role": "user", "content": f"fail {i}"}],
            })
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                port_id = "8002" if "8002" in content else "8001"
                print(f"  ← 路由到: port {port_id}")

        cb_status = await client.get(f"http://localhost:{DISPATCHER_PORT}/admin/status")
        cb_data = cb_status.json()["circuit_breakers"]
        print(f"  熔断器状态: mock-1={info(cb_data.get('mock-1', '?'))}  mock-2={info(cb_data.get('mock-2', '?'))}")

        # ── 重置熔断器 ──
        print("[6/7] 重置 mock-1 熔断器...")
        await client.post(f"http://localhost:{DISPATCHER_PORT}/admin/circuit-breakers/mock-1/reset")
        await client.post(f"http://localhost:{MOCK_PORT}/admin/reset")
        cb2 = await client.get(f"http://localhost:{DISPATCHER_PORT}/admin/status")
        print(f"  恢复后: mock-1={ok(cb2.json()['circuit_breakers']['mock-1'])}")

        # ── 指标 ──
        print("[7/7] 查看指标汇总...")
        r = await client.get(f"http://localhost:{DISPATCHER_PORT}/admin/metrics/summary")
        if r.status_code == 200:
            s = r.json()
            print(f"  总请求: {s['request_count']}  错误: {s['error_count']}")
            print(f"  平均延迟: {s['avg_latency_ms']:.1f}ms  P95: {s['p95_latency_ms']:.1f}ms")

    print(f"\n{ok('🎉 演示完成！')} 健康检查: http://localhost:{DISPATCHER_PORT}/health")
    print(f"  管理面板: http://localhost:{DISPATCHER_PORT}/admin/status")
    print("  Dashboard: uv run streamlit run dashboard/app.py")
    print("  Press Ctrl+C to stop all services...")
    try:
        disp.wait()
    except KeyboardInterrupt:
        pass
    finally:
        mock.terminate()
        mock2.terminate()
        disp.terminate()


if __name__ == "__main__":
    asyncio.run(main())
