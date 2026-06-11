"""Module 1: Shared Models & Config 测试 — 规格书 §4 Module 1.

本文件覆盖 Shared Models 模块的全部 7 个单元测试用例。
在 Phase 2 阶段创建骨架，Core 阶段填充实现。
"""

from __future__ import annotations

import pytest


class TestTokenUsage:
    """TokenUsage 数据模型测试。"""

    def test_total_property(self) -> None:
        """TokenUsage.total = prompt_tokens + completion_tokens."""
        pytest.skip("未实现 — 将在 Core Step 1 中实现")


class TestInferenceRequest:
    """InferenceRequest 数据模型测试。"""

    def test_estimated_input_tokens(self) -> None:
        """estimated_input_tokens = 消息总字符数 // 4，最小为 1。"""
        pytest.skip("未实现 — 将在 Core Step 1 中实现")

    def test_estimated_weight(self) -> None:
        """estimated_weight = estimated_input_tokens + max_tokens * 2.0。"""
        pytest.skip("未实现 — 将在 Core Step 1 中实现")


class TestQueueItem:
    """QueueItem 优先级排序测试。"""

    def test_priority_ordering_different_priority(self) -> None:
        """优先级不同时，priority 值小的排前面。"""
        pytest.skip("未实现 — 将在 Core Step 1 中实现")

    def test_priority_ordering_same_priority(self) -> None:
        """优先级相同时，先入队的排前面（FIFO）。"""
        pytest.skip("未实现 — 将在 Core Step 1 中实现")


class TestScaleConfig:
    """ScaleConfig 配置模型测试。"""

    def test_default_values(self) -> None:
        """ScaleConfig() 使用默认值：min_instances=1, max_instances=10。"""
        pytest.skip("未实现 — 将在 Core Step 1 中实现")


class TestConfig:
    """加载配置测试。"""

    def test_load_config_with_defaults(self) -> None:
        """配置缺失时使用默认值，不报错。"""
        pytest.skip("未实现 — 将在 Core Step 1 中实现")
