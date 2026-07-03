"""端到端测试 — 启动真实子进程，通过 HTTP 验证完整调度链路。

覆盖：
- Mock 引擎启动 + 健康检查
- Dispatcher 启动 + 实例注册
- 推理请求转发（非流式 + 流式 SSE）
- 健康检查 /health + /ready
- 熔断器（故障注入 → OPEN → 重置 → CLOSED）
- 负载均衡（并发请求验证多实例分发）
- 管理端点（/admin/status、/admin/metrics/summary）

使用: uv run pytest tests/test_e2e.py -m e2e -v
默认 CI 中跳过（addopts = "-m \"not e2e\""）。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

# ── 端口定义（避开 demo 和 docker-compose 占用的端口） ─────
MOCK1_PORT = 8191
MOCK2_PORT = 8192
DISPATCHER_PORT = 9290
DISPATCHER_URL = f"http://localhost:{DISPATCHER_PORT}"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 工具 ──────────────────────────────────────────────────────


async def wait_for_http(url: str, timeout: float = 30) -> bool:
    """轮询等待 HTTP 服务就绪。"""
    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(url, timeout=2)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return False


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def e2e_services():
    """模块级 fixture：启动 mock 引擎 + dispatcher 子进程。

    整个模块共享同一组进程，减少启停开销。
    """
    mock1: subprocess.Popen | None = None
    mock2: subprocess.Popen | None = None
    disp: subprocess.Popen | None = None

    def _start():
        nonlocal mock1, mock2, disp

        # 启动 mock 引擎
        mock1 = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "mock_engine.py"),
             f"--port={MOCK1_PORT}", "--latency=0.05"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        mock2 = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "mock_engine.py"),
             f"--port={MOCK2_PORT}", "--latency=0.05"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # 等待 mock 引擎就绪
        if not asyncio.run(wait_for_http(f"http://localhost:{MOCK1_PORT}/health", timeout=15)):
            raise RuntimeError("Mock engine 1 failed to start")
        if not asyncio.run(wait_for_http(f"http://localhost:{MOCK2_PORT}/health", timeout=15)):
            raise RuntimeError("Mock engine 2 failed to start")

        # 启动 dispatcher
        env = os.environ.copy()
        env["DISPATCHER_PORT"] = str(DISPATCHER_PORT)
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""  # 禁用 OTLP 导出，避免连接错误
        disp = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.dispatcher.main:create_app",
             "--factory", f"--port={DISPATCHER_PORT}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, cwd=str(PROJECT_ROOT),
        )

        if not asyncio.run(wait_for_http(f"{DISPATCHER_URL}/health", timeout=15)):
            raise RuntimeError("Dispatcher failed to start")

        return {"dispatcher_url": DISPATCHER_URL}

    def _cleanup():
        for proc, name in [(mock1, "mock-1"), (mock2, "mock-2"), (disp, "dispatcher")]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    try:
        info = _start()
        yield info
    finally:
        _cleanup()


@pytest.fixture
async def e2e_client(e2e_services: dict) -> httpx.AsyncClient:
    """提供指向运行中 dispatcher 的 httpx 客户端。"""
    async with httpx.AsyncClient(timeout=30) as client:
        yield client


# ── 测试类 ───────────────────────────────────────────────────


@pytest.mark.e2e
class TestE2EServiceLifecycle:
    """服务启动、实例注册、基础健康检查。"""

    async def test_dispatcher_health_is_ok(self, e2e_client: httpx.AsyncClient) -> None:
        resp = await e2e_client.get(f"{DISPATCHER_URL}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"  # 未注册实例
        assert data["instances_total"] == 0

    async def test_register_instances(self, e2e_client: httpx.AsyncClient) -> None:
        """注册 2 个 mock 引擎实例。"""
        for inst_id, port in [("e2e-1", MOCK1_PORT), ("e2e-2", MOCK2_PORT)]:
            resp = await e2e_client.post(
                f"{DISPATCHER_URL}/admin/instances",
                json={
                    "instance_id": inst_id,
                    "address": f"http://localhost:{port}",
                    "model": "test-model",
                    "engine_type": "ollama",
                },
            )
            assert resp.status_code == 200, f"Failed to register {inst_id}: {resp.text}"
            assert resp.json()["status"] == "ok"

        # 验证注册表
        resp = await e2e_client.get(f"{DISPATCHER_URL}/admin/status")
        data = resp.json()
        assert data["instances"]["total"] == 2
        assert data["instances"]["healthy"] >= 1  # 健康检查可能需要一些时间

    async def test_health_after_registration(self, e2e_client: httpx.AsyncClient) -> None:
        """注册实例后 health 应变为 ok。"""
        # 给健康检查任务一点时间探测
        await asyncio.sleep(1.5)
        resp = await e2e_client.get(f"{DISPATCHER_URL}/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["instances_healthy"] >= 1

    async def test_ready_endpoint(self, e2e_client: httpx.AsyncClient) -> None:
        resp = await e2e_client.get(f"{DISPATCHER_URL}/ready")
        assert resp.status_code == 200
        assert resp.json()["ready"] is True


@pytest.mark.e2e
class TestE2EInference:
    """推理请求通过真实 HTTP 链路。"""

    async def test_chat_completion_basic(self, e2e_client: httpx.AsyncClient) -> None:
        """基础推理请求返回 OpenAI 兼容格式。"""
        resp = await e2e_client.post(
            f"{DISPATCHER_URL}/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hello E2E!"}],
            },
        )
        assert resp.status_code == 200, f"Inference failed: {resp.text}"
        data = resp.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]
        assert "content" in data["choices"][0]["message"]
        # 响应内容应来自 mock 引擎（包含端口号或引擎信息）
        content = data["choices"][0]["message"]["content"]
        assert len(content) > 0

    async def test_chat_completion_with_priority(self, e2e_client: httpx.AsyncClient) -> None:
        """带优先级的请求正常返回。"""
        for pri in [1, 5, 10]:
            resp = await e2e_client.post(
                f"{DISPATCHER_URL}/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": f"priority-{pri}"}],
                    "priority": pri,
                },
            )
            assert resp.status_code == 200, f"Priority {pri} failed"
            data = resp.json()
            assert "usage" in data
            assert data["usage"]["total_tokens"] > 0

    async def test_chat_completion_streaming(self, e2e_client: httpx.AsyncClient) -> None:
        """SSE 流式推理正常返回。"""
        chunk_count = 0
        async with e2e_client.stream(
            "POST",
            f"{DISPATCHER_URL}/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Tell me a story."}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data:") and "content" in line:
                    chunk_count += 1
                elif line.startswith("{") and "message" in line:
                    chunk_count += 1
        assert chunk_count > 0, "Expected at least one streaming chunk"

    async def test_invalid_body_handled(self, e2e_client: httpx.AsyncClient) -> None:
        """非 JSON 请求体返回 422。"""
        resp = await e2e_client.post(
            f"{DISPATCHER_URL}/v1/chat/completions",
            content="this is not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422


@pytest.mark.e2e
class TestE2ECircuitBreaker:
    """熔断器端到端行为（故障注入 → OPEN → 重置 → 恢复）。"""

    async def test_fault_injection_opens_circuit(self, e2e_client: httpx.AsyncClient) -> None:
        """注入故障使 e2e-1 连续失败 → 熔断器 OPEN。"""
        # 重置 mock 引擎状态
        await e2e_client.post(f"http://localhost:{MOCK1_PORT}/admin/reset")

        # 注入 20 次故障
        await e2e_client.post(f"http://localhost:{MOCK1_PORT}/admin/fail/20")

        # 发送 8 个请求触发熔断
        errors = 0
        for i in range(8):
            resp = await e2e_client.post(
                f"{DISPATCHER_URL}/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": f"cb-test-{i}"}],
                },
            )
            if resp.status_code != 200:
                errors += 1

        # 检查熔断器状态
        resp = await e2e_client.get(f"{DISPATCHER_URL}/admin/status")
        cb_states = resp.json()["circuit_breakers"]
        assert cb_states.get("e2e-1") == "open", (
            f"Expected e2e-1 circuit breaker OPEN, got {cb_states}"
        )

        # 恢复：重置熔断器 + 清除故障
        await e2e_client.post(f"{DISPATCHER_URL}/admin/circuit-breakers/e2e-1/reset")
        await e2e_client.post(f"http://localhost:{MOCK1_PORT}/admin/reset")

        # 验证恢复正常
        resp = await e2e_client.post(
            f"{DISPATCHER_URL}/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "post-recovery"}],
            },
        )
        assert resp.status_code == 200

        resp = await e2e_client.get(f"{DISPATCHER_URL}/admin/status")
        assert resp.json()["circuit_breakers"]["e2e-1"] == "closed"


@pytest.mark.e2e
class TestE2ELoadBalancing:
    """负载均衡：验证并发请求分发到多个实例。"""

    async def test_concurrent_requests_distribute(self, e2e_client: httpx.AsyncClient) -> None:
        """并发 10 个请求，两个实例均有流量。"""
        routes: dict[str, int] = {}

        async def send_one(i: int) -> None:
            resp = await e2e_client.post(
                f"{DISPATCHER_URL}/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": f"lb-{i}"}],
                },
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                if str(MOCK1_PORT) in content:
                    routes["e2e-1"] = routes.get("e2e-1", 0) + 1
                elif str(MOCK2_PORT) in content:
                    routes["e2e-2"] = routes.get("e2e-2", 0) + 1

        await asyncio.gather(*(send_one(i) for i in range(10)))

        assert routes.get("e2e-1", 0) > 0, "e2e-1 received no traffic"
        assert routes.get("e2e-2", 0) > 0, "e2e-2 received no traffic"


@pytest.mark.e2e
class TestE2EAdminEndpoints:
    """管理端点（Dashboard 依赖）。"""

    async def test_admin_status(self, e2e_client: httpx.AsyncClient) -> None:
        resp = await e2e_client.get(f"{DISPATCHER_URL}/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instances"]["total"] == 2
        assert "circuit_breakers" in data
        assert "queue_depth" in data

    async def test_admin_instances_list(self, e2e_client: httpx.AsyncClient) -> None:
        resp = await e2e_client.get(f"{DISPATCHER_URL}/admin/instances")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert {i["instance_id"] for i in data} == {"e2e-1", "e2e-2"}

    async def test_admin_metrics_summary(self, e2e_client: httpx.AsyncClient) -> None:
        resp = await e2e_client.get(f"{DISPATCHER_URL}/admin/metrics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "request_count" in data
        assert data["request_count"] > 0  # 前面已发送过请求

    async def test_admin_scaling_evaluate(self, e2e_client: httpx.AsyncClient) -> None:
        resp = await e2e_client.get(f"{DISPATCHER_URL}/admin/scaling/evaluate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "none"
        # 默认未启用 auto_scaler
        assert "not configured" in data.get("reason", "")
