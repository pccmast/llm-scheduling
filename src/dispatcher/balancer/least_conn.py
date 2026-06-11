"""Module 4: LeastConnectionsBalancer — 规格书 §4 Module 4.

最少连接负载均衡策略：选择当前活跃请求数最少的实例。
"""

from __future__ import annotations

from src.shared.models import InferenceRequest, LoadBalancer, ModelInstance


class LeastConnectionsBalancer(LoadBalancer):
    """最少连接负载均衡。选择当前活跃请求数最少的实例。"""

    def __init__(self) -> None:
        """内部维护 dict[str, int] 记录每个实例的活跃请求数。"""
        self._active: dict[str, int] = {}

    def select(self, candidates: list[ModelInstance], request: InferenceRequest) -> ModelInstance:
        """选择 active_count 最小的候选实例。
        活跃数相同时选择 candidates 中第一个（即与已有活跃计数无关的实例中第一个）。

        Raises:
            ValueError: candidates 为空。
        """
        if not candidates:
            raise ValueError("candidates must not be empty")

        # 选择活跃数最小的实例，相同时保持 candidates 顺序
        best = min(candidates, key=lambda inst: self._active.get(inst.instance_id, 0))
        return best

    def on_request_start(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        """活跃请求数 +1。"""
        self._active[instance_id] = self._active.get(instance_id, 0) + 1

    def on_request_end(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        """活跃请求数 -1（最小为 0）。"""
        if instance_id in self._active:
            self._active[instance_id] = max(0, self._active[instance_id] - 1)

    def get_active_count(self, instance_id: str) -> int:
        """获取指定实例的活跃请求数。"""
        return self._active.get(instance_id, 0)
