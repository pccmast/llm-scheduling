"""Module 4: WeightedBalancer — 规格书 §4 Module 4 扩展.

v3: score = (current_load + estimated_weight) / capacity_factor
v4: score = (current_load + estimated_weight) / (capacity_factor * max(speed_ewma, 1.0))
     加入 EWMA 性能反馈，快实例自动获得更多流量。
"""

from __future__ import annotations

from src.shared.models import InferenceRequest, LoadBalancer, ModelInstance


class WeightedBalancer(LoadBalancer):
    """加权负载均衡 v4 — 感知性能差异。"""

    def __init__(self, ewma_alpha: float = 0.3) -> None:
        """
        Args:
            ewma_alpha: EWMA 平滑系数。越大越敏感 (0 完全平滑, 1 只用最新值).
        """
        self._loads: dict[str, float] = {}
        self._speed_ewma: dict[str, float] = {}  # v4: 每个实例的 EWMA tok/s
        self._alpha: float = ewma_alpha

    def select(self, candidates: list[ModelInstance], request: InferenceRequest) -> ModelInstance:
        """选择 score 最小的实例。

        v4 公式:
          score = (current_load + estimated_weight)
                  / (capacity_factor * max(speed_ewma, 1.0))

        无历史数据时 speed_ewma = 1.0 → 退化为 v3 行为.

        Raises:
            ValueError: candidates 为空。
        """
        if not candidates:
            raise ValueError("candidates must not be empty")

        weight = request.estimated_weight

        def score(inst: ModelInstance) -> float:
            current_load = self._loads.get(inst.instance_id, 0.0)
            speed = max(self._speed_ewma.get(inst.instance_id, 1.0), 1.0)
            return (current_load + weight) / (inst.capacity_factor * speed)

        return min(candidates, key=score)

    def record_performance(self, instance_id: str, completion_tokens: int, duration_ms: float) -> None:
        """proxy 完成请求后回写实测性能 (v4 新增).

        EWMA 公式: speed = alpha * new + (1 - alpha) * old

        Args:
            instance_id: 实例 ID.
            completion_tokens: 实际生成的 token 数.
            duration_ms: 请求耗时 (毫秒).
        """
        if duration_ms <= 0:
            return
        new_speed = completion_tokens / duration_ms * 1000  # tokens/s
        old = self._speed_ewma.get(instance_id, new_speed)
        self._speed_ewma[instance_id] = self._alpha * new_speed + (1 - self._alpha) * old

    def on_request_start(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        delta = request.estimated_weight if request else 0.0
        self._loads[instance_id] = self._loads.get(instance_id, 0.0) + delta

    def on_request_end(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        delta = request.estimated_weight if request else 0.0
        current = self._loads.get(instance_id, 0.0)
        self._loads[instance_id] = max(0.0, current - delta)

    def get_load(self, instance_id: str) -> float:
        return self._loads.get(instance_id, 0.0)

    def get_speed(self, instance_id: str) -> float:
        """获取实例的 EWMA 速度 (v4 新增)."""
        return self._speed_ewma.get(instance_id, 1.0)
