"""可观测性埋点 - 为 proxy 添加 Prometheus 度量和结构化日志。

用法: 在 proxy.py 中导入并包裹 forward_request 函数:
    from benchmark.v2.observability.instrumented_proxy import instrumented_forward
    
    # 替换原来的 forward_request 调用
    result = await instrumented_forward(request, ...)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Coroutine, Optional

from prometheus_client import Counter, Histogram, Gauge

# Prometheus 度量定义
PROXY_REQUESTS_TOTAL = Counter(
    "proxy_requests_total",
    "Total requests processed by proxy",
    ["method", "status"],
)
PROXY_REQUEST_DURATION_MS = Histogram(
    "proxy_request_duration_ms",
    "End-to-end request latency",
    buckets=[10, 50, 100, 200, 300, 500, 1000, 2000, 3000, 5000, 8000, 10000, 15000, 20000, 30000, 60000],
)
PROXY_PARSE_DURATION_MS = Histogram(
    "proxy_parse_duration_ms",
    "Request parsing / header extraction latency",
    buckets=[1, 5, 10, 20, 50, 100, 200, 500],
)
PROXY_ROUTING_DURATION_MS = Histogram(
    "proxy_routing_duration_ms",
    "Instance selection latency",
    buckets=[1, 5, 10, 20, 50, 100, 200, 500],
)
PROXY_BACKEND_WAIT_MS = Gauge(
    "proxy_backend_wait_ms",
    "Time spent waiting for backend to become available",
    ["backend"],
)
PROXY_BACKEND_RESPONSE_MS = Histogram(
    "proxy_backend_response_ms",
    "Backend response time (including round-trip)",
    ["backend"],
    buckets=[50, 100, 200, 500, 1000, 2000, 3000, 5000, 8000, 10000, 15000, 20000, 30000, 60000],
)
PROXY_ASSEMBLE_DURATION_MS = Histogram(
    "proxy_assemble_duration_ms",
    "Response assembly / post-processing latency",
    buckets=[1, 5, 10, 20, 50, 100, 200, 500],
)

logger = logging.getLogger(__name__)


@dataclass
class ProxyMetrics:
    """单次请求的完整可观测性数据。"""
    trace_id: str
    method: str = "POST"
    
    # 阶段时间戳 (毫秒)
    t_start_ms: float = 0.0
    t_parse_done_ms: float = 0.0
    t_routing_done_ms: float = 0.0
    t_backend_start_ms: float = 0.0
    t_backend_done_ms: float = 0.0
    t_assemble_done_ms: float = 0.0
    t_end_ms: float = 0.0
    
    # 结果
    backend_used: Optional[str] = None
    status_code: int = 200
    error: Optional[str] = None
    prompt_length: int = 0
    max_tokens: int = 50
    model: str = ""
    
    # 计算属性
    @property
    def total_ms(self) -> float:
        return self.t_end_ms - self.t_start_ms
    
    @property
    def parse_ms(self) -> float:
        return self.t_parse_done_ms - self.t_start_ms
    
    @property
    def routing_ms(self) -> float:
        return self.t_routing_done_ms - self.t_parse_done_ms
    
    @property
    def backend_wait_ms(self) -> float:
        return self.t_backend_start_ms - self.t_routing_done_ms
    
    @property
    def backend_response_ms(self) -> float:
        return self.t_backend_done_ms - self.t_backend_start_ms
    
    @property
    def assemble_ms(self) -> float:
        return self.t_assemble_done_ms - self.t_backend_done_ms
    
    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "method": self.method,
            "total_ms": round(self.total_ms, 2),
            "parse_ms": round(self.parse_ms, 2),
            "routing_ms": round(self.routing_ms, 2),
            "backend_wait_ms": round(self.backend_wait_ms, 2),
            "backend_response_ms": round(self.backend_response_ms, 2),
            "assemble_ms": round(self.assemble_ms, 2),
            "backend": self.backend_used,
            "status": self.status_code,
            "error": self.error,
            "prompt_length": self.prompt_length,
            "max_tokens": self.max_tokens,
            "model": self.model,
        }


async def instrumented_forward(
    original_forward: Callable[..., Coroutine[Any, Any, Any]],
    request: Any,
    *args,
    **kwargs,
) -> Any:
    """包裹 forward_request 函数，添加可观测性埋点。
    
    用法 (在 proxy.py 中):
        from benchmark.v2.observability.instrumented_proxy import instrumented_forward
        # 在你的类方法中:
        return await instrumented_forward(self.forward_request, request, *args, **kwargs)
    
    注意: 这个包装器需要访问原始函数的内部阶段。由于 proxy 的 forward_request
    是一个单块函数，我们采用以下方式:
    1. 如果是外部测试，可以 monkeypatch proxy 实例
    2. 或者，在 proxy 代码中直接内嵌这些度量点
    
    这里提供的是代理框架，实际集成需要在 proxy.py 中手动添加测量点。
    
    参见: benchmark/v2/scripts/instrument_dispatcher.py - 通过修改 proxy 来实际添加埋点。
    """
    metrics = ProxyMetrics(trace_id=str(id(request)))
    t0 = time.perf_counter()
    metrics.t_start_ms = t0 * 1000
    
    try:
        result = await original_forward(request, *args, **kwargs)
        t1 = time.perf_counter()
        metrics.t_end_ms = t1 * 1000
        metrics.status_code = getattr(result, "status_code", 200)
        
        # 记录整体度量
        PROXY_REQUESTS_TOTAL.labels(method="POST", status=str(metrics.status_code)).inc()
        PROXY_REQUEST_DURATION_MS.observe(metrics.total_ms)
        
        logger.info(f"[{metrics.trace_id}] total={metrics.total_ms:.1f}ms, status={metrics.status_code}")
        return result
    except Exception as e:
        t1 = time.perf_counter()
        metrics.t_end_ms = t1 * 1000
        metrics.error = str(e)
        metrics.status_code = 502
        PROXY_REQUESTS_TOTAL.labels(method="POST", status="502").inc()
        PROXY_REQUEST_DURATION_MS.observe(metrics.total_ms)
        logger.error(f"[{metrics.trace_id}] ERROR: {e}")
        raise


def record_parse_latency(metrics: ProxyMetrics, t_done: float) -> None:
    """记录解析阶段完成时间。"""
    metrics.t_parse_done_ms = t_done
    PROXY_PARSE_DURATION_MS.observe(metrics.parse_ms)


def record_routing_latency(metrics: ProxyMetrics, t_done: float) -> None:
    """记录路由阶段完成时间。"""
    metrics.t_routing_done_ms = t_done
    PROXY_ROUTING_DURATION_MS.observe(metrics.routing_ms)


def record_backend_start(metrics: ProxyMetrics, t_start: float, backend_name: str) -> None:
    """记录后端开始处理时间。"""
    metrics.t_backend_start_ms = t_start
    metrics.backend_used = backend_name
    PROXY_BACKEND_WAIT_MS.labels(backend=backend_name).set(metrics.backend_wait_ms)


def record_backend_done(metrics: ProxyMetrics, t_done: float, backend_name: str) -> None:
    """记录后端响应完成时间。"""
    metrics.t_backend_done_ms = t_done
    PROXY_BACKEND_RESPONSE_MS.labels(backend=backend_name).observe(metrics.backend_response_ms)


def record_assemble_done(metrics: ProxyMetrics, t_done: float) -> None:
    """记录组装阶段完成时间。"""
    metrics.t_assemble_done_ms = t_done
    PROXY_ASSEMBLE_DURATION_MS.observe(metrics.assemble_ms)
    
    # 记录完整 trace
    logger.info(f"[TRACE] {json.dumps(metrics.to_dict())}")
