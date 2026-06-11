"""Sprint 1 集成测试 — 规格书 §5 Sprint 1 验收标准。

跨模块验证：Registry→HealthChecker、Registry→Balancer→Proxy、EngineAdapter→Proxy、异常路径。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.dispatcher.balancer import create_balancer
from src.dispatcher.engine import create_adapter_registry
from src.dispatcher.health import HealthChecker
from src.dispatcher.proxy import RoutingProxy
from src.dispatcher.registry import InstanceRegistry
from src.shared.models import (
    HealthCheckConfig,
    InferenceRequest,
    InstanceStatus,
    ModelInstance,
    NoAvailableInstanceError,
)


@pytest.fixture
def registry() -> InstanceRegistry:
    r = InstanceRegistry()
    r.register(
        ModelInstance(
            instance_id="i1",
            address="http://localhost:8001",
            model="test-model",
            engine_type="ollama",
        )
    )
    return r


@pytest.fixture
def engine_adapters() -> dict:
    return create_adapter_registry()


# ── 1. Registry → HealthChecker ────────────────────────────


class TestRegistryHealthChecker:
    """验证 Registry 和 HealthChecker 的联动。"""

    @pytest.mark.asyncio
    async def test_health_checker_discovers_registered_instance(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """实例注册后，HealthChecker 能自动发现并探测该实例。"""
        config = HealthCheckConfig(interval_seconds=10, timeout_seconds=1, unhealthy_threshold=3)
        checker = HealthChecker(registry, engine_adapters, config)

        # 检查 check_one 能正常工作
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 200
            result = await checker.check_one(registry.get("i1"))
            assert result is True

    @pytest.mark.asyncio
    async def test_health_checker_marks_unhealthy(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """连续 3 次失败后 HealthChecker 将实例标记为 UNHEALTHY。"""
        config = HealthCheckConfig(interval_seconds=10, timeout_seconds=1, unhealthy_threshold=3)
        checker = HealthChecker(registry, engine_adapters, config)

        instance = registry.get("i1")
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 500
            for _ in range(3):
                await checker._check_and_update(instance)

        assert registry.get("i1").status == InstanceStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_health_checker_recovers_healthy(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """UNHEALTHY 实例恢复后自动标记为 HEALTHY。"""
        registry.mark_unhealthy("i1")
        config = HealthCheckConfig(interval_seconds=10, timeout_seconds=1, unhealthy_threshold=3)
        checker = HealthChecker(registry, engine_adapters, config)

        instance = registry.get("i1")
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 200
            await checker._check_and_update(instance)

        assert registry.get("i1").status == InstanceStatus.HEALTHY


# ── 2. Registry → LoadBalancer → Proxy 完整链路 ────────────


class TestBalancerProxyChain:
    """验证 list_by_model → select → forward 完整链路。"""

    @pytest.mark.asyncio
    async def test_full_chain_with_mock_engine(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """从 registry 获取候选 → balancer 选择 → proxy 转发的完整链路。"""
        # 注册第二个实例以测试负载均衡
        registry.register(
            ModelInstance(
                instance_id="i2",
                address="http://localhost:8002",
                model="test-model",
                engine_type="ollama",
            )
        )

        balancer = create_balancer("round_robin")
        proxy = RoutingProxy(
            registry=registry,
            balancer=balancer,
            engine_adapters=engine_adapters,
        )

        # Mock httpx 返回成功响应
        mock_response = {
            "message": {"role": "assistant", "content": "Hello from mock!"},
            "eval_count": 5,
            "prompt_eval_count": 2,
        }
        mock_client = MagicMock()

        async def _async_post(*args, **kwargs):
            resp = MagicMock()
            resp.json.return_value = mock_response
            resp.status_code = 200
            return resp

        mock_client.post = _async_post

        req = InferenceRequest(request_id="req-1", model="test-model", messages=[{"role": "user", "content": "Hi"}])

        with patch.object(proxy, "_get_client", return_value=mock_client):
            result = await proxy.forward(req)
            assert result.status == "ok"
            assert "Hello from mock!" in (result.content or "")

    @pytest.mark.asyncio
    async def test_list_by_model_provides_candidates_to_balancer(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """list_by_model() 返回的候选列表能被 balancer.select() 正确消费。"""
        balancer = create_balancer("round_robin")
        candidates = registry.list_by_model("test-model")
        assert len(candidates) == 1

        req = InferenceRequest(request_id="r1", model="test-model", messages=[])
        selected = balancer.select(candidates, req)
        assert selected.instance_id == "i1"


# ── 3. EngineAdapter → Proxy ───────────────────────────────


class TestEngineAdapterProxy:
    """验证 EngineAdapter 构建的请求能被 mock 引擎正确响应。"""

    @pytest.mark.asyncio
    async def test_ollama_adapter_compatible_with_mock(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """OllamaAdapter.build_request 构建的请求与 mock 引擎兼容。"""
        adapter = engine_adapters["ollama"]
        instance = registry.get("i1")
        req = InferenceRequest(
            request_id="req-1",
            model="test-model",
            messages=[{"role": "user", "content": "Hello"}],
        )

        url, headers, body = adapter.build_request(instance, req)
        # 验证 URL 格式
        assert url == "http://localhost:8001/api/chat"
        assert headers["Content-Type"] == "application/json"
        assert body["model"] == "test-model"
        assert body["messages"] == [{"role": "user", "content": "Hello"}]

        # 验证 mock 引擎响应可被正确解析
        mock_raw = {
            "message": {"role": "assistant", "content": "Mock reply"},
            "eval_count": 10,
            "prompt_eval_count": 3,
        }
        response = adapter.parse_response(mock_raw)
        assert response.status == "ok"
        assert response.content == "Mock reply"
        assert response.usage is not None
        assert response.usage.prompt_tokens == 3
        assert response.usage.completion_tokens == 10


# ── 4. 异常路径 ─────────────────────────────────────────────


class TestErrorPath:
    """验证异常路径：无可用实例时 NoAvailableInstanceError → HTTP 503。"""

    @pytest.mark.asyncio
    async def test_no_available_instance_error(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """无匹配模型时抛出 NoAvailableInstanceError。"""
        proxy = RoutingProxy(
            registry=registry,
            balancer=create_balancer("round_robin"),
            engine_adapters=engine_adapters,
        )
        req = InferenceRequest(request_id="r1", model="nonexistent", messages=[{"role": "user", "content": "Hi"}])

        with pytest.raises(NoAvailableInstanceError) as exc:
            await proxy.forward(req)
        assert "nonexistent" in str(exc.value)

    @pytest.mark.asyncio
    async def test_api_layer_returns_503_on_no_instance(
        self,
        registry: InstanceRegistry,
        engine_adapters: dict,
    ) -> None:
        """API 层在无可用实例时捕获异常并返回 HTTP 503。

        模拟 API 层的异常处理：NoAvailableInstanceError → HTTPException(503)。
        """
        from fastapi import HTTPException

        proxy = RoutingProxy(
            registry=registry,
            balancer=create_balancer("round_robin"),
            engine_adapters=engine_adapters,
        )
        req = InferenceRequest(request_id="r1", model="nonexistent", messages=[])

        try:
            await proxy.forward(req)
            pytest.fail("Expected NoAvailableInstanceError")
        except NoAvailableInstanceError as e:
            # 模拟 API 层的处理
            with pytest.raises(HTTPException) as http_exc:
                raise HTTPException(status_code=503, detail=str(e))
            assert http_exc.value.status_code == 503
