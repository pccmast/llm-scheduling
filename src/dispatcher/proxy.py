"""Module 6: Routing Proxy v4.3 — retry + tier-exhaustion + stream-retry."""

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
    RoutingConfig,
    TokenUsage,
)


class RoutingProxy:
    """Request routing and forwarding engine v4.3."""

    def __init__(
        self,
        registry: InstanceRegistry,
        balancer: LoadBalancer,
        engine_adapters: dict[str, EngineAdapter],
        circuit_breakers: dict[str, CircuitBreaker] | CircuitBreakerRegistry | None = None,
        metrics: MetricsRecorder | None = None,
        timeout: float = 120.0,
        routing_config: RoutingConfig | None = None,
    ) -> None:
        self._registry = registry
        self._balancer = balancer
        self._engine_adapters = engine_adapters
        self._cb_dict: dict[str, CircuitBreaker] = (
            circuit_breakers if isinstance(circuit_breakers, dict) else {}
        )
        self._cb_registry: CircuitBreakerRegistry | None = (
            circuit_breakers
            if isinstance(circuit_breakers, CircuitBreakerRegistry)
            else None
        )
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

    # ── forward (non-streaming, with retry) ──

    async def forward(self, request: InferenceRequest) -> InferenceResponse:
        if self._draining:
            raise DrainInProgressError()

        start_time = time.monotonic()
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

        candidates = self._registry.list_by_model(request.model)
        all_available = self._filter_by_circuit_breaker(candidates)
        # tier-exhaustion: LOCAL first, then REMOTE fallback
        local = [i for i in all_available if i.tier.value == "local"]
        remote = [i for i in all_available if i.tier.value == "remote"]
        available = local if local else remote
        tried: set[str] = set()
        last_response: InferenceResponse | None = None
        retry_count: int = 0
        max_attempts = self._routing_config.max_retries + 1
        total_timeout_s = self._timeout * max_attempts * 1.5
        single_instance = len(available) <= 1
        tier_exhausted = False

        for attempt in range(max_attempts):
            elapsed = time.monotonic() - start_time
            if elapsed >= total_timeout_s:
                last_response = InferenceResponse(
                    request_id=request.request_id,
                    instance_id="",
                    status="timeout",
                    error_message="Total retry timeout",
                    latency_ms=(time.monotonic() - start_time) * 1000,
                )
                root_span.set_attribute("error", True)
                root_span.set_attribute("timeout", "total")
                break

            # tier-exhaustion: LOCAL → REMOTE fallback
            if (not available or (single_instance and tried)) and not tier_exhausted:
                if local and remote and available is local:
                    available = remote
                    tier_exhausted = True
                    root_span.set_attribute("tier_fallback", "remote")
                    continue
                break
            if not available:
                break
            if single_instance and tried:
                break
            if attempt > 0:
                root_span.set_attribute(f"retry_{attempt}", True)

            instance = self._balancer.select(available, request)
            tried.add(instance.instance_id)
            self._balancer.on_request_start(instance.instance_id, request)

            try:
                response = await self._try_one(instance, request, start_time, tracer)
            except (asyncio.TimeoutError, httpx.TimeoutException):
                self._balancer.on_request_end(instance.instance_id, request)
                last_response = InferenceResponse(
                    request_id=request.request_id,
                    instance_id=instance.instance_id,
                    status="timeout",
                    error_message="Upstream timeout",
                    latency_ms=(time.monotonic() - start_time) * 1000,
                )
                self._record_failure(instance, last_response, request, start_time, penalty=0.5)
                retry_count += 1
                root_span.set_attribute(f"retry_{attempt}_error", "timeout")
                if not self._routing_config.retry_on_timeout:
                    break
                available = [i for i in available if i.instance_id not in tried]
                continue
            except httpx.ConnectError:
                self._balancer.on_request_end(instance.instance_id, request)
                last_response = InferenceResponse(
                    request_id=request.request_id,
                    instance_id=instance.instance_id,
                    status="error",
                    error_message="Connection refused",
                    latency_ms=(time.monotonic() - start_time) * 1000,
                )
                self._record_failure(instance, last_response, request, start_time, penalty=0.5)
                retry_count += 1
                root_span.set_attribute(f"retry_{attempt}_error", "connection")
                if not self._routing_config.retry_on_connection:
                    break
                available = [i for i in available if i.instance_id not in tried]
                continue
            except Exception as exc:
                self._balancer.on_request_end(instance.instance_id, request)
                last_response = InferenceResponse(
                    request_id=request.request_id,
                    instance_id=instance.instance_id,
                    status="error",
                    error_message=str(exc),
                    latency_ms=(time.monotonic() - start_time) * 1000,
                )
                self._record_failure(instance, last_response, request, start_time)
                break
            else:
                self._balancer.on_request_end(instance.instance_id, request)

                if response.status == "ok":
                    # degraded — slow but successful
                    if response.latency_ms > self._timeout * 0.8 * 1000:
                        if hasattr(self._balancer, "record_failure"):
                            self._balancer.record_failure(
                                instance.instance_id, penalty=0.3
                            )
                    root_span.set_attribute("success", True)
                    root_span.set_attribute("retries", retry_count)
                    root_span.set_attribute(
                        "total_latency_ms", response.latency_ms
                    )
                    root_span.end()
                    self._active_requests -= 1
                    return response

                # 5xx — retryable
                if response.status == "error" and self._routing_config.retry_on_5xx:
                    self._record_failure(instance, response, request, start_time)
                    available = [i for i in available if i.instance_id not in tried]
                    last_response = response
                    retry_count += 1
                    if available:
                        continue

                # 429 — feedback balancer
                if (response.error_message or "").startswith("429"):
                    if hasattr(self._balancer, "record_failure"):
                        self._balancer.record_failure(
                            instance.instance_id, penalty=0.7
                        )

                root_span.set_attribute("retries", retry_count)
                root_span.set_attribute(
                    "total_latency_ms", response.latency_ms
                )
                root_span.end()
                self._active_requests -= 1
                return response

        if last_response:
            root_span.set_status(Status(StatusCode.ERROR, "All instances failed"))
            root_span.set_attribute("error", True)
            root_span.set_attribute("retries", retry_count)
            root_span.set_attribute(
                "total_latency_ms", (time.monotonic() - start_time) * 1000
            )
            root_span.end()
            self._active_requests -= 1
            return last_response

        raise NoAvailableInstanceError(request.model)

    async def _try_one(
        self,
        instance: ModelInstance,
        request: InferenceRequest,
        start_time: float,
        tracer: trace.Tracer,
    ) -> InferenceResponse:
        adapter = self._get_adapter(instance)
        url, headers, body = adapter.build_request(instance, request)
        client = self._get_client()
        with tracer.start_as_current_span("engine_inference") as s:
            s.set_attribute("instance_id", instance.instance_id)
            s.set_attribute("engine_type", instance.engine_type)
            resp = await client.post(
                url, json=body, headers=headers, timeout=self._timeout
            )
            s.set_attribute("http_status_code", resp.status_code)

        if resp.status_code >= 500:
            return InferenceResponse(
                request_id=request.request_id,
                instance_id=instance.instance_id,
                status="error",
                error_message=f"Upstream returned {resp.status_code}",
                latency_ms=(time.monotonic() - start_time) * 1000,
            )

        raw = resp.json()
        with tracer.start_as_current_span("parse_response"):
            ir = adapter.parse_response(raw)
            ir.request_id = request.request_id
            ir.instance_id = instance.instance_id
            ir.latency_ms = (time.monotonic() - start_time) * 1000

        if ir.status == "ok":
            cb = self._get_cb(instance.instance_id)
            if cb:
                cb.record_success()
            if self._metrics:
                self._metrics.record_request(
                    instance.instance_id,
                    ir.latency_ms,
                    0.0,
                    ir.usage or TokenUsage(),
                    success=True,
                )
            if hasattr(self._balancer, "record_performance") and ir.usage:
                self._balancer.record_performance(
                    instance.instance_id,
                    ir.usage.completion_tokens,
                    ir.latency_ms,
                )
            if hasattr(self._balancer, "record_success"):
                self._balancer.record_success(instance.instance_id)
        return ir

    # ── forward_stream (v4.3: with retry) ──

    async def forward_stream(self, request: InferenceRequest) -> AsyncIterator[str]:
        if self._draining:
            raise DrainInProgressError()

        start_time = time.monotonic()
        self._active_requests += 1
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

        candidates = self._registry.list_by_model(request.model)
        all_avail = self._filter_by_circuit_breaker(candidates)
        local = [i for i in all_avail if i.tier.value == "local"]
        remote_list = [i for i in all_avail if i.tier.value == "remote"]
        available = local if local else remote_list
        tried: set[str] = set()
        tier_exhausted = False
        last_error: str = ""

        for attempt in range(self._routing_config.max_retries + 1):
            if not available and not tier_exhausted:
                if local and remote_list and available is local:
                    available = remote_list
                    tier_exhausted = True
                    continue
                break
            if not available:
                break
            if len(available) <= 1 and tried:
                break

            instance = self._balancer.select(available, request)
            tried.add(instance.instance_id)
            self._balancer.on_request_start(instance.instance_id, request)
            usage = TokenUsage()
            ttft_recorded = False
            ttft_ms = 0.0

            try:
                adapter = self._get_adapter(instance)
                url, headers, body = adapter.build_request(instance, request)
                client = self._get_client()
                last_chunk = ""
                async with client.stream(
                    "POST", url, json=body, headers=headers, timeout=self._timeout
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not ttft_recorded and "content" in line:
                            ttft_ms = (time.monotonic() - start_time) * 1000
                            ttft_recorded = True
                        last_chunk = line
                        yield line + "\n"
                usage = self._parse_stream_usage(last_chunk)

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
                if hasattr(self._balancer, "record_performance"):
                    self._balancer.record_performance(
                        instance.instance_id,
                        usage.completion_tokens,
                        (time.monotonic() - start_time) * 1000,
                    )
                root_span.set_attribute("success", True)
                root_span.set_attribute("retries", attempt)
                return
            except Exception as exc:
                root_span.set_attribute(
                    f"retry_{attempt}_error", type(exc).__name__
                )
                if instance:
                    cb = self._get_cb(instance.instance_id)
                    if cb:
                        cb.record_failure()
                    if hasattr(self._balancer, "record_failure"):
                        self._balancer.record_failure(
                            instance.instance_id, penalty=0.5
                        )
                available = [i for i in available if i.instance_id not in tried]
                last_error = str(exc)
                self._balancer.on_request_end(instance.instance_id, request)
                continue
            finally:
                if instance:
                    self._balancer.on_request_end(instance.instance_id, request)

        yield json.dumps({
            "error": {
                "message": last_error or "All instances failed",
                "type": "NoAvailableInstance",
            }
        }) + "\n"
        yield "data: [DONE]\n\n"
        root_span.set_status(Status(StatusCode.ERROR, "All instances failed"))
        root_span.set_attribute("error", True)
        root_span.set_attribute("retries", len(tried))
        root_span.end()
        self._active_requests -= 1

    # ── helpers ──

    def _get_cb(self, instance_id: str) -> CircuitBreaker | None:
        if instance_id in self._cb_dict:
            return self._cb_dict[instance_id]
        if self._cb_registry:
            return self._cb_registry.get_or_create(instance_id)
        return None

    def _has_cb(self) -> bool:
        return bool(self._cb_dict) or self._cb_registry is not None

    def _filter_by_circuit_breaker(
        self, candidates: list[ModelInstance]
    ) -> list[ModelInstance]:
        if not self._has_cb():
            return candidates
        return [
            inst
            for inst in candidates
            if (cb := self._get_cb(inst.instance_id)) is None
            or cb.allow_request()
        ]

    def _get_adapter(self, instance: ModelInstance) -> EngineAdapter:
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
        penalty: float = 1.0,
    ) -> None:
        """Record failure with differentiated penalty."""
        if instance:
            cb = self._get_cb(instance.instance_id)
            if cb:
                cb.record_failure()
            if hasattr(self._balancer, "record_failure"):
                self._balancer.record_failure(instance.instance_id, penalty=penalty)
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
        if not last_line:
            return TokenUsage()
        try:
            payload = (
                last_line[5:].lstrip()
                if last_line.startswith("data:")
                else last_line
            )
            if payload.strip() == "[DONE]":
                return TokenUsage()
            u = json.loads(payload).get("usage", {})
            return TokenUsage(
                prompt_tokens=u.get("prompt_tokens", 0),
                completion_tokens=u.get("completion_tokens", 0),
                total_tokens=u.get("total_tokens", 0),
            )
        except (json.JSONDecodeError, AttributeError, TypeError):
            return TokenUsage()

    def drain(self) -> None:
        self._draining = True
        if self._active_requests == 0:
            self._drain_event.set()

    async def wait_drain(self, timeout: float = 30.0) -> bool:
        if self._active_requests == 0:
            return True
        try:
            await asyncio.wait_for(self._drain_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
