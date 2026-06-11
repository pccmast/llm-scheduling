"""Module 13: Application Factory — 规格书 §4 Module 13.

创建 FastAPI 应用实例，通过 lifespan 上下文管理器协调所有组件的初始化与清理。
Sprint 1 简化版：只注入 Registry、Balancer、EngineAdapter(Ollama)、HealthChecker、RoutingProxy。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.shared.config import load_config, load_yaml_config
from src.shared.logging import setup_logging, get_logger
from src.shared.models import HealthCheckConfig
from src.dispatcher.registry import InstanceRegistry
from src.dispatcher.balancer import create_balancer
from src.dispatcher.engine import create_adapter_registry
from src.dispatcher.health import HealthChecker
from src.dispatcher.proxy import RoutingProxy

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器。

    Startup 阶段（按顺序）：
    1. 加载 DispatcherSettings 配置
    2. 创建 InstanceRegistry 并从配置加载实例
    3. 创建 EngineAdapter 注册表
    4. 创建 LoadBalancer（根据配置策略）
    5. 创建 RoutingProxy
    6. 创建 HealthChecker 并启动后台检查任务
    7. 将所有组件注入 app.state

    Shutdown 阶段：
    1. 停止 HealthChecker
    2. 关闭 RoutingProxy（httpx.AsyncClient）
    """
    # ── Startup ──
    logger.info("Starting LLM Dispatcher...")

    # 1. 加载配置
    settings = load_config()
    yaml_config = load_yaml_config()
    setup_logging(settings.log_level)

    # 2. 创建并加载实例注册表
    registry = InstanceRegistry()
    instances_cfg = yaml_config.get("instances", [])
    if instances_cfg:
        registry.load_from_config(instances_cfg)
        logger.info("loaded_instances", count=registry.count)

    # 3. 创建引擎适配器
    engine_adapters = create_adapter_registry()

    # 4. 创建负载均衡器
    strategy = yaml_config.get("load_balancer", {}).get("strategy", "round_robin")
    balancer = create_balancer(strategy)
    logger.info("balancer_strategy", strategy=strategy)

    # 5. 创建路由代理（Sprint 1: 无 CircuitBreaker、无 Metrics）
    proxy = RoutingProxy(
        registry=registry,
        balancer=balancer,
        engine_adapters=engine_adapters,
        circuit_breakers=None,
        metrics=None,
        timeout=settings.upstream_timeout,
    )

    # 6. 创建健康检查器（Sprint 1: 后台运行）
    health_config = yaml_config.get("health_check", {})
    health_checker = HealthChecker(
        registry=registry,
        engine_adapters=engine_adapters,
        config=HealthCheckConfig(**health_config) if health_config else HealthCheckConfig(),
    )
    health_task = asyncio.create_task(health_checker.run_forever())

    # 7. 注入 app.state
    app.state.registry = registry
    app.state.balancer = balancer
    app.state.engine_adapters = engine_adapters
    app.state.proxy = proxy
    app.state.health_checker = health_checker
    app.state.settings = settings

    # Sprint 2-3 待注入的组件（占位）
    app.state.request_queue = None
    app.state.batcher = None
    app.state.circuit_breakers = None
    app.state.metrics = None
    app.state.auto_scaler = None

    logger.info("LLM Dispatcher started", port=settings.port)

    yield

    # ── Shutdown ──
    logger.info("Shutting down LLM Dispatcher...")
    health_checker.stop()
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    await proxy.close()
    logger.info("LLM Dispatcher stopped")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。

    Returns:
        配置完成的 FastAPI 实例，包含 lifespan 管理器、推理/管理/健康路由。
    """
    app = FastAPI(
        title="LLM Inference Dispatcher",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 注册路由
    from src.dispatcher.api.health import health_router
    from src.dispatcher.api.inference import inference_router
    from src.dispatcher.api.admin import admin_router

    app.include_router(health_router)
    app.include_router(inference_router)
    app.include_router(admin_router)

    return app
