"""Module 10: Metrics Collector — 规格书 §4 Module 10."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from src.shared.models import MetricsRecorder, TokenUsage


class PrometheusMetricsRecorder(MetricsRecorder):
    """基于 prometheus-client 的指标收集器。"""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        reg_kwargs = {"registry": registry} if registry is not None else {}

        self._requests_total = Counter(
            "dispatcher_requests_total", "Total requests",
            ["instance_id", "status"], **reg_kwargs,
        )
        self._request_latency_ms = Histogram(
            "dispatcher_request_latency_ms", "Latency ms",
            ["instance_id"],
            buckets=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
            **reg_kwargs,
        )
        self._request_ttft_ms = Histogram(
            "dispatcher_request_ttft_ms", "TTFT ms",
            ["instance_id"],
            buckets=[50, 100, 200, 500, 1000, 2000],
            **reg_kwargs,
        )
        self._tokens_total = Counter(
            "dispatcher_tokens_total", "Tokens processed",
            ["instance_id", "type"], **reg_kwargs,
        )
        self._queue_depth = Gauge(
            "dispatcher_queue_depth", "Queue depth", **reg_kwargs,
        )
        self._errors_total = Counter(
            "dispatcher_errors_total", "Total errors",
            ["instance_id", "error_type"], **reg_kwargs,
        )
        self._batch_size = Histogram(
            "dispatcher_batch_size", "Batch size",
            buckets=[1, 2, 4, 8, 16, 32], **reg_kwargs,
        )

        self._latencies: dict[str, list[float]] = {}
        self._request_count: int = 0
        self._error_count: int = 0

    def record_request(self, instance_id: str, latency_ms: float,
                       ttft_ms: float, tokens: TokenUsage,
                       success: bool, queued_ms: float = 0) -> None:
        status = "success" if success else "error"
        self._requests_total.labels(instance_id=instance_id, status=status).inc()
        self._request_latency_ms.labels(instance_id=instance_id).observe(latency_ms)
        self._request_ttft_ms.labels(instance_id=instance_id).observe(ttft_ms)
        self._tokens_total.labels(instance_id=instance_id, type="prompt").inc(tokens.prompt_tokens)
        self._tokens_total.labels(instance_id=instance_id, type="completion").inc(tokens.completion_tokens)
        self._request_count += 1
        self._latencies.setdefault(instance_id, []).append(latency_ms)
        if len(self._latencies[instance_id]) > 1000:
            self._latencies[instance_id] = self._latencies[instance_id][-1000:]
        if not success:
            self._error_count += 1

    def record_error(self, instance_id: str, error_type: str) -> None:
        self._errors_total.labels(instance_id=instance_id, error_type=error_type).inc()
        self._error_count += 1

    def record_queue_depth(self, depth: int) -> None:
        self._queue_depth.set(depth)

    def record_batch(self, batch_size: int) -> None:
        self._batch_size.observe(batch_size)

    def get_summary(self) -> dict:
        all_lats: list[float] = []
        for lats in self._latencies.values():
            all_lats.extend(lats)
        if all_lats:
            s = sorted(all_lats)
            p95 = s[min(int(len(s) * 0.95), len(s) - 1)]
            p99 = s[min(int(len(s) * 0.99), len(s) - 1)]
            avg = sum(s) / len(s)
        else:
            avg, p95, p99 = 0.0, 0.0, 0.0

        per_instance = {
            i: {"request_count": len(l), "avg_latency_ms": sum(l) / len(l) if l else 0}
            for i, l in self._latencies.items() if l
        }
        return {
            "request_count": self._request_count, "error_count": self._error_count,
            "avg_latency_ms": avg, "p95_latency_ms": p95, "p99_latency_ms": p99,
            "queue_depth": self._queue_depth._value.get(), "per_instance": per_instance,
        }
