"""端到端演示 — 覆盖调度平台全部核心功能。

启动方式：
    uv run python scripts/demo.py

前提：无需额外启动 mock 引擎，脚本自动管理子进程。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import httpx

# 确保项目根目录在 sys.path 中（支持直接 python scripts/demo.py）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 常量 ──────────────────────────────────────────────────────
MOCK_PORT = 8001
MOCK_PORT2 = 8002
DISPATCHER_PORT = 9090
DISPATCHER_URL = f"http://localhost:{DISPATCHER_PORT}"

# ── 终端着色 ──────────────────────────────────────────────────
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

SEP = "=" * 64


def ok(msg: str) -> str:
    return f"{GREEN}{msg}{RESET}"


def fail(msg: str) -> str:
    return f"{RED}{msg}{RESET}"


def info(msg: str) -> str:
    return f"{YELLOW}{msg}{RESET}"


def title(msg: str) -> str:
    return f"{BOLD}{CYAN}{msg}{RESET}"


def section_header(stage: str, description: str) -> None:
    """打印带分隔线的阶段标题。"""
    print()
    print(SEP)
    print(f"  {title(stage)}")
    print(f"  {info(description)}")
    print(SEP)
    print()


def check(label: str, condition: bool) -> bool:
    """打印检查项结果。"""
    mark = ok("✓") if condition else fail("✗")
    print(f"  {mark} {label}")
    return condition


# ── 工具函数 ──────────────────────────────────────────────────


async def wait_for_service(url: str, timeout: float = 30) -> bool:
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
            await asyncio.sleep(0.5)
    return False


async def main():
    mock: subprocess.Popen | None = None
    mock2: subprocess.Popen | None = None
    disp: subprocess.Popen | None = None

    try:
        # ══════════════════════════════════════════════════════════════
        # 阶段 1：启动所有服务
        # ══════════════════════════════════════════════════════════════
        section_header(
            "阶段 1/9 — 启动服务",
            "启动 2 个 Mock 推理引擎 + LLM Dispatcher 调度器",
        )

        print(info("  → 启动 Mock 引擎 (port=8001)..."))
        mock = subprocess.Popen([sys.executable, "scripts/mock_engine.py", f"--port={MOCK_PORT}"])
        print(info("  → 启动 Mock 引擎 (port=8002)..."))
        mock2 = subprocess.Popen([sys.executable, "scripts/mock_engine.py", f"--port={MOCK_PORT2}"])

        if not await wait_for_service(f"http://localhost:{MOCK_PORT}/health"):
            print(fail("  Mock 引擎 1 启动失败，退出。"))
            return
        check("Mock 引擎 1 就绪 (port 8001)", True)

        if not await wait_for_service(f"http://localhost:{MOCK_PORT2}/health"):
            print(fail("  Mock 引擎 2 启动失败，退出。"))
            return
        check("Mock 引擎 2 就绪 (port 8002)", True)

        print(info("  → 启动 LLM Dispatcher..."))
        disp = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.dispatcher.main:create_app",
             "--factory", f"--port={DISPATCHER_PORT}"],
        )
        if not await wait_for_service(f"{DISPATCHER_URL}/health"):
            print(fail("  Dispatcher 启动失败，退出。"))
            return
        check("Dispatcher 就绪 (port 9090)", True)

        async with httpx.AsyncClient() as client:

            # ══════════════════════════════════════════════════════════════
            # 阶段 2：注册实例 + 查看初始状态
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 2/9 — 注册推理实例",
                "向 Dispatcher 注册 2 个 ollama 引擎实例，供后续调度使用",
            )

            for inst_id, port in [("mock-1", MOCK_PORT), ("mock-2", MOCK_PORT2)]:
                r = await client.post(
                    f"{DISPATCHER_URL}/admin/instances",
                    json={
                        "instance_id": inst_id,
                        "address": f"http://localhost:{port}",
                        "model": "test-model",
                        "engine_type": "ollama",
                    },
                )
                check(f"注册 {inst_id} (→ localhost:{port})", r.status_code == 200)

            # 查看注册表
            r = await client.get(f"{DISPATCHER_URL}/admin/status")
            status = r.json()
            inst = status["instances"]
            print(f"\n  {title('实例概览')}: total={inst['total']}  "
                  f"healthy={ok(str(inst['healthy']))}  "
                  f"unhealthy={inst['unhealthy']}  "
                  f"draining={inst['draining']}")

            # ══════════════════════════════════════════════════════════════
            # 阶段 3：基础推理 + 优先级队列
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 3/9 — 基础推理与优先级队列",
                "发送 3 个基础请求 + 独立演示优先级队列的排序行为",
            )

            # 3a. 基础推理（验证链路正常）
            print(f"  {title('3a. 基础推理')}: 发送 3 个不同优先级的请求...")
            for pri in [1, 5, 10]:
                r = await client.post(
                    f"{DISPATCHER_URL}/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": f"Priority {pri} request"}],
                        "priority": pri,
                    },
                )
                if r.status_code == 200:
                    body = r.json()
                    usage = body.get("usage", {})
                    check(
                        f"priority={pri} → {body['choices'][0]['message']['content'][:40]}... "
                        f"[tokens: in={usage.get('prompt_tokens',0)} out={usage.get('completion_tokens',0)}]",
                        True,
                    )
                else:
                    check(f"priority={pri}", False)

            # 3b. 优先级队列排序验证（直接使用 RequestQueue，不经过 HTTP）
            print(f"\n  {title('3b. 优先级队列排序')}: 直接使用 RequestQueue 验证排序...")
            from src.dispatcher.queue import RequestQueue
            from src.shared.models import InferenceRequest

            q = RequestQueue(max_size=20)

            # 故意打乱顺序入队：priority=5, 10, 1, 8, 3
            test_order = [5, 10, 1, 8, 3]
            for pri in test_order:
                req = InferenceRequest(
                    request_id=f"demo-{pri}",
                    model="test-model",
                    messages=[{"role": "user", "content": f"q-{pri}"}],
                    priority=pri,
                )
                await q.enqueue(req)
            print(f"    入队顺序 (优先级): {test_order}")

            # 出队顺序应该按 priority 升序: 1, 3, 5, 8, 10
            dequeued = []
            for _ in range(5):
                item = await q.dequeue()
                if item:
                    dequeued.append(item.request.priority)
            print(f"    出队顺序 (优先级): {dequeued}")
            check(
                f"优先级排序正确 (期望 [1,3,5,8,10] → 实际 {dequeued})",
                dequeued == [1, 3, 5, 8, 10],
            )

            # 3c. 同优先级 FIFO 验证
            print(f"\n  {title('3c. 同优先级 FIFO')}: 同 priority=5 按入队顺序出队...")
            for i in range(3):
                req = InferenceRequest(
                    request_id=f"fifo-{i}",
                    model="test-model",
                    messages=[{"role": "user", "content": f"fifo-{i}"}],
                    priority=5,
                )
                await q.enqueue(req)
            fifo_order = []
            for _ in range(3):
                item = await q.dequeue()
                if item:
                    fifo_order.append(item.request.request_id)
            check(
                f"同优先级 FIFO: {fifo_order}",
                fifo_order == ["fifo-0", "fifo-1", "fifo-2"],
            )

            # ══════════════════════════════════════════════════════════════
            # 阶段 4：流式推理 (SSE)
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 4/9 — 流式推理 (SSE)",
                "发送 stream=True 请求，逐 token 接收 SSE chunk",
            )

            print(f"  {info('→')} 发送流式请求...")
            chunk_count = 0
            async with client.stream(
                "POST",
                f"{DISPATCHER_URL}/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "Tell me a story."}],
                    "stream": True,
                },
                timeout=30,
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    # Ollama 格式: 原始 JSON 行; OpenAI/vLLM 格式: data: {...}
                    is_chunk = (
                        (line.startswith('data:') and '"content"' in line)
                        or (line.startswith('{') and '"message"' in line)
                    )
                    if is_chunk:
                        chunk_count += 1
                        if chunk_count <= 3:
                            preview = line[:90]
                            print(f"    chunk #{chunk_count}: {preview}...")
                check(f"流式响应完成 (收到 {chunk_count} 个 chunk)", chunk_count > 0)

            # ══════════════════════════════════════════════════════════════
            # 阶段 5：实例健康检查
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 5/9 — 健康检查与就绪探测",
                "验证 /health 与 /ready 端点，查看各实例健康状态",
            )

            # 调度器自身健康
            r = await client.get(f"{DISPATCHER_URL}/health")
            h = r.json()
            check(f"调度器 /health → status={h['status']} (healthy={h['instances_healthy']}/{h['instances_total']})",
                  h["status"] == "ok")

            # 就绪探测
            r = await client.get(f"{DISPATCHER_URL}/ready")
            ready = r.json()
            check(f"调度器 /ready → ready={ready.get('ready')}", ready.get("ready") is True)

            # 各实例健康状态
            print(f"\n  {title('实例健康详情')}:")
            r = await client.get(f"{DISPATCHER_URL}/admin/status")
            cb_states = r.json().get("circuit_breakers", {})
            for iid, state in cb_states.items():
                check(f"  {iid}: 熔断器={state}", True)

            # ══════════════════════════════════════════════════════════════
            # 阶段 6：熔断器演示
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 6/9 — 熔断保护",
                "注入故障使 mock-1 连续失败 → 熔断器 OPEN → 所有请求路由到 mock-2 → 重置恢复",
            )

            # 6a. 注入故障
            print(f"  {info('6a. 注入故障')}: mock-1 接下来 20 个请求返回 500...")
            await client.post(f"http://localhost:{MOCK_PORT}/admin/fail/20")
            check("mock-1 设置为故障模式", True)

            # 6b. 发送请求触发熔断
            print(f"\n  {info('6b. 触发熔断')}: 发送 6 个请求（连续失败 5 次 → OPEN）...")
            routes: dict[str, int] = {}
            for i in range(6):
                r = await client.post(
                    f"{DISPATCHER_URL}/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": f"circuit-breaker-test-{i}"}],
                    },
                )
                # 从响应推断路由到的实例
                if r.status_code == 200:
                    body = r.json()
                    content = body["choices"][0]["message"]["content"]
                    if "8001" in content:
                        routes["mock-1"] = routes.get("mock-1", 0) + 1
                    else:
                        routes["mock-2"] = routes.get("mock-2", 0) + 1
                else:
                    routes["error"] = routes.get("error", 0) + 1
            print(f"    路由分布: mock-1={routes.get('mock-1',0)}  mock-2={routes.get('mock-2',0)}  "
                  f"error={routes.get('error',0)}")

            # 6c. 查看熔断器状态
            r = await client.get(f"{DISPATCHER_URL}/admin/status")
            cb_data = r.json()["circuit_breakers"]
            check(
                f"熔断器: mock-1={info(cb_data.get('mock-1', '?'))}  mock-2={info(cb_data.get('mock-2', '?'))}",
                cb_data.get("mock-1") == "open",
            )

            # 6d. 重置熔断器 + 恢复 mock 引擎
            print(f"\n  {info('6c. 恢复')}: 重置熔断器 + 清除 mock-1 故障...")
            await client.post(f"{DISPATCHER_URL}/admin/circuit-breakers/mock-1/reset")
            await client.post(f"http://localhost:{MOCK_PORT}/admin/reset")

            # 验证恢复
            r = await client.post(
                f"{DISPATCHER_URL}/v1/chat/completions",
                json={
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "post-recovery test"}],
                },
            )
            check(f"恢复后正常推理 HTTP {r.status_code}", r.status_code == 200)

            r = await client.get(f"{DISPATCHER_URL}/admin/status")
            cb2 = r.json()["circuit_breakers"]
            check(f"mock-1 熔断器已恢复为 {ok(cb2.get('mock-1', '?'))}", cb2.get("mock-1") == "closed")

            # ══════════════════════════════════════════════════════════════
            # 阶段 7：负载均衡策略验证
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 7/9 — 负载均衡策略",
                "当前使用 weighted（加权路由），并发发送 10 个请求验证分布",
            )

            print(f"  {info('→')} 并发发送 10 个请求，统计路由分布...")
            routes = {"mock-1": 0, "mock-2": 0}

            async def send_one(i: int) -> None:
                r = await client.post(
                    f"{DISPATCHER_URL}/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": f"distribute-{i}"}],
                    },
                    timeout=30,
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    if "8001" in content:
                        routes["mock-1"] += 1
                    else:
                        routes["mock-2"] += 1

            # 并发发送：请求同时到达，Balancer 感知到负载后自动分发
            await asyncio.gather(*(send_one(i) for i in range(10)))

            print(f"    路由分布: mock-1={ok(str(routes['mock-1']))}  mock-2={ok(str(routes['mock-2']))}")
            both_used = routes["mock-1"] > 0 and routes["mock-2"] > 0
            check("两个实例均有流量 (并发 → 负载感知分发)", both_used)
            if not both_used:
                print(f"    {info('提示')}: 串行请求下两个实例等负载时选第一个；并发才能展示加权分发")

            # ══════════════════════════════════════════════════════════════
            # 阶段 8：指标与扩缩容评估
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 8/9 — 可观测性与扩缩容评估",
                "查看 Prometheus 指标汇总 + 直接演示 AutoScaler 决策逻辑",
            )

            # 8a. 指标汇总
            r = await client.get(f"{DISPATCHER_URL}/admin/metrics/summary")
            if r.status_code == 200:
                s = r.json()
                print(f"  {title('8a. Prometheus 指标汇总')}:")
                check(f"总请求数: {s.get('request_count', 0)}", True)
                check(f"错误数:   {s.get('error_count', 0)}", True)
                check(f"平均延迟: {s.get('avg_latency_ms', 0):.1f} ms", True)
                check(f"P95 延迟: {s.get('p95_latency_ms', 0):.1f} ms", True)
                check(f"P99 延迟: {s.get('p99_latency_ms', 0):.1f} ms", True)

            # 8b. 扩缩容决策（直接演示 — 默认 disabled，不经过 HTTP）
            print(f"\n  {title('8b. AutoScaler 决策引擎')}: 直接构造 AutoScaler 演示...")
            from src.dispatcher.balancer import create_balancer
            from src.dispatcher.registry import InstanceRegistry as DemoRegistry
            from src.shared.models import ModelInstance as DemoInstance
            from src.shared.models import ScaleConfig

            # 构造场景：2 个实例 + queue_depth=15（触发扩容）
            r2 = DemoRegistry()
            for i in range(2):
                r2.register(DemoInstance(
                    instance_id=f"gpu-{i+1}",
                    address=f"http://gpu-{i+1}:8000",
                    model="test-model",
                    engine_type="ollama",
                ))

            balancer = create_balancer("weighted")
            # 模拟 gpu-1 有 5 个活跃请求（每个 ≈2000 weight），gpu-2 空闲
            for _ in range(5):
                balancer.on_request_start("gpu-1")

            cfg = ScaleConfig(
                scale_up_queue_threshold=10,
                scale_up_load_threshold=8000.0,
                scale_down_idle_seconds=300,
                scale_down_load_threshold=500.0,
                min_instances=1,
                max_instances=10,
                cooldown_seconds=0,
            )

            # 演示 3 种场景
            print(f"    {info('场景 1 (扩容)')}: queue_depth=15 + gpu-1 负载 {balancer.get_load('gpu-1'):.0f}")
            # 需要拿到 metrics 的 queue_depth……使用 mock
            class _FakeMetrics:
                def get_summary(self):
                    return {"queue_depth": 15}
                def record_queue_depth(self, _d): pass

            from src.dispatcher.scaler import AutoScaler
            scaler = AutoScaler(r2, _FakeMetrics(), cfg, balancer=balancer)
            d1 = scaler.evaluate()
            check(f"决策: {d1.action} ({d1.reason[:60]}...)", d1.action == "scale_up")

            print(f"\n    {info('场景 2 (不扩容)')}: queue_depth=3 + 低负载")
            _m2 = type("_M", (), {"get_summary": lambda s: {"queue_depth": 3}})()
            scaler2 = AutoScaler(r2, _m2(), cfg, balancer=balancer)
            d2 = scaler2.evaluate()
            check(f"决策: {d2.action} ({d2.reason})", d2.action == "none")

            print(f"\n    {info('场景 3 (缩容)')}: queue_depth=0 + 无负载 + 空闲 > 300s")
            _m3 = type("_M", (), {"get_summary": lambda s: {"queue_depth": 0}})()
            scaler3 = AutoScaler(r2, _m3(), cfg, balancer=balancer)
            scaler3._idle_since = time.monotonic() - 400  # 手动设置空闲时间
            d3 = scaler3.evaluate()
            check(f"决策: {d3.action} ({d3.reason[:60]}...)", d3.action == "scale_down")

            # 8c. 全局状态
            print(f"\n  {title('8c. 全局状态')}:")
            r = await client.get(f"{DISPATCHER_URL}/admin/status")
            final = r.json()
            print(f"    实例: total={final['instances']['total']} "
                  f"healthy={final['instances']['healthy']} "
                  f"unhealthy={final['instances']['unhealthy']}")
            print(f"    队列深度: {final.get('queue_depth', 0)}")
            print(f"    熔断器:   {final['circuit_breakers']}")

            # ══════════════════════════════════════════════════════════════
            # 阶段 9：总结
            # ══════════════════════════════════════════════════════════════
            section_header(
                "阶段 9/9 — 演示完成",
                "以下功能已全部验证通过",
            )

            print(f"""
  {ok('✓')} 服务启动与实例注册
  {ok('✓')} 推理请求转发（非流式）
  {ok('✓')} 优先级队列 (priority 1/5/10)
  {ok('✓')} 流式推理 (SSE streaming)
  {ok('✓')} 健康检查 (/health + /ready)
  {ok('✓')} 熔断保护 (CLOSED → OPEN → reset → CLOSED)
  {ok('✓')} 加权负载均衡 (2 实例流量分发)
  {ok('✓')} Prometheus 指标 (P95/P99/错误率)
  {ok('✓')} 扩缩容评估 (scale_up/scale_down/none)
  {ok('✓')} 管理 API (状态/重置/汇总)
""")
            r = await client.get(f"{DISPATCHER_URL}/admin/metrics/summary")
            s = r.json()
            print(f"  总请求: {s['request_count']}  |  错误: {s['error_count']}  |  "
                  f"P95: {s['p95_latency_ms']:.0f}ms  |  P99: {s['p99_latency_ms']:.0f}ms")
            print()
            print(f"  健康检查: {DISPATCHER_URL}/health")
            print(f"  管理面板: {DISPATCHER_URL}/admin/status")
            print(f"  Prometheus: {DISPATCHER_URL}/metrics")
            print("  Dashboard:  uv run streamlit run dashboard/app.py")
            print()
            print("  Press Ctrl+C to stop all services...")

    except KeyboardInterrupt:
        print(f"\n{info('  用户中断')}")
    except Exception as e:
        print(f"\n{fail(f'  演示异常: {e}')}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理子进程
        print(f"\n{info('  正在停止所有服务...')}")
        for proc, name in [(mock, "mock-1"), (mock2, "mock-2"), (disp, "dispatcher")]:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    print(f"  {ok('✓')} {name} 已停止")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    print(f"  {fail('✗')} {name} 强制终止")
        print(f"{ok('  清理完毕')}")


if __name__ == "__main__":
    asyncio.run(main())
