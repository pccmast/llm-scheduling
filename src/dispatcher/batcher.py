"""Module 9: Dynamic Batcher — 规格书 §4 Module 9.

将多个并发的推理请求攒成小批量（batch），一次性发送给推理引擎。
通过 max_batch_size 和 max_wait_ms 两个参数控制攒批策略。
"""

from __future__ import annotations

import asyncio
import time

from src.shared.models import InferenceRequest, InferenceResponse


class _PendingRequest:
    """缓冲中的一个待处理请求。"""

    def __init__(self, request: InferenceRequest) -> None:
        self.request = request
        self.future: asyncio.Future[InferenceResponse] = asyncio.Future()
        self.enqueue_time = time.monotonic()


class DynamicBatcher:
    """动态批处理器。将并发请求攒成批量发送。"""

    def __init__(self, proxy,
                 max_batch_size: int = 8,
                 max_wait_ms: float = 50.0) -> None:
        """
        Args:
            proxy: 路由代理（用于实际转发）。
            max_batch_size: 最大批量大小。达到后立即 flush。
            max_wait_ms: 最大等待时间（毫秒）。超时后 flush（即使未达到 max_batch_size）。

        Precondition:
            max_batch_size >= 1。
        """
        self._proxy = proxy
        self._max_batch_size = max_batch_size
        self._max_wait_ms = max_wait_ms
        self._buffer: list[_PendingRequest] = []
        self._total_batches_sent: int = 0
        self._flush_lock = asyncio.Lock()

    async def submit(self, request: InferenceRequest) -> InferenceResponse:
        """提交一个请求到批处理器。

        请求会被放入内部 buffer。当 buffer 满或等待超时时，
        buffer 中的所有请求会被打包发送。

        Args:
            request: 推理请求。

        Returns:
            InferenceResponse（从 batch 结果中提取的对应响应）。

        Postcondition:
            请求一定已被处理（成功或失败）。
        """
        pending = _PendingRequest(request)
        self._buffer.append(pending)

        # 达到最大批量，立即 flush
        if len(self._buffer) >= self._max_batch_size:
            await self.flush()
        else:
            # 否则等待超时后 flush
            # 启动一个延迟 flush 任务
            asyncio.create_task(self._delayed_flush())

        return await pending.future

    async def _delayed_flush(self) -> None:
        """延迟 flush：等待 max_wait_ms 后触发。"""
        await asyncio.sleep(self._max_wait_ms / 1000.0)
        await self.flush()

    async def flush(self) -> None:
        """立即 flush 当前 buffer 中的所有请求。

        Postcondition:
            buffer 被清空。如果 buffer 为空，无操作。
        """
        async with self._flush_lock:
            if not self._buffer:
                return

            # 取出当前 buffer 中的所有请求
            batch = self._buffer[:]
            self._buffer.clear()

        self._total_batches_sent += 1

        # 使用 asyncio.gather 并发发送所有请求
        tasks = [_send_with_result(p, self._proxy) for p in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for pending, result in zip(batch, results):
            if isinstance(result, Exception):
                if not pending.future.done():
                    pending.future.set_exception(result)
            else:
                if not pending.future.done():
                    pending.future.set_result(result)

    @property
    def buffer_size(self) -> int:
        """当前 buffer 中的请求数量。"""
        return len(self._buffer)

    @property
    def total_batches_sent(self) -> int:
        """已发送的 batch 总数。"""
        return self._total_batches_sent


async def _send_with_result(pending: _PendingRequest, proxy) -> InferenceResponse:
    """发送单个请求并返回结果。"""
    return await proxy.forward(pending.request)
