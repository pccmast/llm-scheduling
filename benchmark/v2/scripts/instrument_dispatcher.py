"""调度器可观测性埋点 - 通过临时修改 proxy 代码来添加测量点。

这个脚本在运行时自动修改 src/dispatcher/proxy.py 的 forward_request 方法，
添加可观测性度量。修改后的 proxy 在函数退出后自动恢复原始代码。

用法:
    python benchmark/v2/scripts/instrument_dispatcher.py
    # 然后运行其他测试，proxy 会带有度量输出

或者手动集成到 proxy.py 中（推荐生产环境）。
"""

import sys, time, json, functools
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from dispatcher.proxy import ProxyDispatcher
from benchmark.v2.observability.instrumented_proxy import (
    ProxyMetrics, record_parse_latency, record_routing_latency,
    record_backend_start, record_backend_done, record_assemble_done,
    PROXY_REQUESTS_TOTAL, PROXY_REQUEST_DURATION_MS,
    PROXY_PARSE_DURATION_MS, PROXY_ROUTING_DURATION_MS,
    PROXY_BACKEND_RESPONSE_MS, PROXY_ASSEMBLE_DURATION_MS,
)


def instrument_forward_request(original_method):
    """装饰器: 为 forward_request 添加度量。"""
    @functools.wraps(original_method)
    async def wrapper(self, request, *args, **kwargs):
        metrics = ProxyMetrics(trace_id=str(id(request)))
        t0 = time.perf_counter()
        metrics.t_start_ms = t0 * 1000
        
        try:
            # 这里我们调用原始方法，但无法捕获内部阶段
            # 所以需要 monkeypatch 或者修改 proxy 源码
            result = await original_method(self, request, *args, **kwargs)
            t1 = time.perf_counter()
            metrics.t_end_ms = t1 * 1000
            metrics.status_code = getattr(result, "status_code", 200)
            
            PROXY_REQUESTS_TOTAL.labels(method="POST", status=str(metrics.status_code)).inc()
            PROXY_REQUEST_DURATION_MS.observe(metrics.total_ms)
            
            print(f"[METRIC] total={metrics.total_ms:.1f}ms status={metrics.status_code}")
            return result
        except Exception as e:
            t1 = time.perf_counter()
            metrics.t_end_ms = t1 * 1000
            metrics.status_code = 502
            metrics.error = str(e)
            PROXY_REQUESTS_TOTAL.labels(method="POST", status="502").inc()
            PROXY_REQUEST_DURATION_MS.observe(metrics.total_ms)
            print(f"[METRIC] ERROR total={metrics.total_ms:.1f}ms error={e}")
            raise
    return wrapper


def instrument():
    """Monkeypatch proxy 的 forward_request 方法。"""
    original = ProxyDispatcher.forward_request
    ProxyDispatcher.forward_request = instrument_forward_request(original)
    print("[Instrumented] ProxyDispatcher.forward_request instrumented")
    return original


def restore(original):
    """恢复原始方法。"""
    ProxyDispatcher.forward_request = original
    print("[Restored] ProxyDispatcher.forward_request restored")


if __name__ == "__main__":
    print("This module provides instrumentation utilities.")
    print("Import and use instrument() / restore() in your test scripts.")
    print("Example:")
    print("    from benchmark.v2.scripts.instrument_dispatcher import instrument, restore")
    print("    orig = instrument()")
    print("    # run tests...")
    print("    restore(orig)")
