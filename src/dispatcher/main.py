"""Module 13: Application Factory — 规格书 §4 Module 13.

创建 FastAPI 应用实例，通过 lifespan 上下文管理器协调所有组件的初始化与清理。
完整版：注入 Registry、Balancer、EngineAdapter、HealthChecker、RoutingProxy、
CircuitBreaker、Metrics、RequestQueue、DynamicBatcher、AutoScaler。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_client import make_asgi_app

from src.dispatcher.balancer import create_balancer
from src.dispatcher.batcher import DynamicBatcher
from src.dispatcher.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry
from src.dispatcher.engine import create_adapter_registry
from src.dispatcher.health import HealthChecker
from src.dispatcher.metrics import PrometheusMetricsRecorder
from src.dispatcher.proxy import RoutingProxy
from src.dispatcher.queue import RequestQueue
from src.dispatcher.registry import InstanceRegistry
from src.dispatcher.scaler import AutoScaler
from src.dispatcher.tracing import init_tracing, shutdown_tracing
from src.shared.config import load_config, load_yaml_config
from src.shared.logging import get_logger, setup_logging
from src.shared.models import (
    HealthCheckConfig,
    ScaleConfig,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器。"""
    # ── Startup ──
    logger.info("Starting LLM Dispatcher...")

    settings = load_config()
    yaml_config = load_yaml_config()
    setup_logging(settings.log_level)

    # 0. 初始化分布式追踪（OpenTelemetry）
    init_tracing(service_name="llm-dispatcher")

    # 1. Registry + 实例加载
    registry = InstanceRegistry()
    instances_cfg = yaml_config.get("instances", [])
    if instances_cfg:
        registry.load_from_config(instances_cfg)
        logger.info("loaded_instances", count=registry.count)

    # 2. Engine Adapters
    engine_adapters = create_adapter_registry()

    # 3. Load Balancer
    strategy = yaml_config.get("load_balancer", {}).get("strategy", "round_robin")
    balancer = create_balancer(strategy)

    # 4. Circuit Breakers
    cb_config_data = yaml_config.get("circuit_breaker", {})
    cb_config = CircuitBreakerConfig(**cb_config_data) if cb_config_data else CircuitBreakerConfig()
    cb_registry = CircuitBreakerRegistry(default_config=cb_config)

    # 5. Metrics
    metrics = PrometheusMetricsRecorder()

    # 6. Routing Proxy (pass registry for dynamic CB lookup)
    proxy = RoutingProxy(
        registry=registry,
        balancer=balancer,
        engine_adapters=engine_adapters,
        circuit_breakers=cb_registry,
        metrics=metrics,
        timeout=settings.upstream_timeout,
    )

    # 7. Request Queue
    q_config = yaml_config.get("queue", {})
    request_queue = RequestQueue(
        max_size=q_config.get("max_size", 100),
        max_wait_seconds=q_config.get("max_wait_seconds", 30.0),
    )

    # 8. Dynamic Batcher (optional)
    batcher_config = yaml_config.get("batcher", {})
    batcher = None
    if batcher_config.get("enabled", False):
        batcher = DynamicBatcher(
            proxy=proxy,
            max_batch_size=batcher_config.get("max_batch_size", 8),
            max_wait_ms=batcher_config.get("max_wait_ms", 50),
        )

    # 9. Auto Scaler (optional)
    scaler_config = yaml_config.get("auto_scaler", {})
    auto_scaler = None
    if scaler_config.get("enabled", False):
        scale_cfg = ScaleConfig(**scaler_config)
        auto_scaler = AutoScaler(registry=registry, metrics=metrics, config=scale_cfg, balancer=balancer)

    # 10. Health Checker
    hc_config_data = yaml_config.get("health_check", {})
    hc_config = HealthCheckConfig(**hc_config_data) if hc_config_data else HealthCheckConfig()
    health_checker = HealthChecker(
        registry=registry,
        engine_adapters=engine_adapters,
        config=hc_config,
    )
    health_task = asyncio.create_task(health_checker.run_forever())

    # ── 注入 app.state ──
    app.state.registry = registry
    app.state.balancer = balancer
    app.state.engine_adapters = engine_adapters
    app.state.proxy = proxy
    app.state.health_checker = health_checker
    app.state.settings = settings
    app.state.circuit_breakers = cb_registry
    app.state.metrics = metrics
    app.state.request_queue = request_queue
    app.state.batcher = batcher
    app.state.auto_scaler = auto_scaler

    logger.info("LLM Dispatcher started", port=settings.port)

    yield

    # ── Shutdown ──
    logger.info("Shutting down...")

    # 1. 停止健康检查
    health_checker.stop()
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass

    # 2. 进入 draining 模式，停止接受新请求
    proxy.drain()
    drained = await proxy.wait_drain(timeout=30.0)
    if not drained:
        logger.warning("Graceful shutdown timed out, forcing close")
    else:
        logger.info("All in-flight requests completed")

    # 3. 关闭连接池
    await proxy.close()

    # 4. 刷新并关闭追踪
    shutdown_tracing()

    logger.info("LLM Dispatcher stopped")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""
    app = FastAPI(title="LLM Inference Dispatcher", version="0.2.0", lifespan=lifespan)

    from src.dispatcher.api.admin import admin_router
    from src.dispatcher.api.health import health_router
    from src.dispatcher.api.inference import inference_router
    from src.dispatcher.middleware import AdminAuthMiddleware

    # Admin API 鉴权中间件（优先级最高）
    app.add_middleware(AdminAuthMiddleware)

    app.include_router(health_router)
    app.include_router(inference_router)
    app.include_router(admin_router)

    # Prometheus /metrics
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    return app
