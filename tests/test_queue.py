"""Module 7: Request Queue 测试 — 规格书 §4 Module 7."""

from __future__ import annotations

import pytest

from src.dispatcher.queue import RequestQueue
from src.shared.models import InferenceRequest, QueueFullError


@pytest.fixture
def inference_request() -> InferenceRequest:
    return InferenceRequest(request_id="r1", model="test", messages=[])


class TestEnqueueDequeue:
    """入队出队基本测试。"""

    @pytest.mark.asyncio
    async def test_enqueue_and_dequeue(self, inference_request: InferenceRequest) -> None:
        q = RequestQueue(max_size=10)
        await q.enqueue(inference_request)
        item = await q.dequeue()
        assert item is not None
        assert item.request.request_id == "r1"

    @pytest.mark.asyncio
    async def test_enqueue_full_raises_error(self, inference_request: InferenceRequest) -> None:
        q = RequestQueue(max_size=2)
        await q.enqueue(inference_request)
        req2 = InferenceRequest(request_id="r2", model="test", messages=[])
        await q.enqueue(req2)
        req3 = InferenceRequest(request_id="r3", model="test", messages=[])
        with pytest.raises(QueueFullError):
            await q.enqueue(req3)

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_none(self) -> None:
        q = RequestQueue(max_size=10)
        # dequeue on empty queue should timeout and return None
        item = await q.dequeue()
        assert item is None

    @pytest.mark.asyncio
    async def test_size_property(self, inference_request: InferenceRequest) -> None:
        q = RequestQueue(max_size=10)
        assert q.size == 0
        await q.enqueue(inference_request)
        assert q.size == 1
        await q.enqueue(InferenceRequest(request_id="r2", model="test", messages=[]))
        assert q.size == 2
        await q.dequeue()
        assert q.size == 1

    @pytest.mark.asyncio
    async def test_is_full_property(self, inference_request: InferenceRequest) -> None:
        q = RequestQueue(max_size=1)
        assert not q.is_full
        await q.enqueue(inference_request)
        assert q.is_full


class TestPriorityOrdering:
    """优先级排序测试。"""

    @pytest.mark.asyncio
    async def test_higher_priority_served_first(self) -> None:
        q = RequestQueue(max_size=10)
        req_high = InferenceRequest(request_id="high", model="test", messages=[], priority=1)
        req_mid = InferenceRequest(request_id="mid", model="test", messages=[], priority=5)
        req_low = InferenceRequest(request_id="low", model="test", messages=[], priority=10)

        await q.enqueue(req_low)
        await q.enqueue(req_mid)
        await q.enqueue(req_high)

        item1 = await q.dequeue()
        assert item1 is not None
        assert item1.request.request_id == "high"

        item2 = await q.dequeue()
        assert item2 is not None
        assert item2.request.request_id == "mid"

        item3 = await q.dequeue()
        assert item3 is not None
        assert item3.request.request_id == "low"

    @pytest.mark.asyncio
    async def test_same_priority_fifo(self) -> None:
        """相同优先级保持先进先出。"""
        q = RequestQueue(max_size=10)
        req1 = InferenceRequest(request_id="first", model="test", messages=[], priority=5)
        req2 = InferenceRequest(request_id="second", model="test", messages=[], priority=5)

        await q.enqueue(req1)
        await q.enqueue(req2)

        item1 = await q.dequeue()
        assert item1 is not None
        assert item1.request.request_id == "first"

        item2 = await q.dequeue()
        assert item2 is not None
        assert item2.request.request_id == "second"
