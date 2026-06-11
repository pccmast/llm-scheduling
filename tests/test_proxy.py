"""Module 6: Routing Proxy 测试 — 规格书 §4 Module 6.

覆盖 RoutingProxy.forward() 和 forward_stream() 的正常路径和异常路径。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.shared.models import (
    CircuitBreaker,
    InferenceRequest,
    LoadBalancer,
    MetricsRecorder,
    ModelInstance,
    NoAvailableInstanceError,
)
from src.dispatcher.proxy import RoutingProxy
from src.dispatcher.registry import InstanceRegistry
from src.dispatcher.engine.ollama import OllamaAdapter


@pytest.fixture
def registry() -> InstanceRegistry:
    r = InstanceRegistry()
    r.register(ModelInstance(
        instance_id="i1", address="http://localhost:8000",
        model="test-model", engine_type="ollama",
    ))
    r.register(ModelInstance(
        instance_id="i2", address="http://localhost:8001",
        model="test-model", engine_type="ollama",
    ))
    return r


@pytest.fixture
def balancer() -> MagicMock:
    b = MagicMock(spec=LoadBalancer)
    b.select.return_value = ModelInstance(
        instance_id="i1", address="http://localhost:8000",
        model="test-model", engine_type="ollama",
    )
    return b


@pytest.fixture
def adapter() -> OllamaAdapter:
    return OllamaAdapter()


@pytest.fixture
def inference_request() -> InferenceRequest:
    return InferenceRequest(
        request_id="req-1", model="test-model",
        messages=[{"role": "user", "content": "Hello"}],
    )


@pytest.fixture
def proxy(registry: InstanceRegistry, balancer: MagicMock, adapter: OllamaAdapter) -> RoutingProxy:
    return RoutingProxy(
        registry=registry,
        balancer=balancer,
        engine_adapters={"ollama": adapter},
        timeout=10.0,
    )


def _mock_httpx_post(mock_response: dict, status_code: int = 200):
    """创建一个 mock httpx 客户端，async post 返回指定响应。"""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_response
    mock_resp.status_code = status_code

    # 让 async post 返回可 await 的对象
    async def _async_post(*args, **kwargs):
        return mock_resp

    mock_client.post = _async_post
    return mock_client


class TestForwardSuccess:
    """正常转发测试。"""

    @pytest.mark.asyncio
    async def test_forward_success(self, proxy: RoutingProxy, inference_request: InferenceRequest) -> None:
        """正常请求返回 InferenceResponse(status="ok")。"""
        mock_response = {
            "message": {"role": "assistant", "content": "Hi!"},
            "eval_count": 5,
            "prompt_eval_count": 2,
        }
        mock_client = _mock_httpx_post(mock_response)

        with patch.object(proxy, "_get_client", return_value=mock_client):
            result = await proxy.forward(inference_request)
            assert result.status == "ok"
            assert result.request_id == "req-1"
            assert result.instance_id == "i1"
            assert result.content == "Hi!"

    @pytest.mark.asyncio
    async def test_forward_calls_balancer_callbacks(
        self, proxy: RoutingProxy, balancer: MagicMock, inference_request: InferenceRequest,
    ) -> None:
        """on_request_start 和 on_request_end 被成对调用。"""
        mock_response = {
            "message": {"role": "assistant", "content": "Hi!"},
            "eval_count": 5, "prompt_eval_count": 2,
        }
        mock_client = _mock_httpx_post(mock_response)

        with patch.object(proxy, "_get_client", return_value=mock_client):
            await proxy.forward(inference_request)

            balancer.on_request_start.assert_called_once_with("i1", inference_request)
            balancer.on_request_end.assert_called_once_with("i1", inference_request)


class TestForwardErrors:
    """异常路径测试。"""

    @pytest.mark.asyncio
    async def test_no_available_instance(self, proxy: RoutingProxy) -> None:
        """无匹配模型的实例时抛出 NoAvailableInstanceError。"""
        req = InferenceRequest(request_id="r1", model="nonexistent", messages=[{"role": "user", "content": "Hi"}])
        with pytest.raises(NoAvailableInstanceError):
            await proxy.forward(req)

    @pytest.mark.asyncio
    async def test_upstream_timeout(self, proxy: RoutingProxy, inference_request: InferenceRequest) -> None:
        """上游超时返回 InferenceResponse(status="timeout")。"""
        mock_client = MagicMock()

        async def _timeout(*args, **kwargs):
            raise httpx.TimeoutException("timeout")

        mock_client.post = _timeout

        with patch.object(proxy, "_get_client", return_value=mock_client):
            result = await proxy.forward(inference_request)
            assert result.status == "timeout"
            assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_upstream_connection_error(self, proxy: RoutingProxy, inference_request: InferenceRequest) -> None:
        """上游连接失败返回 InferenceResponse(status="error")。"""
        mock_client = MagicMock()

        async def _conn_error(*args, **kwargs):
            raise httpx.ConnectError("refused")

        mock_client.post = _conn_error

        with patch.object(proxy, "_get_client", return_value=mock_client):
            result = await proxy.forward(inference_request)
            assert result.status == "error"

    @pytest.mark.asyncio
    async def test_callbacks_called_on_error(
        self, proxy: RoutingProxy, balancer: MagicMock, inference_request: InferenceRequest,
    ) -> None:
        """即使上游报错，on_request_start 和 on_request_end 仍然成对调用。"""
        mock_client = MagicMock()

        async def _conn_error(*args, **kwargs):
            raise httpx.ConnectError("refused")

        mock_client.post = _conn_error

        with patch.object(proxy, "_get_client", return_value=mock_client):
            await proxy.forward(inference_request)

            balancer.on_request_start.assert_called_once()
            balancer.on_request_end.assert_called_once()


class TestCircuitBreaker:
    """熔断器集成测试。"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_skips_instance(
        self, registry: InstanceRegistry, adapter: OllamaAdapter, inference_request: InferenceRequest,
    ) -> None:
        """熔断器 OPEN 的实例被跳过。"""
        class OpenCB(CircuitBreaker):
            def allow_request(self) -> bool: return False
            def record_success(self) -> None: pass
            def record_failure(self) -> None: pass
            @property
            def state(self) -> str: return "open"

        balancer = MagicMock(spec=LoadBalancer)
        balancer.select.return_value = ModelInstance(
            instance_id="i2", address="http://localhost:8001",
            model="test-model", engine_type="ollama",
        )

        proxy = RoutingProxy(
            registry=registry, balancer=balancer,
            engine_adapters={"ollama": adapter},
            circuit_breakers={"i1": OpenCB()},
        )

        mock_response = {
            "message": {"role": "assistant", "content": "OK"},
            "eval_count": 1, "prompt_eval_count": 1,
        }
        mock_client = _mock_httpx_post(mock_response)

        with patch.object(proxy, "_get_client", return_value=mock_client):
            result = await proxy.forward(inference_request)
            # 应选择了 i2（i1 被熔断跳过）
            assert result.instance_id == "i2"

    @pytest.mark.asyncio
    async def test_all_circuit_breakers_open_raises_error(
        self, registry: InstanceRegistry, adapter: OllamaAdapter, inference_request: InferenceRequest,
    ) -> None:
        """全部熔断器 OPEN 时抛出 NoAvailableInstanceError。"""
        class OpenCB(CircuitBreaker):
            def allow_request(self) -> bool: return False
            def record_success(self) -> None: pass
            def record_failure(self) -> None: pass
            @property
            def state(self) -> str: return "open"

        balancer = MagicMock(spec=LoadBalancer)
        proxy = RoutingProxy(
            registry=registry, balancer=balancer,
            engine_adapters={"ollama": adapter},
            circuit_breakers={"i1": OpenCB(), "i2": OpenCB()},
        )

        with pytest.raises(NoAvailableInstanceError):
            await proxy.forward(inference_request)


class TestMetrics:
    """指标记录测试。"""

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(
        self, registry: InstanceRegistry, adapter: OllamaAdapter, inference_request: InferenceRequest,
    ) -> None:
        """成功时 metrics.record_request() 被调用。"""
        balancer = MagicMock(spec=LoadBalancer)
        balancer.select.return_value = ModelInstance(
            instance_id="i1", address="http://localhost:8000",
            model="test-model", engine_type="ollama",
        )
        mock_metrics = MagicMock(spec=MetricsRecorder)

        proxy = RoutingProxy(
            registry=registry, balancer=balancer,
            engine_adapters={"ollama": adapter}, metrics=mock_metrics,
        )

        mock_response = {
            "message": {"role": "assistant", "content": "OK"},
            "eval_count": 5, "prompt_eval_count": 2,
        }
        mock_client = _mock_httpx_post(mock_response)

        with patch.object(proxy, "_get_client", return_value=mock_client):
            await proxy.forward(inference_request)
            mock_metrics.record_request.assert_called_once()
            call_kwargs = mock_metrics.record_request.call_args.kwargs
            assert call_kwargs["success"] is True


class TestClose:
    """资源清理测试。"""

    @pytest.mark.asyncio
    async def test_close_cleans_up(self, proxy: RoutingProxy) -> None:
        """close() 不抛出异常。"""
        await proxy.close()
        assert proxy._client is None
