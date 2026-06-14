"""Sprint 2 & 3 集成测试 — 规格书 §5 Sprint 2-3 验收标准。

跨模块验证：Queue→Proxy、CircuitBreaker→Proxy、Weighted→Proxy、Batch→Proxy、
Metrics→Proxy、AutoScaler、Admin API、vLLM/TGI Adapters。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry

from src.dispatcher.balancer import create_balancer
from src.dispatcher.balancer.weighted import WeightedBalancer
from src.dispatcher.batcher import DynamicBatcher
from src.dispatcher.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry, InstanceCircuitBreaker
from src.dispatcher.engine import create_adapter_registry
from src.dispatcher.engine.tgi import TGIAdapter
from src.dispatcher.engine.vllm import VLLMAdapter
from src.dispatcher.metrics import PrometheusMetricsRecorder
from src.dispatcher.proxy import RoutingProxy
from src.dispatcher.queue import RequestQueue
from src.dispatcher.registry import InstanceRegistry
from src.dispatcher.scaler import AutoScaler
from src.shared.models import (
    CircuitBreaker,
    InferenceRequest,
    LoadBalancer,
    ModelInstance,
    NoAvailableInstanceError,
    ScaleConfig,
    TokenUsage,
)


@pytest.fixture
def registry() -> InstanceRegistry:
    r = InstanceRegistry()
    r.register(
        ModelInstance(instance_id="i1", address="http://localhost:8001", model="test-model", engine_type="ollama")
    )
    r.register(
        ModelInstance(instance_id="i2", address="http://localhost:8002", model="test-model", engine_type="ollama")
    )
    return r


@pytest.fixture
def engine_adapters() -> dict:
    return create_adapter_registry()


# ═══════════════════════════════════════════════════════════════
# Sprint 2 集成测试
# ═══════════════════════════════════════════════════════════════

# ── 1. Queue → Proxy ───────────────────────────────────────


class TestQueueProxy:
    """验证请求入队后出队能正确调用 proxy.forward()。"""

    @pytest.mark.asyncio
    async def test_enqueue_dequeue_to_proxy(self, registry: InstanceRegistry, engine_adapters: dict) -> None:
        q = RequestQueue(max_size=10, max_wait_seconds=30)
        proxy = RoutingProxy(
            registry=registry, balancer=create_balancer("round_robin"), engine_adapters=engine_adapters
        )

        req = InferenceRequest(
            request_id="req-q1", model="test-model", messages=[{"role": "user", "content": "Hi"}], priority=1
        )
        await q.enqueue(req)

        item = await q.dequeue()
        assert item is not None

        mock_resp = {"message": {"role": "assistant", "content": "OK"}, "eval_count": 1, "prompt_eval_count": 1}
        mock_client = MagicMock()

        async def _post(*a, **kw):
            r = MagicMock()
            r.json.return_value = mock_resp
            r.status_code = 200
            return r

        mock_client.post = _post

        with patch.object(proxy, "_get_client", return_value=mock_client):
            result = await proxy.forward(item.request)
            assert result.status == "ok"


# ── 2. CircuitBreaker → Proxy ─────────────────────────────


class TestCircuitBreakerProxy:
    """验证实例熔断后 Proxy 自动跳过；恢复后流量恢复。"""

    @pytest.mark.asyncio
    async def test_open_circuit_skips_instance(self, registry: InstanceRegistry, engine_adapters: dict) -> None:
        class AlwaysOpenCB(CircuitBreaker):
            def allow_request(self) -> bool:
                return False

            def record_success(self) -> None:
                pass

            def record_failure(self) -> None:
                pass

            @property
            def state(self) -> str:
                return "open"

        balancer = MagicMock(spec=LoadBalancer)
        balancer.select.return_value = registry.get("i2")

        proxy = RoutingProxy(
            registry=registry,
            balancer=balancer,
            engine_adapters=engine_adapters,
            circuit_breakers={"i1": AlwaysOpenCB()},
        )
        req = InferenceRequest(request_id="r1", model="test-model", messages=[])
        mock_resp = {"message": {"role": "assistant", "content": "OK"}, "eval_count": 1, "prompt_eval_count": 1}
        mock_client = MagicMock()

        async def _post(*a, **kw):
            r = MagicMock()
            r.json.return_value = mock_resp
            r.status_code = 200
            return r

        mock_client.post = _post

        with patch.object(proxy, "_get_client", return_value=mock_client):
            result = await proxy.forward(req)
            assert result.instance_id == "i2"

    def test_circuit_breaker_recovery_flow(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=2, recovery_timeout=0.01, half_open_max_calls=1)
        cb = InstanceCircuitBreaker("test", config)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"

        import time

        time.sleep(0.02)
        assert cb.allow_request() is True
        assert cb.state == "half_open"
        cb.record_success()
        assert cb.state == "closed"


# ── 3. LoadBalancer(Weighted) → Proxy ────────────────────


class TestWeightedBalancerProxy:
    """验证大请求被路由到负载较轻的实例。"""

    @pytest.mark.asyncio
    async def test_weighted_routes_large_request_to_light_instance(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        balancer = WeightedBalancer()
        # 手动设置负载
        balancer._loads["i1"] = 500.0  # heavy
        balancer._loads["i2"] = 50.0  # light

        req = InferenceRequest(
            request_id="r1",
            model="test-model",
            messages=[{"role": "user", "content": "x" * 400}],
            max_tokens=200,
        )
        candidates = registry.list_by_model("test-model")
        selected = balancer.select(candidates, req)
        assert selected.instance_id == "i2"


# ── 4. Batch → Proxy ──────────────────────────────────────


class TestBatchProxy:
    """验证 batcher.flush() 并发调用 proxy.forward()。"""

    @pytest.mark.asyncio
    async def test_batcher_flush_calls_proxy_concurrent(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        proxy = RoutingProxy(
            registry=registry, balancer=create_balancer("round_robin"), engine_adapters=engine_adapters
        )
        batcher = DynamicBatcher(proxy, max_batch_size=2, max_wait_ms=5000)

        mock_resp = {"message": {"role": "assistant", "content": "OK"}, "eval_count": 1, "prompt_eval_count": 1}
        mock_client = MagicMock()

        async def _post(*a, **kw):
            r = MagicMock()
            r.json.return_value = mock_resp
            r.status_code = 200
            return r

        mock_client.post = _post

        with patch.object(proxy, "_get_client", return_value=mock_client):
            r1 = InferenceRequest(request_id="b1", model="test-model", messages=[])
            r2 = InferenceRequest(request_id="b2", model="test-model", messages=[])
            resp1, resp2 = await asyncio.gather(batcher.submit(r1), batcher.submit(r2))
            assert resp1.status == "ok"
            assert resp2.status == "ok"
            assert batcher.total_batches_sent >= 1


# ── 5. 熔断器全开时 POST 返回 503 ──────────────────────────


class TestAllCircuitBreakersOpen:
    @pytest.mark.asyncio
    async def test_all_breakers_open_returns_503(self, registry: InstanceRegistry, engine_adapters: dict) -> None:
        class AlwaysOpenCB(CircuitBreaker):
            def allow_request(self) -> bool:
                return False

            def record_success(self) -> None:
                pass

            def record_failure(self) -> None:
                pass

            @property
            def state(self) -> str:
                return "open"

        proxy = RoutingProxy(
            registry=registry,
            balancer=create_balancer("round_robin"),
            engine_adapters=engine_adapters,
            circuit_breakers={"i1": AlwaysOpenCB(), "i2": AlwaysOpenCB()},
        )
        req = InferenceRequest(request_id="r1", model="test-model", messages=[])

        with pytest.raises(NoAvailableInstanceError):
            await proxy.forward(req)


# ═══════════════════════════════════════════════════════════════
# Sprint 3 集成测试
# ═══════════════════════════════════════════════════════════════

# ── 6. MetricsRecorder → Proxy ────────────────────────────


class TestMetricsProxy:
    """验证转发请求后 metrics 自动记录延迟和 token 消耗。"""

    @pytest.mark.asyncio
    async def test_metrics_records_on_forward(self, registry: InstanceRegistry, engine_adapters: dict) -> None:
        metrics = PrometheusMetricsRecorder(registry=CollectorRegistry())
        proxy = RoutingProxy(
            registry=registry,
            balancer=create_balancer("round_robin"),
            engine_adapters=engine_adapters,
            metrics=metrics,
        )
        req = InferenceRequest(request_id="r1", model="test-model", messages=[])

        mock_resp = {"message": {"role": "assistant", "content": "Hi"}, "eval_count": 5, "prompt_eval_count": 2}
        mock_client = MagicMock()

        async def _post(*a, **kw):
            r = MagicMock()
            r.json.return_value = mock_resp
            r.status_code = 200
            return r

        mock_client.post = _post

        with patch.object(proxy, "_get_client", return_value=mock_client):
            await proxy.forward(req)

        summary = metrics.get_summary()
        assert summary["request_count"] == 1
        assert summary["error_count"] == 0


# ── 7. AutoScaler → MetricsRecorder + Registry ────────────


class TestAutoScalerIntegration:
    """验证 evaluate() 正确读取 metrics 汇总数据和实例数量。"""

    def test_autoscaler_reads_metrics_and_registry(self, registry: InstanceRegistry) -> None:
        metrics = PrometheusMetricsRecorder(registry=CollectorRegistry())
        metrics.record_request("i1", 100.0, 50.0, TokenUsage(prompt_tokens=10, completion_tokens=5), success=True)
        metrics.record_queue_depth(15)

        config = ScaleConfig(
            scale_up_queue_threshold=10,
            scale_up_load_threshold=0.1,
            scale_down_idle_seconds=300,
            scale_down_load_threshold=0.01,
            min_instances=1,
            max_instances=10,
            cooldown_seconds=1,
        )
        scaler = AutoScaler(registry, metrics, config)
        scaler._last_scale_time = 0  # bypass cooldown
        decision = scaler.evaluate()
        assert decision.action == "scale_up"

    def test_autoscaler_no_action_when_idle(self, registry: InstanceRegistry) -> None:
        metrics = PrometheusMetricsRecorder(registry=CollectorRegistry())
        config = ScaleConfig()
        scaler = AutoScaler(registry, metrics, config)
        decision = scaler.evaluate()
        assert decision.action == "none"


# ── 8. Admin API → Registry ───────────────────────────────


class TestAdminRegistry:
    """验证通过 Admin API 注册/注销实例后 Registry 状态正确。"""

    def test_register_updates_registry(self) -> None:
        r = InstanceRegistry()
        inst = ModelInstance(instance_id="new-1", address="http://localhost:9000", model="m1", engine_type="ollama")
        r.register(inst)
        assert r.count == 1
        assert r.get("new-1").model == "m1"

    def test_deregister_updates_registry(self, registry: InstanceRegistry) -> None:
        registry.deregister("i1")
        assert registry.count == 1
        with pytest.raises(KeyError):
            registry.get("i1")


# ── 9. Admin API → CircuitBreaker ─────────────────────────


class TestAdminCircuitBreaker:
    def test_reset_circuit_breaker(self) -> None:
        cb_registry = CircuitBreakerRegistry()
        cb = cb_registry.get_or_create("test-inst")
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"

        cb = cb_registry.get_or_create("test-inst")
        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0


# ── 10. EngineAdapter(vLLM/TGI) → Proxy ───────────────────


class TestEngineAdapters:
    def test_vllm_adapter_build_request(self) -> None:
        adapter = VLLMAdapter()
        inst = ModelInstance(instance_id="v1", address="http://gpu:8000", model="llama", engine_type="vllm")
        req = InferenceRequest(request_id="r1", model="llama", messages=[{"role": "user", "content": "Hi"}])
        url, headers, body = adapter.build_request(inst, req)
        assert url == "http://gpu:8000/v1/chat/completions"

    def test_vllm_adapter_parse_response(self) -> None:
        adapter = VLLMAdapter()
        raw = {
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        resp = adapter.parse_response(raw)
        assert resp.status == "ok"
        assert resp.content == "Hello"

    def test_tgi_adapter_build_request(self) -> None:
        adapter = TGIAdapter()
        inst = ModelInstance(instance_id="t1", address="http://gpu:8080", model="m", engine_type="tgi")
        req = InferenceRequest(
            request_id="r1",
            model="m",
            messages=[{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}],
        )
        url, headers, body = adapter.build_request(inst, req)
        assert url == "http://gpu:8080/generate"
        assert "<|user|>" in body["inputs"]
        assert "Hello" in body["inputs"]
        assert "<|assistant|>" in body["inputs"]
        assert "Hi" in body["inputs"]

    def test_tgi_adapter_parse_response(self) -> None:
        adapter = TGIAdapter()
        raw = {
            "generated_text": "Bonjour",
            "details": {"generated_tokens": 10, "prefill": [{"text": "hi", "tokens": [1, 2]}]},
        }
        resp = adapter.parse_response(raw)
        assert resp.status == "ok"
        assert resp.content == "Bonjour"
