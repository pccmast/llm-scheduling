"""Module 6: Routing Proxy — 规格书 §4 Module 6.

请求路由与转发引擎。协调 Registry、LoadBalancer、EngineAdapter、CircuitBreaker
完成请求转发。处理非流式和流式（SSE）两种响应模式。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Optional

import httpx

from src.shared.models import (
    CircuitBreaker,
    EngineAdapter,
    InferenceRequest,
    InferenceResponse,
    LoadBalancer,
    MetricsRecorder,
    ModelInstance,
    NoAvailableInstanceError,
    TokenUsage,
)
from src.dispatcher.registry import InstanceRegistry


class RoutingProxy:
    """请求路由与转发引擎。"""

    def __init__(self,
                 registry: InstanceRegistry,
                 balancer: LoadBalancer,
                 engine_adapters: dict[str, EngineAdapter],
                 circuit_breakers: dict[str, CircuitBreaker] | None = None,
                 metrics: MetricsRecorder | None = None,
                 timeout: float = 120.0) -> None:
        """
        Args:
            registry: 实例注册表。
            balancer: 负载均衡策略。
            engine_adapters: 引擎类型 → 适配器的映射。
            circuit_breakers: 实例 ID → 熔断器的映射。Sprint 1 可为空。
            metrics: 指标记录器。Sprint 1 可为 None。
            timeout: 上游转发超时（秒）。

        Precondition:
            circuit_breakers 中包含所有已注册实例的熔断器。
        """
        self._registry = registry
        self._balancer = balancer
        self._engine_adapters = engine_adapters
        self._circuit_breakers = circuit_breakers or {}
        self._metrics = metrics
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 客户端。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def forward(self, request: InferenceRequest) -> InferenceResponse:
        """转发非流式推理请求。

        流程：
        1. 从 registry 获取候选实例（list_by_model）
        2. 过滤掉熔断器处于 OPEN 状态的实例
        3. 用 balancer 选择实例
        4. 调用 balancer.on_request_start(instance_id, request)
        5. 用 engine_adapter 构建请求
        6. 用 httpx 转发
        7. 解析响应
        8. 记录指标 + 更新熔断器状态
        9. 调用 balancer.on_request_end(instance_id, request)

        Args:
            request: 推理请求。

        Returns:
            InferenceResponse，status 可能为 "ok"、"error" 或 "timeout"。

        Raises:
            NoAvailableInstanceError: 没有可用的健康实例。

        Postcondition:
            balancer.on_request_end() 和 metrics.record_request() 一定被调用。
        """
        start_time = time.monotonic()
        instance: Optional[ModelInstance] = None

        try:
            # 1. 获取候选实例
            candidates = self._registry.list_by_model(request.model)
            if not candidates:
                raise NoAvailableInstanceError(request.model)

            # 2. 过滤熔断器 OPEN 的实例
            available = self._filter_by_circuit_breaker(candidates)
            if not available:
                raise NoAvailableInstanceError(request.model)

            # 3. 选择实例
            instance = self._balancer.select(available, request)

            # 4. 记录请求开始
            self._balancer.on_request_start(instance.instance_id, request)

            # 5. 获取适配器并构建请求
            adapter = self._get_adapter(instance)
            url, headers, body = adapter.build_request(instance, request)

            # 6. 转发
            client = self._get_client()
            response = await client.post(url, json=body, headers=headers, timeout=self._timeout)
            raw = response.json()

            # 7. 解析响应
            inference_response = adapter.parse_response(raw)
            inference_response.request_id = request.request_id
            inference_response.instance_id = instance.instance_id
            inference_response.latency_ms = (time.monotonic() - start_time) * 1000

            # 8. 更新熔断器
            cb = self._circuit_breakers.get(instance.instance_id)
            if cb:
                cb.record_success()

            # 9. 记录指标
            if self._metrics:
                usage = inference_response.usage or TokenUsage()
                self._metrics.record_request(
                    instance.instance_id,
                    inference_response.latency_ms,
                    0.0,
                    usage,
                    success=True,
                )

            return inference_response

        except NoAvailableInstanceError:
            raise
        except httpx.TimeoutException:
            error_resp = InferenceResponse(
                request_id=request.request_id,
                instance_id=instance.instance_id if instance else "",
                status="timeout",
                error_message="Upstream timeout",
                latency_ms=(time.monotonic() - start_time) * 1000,
            )
            self._record_failure(instance, error_resp, request, start_time)
            return error_resp
        except httpx.ConnectError:
            error_resp = InferenceResponse(
                request_id=request.request_id,
                instance_id=instance.instance_id if instance else "",
                status="error",
                error_message="Connection refused",
                latency_ms=(time.monotonic() - start_time) * 1000,
            )
            self._record_failure(instance, error_resp, request, start_time)
            return error_resp
        except Exception as exc:
            error_resp = InferenceResponse(
                request_id=request.request_id,
                instance_id=instance.instance_id if instance else "",
                status="error",
                error_message=str(exc),
                latency_ms=(time.monotonic() - start_time) * 1000,
            )
            self._record_failure(instance, error_resp, request, start_time)
            return error_resp
        finally:
            if instance:
                self._balancer.on_request_end(instance.instance_id, request)

    async def forward_stream(self, request: InferenceRequest) -> AsyncIterator[str]:
        """转发流式推理请求，返回 SSE chunk 的异步迭代器。

        流程与 forward() 类似，但使用 httpx.stream() 做流式转发。
        在流结束时记录指标。

        Yields:
            SSE 格式的字符串（"data: {...}\n\n"）。

        Raises:
            NoAvailableInstanceError: 没有可用的健康实例。
        """
        start_time = time.monotonic()
        ttft_recorded = False
        instance: Optional[ModelInstance] = None
        usage = TokenUsage()

        try:
            # 1. 获取候选实例
            candidates = self._registry.list_by_model(request.model)
            if not candidates:
                raise NoAvailableInstanceError(request.model)

            # 2. 过滤熔断器 OPEN
            available = self._filter_by_circuit_breaker(candidates)
            if not available:
                raise NoAvailableInstanceError(request.model)

            # 3. 选择实例
            instance = self._balancer.select(available, request)
            self._balancer.on_request_start(instance.instance_id, request)

            # 4. 构建请求
            adapter = self._get_adapter(instance)
            url, headers, body = adapter.build_request(instance, request)

            # 5. 流式转发
            client = self._get_client()
            async with client.stream("POST", url, json=body, headers=headers, timeout=self._timeout) as response:
                async for line in response.aiter_lines():
                    if not ttft_recorded and line.startswith("data:") and "content" in line:
                        ttft_ms = (time.monotonic() - start_time) * 1000
                        ttft_recorded = True

                    yield line + "\n"

            # 记录指标
            cb = self._circuit_breakers.get(instance.instance_id)
            if cb:
                cb.record_success()

            if self._metrics:
                self._metrics.record_request(
                    instance.instance_id,
                    (time.monotonic() - start_time) * 1000,
                    0.0,
                    usage,
                    success=True,
                )

        except NoAvailableInstanceError:
            raise
        except Exception as exc:
            error_event = json.dumps({
                "error": {"message": str(exc), "type": type(exc).__name__},
            })
            yield f"data: {error_event}\n\n"
            yield "data: [DONE]\n\n"
            if instance:
                cb = self._circuit_breakers.get(instance.instance_id)
                if cb:
                    cb.record_failure()
            if self._metrics and instance:
                self._metrics.record_request(
                    instance.instance_id, 0, 0, usage, success=False,
                )
        finally:
            if instance:
                self._balancer.on_request_end(instance.instance_id, request)

    def _filter_by_circuit_breaker(self, candidates: list[ModelInstance]) -> list[ModelInstance]:
        """过滤掉熔断器 OPEN 的实例。"""
        if not self._circuit_breakers:
            return candidates
        return [
            inst for inst in candidates
            if self._circuit_breakers.get(inst.instance_id) is None
            or self._circuit_breakers[inst.instance_id].allow_request()
        ]

    def _get_adapter(self, instance: ModelInstance) -> EngineAdapter:
        """获取实例对应的引擎适配器。"""
        adapter = self._engine_adapters.get(instance.engine_type)
        if adapter is None:
            raise NoAvailableInstanceError(instance.model)
        return adapter

    def _record_failure(self, instance: Optional[ModelInstance],
                        error_resp: InferenceResponse,
                        request: InferenceRequest,
                        start_time: float) -> None:
        """记录失败的请求（熔断器 + 指标）。"""
        if instance:
            cb = self._circuit_breakers.get(instance.instance_id)
            if cb:
                cb.record_failure()
            if self._metrics:
                self._metrics.record_request(
                    instance.instance_id,
                    error_resp.latency_ms,
                    0.0,
                    TokenUsage(),
                    success=False,
                )

    async def close(self) -> None:
        """关闭内部的 httpx.AsyncClient。在应用 shutdown 时调用。"""
        if self._client:
            await self._client.aclose()
            self._client = None
