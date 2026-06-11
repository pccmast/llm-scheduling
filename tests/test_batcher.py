"""Module 9: Dynamic Batcher 测试 — 规格书 §4 Module 9."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dispatcher.batcher import DynamicBatcher
from src.shared.models import InferenceRequest, InferenceResponse, TokenUsage


@pytest.fixture
def mock_proxy() -> MagicMock:
    p = MagicMock()
    p.forward = AsyncMock()
    return p


@pytest.fixture
def inference_request() -> InferenceRequest:
    return InferenceRequest(request_id="r1", model="test", messages=[])


class TestBatchTrigger:
    """攒批触发测试。"""

    @pytest.mark.asyncio
    async def test_flush_on_max_batch_size(
        self,
        mock_proxy: MagicMock,
        inference_request: InferenceRequest,
    ) -> None:
        """达到 max_batch_size 时立即 flush。"""
        mock_proxy.forward.return_value = InferenceResponse(
            request_id="r1",
            instance_id="i1",
            status="ok",
            usage=TokenUsage(),
        )
        batcher = DynamicBatcher(mock_proxy, max_batch_size=3, max_wait_ms=5000)

        async def submit_n(n: int):
            tasks = []
            for i in range(n):
                req = InferenceRequest(request_id=f"r{i}", model="test", messages=[])
                tasks.append(asyncio.create_task(batcher.submit(req)))
            return await asyncio.gather(*tasks)

        await submit_n(3)
        assert mock_proxy.forward.call_count == 3
        assert batcher.total_batches_sent >= 1

    @pytest.mark.asyncio
    async def test_flush_on_timeout(
        self,
        mock_proxy: MagicMock,
        inference_request: InferenceRequest,
    ) -> None:
        """未达到 max_batch_size 时等待超时 flush。"""
        mock_proxy.forward.return_value = InferenceResponse(
            request_id="r1",
            instance_id="i1",
            status="ok",
            usage=TokenUsage(),
        )
        batcher = DynamicBatcher(mock_proxy, max_batch_size=10, max_wait_ms=50)

        await batcher.submit(inference_request)
        # 等待超时 flush
        await asyncio.sleep(0.1)
        assert mock_proxy.forward.call_count >= 1
        assert batcher.buffer_size == 0


class TestBatchResults:
    """批量结果测试。"""

    @pytest.mark.asyncio
    async def test_all_requests_get_response(
        self,
        mock_proxy: MagicMock,
    ) -> None:
        """所有请求都收到响应。"""
        mock_proxy.forward.side_effect = lambda req: InferenceResponse(
            request_id=req.request_id,
            instance_id="i1",
            status="ok",
            usage=TokenUsage(),
        )
        batcher = DynamicBatcher(mock_proxy, max_batch_size=2, max_wait_ms=5000)

        req1 = InferenceRequest(request_id="r1", model="test", messages=[])
        req2 = InferenceRequest(request_id="r2", model="test", messages=[])

        resp1, resp2 = await asyncio.gather(
            batcher.submit(req1),
            batcher.submit(req2),
        )
        assert resp1.request_id == "r1"
        assert resp2.request_id == "r2"

    @pytest.mark.asyncio
    async def test_forward_error_handled(
        self,
        mock_proxy: MagicMock,
    ) -> None:
        """proxy.forward 抛出异常时，对应请求收到异常。"""
        mock_proxy.forward.side_effect = RuntimeError("test error")
        batcher = DynamicBatcher(mock_proxy, max_batch_size=1, max_wait_ms=5000)

        req = InferenceRequest(request_id="r1", model="test", messages=[])
        with pytest.raises(RuntimeError, match="test error"):
            await batcher.submit(req)


class TestBufferState:
    """buffer 状态测试。"""

    @pytest.mark.asyncio
    async def test_buffer_size_tracking(
        self,
        mock_proxy: MagicMock,
        inference_request: InferenceRequest,
    ) -> None:
        """buffer_size 正确跟踪。"""
        mock_proxy.forward.return_value = InferenceResponse(
            request_id="r1",
            instance_id="i1",
            status="ok",
            usage=TokenUsage(),
        )
        batcher = DynamicBatcher(mock_proxy, max_batch_size=5, max_wait_ms=5000)

        assert batcher.buffer_size == 0
        # Submit without awaiting (to check buffer before flush)
        task = asyncio.create_task(batcher.submit(inference_request))
        await asyncio.sleep(0.01)
        assert batcher.buffer_size == 1
        task.cancel()

    def test_total_batches_sent_initially_zero(
        self,
        mock_proxy: MagicMock,
    ) -> None:
        batcher = DynamicBatcher(mock_proxy)
        assert batcher.total_batches_sent == 0


class TestEmptyFlush:
    """空 buffer flush 测试。"""

    @pytest.mark.asyncio
    async def test_flush_empty_buffer_no_error(
        self,
        mock_proxy: MagicMock,
    ) -> None:
        """空 buffer flush 不报错。"""
        batcher = DynamicBatcher(mock_proxy)
        await batcher.flush()
        assert batcher.buffer_size == 0
        assert batcher.total_batches_sent == 0
