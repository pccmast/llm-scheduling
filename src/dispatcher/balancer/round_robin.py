"""Module 4: RoundRobinBalancer — 规格书 §4 Module 4.

轮询负载均衡策略：按顺序依次将请求分配给候选实例。
"""

from __future__ import annotations

from src.shared.models import InferenceRequest, LoadBalancer, ModelInstance


class RoundRobinBalancer(LoadBalancer):
    """轮询负载均衡。按顺序依次分配请求。"""

    def __init__(self) -> None:
        """内部维护一个计数器 int。"""
        self._counter: int = 0

    def select(self, candidates: list[ModelInstance],
               request: InferenceRequest) -> ModelInstance:
        """按计数器取模选择候选实例。

        Precondition:
            candidates 非空。

        Returns:
            candidates[counter % len(candidates)]。

        Raises:
            ValueError: candidates 为空。
        """
        if not candidates:
            raise ValueError("candidates must not be empty")
        index = self._counter % len(candidates)
        self._counter += 1
        return candidates[index]
