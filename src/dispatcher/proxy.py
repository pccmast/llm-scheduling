"""Module 6: Routing Proxy — 规格书 §4 Module 6.

请求路由与转发引擎。协调 Registry、LoadBalancer、EngineAdapter、CircuitBreaker
完成请求转发。处理非流式和流式（SSE）两种响应模式。

v4.3: 重试循环 + 总超时 + 降级反馈 + 429处理 + 重试指标
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
    """请求路由与转发引擎。v4.3: 重试 + 总超时 + 降级反馈。"""

    def __init__(
        self,
        registry: InstanceRegistry,
        balancer: LoadBalancer,
        engine_adapters: dict[str, EngineAdapter],
        circuit_breakers: dict[str, CircuitBreaker] | CircuitBreakerRegistry | None = None,
        metrics: MetricsRecorder | None = None,
        timeout: float = 120.0,
        routing_config: "RoutingConfig | None" = None,  # type: ignore[name-defined]
    ) -> None:
        from src.shared.models import RoutingConfig

        self._registry = registry
        self._balancer = balancer
        self._engine_adapters = engine_adapters
        self._cb_dict: dict[str, CircuitBreaker] = circuit_breakers if isinstance(circuit_breakers, dict) else {}  # type: ignore[assignment]
        self._cb_registry: CircuitBreakerRegistry | None = (
            circuit_breakers if isinstance(circuit_breakers, CircuitBreakerRegistry) else None
        )  # type: ignore[assignment]
        self._metrics = metrics
        self._timeout = timeout
        self._routing_config = routing_config or RoutingConfig()
        self._client: httpx.AsyncClient | None = None
        self._active_requests: int = 0
        self._draining: bool = False
        self._drain_event: asyncio.Event = asyncio.Event()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        return self._client

    async def forward(self, request: InferenceRequest) -> InferenceResponse:
        """转发非流式推理请求。v4.3: 重试循环 + 总超时 + 降级。"""
        if self._draining:
            raise DrainInProgressError()

        start_time = time.monotonic()
        self._active_requests += 1

        tracer = trace.get_tracer(__name__)
        root_span = tracer.start_span("forward", attributes={
            "request_id": request.request_id, "model": request.model,
            "priority": request.priority, "estimated_weight": request.estimated_weight,
            "stream": request.stream,
        })

        candidates = self._registry.list_by_model(request.model)
        available = self._filter_by_circuit_breaker(candidates)
        tried: set[str] = set()
        last_response: InferenceResponse | None = None
        retry_count: int = 0
        max_attempts = self._routing_config.max_retries + 1
        # 总超时 = 单次 × 最大尝试 × 1.5
        total_timeout_s = self._timeout * max_attempts * 1.5
        # 单实例退避
        single_instance = len(available) <= 1

        for attempt in range(max_attempts):
            elapsed = (time.monotonic() - start_time)
            if elapsed >= total_timeout_s:
                last_response = InferenceResponse(request_id=request.request_id, instance_id="",
                    status="timeout", error_message="Total retry timeout",
                    latency_ms=(time.monotonic() - start_time) * 1000)
                root_span.set_attribute("error", True)
                root_span.set_attribute("timeout", "total")
                break
            if not available:
                break
            if attempt > 0:
                root_span.set_attribute(f"retry_{attempt}", True)

            # 单实例无退路 → 不重试同一实例
            if single_instance and tried:
                break

            instance = self._balancer.select(available, request)
            tried.add(instance.instance_id)
            self._balancer.on_request_start(instance.instance_id, request)

            try:
                response = await self._try_one_attempt(instance, request, start_time, tracer)
            except (asyncio.TimeoutError, httpx.TimeoutException):
                self._balancer.on_request_end(instance.instance_id, request)
                last_response = self._make_error("timeout", "Upstream timeout", request, instance, start_time)
                self._record_failure(instance, last_response, request, start_time)
                retry_count += 1
                root_span.set_attribute(f"retry_{attempt}_error", "timeout")
                if not self._routing_config.retry_on_timeout:
                    break
                available = [i for i in available if i.instance_id not in tried]
                continue
            except httpx.ConnectError:
                self._balancer.on_request_end(instance.instance_id, request)
                last_response = self._make_error("error", "Connection refused", request, instance, start_time)
                self._record_failure(instance, last_response, request, start_time)
                retry_count += 1
                root_span.set_attribute(f"retry_{attempt}_error", "connection")
                if not self._routing_config.retry_on_connection:
                    break
                available = [i for i in available if i.instance_id not in tried]
                continue
            except Exception as exc:
                self._balancer.on_request_end(instance.instance_id, request)
                last_response = self._make_error("error", str(exc), request, instance, start_time)
                self._record_failure(instance, last_response, request, start_time)
                break
            else:
                self._balancer.on_request_end(instance.instance_id, request)

                if response.status == "ok":
                    # v4: 降级检查 — 成功但极慢 (>80%超时)
                    if response.latency_ms > self._timeout * 0.8 * 1000:
                        if hasattr(self._balancer, "record_failure"):
                            self._balancer.record_failure(instance.instance_id)

                    response.error_message = getattr(response, "error_message", None)
                    root_span.set_attribute("success", True)
                    root_span.set_attribute("retries", retry_count)
                    root_span.set_attribute("total_latency_ms", response.latency_ms)
                    root_span.end()
                    self._active_requests -= 1
                    return response

                # 5xx → 重试
                if response.status == "error" and self._routing_config.retry_on_5xx:
                    self._record_failure(instance, response, request, start_time)
                    available = [i for i in available if i.instance_id not in tried]
                    last_response = response
                    retry_count += 1
                    if available:
                        continue

                # 429 → 反馈可靠性
                if (response.error_message or "").startswith("429"):
                    if hasattr(self._balancer, "record_failure"):
                        self._balancer.record_failure(instance.instance_id)

                root_span.set_attribute("retries", retry_count)
                root_span.set_attribute("total_latency_ms", response.latency_ms)
                root_span.end()
                self._active_requests -= 1
                return response

        if last_response:
            root_span.set_status(Status(StatusCode.ERROR, "All instances failed"))
            root_span.set_attribute("error", True)
            root_span.set_attribute("retries", retry_count)
            root_span.set_attribute("total_latency_ms", (time.monotonic() - start_time) * 1000)
            root_span.end()
            self._active_requests -= 1
            return last_response

        raise NoAvailableInstanceError(request.model)

    async def _try_one_attempt(self, instance: ModelInstance, request: InferenceRequest,
                                start_time: float, tracer) -> InferenceResponse:
        """单次转发尝试。"""
        adapter = self._get_adapter(instance)
        url, headers, body = adapter.build_request(instance, request)
        client = self._get_client()

        with tracer.start_as_current_span("engine_inference") as infer_span:
            infer_span.set_attribute("instance_id", instance.instance_id)
            infer_span.set_attribute("engine_type", instance.engine_type)
            response = await client.post(url, json=body, headers=headers, timeout=self._timeout)
            infer_span.set_attribute("http_status_code", response.status_code)

        if response.status_code >= 500:
            return InferenceResponse(request_id=request.request_id, instance_id=instance.instance_id,
                                      status="error", error_message=f"Upstream returned {response.status_code}",
                                      latency_ms=(time.monotonic() - start_time) * 1000)

        raw = response.json()
        with tracer.start_as_current_span("parse_response"):
            inference_response = adapter.parse_response(raw)
            inference_response.request_id = request.request_id
            inference_response.instance_id = instance.instance_id
            inference_response.latency_ms = (time.monotonic() - start_time) * 1000

        if inference_response.status == "ok":
            cb = self._get_cb(instance.instance_id)
            if cb:
                cb.record_success()
            if self._metrics:
                usage = inference_response.usage or TokenUsage()
                self._metrics.record_request(instance.instance_id, inference_response.latency_ms, 0.0, usage, success=True)
            if hasattr(self._balancer, "record_performance") and inference_response.usage:
                self._balancer.record_performance(instance.instance_id,
                    inference_response.usage.completion_tokens, inference_response.latency_ms)
            if hasattr(self._balancer, "record_success"):
                self._balancer.record_success(instance.instance_id)

        return inference_response

    def _make_error(self, status: str, msg: str, request: InferenceRequest,
                    instance: ModelInstance | None = None, start_time: float = 0) -> InferenceResponse:
        return InferenceResponse(request_id=request.request_id,
                                  instance_id=instance.instance_id if instance else "",
                                  status=status, error_message=msg,
                                  latency_ms=(time.monotonic() - start_time) * 1000 if start_time else 0)

    # ── 流式 (保留原有逻辑, 不加重试) ──

    async def forward_stream(self, request: InferenceRequest) -> AsyncIterator[str]:
        if self._draining:
            raise DrainInProgressError()
        start_time = time.monotonic()
        ttft_recorded = False; ttft_ms = 0.0
        instance: Optional[ModelInstance] = None
        self._active_requests += 1; usage = TokenUsage()
        tracer = trace.get_tracer(__name__)
        root_span = tracer.start_span("forward_stream", attributes={
            "request_id": request.request_id, "model": request.model,
            "priority": request.priority, "estimated_weight": request.estimated_weight,
        })
        try:
            with tracer.start_as_current_span("balancer_select") as select_span:
                candidates = self._registry.list_by_model(request.model)
                select_span.set_attribute("candidates_count", len(candidates))
                if not candidates: raise NoAvailableInstanceError(request.model)
                available = self._filter_by_circuit_breaker(candidates)
                select_span.set_attribute("available_count", len(available))
                if not available: raise NoAvailableInstanceError(request.model)
                instance = self._balancer.select(available, request)
                select_span.set_attribute("selected_instance", instance.instance_id)
            self._balancer.on_request_start(instance.instance_id, request)
            adapter = self._get_adapter(instance)
            url, headers, body = adapter.build_request(instance, request)
            with tracer.start_as_current_span("engine_inference") as infer_span:
                infer_span.set_attribute("instance_id", instance.instance_id)
                infer_span.set_attribute("engine_type", instance.engine_type)
                client = self._get_client()
                last_chunk = ""
                async with client.stream("POST", url, json=body, headers=headers, timeout=self._timeout) as response:
                    async for line in response.aiter_lines():
                        if not ttft_recorded and line.startswith("data:") and "content" in line:
                            ttft_ms = (time.monotonic() - start_time) * 1000; ttft_recorded = True
                            infer_span.set_attribute("ttft_ms", ttft_ms)
                        last_chunk = line; yield line + "\n"
                infer_span.set_attribute("ttft_recorded", ttft_recorded)
            usage = self._parse_stream_usage(last_chunk)
            cb = self._get_cb(instance.instance_id)
            if cb: cb.record_success()
            if self._metrics:
                self._metrics.record_request(instance.instance_id, (time.monotonic() - start_time) * 1000, ttft_ms, usage, success=True)
            root_span.set_attribute("success", True)
        except NoAvailableInstanceError:
            root_span.set_status(Status(StatusCode.ERROR, "No available instance")); root_span.set_attribute("error", True); raise
        except Exception as exc:
            root_span.set_status(Status(StatusCode.ERROR, str(exc))); root_span.set_attribute("error", True)
            root_span.record_exception(exc)
            yield json.dumps({"error": {"message": str(exc), "type": type(exc).__name__}}) + "\n"
            yield "data: [DONE]\n\n"
            if instance:
                cb = self._get_cb(instance.instance_id)
                if cb: cb.record_failure()
            if self._metrics and instance:
                self._metrics.record_request(instance.instance_id, 0, 0, usage, success=False)
        finally:
            root_span.set_attribute("total_latency_ms", (time.monotonic() - start_time) * 1000); root_span.end()
            if instance: self._balancer.on_request_end(instance.instance_id, request)
            self._active_requests -= 1
            if self._draining and self._active_requests == 0: self._drain_event.set()

    # ── helpers ──

    def _get_cb(self, instance_id: str) -> CircuitBreaker | None:
        if instance_id in self._cb_dict: return self._cb_dict[instance_id]
        if self._cb_registry: return self._cb_registry.get_or_create(instance_id)
        return None

    def _has_circuit_breakers(self) -> bool:
        return bool(self._cb_dict) or self._cb_registry is not None

    def _filter_by_circuit_breaker(self, candidates: list[ModelInstance]) -> list[ModelInstance]:
        if not self._has_circuit_breakers(): return candidates
        return [inst for inst in candidates if (cb := self._get_cb(inst.instance_id)) is None or cb.allow_request()]

    def _get_adapter(self, instance: ModelInstance) -> EngineAdapter:
        adapter = self._engine_adapters.get(instance.engine_type)
        if adapter is None: raise MissingAdapterError(instance.engine_type)
        return adapter

    def _record_failure(self, instance: Optional[ModelInstance], error_resp: InferenceResponse,
                         request: InferenceRequest, start_time: float) -> None:
        if instance:
            cb = self._get_cb(instance.instance_id)
            if cb: cb.record_failure()
            if hasattr(self._balancer, "record_failure"):
                self._balancer.record_failure(instance.instance_id)
            if self._metrics:
                self._metrics.record_request(instance.instance_id, error_resp.latency_ms, 0.0, TokenUsage(), success=False)

    @staticmethod
    def _parse_stream_usage(last_line: str) -> TokenUsage:
        if not last_line: return TokenUsage()
        try:
            payload = last_line[5:].lstrip() if last_line.startswith("data:") else last_line
            if payload.strip() == "[DONE]": return TokenUsage()
            data = json.loads(payload)
            u = data.get("usage", {})
            return TokenUsage(prompt_tokens=u.get("prompt_tokens", 0), completion_tokens=u.get("completion_tokens", 0), total_tokens=u.get("total_tokens", 0))
        except: return TokenUsage()

    def drain(self) -> None:
        self._draining = True
        if self._active_requests == 0: self._drain_event.set()

    async def wait_drain(self, timeout: float = 30.0) -> bool:
        if self._active_requests == 0: return True
        try: await asyncio.wait_for(self._drain_event.wait(), timeout=timeout); return True
        except asyncio.TimeoutError: return False

    async def close(self) -> None:
        if self._client: await self._client.aclose(); self._client = None
