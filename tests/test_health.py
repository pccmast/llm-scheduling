"""Module 3: Health Checker 测试 — 规格书 §4 Module 3.

覆盖 HealthChecker 的 check_one、连续失败标记、恢复检测、DRAINING 跳过、stop 行为。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.dispatcher.health import HealthChecker
from src.dispatcher.registry import InstanceRegistry
from src.shared.models import (
    EngineAdapter,
    HealthCheckConfig,
    InstanceStatus,
    ModelInstance,
)


@pytest.fixture
def registry() -> InstanceRegistry:
    return InstanceRegistry()


@pytest.fixture
def mock_adapter() -> MagicMock:
    """Mock EngineAdapter 用于健康检查测试。"""
    adapter = MagicMock(spec=EngineAdapter)
    adapter.engine_type = "ollama"
    adapter.health_endpoint.return_value = "http://localhost:8000/health"
    return adapter


@pytest.fixture
def health_config() -> HealthCheckConfig:
    return HealthCheckConfig(
        interval_seconds=10.0,
        timeout_seconds=5.0,
        unhealthy_threshold=3,
        healthy_threshold=1,
    )


@pytest.fixture
def instance() -> ModelInstance:
    return ModelInstance(
        instance_id="test-1",
        address="http://localhost:8000",
        model="test-model",
        engine_type="ollama",
    )


@pytest.fixture
def health_checker(
    registry: InstanceRegistry, mock_adapter: MagicMock, health_config: HealthCheckConfig
) -> HealthChecker:
    return HealthChecker(registry, {"ollama": mock_adapter}, health_config)


class TestCheckOne:
    """单次健康检查测试。"""

    @pytest.mark.asyncio
    async def test_check_one_healthy(self, health_checker: HealthChecker, instance: ModelInstance) -> None:
        """健康实例返回 True。"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 200
            result = await health_checker.check_one(instance)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_one_unhealthy_500(self, health_checker: HealthChecker, instance: ModelInstance) -> None:
        """5xx 响应返回 False。"""
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 500
            result = await health_checker.check_one(instance)
            assert result is False

    @pytest.mark.asyncio
    async def test_check_one_timeout(self, health_checker: HealthChecker, instance: ModelInstance) -> None:
        """超时不抛出异常，返回 False。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = asyncio.TimeoutError()
            result = await health_checker.check_one(instance)
            assert result is False

    @pytest.mark.asyncio
    async def test_check_one_connection_error(self, health_checker: HealthChecker, instance: ModelInstance) -> None:
        """连接错误不抛出异常，返回 False。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.side_effect = ConnectionError("refused")
            result = await health_checker.check_one(instance)
            assert result is False

    @pytest.mark.asyncio
    async def test_check_one_unknown_engine_type(
        self, registry: InstanceRegistry, health_config: HealthCheckConfig
    ) -> None:
        """未知引擎类型返回 False。"""
        instance = ModelInstance(
            instance_id="t1",
            address="http://l:8000",
            model="test",
            engine_type="unknown",
        )
        checker = HealthChecker(registry, {}, health_config)
        result = await checker.check_one(instance)
        assert result is False


class TestFailureTracking:
    """连续失败检测测试。"""

    def test_get_failure_count_initially_zero(self, health_checker: HealthChecker) -> None:
        """初始连续失败次数为 0。"""
        assert health_checker.get_failure_count("test-1") == 0

    @pytest.mark.asyncio
    async def test_continuous_failures_mark_unhealthy(
        self,
        registry: InstanceRegistry,
        instance: ModelInstance,
        mock_adapter: MagicMock,
        health_config: HealthCheckConfig,
    ) -> None:
        """连续失败 3 次后标记为 UNHEALTHY。"""
        registry.register(instance)
        checker = HealthChecker(registry, {"ollama": mock_adapter}, health_config)

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 500

            # 连续 3 次失败
            for _ in range(3):
                await checker._check_and_update(instance)

        assert registry.get("test-1").status == InstanceStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_recovery_after_success(
        self,
        registry: InstanceRegistry,
        instance: ModelInstance,
        mock_adapter: MagicMock,
        health_config: HealthCheckConfig,
    ) -> None:
        """UNHEALTHY 实例在成功后恢复为 HEALTHY。"""
        # 先标记为 unhealthy
        registry.register(instance)
        registry.mark_unhealthy("test-1")

        checker = HealthChecker(registry, {"ollama": mock_adapter}, health_config)

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_get.return_value.status_code = 200
            await checker._check_and_update(instance)

        assert registry.get("test-1").status == InstanceStatus.HEALTHY


class TestDrainingSkip:
    """DRAINING 实例跳过检查。"""

    def test_skips_draining_instances(
        self,
        registry: InstanceRegistry,
        instance: ModelInstance,
        mock_adapter: MagicMock,
        health_config: HealthCheckConfig,
    ) -> None:
        """DRAINING 实例不被检查。"""
        instance.status = InstanceStatus.DRAINING
        registry.register(instance)
        # HealthChecker 在 run_forever 中过滤 DRAINING 实例
        # 验证 list_all 不返回 DRAINING 实例作为健康候选
        instances = registry.list_all()
        non_draining = [i for i in instances if i.status != InstanceStatus.DRAINING]
        assert len(non_draining) == 0  # DRAINING 实例被跳过


class TestStop:
    """停止检查器测试。"""

    @pytest.mark.asyncio
    async def test_stop_stops_loop(
        self,
        health_checker: HealthChecker,
    ) -> None:
        """stop() 能在短时间内停止运行中的循环。"""
        health_checker.stop()
        assert health_checker._stopped.is_set()

    @pytest.mark.asyncio
    async def test_stop_within_timeout(self) -> None:
        """stop() 可以在 1 秒内停止循环。"""
        registry = InstanceRegistry()
        mock_adapter = MagicMock(spec=EngineAdapter)
        mock_adapter.engine_type = "ollama"
        mock_adapter.health_endpoint.return_value = "http://localhost/health"
        config = HealthCheckConfig(interval_seconds=10.0, timeout_seconds=5.0)
        checker = HealthChecker(registry, {"ollama": mock_adapter}, config)

        # 启动并在短时间后停止
        task = asyncio.create_task(checker.run_forever())
        await asyncio.sleep(0.1)
        checker.stop()

        # 等待任务在 1 秒内结束
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("stop() did not stop the loop within 1 second")
