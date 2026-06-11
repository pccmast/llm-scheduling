"""Module 1: Shared Models & Config 测试 — 规格书 §4 Module 1.

覆盖 Shared Models 模块的全部 7 个单元测试用例。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.shared.config import DispatcherSettings, load_config
from src.shared.models import (
    InferenceRequest,
    QueueItem,
    ScaleConfig,
    TokenUsage,
)


class TestTokenUsage:
    """TokenUsage 数据模型测试。"""

    def test_total_property(self) -> None:
        """TokenUsage.total = prompt_tokens + completion_tokens。"""
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50)
        assert usage.total == 150
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50

    def test_total_property_defaults(self) -> None:
        """默认值下 total 返回 0。"""
        usage = TokenUsage()
        assert usage.total == 0


class TestInferenceRequest:
    """InferenceRequest 数据模型测试。"""

    def test_estimated_input_tokens(self) -> None:
        """estimated_input_tokens = 消息总字符数 // 4，最小为 1。"""
        req = InferenceRequest(
            request_id="r1",
            model="test",
            messages=[{"role": "user", "content": "hello world"}],  # 11 chars
        )
        assert req.estimated_input_tokens == 2  # 11 // 4 = 2

    def test_estimated_input_tokens_min_one(self) -> None:
        """空消息或极短消息时 estimated_input_tokens 最小为 1。"""
        req = InferenceRequest(request_id="r1", model="test", messages=[{"role": "user", "content": "a"}])
        assert req.estimated_input_tokens == 1

        req2 = InferenceRequest(request_id="r2", model="test", messages=[])
        assert req2.estimated_input_tokens == 1

    def test_estimated_weight(self) -> None:
        """estimated_weight = estimated_input_tokens + max_tokens * 2.0。"""
        req = InferenceRequest(
            request_id="r1",
            model="test",
            messages=[{"role": "user", "content": "a" * 400}],  # 400 chars → 100 tokens
            max_tokens=500,
        )
        assert req.estimated_weight == 100.0 + 500 * 2.0  # 1100.0

    def test_estimated_weight_defaults(self) -> None:
        """默认 max_tokens=1024 时 estimated_weight 计算正确。"""
        req = InferenceRequest(request_id="r1", model="test", messages=[])
        assert req.estimated_weight == 1.0 + 1024 * 2.0


class TestQueueItem:
    """QueueItem 优先级排序测试。"""

    def test_priority_ordering_different_priority(self) -> None:
        """优先级不同时，priority 值小的排前面。"""
        req = InferenceRequest(request_id="r1", model="test", messages=[])
        item1 = QueueItem(priority=1, enqueue_time=100.0, request=req)
        item2 = QueueItem(priority=5, enqueue_time=50.0, request=req)
        assert item1 < item2
        assert not (item2 < item1)

    def test_priority_ordering_same_priority(self) -> None:
        """优先级相同时，先入队的排前面（FIFO）。"""
        req = InferenceRequest(request_id="r1", model="test", messages=[])
        item1 = QueueItem(priority=3, enqueue_time=100.0, request=req)
        item2 = QueueItem(priority=3, enqueue_time=50.0, request=req)
        # item2 入队更早（time 更小），应排在前面
        assert item2 < item1
        assert not (item1 < item2)

    def test_priority_ordering_equal(self) -> None:
        """完全相同优先级和时间的项不满足 < 关系。"""
        req = InferenceRequest(request_id="r1", model="test", messages=[])
        item1 = QueueItem(priority=5, enqueue_time=50.0, request=req)
        item2 = QueueItem(priority=5, enqueue_time=50.0, request=req)
        assert not (item1 < item2)
        assert not (item2 < item1)

    def test_queue_item_is_frozen(self) -> None:
        """QueueItem 是不可变对象。"""
        req = InferenceRequest(request_id="r1", model="test", messages=[])
        item = QueueItem(priority=5, enqueue_time=50.0, request=req)
        with pytest.raises(Exception):  # pydantic frozen model raises on mutation
            item.priority = 1  # type: ignore[misc]


class TestScaleConfig:
    """ScaleConfig 配置模型测试。"""

    def test_default_values(self) -> None:
        """ScaleConfig() 使用默认值：min_instances=1, max_instances=10。"""
        cfg = ScaleConfig()
        assert cfg.min_instances == 1
        assert cfg.max_instances == 10
        assert cfg.cooldown_seconds == 60
        assert cfg.scale_up_queue_threshold == 10
        assert cfg.scale_up_load_threshold == 0.8
        assert cfg.scale_down_idle_seconds == 300
        assert cfg.scale_down_load_threshold == 0.1

    def test_custom_values(self) -> None:
        """可以设置自定义值。"""
        cfg = ScaleConfig(min_instances=2, max_instances=5, cooldown_seconds=120)
        assert cfg.min_instances == 2
        assert cfg.max_instances == 5
        assert cfg.cooldown_seconds == 120


class TestConfig:
    """加载配置测试。"""

    def test_load_config_with_defaults(self) -> None:
        """配置缺失时使用默认值，不报错。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 空 YAML 文件
            config_path = Path(tmpdir) / "empty.yaml"
            config_path.write_text("{}", encoding="utf-8")

            settings = load_config(str(config_path))
            assert settings.host == "0.0.0.0"
            assert settings.port == 9090
            assert settings.upstream_timeout == 120.0
            assert settings.log_level == "info"

    def test_load_config_from_yaml(self) -> None:
        """YAML 配置正确加载。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_data = {"dispatcher": {"host": "127.0.0.1", "port": 8080, "log_level": "debug"}}
            config_path = Path(tmpdir) / "test.yaml"
            config_path.write_text(yaml.dump(yaml_data), encoding="utf-8")

            settings = load_config(str(config_path))
            assert settings.host == "127.0.0.1"
            assert settings.port == 8080
            assert settings.log_level == "debug"
            # 未配置的字段使用默认值
            assert settings.upstream_timeout == 120.0

    def test_load_config_nonexistent_file(self) -> None:
        """配置文件不存在时不报错，使用默认值。"""
        settings = load_config("./nonexistent/path/config.yaml")
        assert settings.port == 9090

    def test_dispatcher_settings_defaults(self) -> None:
        """DispatcherSettings 直接实例化使用默认值。"""
        # 清除环境变量影响
        with patch.dict("os.environ", {}, clear=True):
            settings = DispatcherSettings()
            assert settings.host == "0.0.0.0"
            assert settings.port == 9090
