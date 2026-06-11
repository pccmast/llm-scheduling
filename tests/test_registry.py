"""Module 2: Instance Registry 测试 — 规格书 §4 Module 2.

本文件覆盖 InstanceRegistry 的全部单元测试用例。
在 Phase 2 阶段创建骨架，Core 阶段填充实现。
"""

from __future__ import annotations

import pytest


class TestRegisterAndGet:
    """基本注册与查询测试。"""

    def test_register_and_get(self) -> None:
        """注册实例后可通过 get() 查询。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")

    def test_register_duplicate_id_raises_error(self) -> None:
        """重复 instance_id 注册时抛出 ValueError。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")


class TestDeregister:
    """注销实例测试。"""

    def test_deregister_existing_instance(self) -> None:
        """注销已注册实例后 count 减为 0。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")

    def test_deregister_nonexistent_raises_key_error(self) -> None:
        """注销不存在的 instance_id 时抛出 KeyError。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")


class TestListByModel:
    """按模型查询测试。"""

    def test_list_by_model_filters_healthy_only(self) -> None:
        """list_by_model 只返回指定模型的 HEALTHY 实例。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")

    def test_list_by_model_returns_empty_for_unknown_model(self) -> None:
        """查询不存在的模型名时返回空列表。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")


class TestStatusUpdate:
    """实例状态更新测试。"""

    def test_mark_unhealthy_excludes_from_list_by_model(self) -> None:
        """unhealthy 实例不出现在 list_by_model 结果中。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")

    def test_mark_healthy_does_not_recover_draining(self) -> None:
        """DRAINING 状态的实例不会被 mark_healthy 恢复为 HEALTHY。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")


class TestLoadFromConfig:
    """从配置文件加载实例测试。"""

    def test_load_from_config(self) -> None:
        """load_from_config 正确解析配置字典列表并注册实例。"""
        pytest.skip("未实现 — 将在 Core Step 2 中实现")
