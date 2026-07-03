"""Module 4: WeightedBalancer v4.3 — cooldown + 线性权重 + priority.

v3: score = (load + weight) / cap
v4.3: cooldown 冷却期 + 线性权重 + priority 因子
"""

from __future__ import annotations

import time

from src.shared.models import BalancerWeights, InferenceRequest, LoadBalancer, ModelInstance


class WeightedBalancer(LoadBalancer):
    """加权负载均衡 v4.3 — cooldown 冷却期 + 线性权重 + priority。"""

    def __init__(self, weights: BalancerWeights | None = None, ewma_alpha: float = 0.3) -> None:
        self._loads: dict[str, float] = {}
        self._speed_ewma: dict[str, float] = {}
        self._reliability_ewma: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}  # v4.3: instance_id → timestamp
        self._alpha: float = ewma_alpha
        self._weights: BalancerWeights = weights or BalancerWeights()

    def select(self, candidates: list[ModelInstance], request: InferenceRequest) -> ModelInstance:
        """选择 score 最小的实例。

        v4.3: cooldown 硬过滤 — 冷却中的实例不参与评分,
        除非全部在冷却中才放宽限制.

        公式:
          speed_bonus = 1 + w_speed * (speed - 1)
          rel_penalty  = 1 + w_reliability * (1 - reliability)
          pri_bonus   = 1 + 0.3 * (10 - priority) / 10
          score = (load * w_load + weight) / (cap * speed_bonus * pri_bonus / rel_penalty)
        """
        if not candidates:
            raise ValueError("candidates must not be empty")

        now = time.monotonic()
        cooldown_s = self._weights.cooldown_seconds

        # v4.3: 冷却过滤 — 筛掉刚失败的实例
        if cooldown_s > 0:
            active = [i for i in candidates
                      if self._cooldown_until.get(i.instance_id, 0) <= now]
            if active:
                # v4.4: 出冷的实例 → 速度重置为平均值, 避免"富者愈富"
                for inst in active:
                    iid = inst.instance_id
                    if self._speed_ewma.get(iid, 0.3) <= 0.5 and len(candidates) > 1:
                        other_speeds = [self._speed_ewma.get(i.instance_id, 0.3)
                                        for i in candidates if i.instance_id != iid]
                        avg_other = sum(other_speeds) / len(other_speeds) if other_speeds else 0.3
                        # 重置为比较保守的估计
                        if avg_other > 0.5:
                            self._speed_ewma[iid] = avg_other * 0.7
                candidates = active
        # 全部冷却 → 不退避, 全部候选

        weight = request.estimated_weight
        w_load = self._weights.load
        w_speed = self._weights.speed
        w_reliability = self._weights.reliability

        def score(inst: ModelInstance) -> float:
            iid = inst.instance_id
            current_load = self._loads.get(iid, 0.0)
            speed = max(self._speed_ewma.get(iid, 0.3), 0.1)
            reliability = self._reliability_ewma.get(iid, 1.0)

            speed_bonus = 1 + w_speed * max(speed - 1.0, 0)
            rel_penalty = 1 + w_reliability * (1 - reliability)
            pri_bonus = 1 + 0.3 * (10 - request.priority) / 10

            numerator = (current_load * w_load) + weight
            denominator = inst.capacity_factor * speed_bonus * pri_bonus / max(rel_penalty, 0.01)
            return numerator / max(denominator, 0.001)

        return min(candidates, key=score)

    def _set_cooldown(self, instance_id: str) -> None:
        """启动冷却期。"""
        if self._weights.cooldown_seconds > 0:
            self._cooldown_until[instance_id] = time.monotonic() + self._weights.cooldown_seconds

    def _clear_cooldown(self, instance_id: str) -> None:
        """成功 → 清除冷却。"""
        self._cooldown_until.pop(instance_id, None)

    def record_performance(self, instance_id: str, completion_tokens: int, duration_ms: float) -> None:
        if duration_ms <= 0 or completion_tokens <= 0:
            return
        new_speed = completion_tokens / duration_ms * 1000
        old = self._speed_ewma.get(instance_id, new_speed)
        self._speed_ewma[instance_id] = self._alpha * new_speed + (1 - self._alpha) * old
        # 成功 → 取消冷却
        self._clear_cooldown(instance_id)

    def record_failure(self, instance_id: str, penalty: float = 1.0) -> None:
        old = self._reliability_ewma.get(instance_id, 1.0)
        self._reliability_ewma[instance_id] = self._alpha * (1 - penalty) + (1 - self._alpha) * old
        # 失败 → 进入冷却, 避免短时间内反复重试同一实例
        self._set_cooldown(instance_id)

    def record_success(self, instance_id: str) -> None:
        old = self._reliability_ewma.get(instance_id, 1.0)
        self._reliability_ewma[instance_id] = self._alpha * 1.0 + (1 - self._alpha) * old
        self._clear_cooldown(instance_id)

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

    def is_in_cooldown(self, instance_id: str) -> bool:
        """检查实例是否在冷却期 (v4.3 新增)."""
        return self._cooldown_until.get(instance_id, 0) > time.monotonic()
