"""Module 4: WeightedBalancer — 规格书 §4 Module 4 扩展.

加权负载均衡策略：根据请求的计算重量（estimated_weight）和实例的容量因子（capacity_factor）
选择最优实例。score = (current_load + estimated_weight) / capacity_factor，最小者胜出。
"""

from __future__ import annotations

from src.shared.models import InferenceRequest, LoadBalancer, ModelInstance


class WeightedBalancer(LoadBalancer):
    """加权负载均衡。根据请求重量和实例负载做最优选择。"""

    def __init__(self) -> None:
        """内部维护每个实例的当前负载分数（float）。"""
        self._loads: dict[str, float] = {}

    def select(self, candidates: list[ModelInstance], request: InferenceRequest) -> ModelInstance:
        """选择 (当前负载 + 请求预估重量) / 实例容量因子 最小的实例。

        计算公式：score = (current_load + request.estimated_weight) / instance.capacity_factor
        选择 score 最小的实例。

        Raises:
            ValueError: candidates 为空。
        """
        if not candidates:
            raise ValueError("candidates must not be empty")

        weight = request.estimated_weight

        def score(inst: ModelInstance) -> float:
            current_load = self._loads.get(inst.instance_id, 0.0)
            return (current_load + weight) / inst.capacity_factor

        return min(candidates, key=score)

    def on_request_start(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        """增加实例的当前负载。

        如果 request 不为 None，使用 request.estimated_weight 作为增量；
        否则增量为 0。
        """
        delta = request.estimated_weight if request else 0.0
        self._loads[instance_id] = self._loads.get(instance_id, 0.0) + delta

    def on_request_end(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        """减少实例的当前负载。

        如果 request 不为 None，使用 request.estimated_weight 作为减量；
        否则减量为 0。负载最小为 0。
        """
        delta = request.estimated_weight if request else 0.0
        current = self._loads.get(instance_id, 0.0)
        self._loads[instance_id] = max(0.0, current - delta)

    def get_load(self, instance_id: str) -> float:
        """获取指定实例的当前负载分数。"""
        return self._loads.get(instance_id, 0.0)
