"""Module 11: Auto Scaler — 规格书 §4 Module 11.

基于队列深度和实例负载率，输出扩缩容决策信号（ScaleDecision）。
只做决策不做执行，解耦执行层（K8s HPA、云 API 等）。
"""

from __future__ import annotations

import time

from src.dispatcher.registry import InstanceRegistry
from src.shared.models import LoadBalancer, MetricsRecorder, ScaleConfig, ScaleDecision


class AutoScaler:
    """自动扩缩容决策引擎。"""

    def __init__(
        self,
        registry: InstanceRegistry,
        metrics: MetricsRecorder,
        config: ScaleConfig,
        balancer: LoadBalancer | None = None,
    ) -> None:
        """
        Args:
            registry: 实例注册表（用于获取当前实例数）。
            metrics: 指标记录器（用于获取队列数据）。
            config: 扩缩容配置。
            balancer: 负载均衡器（用于读取各实例的实际负载分数）。
                      为 None 时负载检查退化为仅依赖队列深度。
        """
        self._registry = registry
        self._metrics = metrics
        self._config = config
        self._balancer = balancer
        self._last_scale_time: float = 0.0
        self._idle_since: float | None = None

    def evaluate(self) -> ScaleDecision:
        """评估当前状态，输出扩缩容决策。

        扩容条件（同时满足）：
        - 队列深度 > scale_up_queue_threshold
        - 平均实例负载 > scale_up_load_threshold
        - 当前实例数 < max_instances
        - 距离上次扩缩容 > cooldown_seconds

        缩容条件（同时满足）：
        - 队列深度 == 0
        - 平均实例负载 < scale_down_load_threshold
        - 当前实例数 > min_instances
        - 空闲持续时间 > scale_down_idle_seconds
        - 距离上次扩缩容 > cooldown_seconds

        Returns:
            ScaleDecision。action 为 "scale_up"、"scale_down" 或 "none"。

        Postcondition:
            不修改任何内部状态（纯决策函数，但记录 idle 时间）。
        """
        now = time.monotonic()
        summary = self._metrics.get_summary()
        current_count = self._registry.count
        queue_depth = summary.get("queue_depth", 0)

        if current_count == 0:
            return ScaleDecision(action="none", reason="No instances registered")

        # 计算平均负载：从 balancer 读取各实例的实际负载分数
        if self._balancer:
            all_instances = self._registry.list_all()
            loads = [self._balancer.get_load(inst.instance_id) for inst in all_instances]
            total_load = sum(loads)
            # 归一化为平均每实例负载
            avg_load = total_load / current_count if current_count > 0 else 0.0
        else:
            # 无 balancer 引用时退化为队列深度代理
            avg_load = float(queue_depth) / max(current_count, 1)

        # Cooldown 检查
        in_cooldown = (now - self._last_scale_time) < self._config.cooldown_seconds

        # ── 扩容判断 ──
        if (
            queue_depth > self._config.scale_up_queue_threshold
            and avg_load > self._config.scale_up_load_threshold
            and current_count < self._config.max_instances
            and not in_cooldown
        ):
            # 重置空闲计时
            self._idle_since = None
            return ScaleDecision(
                action="scale_up",
                count=1,
                reason=f"Queue depth {queue_depth} > threshold {self._config.scale_up_queue_threshold}, "
                f"avg load {avg_load:.2f} > {self._config.scale_up_load_threshold}",
            )

        # ── 缩容判断 ──
        if queue_depth == 0 and avg_load < self._config.scale_down_load_threshold:
            if self._idle_since is None:
                self._idle_since = now

            idle_seconds = now - self._idle_since
            if (
                idle_seconds > self._config.scale_down_idle_seconds
                and current_count > self._config.min_instances
                and not in_cooldown
            ):
                return ScaleDecision(
                    action="scale_down",
                    count=1,
                    reason=f"Idle for {idle_seconds:.0f}s > {self._config.scale_down_idle_seconds}s, "
                    f"avg load {avg_load:.2f} < {self._config.scale_down_load_threshold}",
                )
        else:
            # 有负载，重置空闲计时
            self._idle_since = None

        return ScaleDecision(action="none", reason="Conditions not met")

    def record_scale_event(self, decision: ScaleDecision) -> None:
        """记录一次扩缩容事件（更新 cooldown 计时器）。

        Args:
            decision: 已执行的扩缩容决策。

        Postcondition:
            _last_scale_time 被更新为当前时间。
        """
        self._last_scale_time = time.monotonic()
