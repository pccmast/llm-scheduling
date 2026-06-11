"""Module 8: Circuit Breaker — 规格书 §4 Module 8.

为每个推理引擎实例维护独立的熔断器，实现三态模型（CLOSED → OPEN → HALF_OPEN → CLOSED）。
使用惰性状态转换（规格书 §6 D8）：在 allow_request() 中检查时间触发转换。
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from src.shared.models import CircuitBreaker


class CircuitBreakerConfig(BaseModel):
    """单个熔断器的配置。"""

    failure_threshold: int = 5  # 连续失败 N 次后打开熔断器
    recovery_timeout: float = 30.0  # OPEN 状态持续多久后进入 HALF_OPEN（秒）
    half_open_max_calls: int = 1  # HALF_OPEN 状态允许的最大试探请求数


class InstanceCircuitBreaker(CircuitBreaker):
    """实例级熔断器。实现 CLOSED → OPEN → HALF_OPEN → CLOSED 状态机。

    惰性转换：OPEN → HALF_OPEN 在 allow_request() 中触发，不使用后台定时器。
    """

    _CLOSED = "closed"
    _OPEN = "open"
    _HALF_OPEN = "half_open"

    def __init__(self, instance_id: str, config: CircuitBreakerConfig | None = None) -> None:
        """
        Args:
            instance_id: 关联的实例 ID。
            config: 熔断器配置。None 时使用默认值。

        Precondition:
            instance_id 非空。
        """
        self._instance_id = instance_id
        self._config = config or CircuitBreakerConfig()
        self._state: str = self._CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0  # HALF_OPEN 状态下的成功计数
        self._opened_at: float = 0.0  # 进入 OPEN 状态的时间
        self._half_open_requests: int = 0  # HALF_OPEN 状态下已发送的试探请求数

    def allow_request(self) -> bool:
        """判断当前是否允许发送请求。

        状态逻辑：
        - CLOSED: 始终返回 True。
        - OPEN: 如果当前时间 - 上次打开时间 > recovery_timeout，
                自动转为 HALF_OPEN 并返回 True；否则返回 False。
        - HALF_OPEN: 如果已发送的试探请求数 < half_open_max_calls，
                     返回 True；否则返回 False。

        Returns:
            True 表示允许发送请求，False 表示拒绝。

        Postcondition:
            OPEN 状态可能自动转为 HALF_OPEN（时间驱动）。
        """
        # CLOSED: always allow
        if self._state == self._CLOSED:
            return True

        # OPEN: check if recovery timeout elapsed
        if self._state == self._OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._config.recovery_timeout:
                # Transition to HALF_OPEN (lazy)
                self._state = self._HALF_OPEN
                self._half_open_requests = 0
                self._success_count = 0
                return True
            return False

        # HALF_OPEN: allow limited probe requests
        if self._state == self._HALF_OPEN:
            if self._half_open_requests < self._config.half_open_max_calls:
                self._half_open_requests += 1
                return True
            return False

        return False

    def record_success(self) -> None:
        """记录一次成功的请求。

        状态转换：
        - CLOSED: 重置连续失败计数为 0。
        - HALF_OPEN: 成功计数 +1。如果成功次数达到 half_open_max_calls，
                     转为 CLOSED 并重置所有计数器。
        - OPEN: 无操作。
        """
        if self._state == self._CLOSED:
            self._failure_count = 0

        elif self._state == self._HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._config.half_open_max_calls:
                self._state = self._CLOSED
                self._failure_count = 0
                self._success_count = 0
                self._half_open_requests = 0

        # OPEN: no-op

    def record_failure(self) -> None:
        """记录一次失败的请求。

        状态转换：
        - CLOSED: 连续失败计数 +1。如果达到 failure_threshold，转为 OPEN。
        - HALF_OPEN: 立即转为 OPEN（试探失败，不再等待）。
        - OPEN: 无操作。
        """
        if self._state == self._CLOSED:
            self._failure_count += 1
            if self._failure_count >= self._config.failure_threshold:
                self._state = self._OPEN
                self._opened_at = time.monotonic()

        elif self._state == self._HALF_OPEN:
            # Immediate transition back to OPEN on failure
            self._state = self._OPEN
            self._opened_at = time.monotonic()
            self._half_open_requests = 0

        # OPEN: no-op

    @property
    def state(self) -> str:
        """返回当前状态："closed" | "open" | "half_open"。

        Postcondition:
            如果内部 OPEN 状态已超时，allow_request() 调用时才会实际转换。
            此属性只做读取，不会触发状态转换。
        """
        return self._state

    @property
    def failure_count(self) -> int:
        """返回当前连续失败次数。"""
        return self._failure_count

    def reset(self) -> None:
        """手动重置熔断器为 CLOSED 状态。用于管理 API 或测试。"""
        self._state = self._CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_requests = 0


class CircuitBreakerRegistry:
    """管理所有实例的熔断器。"""

    def __init__(self, default_config: CircuitBreakerConfig | None = None) -> None:
        """
        Args:
            default_config: 新建熔断器时使用的默认配置。
        """
        self._breakers: dict[str, InstanceCircuitBreaker] = {}
        self._default_config = default_config or CircuitBreakerConfig()

    def get_or_create(self, instance_id: str) -> InstanceCircuitBreaker:
        """获取指定实例的熔断器。如果不存在则创建一个新的。

        Args:
            instance_id: 实例 ID。

        Returns:
            对应的 InstanceCircuitBreaker 实例。
        """
        if instance_id not in self._breakers:
            self._breakers[instance_id] = InstanceCircuitBreaker(
                instance_id=instance_id,
                config=self._default_config,
            )
        return self._breakers[instance_id]

    def remove(self, instance_id: str) -> None:
        """移除指定实例的熔断器（在实例注销时调用）。

        Args:
            instance_id: 实例 ID。
        """
        self._breakers.pop(instance_id, None)

    def get_all_states(self) -> dict[str, str]:
        """返回所有熔断器的状态快照。

        Returns:
            {instance_id: state} 字典。
        """
        return {inst_id: cb.state for inst_id, cb in self._breakers.items()}
