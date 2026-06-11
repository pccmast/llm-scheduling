"""Module 10: Metrics Collector — 规格书 §4 Module 10.

基于 prometheus-client 的指标收集器。收集调度系统的运行指标
（请求延迟、TTFT、token 消耗、错误率、队列深度），暴露给 Prometheus 和 Dashboard。
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from src.shared.models import MetricsRecorder, TokenUsage


class PrometheusMetricsRecorder(MetricsRecorder):
    """基于 prometheus-client 的指标收集器。"""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        """初始化 Prometheus 指标定义。

        Args:
            registry: Prometheus 注册中心。None 时使用默认全局 registry。
        """
        reg = registry or CollectorRegistry(auto_describe=True)

        self._requests_total = Counter(
            "dispatcher_requests_total",
            "Total number of inference requests",
            ["instance_id", "status"],
            registry=reg,
        )
        self._request_latency_ms = Histogram(
            "dispatcher_request_latency_ms",
            "Request latency in milliseconds",
            ["instance_id"],
            buckets=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
            registry=reg,
        )
        self._request_ttft_ms = Histogram(
            "dispatcher_request_ttft_ms",
            "Time to first token in milliseconds",
            ["instance_id"],
            buckets=[50, 100, 200, 500, 1000, 2000],
            registry=reg,
        )
        self._tokens_total = Counter(
            "dispatcher_tokens_total",
            "Total tokens processed",
            ["instance_id", "type"],
            registry=reg,
        )
        self._queue_depth = Gauge(
            "dispatcher_queue_depth",
            "Current request queue depth",
            registry=reg,
        )
        self._errors_total = Counter(
            "dispatcher_errors_total",
            "Total errors",
            ["instance_id", "error_type"],
            registry=reg,
        )
        self._batch_size = Histogram(
            "dispatcher_batch_size",
            "Batch size distribution",
            buckets=[1, 2, 4, 8, 16, 32],
            registry=reg,
        )

        self._registry = reg
        self._latencies: dict[str, list[float]] = {}
        self._request_count: int = 0
        self._error_count: int = 0

    def record_request(
        self,
        instance_id: str,
        latency_ms: float,
        ttft_ms: float,
        tokens: TokenUsage,
        success: bool,
        queued_ms: float = 0,
    ) -> None:
        status = "success" if success else "error"
        self._requests_total.labels(instance_id=instance_id, status=status).inc()
        self._request_latency_ms.labels(instance_id=instance_id).observe(latency_ms)
        self._request_ttft_ms.labels(instance_id=instance_id).observe(ttft_ms)
        self._tokens_total.labels(instance_id=instance_id, type="prompt").inc(tokens.prompt_tokens)
        self._tokens_total.labels(instance_id=instance_id, type="completion").inc(tokens.completion_tokens)

        self._request_count += 1
        if instance_id not in self._latencies:
            self._latencies[instance_id] = []
        self._latencies[instance_id].append(latency_ms)
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
        all_latencies: list[float] = []
        for lats in self._latencies.values():
            all_latencies.extend(lats)

        if all_latencies:
            sorted_lats = sorted(all_latencies)
            avg = sum(all_latencies) / len(all_latencies)
            p95_idx = int(len(sorted_lats) * 0.95)
            p99_idx = int(len(sorted_lats) * 0.99)
            p95 = sorted_lats[min(p95_idx, len(sorted_lats) - 1)]
            p99 = sorted_lats[min(p99_idx, len(sorted_lats) - 1)]
        else:
            avg = 0.0
            p95 = 0.0
            p99 = 0.0

        per_instance: dict[str, dict] = {}
        for inst_id, lats in self._latencies.items():
            if lats:
                per_instance[inst_id] = {
                    "request_count": len(lats),
                    "avg_latency_ms": sum(lats) / len(lats),
                }

        return {
            "request_count": self._request_count,
            "error_count": self._error_count,
            "avg_latency_ms": avg,
            "p95_latency_ms": p95,
            "p99_latency_ms": p99,
            "queue_depth": self._queue_depth._value.get(),
            "per_instance": per_instance,
        }
