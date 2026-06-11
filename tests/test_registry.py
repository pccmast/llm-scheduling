"""Module 2: Instance Registry 测试 — 规格书 §4 Module 2.

覆盖 InstanceRegistry 的全部单元测试用例。
"""

from __future__ import annotations

import pytest

from src.dispatcher.registry import InstanceRegistry
from src.shared.models import InstanceStatus, ModelInstance


@pytest.fixture
def registry() -> InstanceRegistry:
    """返回一个空的注册表实例。"""
    return InstanceRegistry()


@pytest.fixture
def sample_instance() -> ModelInstance:
    """返回一个标准测试实例。"""
    return ModelInstance(
        instance_id="i1",
        address="http://localhost:8000",
        model="llama-3",
        engine_type="ollama",
    )


class TestRegisterAndGet:
    """基本注册与查询测试。"""

    def test_register_and_get(self, registry: InstanceRegistry, sample_instance: ModelInstance) -> None:
        """注册实例后可通过 get() 查询。"""
        registry.register(sample_instance)
        retrieved = registry.get("i1")
        assert retrieved.instance_id == "i1"
        assert retrieved.model == "llama-3"
        assert retrieved.address == "http://localhost:8000"

    def test_register_duplicate_id_raises_error(
        self, registry: InstanceRegistry, sample_instance: ModelInstance
    ) -> None:
        """重复 instance_id 注册时抛出 ValueError。"""
        registry.register(sample_instance)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(sample_instance)

    def test_register_multiple_instances(self, registry: InstanceRegistry) -> None:
        """可以注册多个不同 ID 的实例。"""
        i1 = ModelInstance(instance_id="i1", address="http://a:8000", model="m1", engine_type="ollama")
        i2 = ModelInstance(instance_id="i2", address="http://b:8000", model="m2", engine_type="vllm")
        registry.register(i1)
        registry.register(i2)
        assert registry.count == 2


class TestDeregister:
    """注销实例测试。"""

    def test_deregister_existing_instance(self, registry: InstanceRegistry, sample_instance: ModelInstance) -> None:
        """注销已注册实例后 count 减为 0。"""
        registry.register(sample_instance)
        assert registry.count == 1
        registry.deregister("i1")
        assert registry.count == 0

    def test_deregister_nonexistent_raises_key_error(self, registry: InstanceRegistry) -> None:
        """注销不存在的 instance_id 时抛出 KeyError。"""
        with pytest.raises(KeyError, match="not found"):
            registry.deregister("not_exist")


class TestListByModel:
    """按模型查询测试。"""

    def test_list_by_model_filters_healthy_only(self, registry: InstanceRegistry) -> None:
        """list_by_model 只返回指定模型的 HEALTHY 实例。"""
        i1 = ModelInstance(
            instance_id="i1",
            address="http://a:8000",
            model="llama-3",
            engine_type="ollama",
            status=InstanceStatus.HEALTHY,
        )
        i2 = ModelInstance(
            instance_id="i2", address="http://b:8000", model="gpt-4o", engine_type="vllm", status=InstanceStatus.HEALTHY
        )
        i3 = ModelInstance(
            instance_id="i3",
            address="http://c:8000",
            model="llama-3",
            engine_type="tgi",
            status=InstanceStatus.UNHEALTHY,
        )

        registry.register(i1)
        registry.register(i2)
        registry.register(i3)

        result = registry.list_by_model("llama-3")
        assert len(result) == 1
        assert result[0].instance_id == "i1"

    def test_list_by_model_returns_empty_for_unknown_model(
        self, registry: InstanceRegistry, sample_instance: ModelInstance
    ) -> None:
        """查询不存在的模型名时返回空列表。"""
        registry.register(sample_instance)
        result = registry.list_by_model("unknown_model")
        assert result == []

    def test_list_by_model_returns_empty_for_unhealthy_only(self, registry: InstanceRegistry) -> None:
        """所有匹配模型的实例都不健康时返回空列表。"""
        i1 = ModelInstance(
            instance_id="i1",
            address="http://a:8000",
            model="llama-3",
            engine_type="ollama",
            status=InstanceStatus.UNHEALTHY,
        )
        registry.register(i1)
        assert registry.list_by_model("llama-3") == []


class TestStatusUpdate:
    """实例状态更新测试。"""

    def test_mark_unhealthy_excludes_from_list_by_model(
        self, registry: InstanceRegistry, sample_instance: ModelInstance
    ) -> None:
        """unhealthy 实例不出现在 list_by_model 结果中。"""
        registry.register(sample_instance)
        assert len(registry.list_by_model("llama-3")) == 1

        registry.mark_unhealthy("i1")
        assert len(registry.list_by_model("llama-3")) == 0

    def test_mark_healthy_restores_from_unhealthy(self, registry: InstanceRegistry) -> None:
        """UNHEALTHY 实例可以通过 mark_healthy 恢复。"""
        i1 = ModelInstance(
            instance_id="i1",
            address="http://a:8000",
            model="llama-3",
            engine_type="ollama",
            status=InstanceStatus.UNHEALTHY,
        )
        registry.register(i1)
        assert len(registry.list_by_model("llama-3")) == 0

        registry.mark_healthy("i1")
        assert len(registry.list_by_model("llama-3")) == 1

    def test_mark_healthy_does_not_recover_draining(self, registry: InstanceRegistry) -> None:
        """DRAINING 状态的实例不会被 mark_healthy 恢复为 HEALTHY。"""
        i1 = ModelInstance(
            instance_id="i1",
            address="http://a:8000",
            model="llama-3",
            engine_type="ollama",
            status=InstanceStatus.DRAINING,
        )
        registry.register(i1)
        registry.mark_healthy("i1")
        # DRAINING 应保持不变
        assert registry.get("i1").status == InstanceStatus.DRAINING

    def test_update_status_to_draining(self, registry: InstanceRegistry, sample_instance: ModelInstance) -> None:
        """可以将实例状态更新为 DRAINING。"""
        registry.register(sample_instance)
        registry.update_status("i1", InstanceStatus.DRAINING)
        assert registry.get("i1").status == InstanceStatus.DRAINING


class TestLoadFromConfig:
    """从配置文件加载实例测试。"""

    def test_load_from_config(self, registry: InstanceRegistry) -> None:
        """load_from_config 正确解析配置字典列表并注册实例。"""
        configs = [
            {"instance_id": "i1", "address": "http://a:8000", "model": "llama-3", "engine_type": "ollama"},
            {"instance_id": "i2", "address": "http://b:8000", "model": "llama-3", "engine_type": "vllm"},
        ]
        registry.load_from_config(configs)
        assert registry.count == 2
        assert registry.get("i1").engine_type == "ollama"
        assert registry.get("i2").engine_type == "vllm"

    def test_load_from_config_empty(self, registry: InstanceRegistry) -> None:
        """空配置列表不报错。"""
        registry.load_from_config([])
        assert registry.count == 0


class TestListAll:
    """list_all 测试。"""

    def test_list_all_returns_all_instances(self, registry: InstanceRegistry) -> None:
        """list_all 返回所有实例（包括 unhealthy 和 draining）。"""
        i1 = ModelInstance(
            instance_id="i1", address="http://a:8000", model="m1", engine_type="ollama", status=InstanceStatus.HEALTHY
        )
        i2 = ModelInstance(
            instance_id="i2", address="http://b:8000", model="m2", engine_type="vllm", status=InstanceStatus.UNHEALTHY
        )
        registry.register(i1)
        registry.register(i2)
        all_instances = registry.list_all()
        assert len(all_instances) == 2

    def test_list_all_returns_copy(self, registry: InstanceRegistry, sample_instance: ModelInstance) -> None:
        """list_all 返回副本，外部修改不影响注册表。"""
        registry.register(sample_instance)
        instances = registry.list_all()
        instances.clear()
        assert registry.count == 1  # 注册表不受影响
