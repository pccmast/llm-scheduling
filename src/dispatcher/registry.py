"""Module 2: Instance Registry — 规格书 §4 Module 2.

管理所有推理引擎实例的注册表。提供注册、注销、按模型查询、状态更新等操作。
这是调度系统的"服务发现"层。
"""

from __future__ import annotations

import asyncio

from src.shared.models import InstanceStatus, ModelInstance


class InstanceRegistry:
    """推理引擎实例的注册表。"""

    def __init__(self) -> None:
        """初始化空的注册表。"""
        self._instances: dict[str, ModelInstance] = {}
        self._lock = asyncio.Lock()

    def register(self, instance: ModelInstance) -> None:
        """注册一个新实例。

        Args:
            instance: 要注册的实例。instance_id 必须唯一。

        Raises:
            ValueError: instance_id 已存在时抛出。
        """
        if instance.instance_id in self._instances:
            raise ValueError(f"Instance '{instance.instance_id}' is already registered")
        self._instances[instance.instance_id] = instance

    def deregister(self, instance_id: str) -> None:
        """注销一个实例。

        Args:
            instance_id: 要注销的实例 ID。

        Raises:
            KeyError: instance_id 不存在时抛出。
        """
        if instance_id not in self._instances:
            raise KeyError(f"Instance '{instance_id}' not found")
        del self._instances[instance_id]

    def get(self, instance_id: str) -> ModelInstance:
        """获取指定实例。

        Raises:
            KeyError: instance_id 不存在时抛出。
        """
        if instance_id not in self._instances:
            raise KeyError(f"Instance '{instance_id}' not found")
        return self._instances[instance_id]

    def list_all(self) -> list[ModelInstance]:
        """返回所有已注册实例的列表（快照副本）。"""
        return list(self._instances.values())

    def list_by_model(self, model: str) -> list[ModelInstance]:
        """返回支持指定模型的所有健康实例。

        Args:
            model: 模型名称。

        Returns:
            状态为 HEALTHY 且 model 匹配的实例列表。
            如果没有匹配实例，返回空列表。
        """
        return [
            inst
            for inst in self._instances.values()
            if inst.model == model and inst.status == InstanceStatus.HEALTHY
        ]

    def update_status(self, instance_id: str, status: InstanceStatus) -> None:
        """更新实例状态。

        Raises:
            KeyError: instance_id 不存在时抛出。
        """
        if instance_id not in self._instances:
            raise KeyError(f"Instance '{instance_id}' not found")
        self._instances[instance_id].status = status

    def mark_unhealthy(self, instance_id: str) -> None:
        """将实例标记为 UNHEALTHY。等同于 update_status(id, UNHEALTHY)。

        Raises:
            KeyError: instance_id 不存在时抛出。
        """
        self.update_status(instance_id, InstanceStatus.UNHEALTHY)

    def mark_healthy(self, instance_id: str) -> None:
        """将实例恢复为 HEALTHY（仅当当前状态为 UNHEALTHY 时生效）。

        Postcondition:
            如果当前状态为 DRAINING，状态不变。

        Raises:
            KeyError: instance_id 不存在时抛出。
        """
        instance = self.get(instance_id)
        if instance.status == InstanceStatus.UNHEALTHY:
            instance.status = InstanceStatus.HEALTHY

    @property
    def count(self) -> int:
        """返回已注册实例总数。"""
        return len(self._instances)

    def load_from_config(self, instances: list[dict]) -> None:
        """从配置字典列表批量加载实例。

        Args:
            instances: YAML 配置中的 instances 列表，每项包含
                       instance_id, address, model, engine_type 等字段。

        Raises:
            pydantic.ValidationError: 配置格式不正确时抛出。
        """
        for cfg in instances:
            instance = ModelInstance(**cfg)
            self.register(instance)
