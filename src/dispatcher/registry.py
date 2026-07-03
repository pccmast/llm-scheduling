"""Module 2: Instance Registry — 规格书 §4 Module 2.

管理所有推理引擎实例的注册表。提供注册、注销、按模型查询、状态更新等操作。
这是调度系统的"服务发现"层。
"""

from __future__ import annotations

from src.shared.models import InstanceStatus, InstanceTier, ModelInstance


class InstanceRegistry:
    """推理引擎实例的注册表。

    所有操作在 asyncio 单线程模型下不需要显式加锁；
    实例状态变更（如 HealthChecker 的 mark_unhealthy）与读取操作（如 Proxy 的 list_by_model）
    在协程协作调度下是安全的。
    """

    def __init__(self) -> None:
        """初始化空的注册表。"""
        self._instances: dict[str, ModelInstance] = {}

    def register(self, instance: ModelInstance) -> list[str]:
        """注册一个新实例，返回校验警告列表。

        校验规则:
          1. 同 address + 同 model 的重复注册 → 错误
          2. model="*" 与同一 address 的精确 model 共存 → 警告
          3. 多个 "*" 实例指向同一 address → 警告 (冗余)

        Args:
            instance: 要注册的实例。instance_id 必须唯一。

        Returns:
            校验警告消息列表 (空列表 = 无警告)。

        Raises:
            ValueError: instance_id 已存在 / 重复注册时抛出。
        """
        if instance.instance_id in self._instances:
            raise ValueError(f"Instance '{instance.instance_id}' is already registered")

        warnings = self._validate_registration(instance)
        self._instances[instance.instance_id] = instance
        return warnings

    def _validate_registration(self, instance: ModelInstance) -> list[str]:
        """校验新实例与已有实例是否冲突，返回警告列表。"""
        warnings: list[str] = []

        # 规则 1: 同 address + 同 model = 重复注册
        for existing in self._instances.values():
            if existing.address == instance.address and existing.model == instance.model:
                raise ValueError(
                    f"Instance with address='{instance.address}' "
                    f"and model='{instance.model}' already registered as '{existing.instance_id}'. "
                    f"To add another instance for the same model, use a different instance_id "
                    f"(e.g., 'gpu-2') or different address (e.g., another GPU server)."
                )

        # 规则 2: model="*" 和同一 address 的精确 model 共存 → 造成路由歧义
        if instance.model == "*":
            exact_on_same_addr = [
                e.instance_id for e in self._instances.values()
                if e.address == instance.address and e.model != "*"
            ]
            if exact_on_same_addr:
                warnings.append(
                    f"model='*' on address='{instance.address}' conflicts with existing exact-model "
                    f"instances: {exact_on_same_addr}. "
                    f"Use exact model names for unambiguous routing."
                )
        else:
            # 规则 3: 注册精确 model 但已有 "*" 实例在相同 address 上
            wildcard_on_same_addr = [
                e.instance_id for e in self._instances.values()
                if e.address == instance.address and e.model == "*"
            ]
            if wildcard_on_same_addr:
                warnings.append(
                    f"Existing wildcard instance(s) {wildcard_on_same_addr} on same address "
                    f"'{instance.address}' will also match requests for model='{instance.model}'. "
                    f"Consider using exact model names for all instances."
                )

        # 规则 4: 多个 "*" 实例指向同一 address = 伪冗余
        wildcards_same_addr = [
            e.instance_id for e in self._instances.values()
            if e.address == instance.address and e.model == "*"
        ]
        if instance.model == "*" and len(wildcards_same_addr) >= 1:
            warnings.append(
                f"Multiple wildcard instances ({wildcards_same_addr + [instance.instance_id]}) "
                f"point to the same address '{instance.address}'. "
                f"This creates pseudo-redundancy — one physical backend appears as multiple. "
                f"Use exact model names or different addresses for real redundancy."
            )

        return warnings

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

        路由规则：
        - model 精确匹配的实例 → 优先级路由
        - model="*" 的通配符实例 → 可处理任意模型（如 LM Studio 多模型后端）
        - 两者同时存在时，均返回给 balancer 做负载均衡

        v4: 按 tier 排序 — LOCAL 在前，REMOTE 在后。
            同 tier 内按 capacity_factor 降序。

        Args:
            model: 模型名称。

        Returns:
            HEALTHY 状态的候选实例列表。无匹配时返回空列表。
        """
        candidates = [
            inst for inst in self._instances.values()
            if (inst.model == model or inst.model == "*")
            and inst.status == InstanceStatus.HEALTHY
        ]
        # v4: LOCAL 优先, 同 tier 内容量大的优先
        candidates.sort(key=lambda i: (0 if i.tier == InstanceTier.LOCAL else 1, -i.capacity_factor))
        return candidates

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
