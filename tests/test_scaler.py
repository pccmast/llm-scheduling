"""Module 11: Auto Scaler 测试 — 规格书 §4 Module 11."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src.dispatcher.registry import InstanceRegistry
from src.dispatcher.scaler import AutoScaler
from src.shared.models import ScaleConfig, ScaleDecision


@pytest.fixture
def registry() -> InstanceRegistry:
    r = InstanceRegistry()
    from src.shared.models import ModelInstance

    for i in range(3):
        r.register(
            ModelInstance(
                instance_id=f"i{i + 1}",
                address=f"http://gpu-{i + 1}:8000",
                model="test",
                engine_type="ollama",
            )
        )
    return r


@pytest.fixture
def config() -> ScaleConfig:
    return ScaleConfig(
        scale_up_queue_threshold=10,
        scale_up_load_threshold=0.8,
        scale_down_idle_seconds=2,
        scale_down_load_threshold=0.1,
        min_instances=1,
        max_instances=10,
        cooldown_seconds=1,
    )


@pytest.fixture
def scaler(registry: InstanceRegistry, config: ScaleConfig) -> AutoScaler:
    mock_metrics = MagicMock()
    return AutoScaler(registry, mock_metrics, config)


class TestScaleUp:
    def test_scale_up_triggered(self, scaler: AutoScaler) -> None:
        scaler._metrics.get_summary.return_value = {
            "queue_depth": 15,
            "per_instance": {"i1": {"request_count": 5}, "i2": {"request_count": 5}, "i3": {"request_count": 5}},
        }
        scaler._last_scale_time = 0  # no cooldown
        decision = scaler.evaluate()
        assert decision.action == "scale_up"

    def test_no_scale_up_at_max(self, scaler: AutoScaler, registry: InstanceRegistry) -> None:
        # Set max_instances to current count
        scaler._config.max_instances = 3
        scaler._metrics.get_summary.return_value = {
            "queue_depth": 15,
            "per_instance": {"i1": {"request_count": 5}},
        }
        scaler._last_scale_time = 0
        decision = scaler.evaluate()
        assert decision.action == "none"

    def test_no_scale_up_during_cooldown(self, scaler: AutoScaler) -> None:
        scaler._metrics.get_summary.return_value = {
            "queue_depth": 15,
            "per_instance": {"i1": {"request_count": 5}},
        }
        scaler._last_scale_time = time.monotonic()  # just now
        decision = scaler.evaluate()
        assert decision.action == "none"


class TestScaleDown:
    def test_scale_down_triggered(self, scaler: AutoScaler, registry: InstanceRegistry) -> None:
        scaler._metrics.get_summary.return_value = {
            "queue_depth": 0,
            "per_instance": {"i1": {"request_count": 0}},
        }
        scaler._last_scale_time = 0
        scaler._idle_since = time.monotonic() - 10  # idle for 10s
        decision = scaler.evaluate()
        assert decision.action == "scale_down"

    def test_no_scale_down_at_min(self, scaler: AutoScaler, registry: InstanceRegistry) -> None:
        # Set registry to min count
        for i in range(2):
            registry.deregister(f"i{i + 2}")
        scaler._config.min_instances = 1
        scaler._metrics.get_summary.return_value = {
            "queue_depth": 0,
            "per_instance": {"i1": {"request_count": 0}},
        }
        scaler._last_scale_time = 0
        scaler._idle_since = time.monotonic() - 10
        decision = scaler.evaluate()
        assert decision.action == "none"


class TestNoAction:
    def test_normal_operation(self, scaler: AutoScaler) -> None:
        scaler._metrics.get_summary.return_value = {
            "queue_depth": 3,
            "per_instance": {"i1": {"request_count": 2}},
        }
        decision = scaler.evaluate()
        assert decision.action == "none"

    def test_insufficient_data(self, scaler: AutoScaler) -> None:
        scaler._metrics.get_summary.return_value = {
            "queue_depth": 0,
            "per_instance": {},
        }
        decision = scaler.evaluate()
        assert decision.action == "none"

    def test_no_instances(self, scaler: AutoScaler, registry: InstanceRegistry) -> None:
        # Clear registry
        for i in range(3):
            registry.deregister(f"i{i + 1}")
        scaler._metrics.get_summary.return_value = {"queue_depth": 0, "per_instance": {}}
        decision = scaler.evaluate()
        assert decision.action == "none"
        assert "No instances" in decision.reason


class TestRecordScaleEvent:
    def test_updates_last_scale_time(self, scaler: AutoScaler) -> None:
        old_time = scaler._last_scale_time
        scaler.record_scale_event(ScaleDecision(action="scale_up", count=1))
        assert scaler._last_scale_time > old_time
