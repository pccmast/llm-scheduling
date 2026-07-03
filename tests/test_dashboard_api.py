"""Dashboard API 端点测试 — 验证 Streamlit Dashboard 依赖的 4 个 admin 端点。

测试覆盖:
- GET /admin/status       → 实例概览 + 熔断器状态 + 队列深度
- GET /admin/instances    → 实例列表（支持 model 过滤）
- GET /admin/metrics/summary → 请求数/错误数/P95/P99
- GET /admin/scaling/evaluate → 扩缩容决策（未启用/已启用两种场景）

使用 TestClient + 真实组件（非 Mock），模拟 dashboard 实际调用的数据路径。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from src.dispatcher.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry
from src.dispatcher.metrics import PrometheusMetricsRecorder
from src.dispatcher.registry import InstanceRegistry
from src.shared.models import ModelInstance, TokenUsage

# ── Fixtures ─────────────────────────────────────────────────

def _make_token_usage(prompt: int = 10, completion: int = 5) -> TokenUsage:
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion)


@pytest.fixture
def registry_with_instances() -> InstanceRegistry:
    """注册 2 个健康实例（模拟 docker-compose 环境）。"""
    r = InstanceRegistry()
    r.register(ModelInstance(
        instance_id="gpu-1",
        address="http://gpu-1:8000",
        model="test-model",
        engine_type="ollama",
        capacity_factor=1.0,
    ))
    r.register(ModelInstance(
        instance_id="gpu-2",
        address="http://gpu-2:8000",
        model="test-model",
        engine_type="vllm",
        capacity_factor=1.5,
    ))
    return r


@pytest.fixture
def dashboard_client(registry_with_instances: InstanceRegistry) -> TestClient:
    """创建注入真实组件的 TestClient，模拟 Dashboard 的 HTTP 调用路径。"""
    from src.dispatcher.main import create_app

    app = create_app()

    # 注入真实组件到 app.state（绕过 lifespan）
    app.state.registry = registry_with_instances
    app.state.circuit_breakers = CircuitBreakerRegistry(default_config=CircuitBreakerConfig())
    # 使用独立 registry 避免与 create_app() 中 make_asgi_app() 的全局 registry 冲突
    app.state.metrics = PrometheusMetricsRecorder(registry=CollectorRegistry())
    app.state.request_queue = None
    app.state.auto_scaler = None
    app.state.balancer = None
    app.state.proxy = None
    app.state.health_checker = None
    app.state.engine_adapters = None
    app.state.settings = type("obj", (object,), {"port": 9090})()

    # 记录一些指标数据 (signature: instance_id, latency_ms, ttft_ms, tokens, success)
    app.state.metrics.record_request("gpu-1", 45.2, 12.0, _make_token_usage(10, 5), True)
    app.state.metrics.record_request("gpu-2", 120.5, 30.0, _make_token_usage(20, 10), True)
    app.state.metrics.record_request("gpu-1", 380.0, 80.0, _make_token_usage(50, 30), True)
    app.state.metrics.record_request("gpu-1", 15.0, 3.0, _make_token_usage(5, 0), False)
    app.state.metrics.record_request("gpu-2", 5200.0, 200.0, _make_token_usage(100, 50), True)
    # 初始化熔断器（模拟正常运行中已创建）
    for iid in ["gpu-1", "gpu-2"]:
        app.state.circuit_breakers.get_or_create(iid)

    return TestClient(app)


def _make_empty_app():
    """创建无状态 app 的辅助函数。"""
    from src.dispatcher.main import create_app
    app = create_app()
    app.state.registry = InstanceRegistry()
    app.state.circuit_breakers = None
    app.state.metrics = PrometheusMetricsRecorder(registry=CollectorRegistry())
    app.state.request_queue = None
    app.state.auto_scaler = None
    app.state.balancer = None
    app.state.proxy = None
    app.state.health_checker = None
    app.state.engine_adapters = None
    app.state.settings = type("obj", (object,), {"port": 9090})()
    return app


# ── /admin/status ────────────────────────────────────────────


class TestAdminStatus:
    """Dashboard 概览页依赖的核心端点。"""

    def test_status_returns_instances_summary(self, dashboard_client: TestClient) -> None:
        """返回 instances 字段包含 total/healthy/unhealthy/draining。"""
        resp = dashboard_client.get("/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        inst = data["instances"]
        assert inst["total"] == 2
        assert inst["healthy"] == 2
        assert inst["unhealthy"] == 0
        assert inst["draining"] == 0

    def test_status_returns_circuit_breakers(self, dashboard_client: TestClient) -> None:
        """返回 circuit_breakers 字典，key 为 instance_id，value 为状态字符串。"""
        resp = dashboard_client.get("/admin/status")
        assert resp.status_code == 200
        cb = resp.json()["circuit_breakers"]
        assert "gpu-1" in cb
        assert "gpu-2" in cb
        assert cb["gpu-1"] == "closed"
        assert cb["gpu-2"] == "closed"

    def test_status_returns_queue_depth(self, dashboard_client: TestClient) -> None:
        """返回 queue_depth 字段（无队列时为 0）。"""
        resp = dashboard_client.get("/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "queue_depth" in data
        assert data["queue_depth"] == 0

    def test_status_empty_registry(self) -> None:
        """空注册表时返回正确的默认值。"""
        app = _make_empty_app()
        client = TestClient(app)
        resp = client.get("/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instances"]["total"] == 0
        assert data["instances"]["healthy"] == 0
        assert data["circuit_breakers"] == {}
        assert data["queue_depth"] == 0


# ── /admin/instances ─────────────────────────────────────────


class TestAdminInstances:
    """Dashboard 实例详情页依赖的端点。"""

    def test_list_all_instances(self, dashboard_client: TestClient) -> None:
        """列出所有实例，返回包含 instance_id/address/model/engine_type/status 的字典列表。"""
        resp = dashboard_client.get("/admin/instances")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2

        instance_ids = {i["instance_id"] for i in data}
        assert instance_ids == {"gpu-1", "gpu-2"}

        for inst in data:
            assert "instance_id" in inst
            assert "address" in inst
            assert "model" in inst
            assert "engine_type" in inst
            assert "status" in inst
            assert inst["status"] == "healthy"

    def test_filter_by_model(self, dashboard_client: TestClient) -> None:
        """支持 ?model= 参数过滤。"""
        # 注册一个不同 model 的实例
        r = dashboard_client.app.state.registry
        r.register(ModelInstance(
            instance_id="gpu-3",
            address="http://gpu-3:8000",
            model="other-model",
            engine_type="tgi",
        ))

        # 过滤 test-model
        resp = dashboard_client.get("/admin/instances?model=test-model")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(i["model"] == "test-model" for i in data)

        # 过滤不存在的 model
        resp = dashboard_client.get("/admin/instances?model=nonexistent")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_instances_include_capacity_factor(self, dashboard_client: TestClient) -> None:
        """实例详情包含 capacity_factor 和 max_concurrent 字段。"""
        resp = dashboard_client.get("/admin/instances")
        data = resp.json()
        gpu1 = next(i for i in data if i["instance_id"] == "gpu-1")
        assert gpu1["capacity_factor"] == 1.0
        assert gpu1["max_concurrent"] == 10


# ── /admin/metrics/summary ───────────────────────────────────


class TestAdminMetricsSummary:
    """Dashboard 概览页指标卡片依赖的端点。"""

    def test_metrics_summary_basic(self, dashboard_client: TestClient) -> None:
        """返回 request_count、error_count、p95、p99 等字段。"""
        resp = dashboard_client.get("/admin/metrics/summary")
        assert resp.status_code == 200
        data = resp.json()

        assert data["request_count"] == 5
        assert data["error_count"] == 1
        assert "avg_latency_ms" in data
        assert "p95_latency_ms" in data
        assert "p99_latency_ms" in data
        # P99 应覆盖 5200ms 的长尾请求
        assert data["p99_latency_ms"] >= 5000.0
        # P95 应覆盖 380ms
        assert data["p95_latency_ms"] >= 360.0

    def test_metrics_summary_empty(self) -> None:
        """无指标数据时返回各项为 0。"""
        app = _make_empty_app()
        client = TestClient(app)
        resp = client.get("/admin/metrics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["request_count"] == 0
        assert data["error_count"] == 0

    def test_metrics_summary_per_instance(self, dashboard_client: TestClient) -> None:
        """返回 per_instance 字典，key 为 instance_id。"""
        resp = dashboard_client.get("/admin/metrics/summary")
        data = resp.json()
        assert "per_instance" in data
        assert "gpu-1" in data["per_instance"]
        assert "gpu-2" in data["per_instance"]


# ── /admin/scaling/evaluate ──────────────────────────────────


class TestAdminScalingEvaluate:
    """Dashboard 扩缩容页依赖的端点。"""

    def test_scaling_not_configured(self, dashboard_client: TestClient) -> None:
        """未启用 auto_scaler 时返回 'Auto scaler not configured'。"""
        resp = dashboard_client.get("/admin/scaling/evaluate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "none"
        assert data["reason"] == "Auto scaler not configured"

    def test_scaling_enabled_returns_decision(self, registry_with_instances: InstanceRegistry) -> None:
        """启用 auto_scaler 后返回有效决策对象。"""
        from src.dispatcher.balancer import create_balancer
        from src.dispatcher.main import create_app
        from src.dispatcher.scaler import AutoScaler
        from src.shared.models import ScaleConfig

        app = create_app()
        app.state.registry = registry_with_instances
        app.state.metrics = PrometheusMetricsRecorder(registry=CollectorRegistry())
        app.state.balancer = create_balancer("round_robin")
        app.state.circuit_breakers = None
        app.state.request_queue = None
        app.state.health_checker = None
        app.state.proxy = None
        app.state.engine_adapters = None
        app.state.settings = type("obj", (object,), {"port": 9090})()

        cfg = ScaleConfig(
            scale_up_queue_threshold=10,
            scale_up_load_threshold=8000.0,
            scale_down_idle_seconds=300,
            scale_down_load_threshold=500.0,
            min_instances=1,
            max_instances=10,
            cooldown_seconds=0,
        )
        app.state.auto_scaler = AutoScaler(
            registry=registry_with_instances,
            metrics=app.state.metrics,
            config=cfg,
            balancer=app.state.balancer,
        )

        client = TestClient(app)
        resp = client.get("/admin/scaling/evaluate")
        assert resp.status_code == 200
        data = resp.json()
        assert "action" in data
        assert "reason" in data
        assert data["action"] in ("scale_up", "scale_down", "none")
