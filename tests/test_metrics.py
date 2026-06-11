"""Module 10: Metrics Collector 测试 — 规格书 §4 Module 10."""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from src.dispatcher.metrics import PrometheusMetricsRecorder
from src.shared.models import TokenUsage


@pytest.fixture
def recorder() -> PrometheusMetricsRecorder:
    return PrometheusMetricsRecorder(registry=CollectorRegistry())


class TestRecordRequest:
    def test_record_request_success(self, recorder: PrometheusMetricsRecorder) -> None:
        tokens = TokenUsage(prompt_tokens=100, completion_tokens=50)
        recorder.record_request("inst-1", 200.0, 50.0, tokens, success=True)
        summary = recorder.get_summary()
        assert summary["request_count"] == 1
        assert summary["error_count"] == 0

    def test_record_request_failure(self, recorder: PrometheusMetricsRecorder) -> None:
        tokens = TokenUsage(prompt_tokens=10, completion_tokens=0)
        recorder.record_request("inst-1", 500.0, 0.0, tokens, success=False)
        summary = recorder.get_summary()
        assert summary["request_count"] == 1
        assert summary["error_count"] == 1

    def test_record_multiple_requests(self, recorder: PrometheusMetricsRecorder) -> None:
        for i in range(10):
            recorder.record_request(
                "inst-1", 100.0 + i * 10, 20.0, TokenUsage(prompt_tokens=10, completion_tokens=5), success=True
            )
        summary = recorder.get_summary()
        assert summary["request_count"] == 10


class TestGetSummary:
    def test_keys(self, recorder: PrometheusMetricsRecorder) -> None:
        tokens = TokenUsage(prompt_tokens=50, completion_tokens=25)
        recorder.record_request("inst-1", 100.0, 30.0, tokens, success=True)
        summary = recorder.get_summary()
        for key in (
            "request_count",
            "error_count",
            "avg_latency_ms",
            "p95_latency_ms",
            "p99_latency_ms",
            "queue_depth",
            "per_instance",
        ):
            assert key in summary, f"missing key: {key}"

    def test_empty(self, recorder: PrometheusMetricsRecorder) -> None:
        summary = recorder.get_summary()
        assert summary["request_count"] == 0

    def test_per_instance(self, recorder: PrometheusMetricsRecorder) -> None:
        recorder.record_request("inst-1", 100.0, 20.0, TokenUsage(prompt_tokens=10, completion_tokens=5), success=True)
        recorder.record_request("inst-2", 200.0, 30.0, TokenUsage(prompt_tokens=20, completion_tokens=10), success=True)
        summary = recorder.get_summary()
        per_instance = summary["per_instance"]
        assert "inst-1" in per_instance
        assert "inst-2" in per_instance


class TestRecordError:
    def test_increments(self, recorder: PrometheusMetricsRecorder) -> None:
        recorder.record_error("inst-1", "timeout")
        recorder.record_error("inst-1", "connection_refused")
        assert recorder.get_summary()["error_count"] == 2


class TestQueueDepth:
    def test_record(self, recorder: PrometheusMetricsRecorder) -> None:
        recorder.record_queue_depth(5)
        assert recorder.get_summary()["queue_depth"] == 5
        recorder.record_queue_depth(0)
        assert recorder.get_summary()["queue_depth"] == 0


class TestBatchSize:
    def test_record(self, recorder: PrometheusMetricsRecorder) -> None:
        recorder.record_batch(4)
        recorder.record_batch(8)
        assert recorder.get_summary()["request_count"] == 0


class TestPercentiles:
    def test_p95_p99(self, recorder: PrometheusMetricsRecorder) -> None:
        for i in range(100):
            recorder.record_request(
                "inst-1", float(i + 1), 0.0, TokenUsage(prompt_tokens=1, completion_tokens=0), success=True
            )
        summary = recorder.get_summary()
        assert summary["p95_latency_ms"] > 0
        assert summary["p99_latency_ms"] >= summary["p95_latency_ms"]
