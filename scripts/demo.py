"""端到端演示脚本 — 启动 mock 引擎 + 调度器，演示完整推理流程。

使用方式：uv run python scripts/demo.py
"""

import asyncio
import subprocess
import time

import httpx

MOCK_PORT = 8001
DISPATCHER_PORT = 9090


async def wait_for_service(url: str, timeout: float = 30) -> bool:
    """等待服务就绪。"""
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
    # 启动 mock 引擎
    print("[1/5] 启动 Mock 推理引擎...")
    mock = subprocess.Popen(["uv", "run", "python", "scripts/mock_engine.py", f"--port={MOCK_PORT}"])
    if not await wait_for_service(f"http://localhost:{MOCK_PORT}/health"):
        print("  ❌ Mock 引擎启动失败")
        return
    print("  ✅ Mock 引擎就绪")

    # 启动调度服务
    print("[2/5] 启动 LLM Dispatcher...")
    dispatcher = subprocess.Popen(
        ["uv", "run", "uvicorn", "src.dispatcher.main:create_app", "--factory", f"--port={DISPATCHER_PORT}"]
    )
    if not await wait_for_service(f"http://localhost:{DISPATCHER_PORT}/health"):
        print("  ❌ 调度服务启动失败")
        return
    print("  ✅ 调度服务就绪")

    async with httpx.AsyncClient() as client:
        # 注册实例
        print("[3/5] 注册 Mock 实例...")
        resp = await client.post(
            f"http://localhost:{DISPATCHER_PORT}/admin/instances",
            json={
                "instance_id": "mock-1",
                "address": f"http://localhost:{MOCK_PORT}",
                "model": "test-model",
                "engine_type": "ollama",
            },
        )
        print(f"  {'✅' if resp.status_code == 200 else '❌'} {resp.json()}")

        # 发送推理请求
        print("[4/5] 发送推理请求...")
        resp = await client.post(
            f"http://localhost:{DISPATCHER_PORT}/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "Hello, LLM Dispatcher!"}]},
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print(f"  ✅ 响应: {content[:80]}...")
        else:
            print(f"  ❌ {resp.status_code}: {resp.text}")

        # 查看状态
        print("[5/5] 查看系统状态...")
        resp = await client.get(f"http://localhost:{DISPATCHER_PORT}/admin/status")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  ✅ 实例: {data['instances']}")
            print(f"  ✅ 熔断器: {data['circuit_breakers']}")
            print(f"  ✅ 队列: {data['queue_depth']}")

    print("\n🎉 演示完成！")
    print("Press Ctrl+C to stop all services...")
    try:
        dispatcher.wait()
    except KeyboardInterrupt:
        pass
    finally:
        mock.terminate()
        dispatcher.terminate()


if __name__ == "__main__":
    asyncio.run(main())
