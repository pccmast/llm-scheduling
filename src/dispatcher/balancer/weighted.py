"""Module 4: WeightedBalancer — v4.3 稳定评分公式.

v3: score = (load + weight) / cap
v4.3: score = (load*w_load + weight) / (cap * speed_bonus * rel_bonus * priority_bonus)
      线性权重, 避免指数放大. 冷启动默认 0.3. 支持 priority.
"""

from __future__ import annotations

from src.shared.models import BalancerWeights, InferenceRequest, LoadBalancer, ModelInstance


class WeightedBalancer(LoadBalancer):
    """加权负载均衡 v4.3 — 线性权重 + priority。"""

    def __init__(self, weights: BalancerWeights | None = None, ewma_alpha: float = 0.3) -> None:
        self._loads: dict[str, float] = {}
        self._speed_ewma: dict[str, float] = {}
        self._reliability_ewma: dict[str, float] = {}
        self._alpha: float = ewma_alpha
        self._weights: BalancerWeights = weights or BalancerWeights()

    def select(self, candidates: list[ModelInstance], request: InferenceRequest) -> ModelInstance:
        """选择 score 最小的实例。

        v4.3 公式 (线性权重, 避免指数放大):
          speed_bonus = 1 + w_speed * (speed - 1)
          rel_bonus   = 1 + w_reliability * (1 - reliability)
          pri_bonus   = 1 + 0.3 * (10 - priority) / 10

          score = (load * w_load + weight) / (cap * speed_bonus * pri_bonus / rel_bonus)
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
            # 冷启动默认 0.3 (介于 0.1过于保守和 1.0过于乐观之间)
            speed = max(self._speed_ewma.get(iid, 0.3), 0.1)
            reliability = self._reliability_ewma.get(iid, 1.0)

            # 线性 bonus: w=2 时 2x 速度 → 3x bonus (而非 4x)
            speed_bonus = 1 + w_speed * max(speed - 1.0, 0)
            # reliability 越低 → penalty 越大
            rel_penalty = 1 + w_reliability * (1 - reliability)

            # priority: 1(高优)→bonus=1.27, 10(低优)→bonus=1.0
            pri_bonus = 1 + 0.3 * (10 - request.priority) / 10

            numerator = (current_load * w_load) + weight
            denominator = inst.capacity_factor * speed_bonus * pri_bonus / max(rel_penalty, 0.01)
            return numerator / max(denominator, 0.001)

        return min(candidates, key=score)

    def record_performance(self, instance_id: str, completion_tokens: int, duration_ms: float) -> None:
        if duration_ms <= 0 or completion_tokens <= 0:
            return
        new_speed = completion_tokens / duration_ms * 1000
        old = self._speed_ewma.get(instance_id, new_speed)
        self._speed_ewma[instance_id] = self._alpha * new_speed + (1 - self._alpha) * old

    def record_failure(self, instance_id: str, penalty: float = 1.0) -> None:
        """记录失败。penalty=1.0 全惩罚, 0.5 半惩罚 (区分错误类型)。"""
        old = self._reliability_ewma.get(instance_id, 1.0)
        self._reliability_ewma[instance_id] = self._alpha * (1 - penalty) + (1 - self._alpha) * old

    def record_success(self, instance_id: str) -> None:
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
        return self._speed_ewma.get(instance_id, 0.3)

    def get_reliability(self, instance_id: str) -> float:
        return self._reliability_ewma.get(instance_id, 1.0)
