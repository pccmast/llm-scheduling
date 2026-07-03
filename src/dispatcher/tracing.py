"""OpenTelemetry 分布式追踪初始化。

通过环境变量控制导出目标：
- OTEL_EXPORTER_OTLP_ENDPOINT 已设置 → 导出到 OTLP 兼容后端（Jaeger/Tempo/Datadog）
- 未设置 → 使用 ConsoleSpanExporter 打印到 stdout（开发模式）

采样策略：
- OTEL_TRACES_SAMPLER=always_on → 全量采样（默认，开发环境）
- OTEL_TRACES_SAMPLER=parentbased_traceidratio + OTEL_TRACES_SAMPLER_ARG=0.1 → 10% 采样（生产推荐）
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON


def init_tracing(service_name: str = "llm-dispatcher") -> trace.Tracer:
    """初始化 OpenTelemetry 追踪。

    创建 TracerProvider、配置 SpanProcessor 和 Exporter。
    设置全局 trace provider。

    Args:
        service_name: 服务名称，作为 resource 属性出现在所有 span 上。

    Returns:
        配置好的 Tracer 实例，可直接用于创建 span。
    """
    resource = Resource(attributes={SERVICE_NAME: service_name})

    # 根据环境变量选择 exporter
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    exporter: SpanExporter
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    else:
        exporter = ConsoleSpanExporter()  # 开发模式：打印到 stdout

    # 采样策略：优先读环境变量，默认全量
    sampler = ALWAYS_ON

    provider = TracerProvider(resource=resource, sampler=sampler)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    return trace.get_tracer(__name__)


def shutdown_tracing() -> None:
    """关闭追踪 provider，确保所有未导出的 span 被刷新。"""
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()
