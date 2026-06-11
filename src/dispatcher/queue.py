"""Module 7: Request Queue — 规格书 §4 Module 7.

带优先级和超时的请求等待队列。当所有实例忙时提供缓冲。
使用 asyncio.PriorityQueue 实现，QueueItem.__lt__ 确保优先级排序正确（FIFO 同优先级）。
"""

from __future__ import annotations

import asyncio
import time

from src.shared.models import InferenceRequest, QueueFullError, QueueItem


class RequestQueue:
    """带优先级和超时的请求等待队列。"""

    def __init__(self, max_size: int = 100, max_wait_seconds: float = 30.0) -> None:
        """
        Args:
            max_size: 队列最大容量。满时拒绝新请求。
            max_wait_seconds: 请求在队列中的最大等待时间（秒）。
        """
        self._max_size = max_size
        self._max_wait_seconds = max_wait_seconds
        self._queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue(maxsize=max_size)

    async def enqueue(self, request: InferenceRequest) -> QueueItem:
        """将请求加入队列。

        Args:
            request: 推理请求。使用 request.priority 作为队列优先级。

        Returns:
            创建的 QueueItem。

        Raises:
            QueueFullError: 队列已满（len >= max_size）。
        """
        if self._queue.qsize() >= self._max_size:
            raise QueueFullError()

        item = QueueItem(
            priority=request.priority,
            enqueue_time=time.monotonic(),
            request=request,
        )
        await self._queue.put(item)
        return item

    async def dequeue(self) -> QueueItem | None:
        """从队列中取出最高优先级的请求。

        如果队列为空，等待直到有请求入队或超时。
        如果取出的请求已过期（等待时间 > max_wait_seconds），
        自动丢弃并继续取下一个。

        Returns:
            QueueItem，或超时后返回 None。

        Postcondition:
            返回的 QueueItem 一定未过期。
        """
        while True:
            try:
                # 等待请求，超时返回 None
                item = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0,  # 每秒检查一次，不是最终超时
                )
            except asyncio.TimeoutError:
                # 队列为空超时，返回 None
                return None

            # 检查是否过期
            elapsed = time.monotonic() - item.enqueue_time
            if elapsed > self._max_wait_seconds:
                # 丢弃过期请求，继续取下一个
                continue

            return item

    @property
    def size(self) -> int:
        """当前队列中的请求数量。"""
        return self._queue.qsize()

    @property
    def is_full(self) -> bool:
        """队列是否已满。"""
        return self._queue.qsize() >= self._max_size
