"""Module 6: Routing Proxy — 规格书 §4 Module 6.

请求路由与转发引擎。协调 Registry、LoadBalancer、EngineAdapter、CircuitBreaker
完成请求转发。处理非流式和流式（SSE）两种响应模式。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Optional

import httpx
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from src.dispatcher.circuit_breaker import CircuitBreakerRegistry
from src.dispatcher.registry import InstanceRegistry
from src.shared.models import (
    CircuitBreaker,
    DrainInProgressError,
    EngineAdapter,
    InferenceRequest,
    InferenceResponse,
    LoadBalancer,
    MetricsRecorder,
    MissingAdapterError,
    ModelInstance,
    NoAvailableInstanceError,
    TokenUsage,
)


class RoutingProxy:
    """请求路由与转发引擎。"""

    def __init__(
        self,
        registry: InstanceRegistry,
        balancer: LoadBalancer,
        engine_adapters: dict[str, EngineAdapter],
        circuit_breakers: dict[str, CircuitBreaker] | CircuitBreakerRegistry | None = None,
        metrics: MetricsRecorder | None = None,
        timeout: float = 120.0,
    ) -> None:
        """
        Args:
            registry: 实例注册表。
            balancer: 负载均衡策略。
            engine_adapters: 引擎类型 → 适配器的映射。
            circuit_breakers: 实例 ID → 熔断器的映射或 CircuitBreakerRegistry。
            metrics: 指标记录器。Sprint 1 可为 None。
            timeout: 上游转发超时（秒）。
        """
        self._registry = registry
        self._balancer = balancer
        self._engine_adapters = engine_adapters
        self._cb_dict: dict[str, CircuitBreaker] = circuit_breakers if isinstance(circuit_breakers, dict) else {}  # type: ignore[assignment]
        self._cb_registry: CircuitBreakerRegistry | None = (
            circuit_breakers if isinstance(circuit_breakers, CircuitBreakerRegistry) else None
        )  # type: ignore[assignment]
        self._metrics = metrics
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # Graceful shutdown state
        self._active_requests: int = 0
        self._draining: bool = False
        self._drain_event: asyncio.Event = asyncio.Event()

    def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 客户端。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
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
        if self._draining:
            raise DrainInProgressError()

        start_time = time.monotonic()
        instance: Optional[ModelInstance] = None
        self._active_requests += 1

        tracer = trace.get_tracer(__name__)
        root_span = tracer.start_span(
            "forward",
            attributes={
                "request_id": request.request_id,
                "model": request.model,
                "priority": request.priority,
                "estimated_weight": request.estimated_weight,
                "stream": request.stream,
            },
        )

        try:
            with tracer.start_as_current_span("balancer_select") as select_span:
                # 1. 获取候选实例
                candidates = self._registry.list_by_model(request.model)
                select_span.set_attribute("candidates_count", len(candidates))
                if not candidates:
                    raise NoAvailableInstanceError(request.model)

                # 2. 过滤熔断器 OPEN 的实例
                available = self._filter_by_circuit_breaker(candidates)
                select_span.set_attribute("available_count", len(available))
                if not available:
                    raise NoAvailableInstanceError(request.model)

                # 3. 选择实例
                instance = self._balancer.select(available, request)
                select_span.set_attribute("selected_instance", instance.instance_id)

            # 4. 记录请求开始
            self._balancer.on_request_start(instance.instance_id, request)

            # 5. 获取适配器并构建请求
            adapter = self._get_adapter(instance)
            url, headers, body = adapter.build_request(instance, request)

            # 6. 转发
            with tracer.start_as_current_span("engine_inference") as infer_span:
                infer_span.set_attribute("instance_id", instance.instance_id)
                infer_span.set_attribute("engine_type", instance.engine_type)
                client = self._get_client()
                response = await client.post(url, json=body, headers=headers, timeout=self._timeout)
                infer_span.set_attribute("http_status_code", response.status_code)

            # 6a. 检查 HTTP 状态码
            if response.status_code >= 500:
                root_span.set_status(Status(StatusCode.ERROR, f"Upstream returned {response.status_code}"))
                root_span.set_attribute("error", True)
                error_resp = InferenceResponse(
                    request_id=request.request_id,
                    instance_id=instance.instance_id,
                    status="error",
                    error_message=f"Upstream returned {response.status_code}",
                    latency_ms=(time.monotonic() - start_time) * 1000,
                )
                self._record_failure(instance, error_resp, request, start_time)
                return error_resp

            raw = response.json()

            # 7. 解析响应
            with tracer.start_as_current_span("parse_response"):
                inference_response = adapter.parse_response(raw)
                inference_response.request_id = request.request_id
                inference_response.instance_id = instance.instance_id
                inference_response.latency_ms = (time.monotonic() - start_time) * 1000

            # 8. 更新熔断器
            cb = self._get_cb(instance.instance_id)
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

            root_span.set_attribute("success", True)
            return inference_response

        except NoAvailableInstanceError:
            root_span.set_status(Status(StatusCode.ERROR, "No available instance"))
            root_span.set_attribute("error", True)
            raise
        except httpx.TimeoutException:
            root_span.set_status(Status(StatusCode.ERROR, "Upstream timeout"))
            root_span.set_attribute("error", True)
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
            root_span.set_status(Status(StatusCode.ERROR, "Connection refused"))
            root_span.set_attribute("error", True)
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
            root_span.set_status(Status(StatusCode.ERROR, str(exc)))
            root_span.set_attribute("error", True)
            root_span.record_exception(exc)
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
            latency_ms = (time.monotonic() - start_time) * 1000
            root_span.set_attribute("total_latency_ms", latency_ms)
            root_span.end()
            if instance:
                self._balancer.on_request_end(instance.instance_id, request)
            self._active_requests -= 1
            if self._draining and self._active_requests == 0:
                self._drain_event.set()

    async def forward_stream(self, request: InferenceRequest) -> AsyncIterator[str]:
        """转发流式推理请求，返回 SSE chunk 的异步迭代器。

        流程与 forward() 类似，但使用 httpx.stream() 做流式转发。
        在流结束时记录指标。

        Yields:
            SSE 格式的字符串（"data: {...}\n\n"）。

        Raises:
            NoAvailableInstanceError: 没有可用的健康实例。
        """
        if self._draining:
            raise DrainInProgressError()

        start_time = time.monotonic()
        ttft_recorded = False
        ttft_ms = 0.0
        instance: Optional[ModelInstance] = None
        self._active_requests += 1
        usage = TokenUsage()

        tracer = trace.get_tracer(__name__)
        root_span = tracer.start_span(
            "forward_stream",
            attributes={
                "request_id": request.request_id,
                "model": request.model,
                "priority": request.priority,
                "estimated_weight": request.estimated_weight,
            },
        )

        try:
            with tracer.start_as_current_span("balancer_select") as select_span:
                # 1. 获取候选实例
                candidates = self._registry.list_by_model(request.model)
                select_span.set_attribute("candidates_count", len(candidates))
                if not candidates:
                    raise NoAvailableInstanceError(request.model)

                # 2. 过滤熔断器 OPEN
                available = self._filter_by_circuit_breaker(candidates)
                select_span.set_attribute("available_count", len(available))
                if not available:
                    raise NoAvailableInstanceError(request.model)

                # 3. 选择实例
                instance = self._balancer.select(available, request)
                select_span.set_attribute("selected_instance", instance.instance_id)

            self._balancer.on_request_start(instance.instance_id, request)

            # 4. 构建请求
            adapter = self._get_adapter(instance)
            url, headers, body = adapter.build_request(instance, request)

            # 5. 流式转发
            with tracer.start_as_current_span("engine_inference") as infer_span:
                infer_span.set_attribute("instance_id", instance.instance_id)
                infer_span.set_attribute("engine_type", instance.engine_type)

                client = self._get_client()
                last_chunk = ""
                async with client.stream("POST", url, json=body, headers=headers, timeout=self._timeout) as response:
                    async for line in response.aiter_lines():
                        if not ttft_recorded and line.startswith("data:") and "content" in line:
                            ttft_ms = (time.monotonic() - start_time) * 1000
                            ttft_recorded = True
                            infer_span.set_attribute("ttft_ms", ttft_ms)

                        last_chunk = line
                        yield line + "\n"

                infer_span.set_attribute("ttft_recorded", ttft_recorded)

            # 从最后一个 SSE chunk 解析 usage（引擎在流末尾报告 token 数）
            usage = self._parse_stream_usage(last_chunk)

            # 记录指标
            cb = self._get_cb(instance.instance_id)
            if cb:
                cb.record_success()

            if self._metrics:
                self._metrics.record_request(
                    instance.instance_id,
                    (time.monotonic() - start_time) * 1000,
                    ttft_ms,
                    usage,
                    success=True,
                )

            root_span.set_attribute("success", True)

        except NoAvailableInstanceError:
            root_span.set_status(Status(StatusCode.ERROR, "No available instance"))
            root_span.set_attribute("error", True)
            raise
        except Exception as exc:
            root_span.set_status(Status(StatusCode.ERROR, str(exc)))
            root_span.set_attribute("error", True)
            root_span.record_exception(exc)
            error_event = json.dumps(
                {
                    "error": {"message": str(exc), "type": type(exc).__name__},
                }
            )
            yield f"data: {error_event}\n\n"
            yield "data: [DONE]\n\n"
            if instance:
                cb = self._get_cb(instance.instance_id)
                if cb:
                    cb.record_failure()
            if self._metrics and instance:
                self._metrics.record_request(
                    instance.instance_id,
                    0,
                    0,
                    usage,
                    success=False,
                )
        finally:
            latency_ms = (time.monotonic() - start_time) * 1000
            root_span.set_attribute("total_latency_ms", latency_ms)
            root_span.end()
            if instance:
                self._balancer.on_request_end(instance.instance_id, request)
            self._active_requests -= 1
            if self._draining and self._active_requests == 0:
                self._drain_event.set()

    def _get_cb(self, instance_id: str) -> CircuitBreaker | None:
        """获取实例的熔断器（支持 dict 和 CircuitBreakerRegistry）。"""
        if instance_id in self._cb_dict:
            return self._cb_dict[instance_id]
        if self._cb_registry:
            return self._cb_registry.get_or_create(instance_id)
        return None

    def _has_circuit_breakers(self) -> bool:
        """是否有任何熔断器配置。"""
        return bool(self._cb_dict) or self._cb_registry is not None

    def _filter_by_circuit_breaker(self, candidates: list[ModelInstance]) -> list[ModelInstance]:
        """过滤掉熔断器 OPEN 的实例。"""
        if not self._has_circuit_breakers():
            return candidates
        return [inst for inst in candidates if (cb := self._get_cb(inst.instance_id)) is None or cb.allow_request()]

    def _get_adapter(self, instance: ModelInstance) -> EngineAdapter:
        """获取实例对应的引擎适配器。

        Raises:
            MissingAdapterError: 没有注册对应 engine_type 的适配器。
        """
        adapter = self._engine_adapters.get(instance.engine_type)
        if adapter is None:
            raise MissingAdapterError(instance.engine_type)
        return adapter

    def _record_failure(
        self,
        instance: Optional[ModelInstance],
        error_resp: InferenceResponse,
        request: InferenceRequest,
        start_time: float,
    ) -> None:
        """记录失败的请求（熔断器 + 指标）。"""
        if instance:
            cb = self._get_cb(instance.instance_id)
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

    @staticmethod
    def _parse_stream_usage(last_line: str) -> TokenUsage:
        """从 SSE 流的最后一个 chunk 解析 token usage。

        LLM 引擎通常在流结束时发送一个包含 usage 字段的 chunk，
        格式为: data: {"usage": {"prompt_tokens": N, "completion_tokens": M, "total_tokens": T}}

        Args:
            last_line: SSE 流的最后一行（可能包含 usage）。

        Returns:
            解析出的 TokenUsage；如果解析失败返回空 TokenUsage。
        """
        if not last_line:
            return TokenUsage()
        try:
            # 去除 SSE 前缀 "data: "
            payload = last_line
            if payload.startswith("data:"):
                payload = payload[5:].lstrip()
            # 跳过 "[DONE]" 标记
            if payload.strip() == "[DONE]":
                return TokenUsage()
            data = json.loads(payload)
            usage_raw = data.get("usage")
            if usage_raw:
                return TokenUsage(
                    prompt_tokens=usage_raw.get("prompt_tokens", 0),
                    completion_tokens=usage_raw.get("completion_tokens", 0),
                    total_tokens=usage_raw.get("total_tokens", 0),
                )
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        return TokenUsage()

    def drain(self) -> None:
        """进入 draining 模式——停止接受新请求，等待现有请求完成。

        调用后 forward() 将拒绝新请求并抛出 RuntimeError。
        应在 shutdown 流程中最先调用。
        """
        self._draining = True
        if self._active_requests == 0:
            self._drain_event.set()

    async def wait_drain(self, timeout: float = 30.0) -> bool:
        """等待所有活跃请求完成。

        Args:
            timeout: 最大等待时间（秒）。超时后强制返回 False。

        Returns:
            True 表示所有请求已完成，False 表示超时（仍有未完成的请求）。
        """
        if self._active_requests == 0:
            return True
        try:
            await asyncio.wait_for(self._drain_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        """关闭内部的 httpx.AsyncClient。在应用 shutdown 时调用。"""
        if self._client:
            await self._client.aclose()
            self._client = None
