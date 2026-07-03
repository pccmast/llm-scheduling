"""Module 4: WeightedBalancer — 规格书 §4 Module 4 扩展.

v3: score = (current_load + estimated_weight) / capacity_factor
v4: score = (load*w_load + weight) / (cap * speed^w_speed * reliability^w_reliability)
     性能感知 + 可靠性感知 + 可配置权重
"""

from __future__ import annotations

from src.shared.models import BalancerWeights, InferenceRequest, LoadBalancer, ModelInstance


class WeightedBalancer(LoadBalancer):
    """加权负载均衡 v4 — 感知性能 + 可靠性差异。"""

    def __init__(self, weights: BalancerWeights | None = None, ewma_alpha: float = 0.3) -> None:
        """
        Args:
            weights: 评分权重配置。None 使用默认 (1:1:1).
            ewma_alpha: EWMA 平滑系数 (0=极平滑, 1=只用最新值).
        """
        self._loads: dict[str, float] = {}
        self._speed_ewma: dict[str, float] = {}
        self._reliability_ewma: dict[str, float] = {}  # v4: 1.0=全成功, 0=全失败
        self._alpha: float = ewma_alpha
        self._weights: BalancerWeights = weights or BalancerWeights()

    def select(self, candidates: list[ModelInstance], request: InferenceRequest) -> ModelInstance:
        """选择 score 最小的实例。

        v4 公式:
          score = (load * w_load + weight)
                  / (cap * speed^w_speed * reliability^w_reliability)

        无历史数据时 speed=1.0, reliability=1.0 → 退化为 v3 行为.

        Raises:
            ValueError: candidates 为空。
        """
        if not candidates:
            raise ValueError("candidates must not be empty")

        weight = request.estimated_weight
        w_load = self._weights.load
        w_speed = self._weights.speed
        w_reliability = self._weights.reliability

        def score(inst: ModelInstance) -> float:
            iid = inst.instance_id
            current_load = self._loads.get(iid, 0.0)
            # v4.3: 冷启动默认 0.1 → 新实例前几个请求只收到很少流量
            speed = max(self._speed_ewma.get(iid, 0.1), 0.1)
            reliability = max(self._reliability_ewma.get(iid, 1.0), 0.01)

            numerator = (current_load * w_load) + weight
            denominator = inst.capacity_factor * (speed ** w_speed) * (reliability ** w_reliability)
            return numerator / max(denominator, 0.001)

        return min(candidates, key=score)

    def record_performance(self, instance_id: str, completion_tokens: int, duration_ms: float) -> None:
        """proxy 成功完成请求 → 回写速度 (v4)."""
        if duration_ms <= 0 or completion_tokens <= 0:
            return
        new_speed = completion_tokens / duration_ms * 1000
        old = self._speed_ewma.get(instance_id, new_speed)
        self._speed_ewma[instance_id] = self._alpha * new_speed + (1 - self._alpha) * old

    def record_failure(self, instance_id: str) -> None:
        """proxy 请求失败 → 降低可靠性 (v4 新增)."""
        old = self._reliability_ewma.get(instance_id, 1.0)
        self._reliability_ewma[instance_id] = self._alpha * 0.0 + (1 - self._alpha) * old

    def record_success(self, instance_id: str) -> None:
        """proxy 请求成功 → 提升可靠性 (v4 新增)."""
        old = self._reliability_ewma.get(instance_id, 1.0)
        self._reliability_ewma[instance_id] = self._alpha * 1.0 + (1 - self._alpha) * old

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
        return self._speed_ewma.get(instance_id, 1.0)

    def get_reliability(self, instance_id: str) -> float:
        return self._reliability_ewma.get(instance_id, 1.0)
