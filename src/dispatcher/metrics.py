"""Module 10: Metrics Collector — 规格书 §4 Module 10."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from src.shared.models import MetricsRecorder, TokenUsage


class PrometheusMetricsRecorder(MetricsRecorder):
    """基于 prometheus-client 的指标收集器。"""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        if registry is not None:
            self._requests_total = Counter(
                "dispatcher_requests_total", "Total requests",
                ["instance_id", "status"], registry=registry,
            )
            self._request_latency_ms = Histogram(
                "dispatcher_request_latency_ms", "Latency ms",
                ["instance_id"],
                buckets=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
                registry=registry,
            )
            self._request_ttft_ms = Histogram(
                "dispatcher_request_ttft_ms", "TTFT ms",
                ["instance_id"],
                buckets=[50, 100, 200, 500, 1000, 2000],
                registry=registry,
            )
            self._tokens_total = Counter(
                "dispatcher_tokens_total", "Tokens processed",
                ["instance_id", "type"], registry=registry,
            )
            self._queue_depth = Gauge(
                "dispatcher_queue_depth", "Queue depth", registry=registry,
            )
            self._errors_total = Counter(
                "dispatcher_errors_total", "Total errors",
                ["instance_id", "error_type"], registry=registry,
            )
            self._batch_size = Histogram(
                "dispatcher_batch_size", "Batch size",
                buckets=[1, 2, 4, 8, 16, 32], registry=registry,
            )
        else:
            self._requests_total = Counter(
                "dispatcher_requests_total", "Total requests",
                ["instance_id", "status"],
            )
            self._request_latency_ms = Histogram(
                "dispatcher_request_latency_ms", "Latency ms",
                ["instance_id"],
                buckets=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
            )
            self._request_ttft_ms = Histogram(
                "dispatcher_request_ttft_ms", "TTFT ms",
                ["instance_id"],
                buckets=[50, 100, 200, 500, 1000, 2000],
            )
            self._tokens_total = Counter(
                "dispatcher_tokens_total", "Tokens processed",
                ["instance_id", "type"],
            )
            self._queue_depth = Gauge(
                "dispatcher_queue_depth", "Queue depth",
            )
            self._errors_total = Counter(
                "dispatcher_errors_total", "Total errors",
                ["instance_id", "error_type"],
            )
            self._batch_size = Histogram(
                "dispatcher_batch_size", "Batch size",
                buckets=[1, 2, 4, 8, 16, 32],
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
        for lat_list in self._latencies.values():
            all_lats.extend(lat_list)
        if all_lats:
            s = sorted(all_lats)
            p95 = s[min(int(len(s) * 0.95), len(s) - 1)]
            p99 = s[min(int(len(s) * 0.99), len(s) - 1)]
            avg = sum(s) / len(s)
        else:
            avg, p95, p99 = 0.0, 0.0, 0.0

        per_instance = {
            inst: {"request_count": len(lats), "avg_latency_ms": sum(lats) / len(lats) if lats else 0}
            for inst, lats in self._latencies.items()
            if lats
        }
        return {
            "request_count": self._request_count, "error_count": self._error_count,
            "avg_latency_ms": avg, "p95_latency_ms": p95, "p99_latency_ms": p99,
            "queue_depth": self._queue_depth._value.get(), "per_instance": per_instance,
        }
