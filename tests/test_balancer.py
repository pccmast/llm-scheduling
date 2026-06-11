"""Module 4: Load Balancer 测试 — 规格书 §4 Module 4.

覆盖 RoundRobinBalancer、LeastConnectionsBalancer 和 create_balancer 工厂函数。
"""

from __future__ import annotations

import pytest

from src.shared.models import InferenceRequest, ModelInstance
from src.dispatcher.balancer import create_balancer
from src.dispatcher.balancer.round_robin import RoundRobinBalancer
from src.dispatcher.balancer.least_conn import LeastConnectionsBalancer


@pytest.fixture
def inference_request() -> InferenceRequest:
    return InferenceRequest(request_id="r1", model="test", messages=[])


@pytest.fixture
def candidates() -> list[ModelInstance]:
    return [
        ModelInstance(instance_id="i1", address="http://a:8000", model="test", engine_type="ollama"),
        ModelInstance(instance_id="i2", address="http://b:8000", model="test", engine_type="vllm"),
        ModelInstance(instance_id="i3", address="http://c:8000", model="test", engine_type="tgi"),
    ]


# ── RoundRobinBalancer ──────────────────────────────────────

class TestRoundRobinBalancer:
    """轮询负载均衡策略测试。"""

    def test_round_robin_distribution(self, inference_request: InferenceRequest, candidates: list[ModelInstance]) -> None:
        """3 个实例连续 6 次 select，每个实例被选中 2 次。"""
        balancer = RoundRobinBalancer()
        counts: dict[str, int] = {}
        for _ in range(6):
            inst = balancer.select(candidates, inference_request)
            counts[inst.instance_id] = counts.get(inst.instance_id, 0) + 1

        assert counts["i1"] == 2
        assert counts["i2"] == 2
        assert counts["i3"] == 2

    def test_round_robin_single_instance(self, inference_request: InferenceRequest) -> None:
        """单实例场景始终返回该实例。"""
        balancer = RoundRobinBalancer()
        single = [ModelInstance(instance_id="i1", address="http://a:8000", model="test", engine_type="ollama")]
        for _ in range(5):
            inst = balancer.select(single, inference_request)
            assert inst.instance_id == "i1"

    def test_empty_candidates_raises_value_error(self, inference_request: InferenceRequest) -> None:
        """candidates 为空列表时抛出 ValueError。"""
        balancer = RoundRobinBalancer()
        with pytest.raises(ValueError, match="must not be empty"):
            balancer.select([], inference_request)


# ── LeastConnectionsBalancer ────────────────────────────────

class TestLeastConnectionsBalancer:
    """最少连接负载均衡策略测试。"""

    def test_select_least_active_instance(self, inference_request: InferenceRequest, candidates: list[ModelInstance]) -> None:
        """选择活跃连接数最少的实例。"""
        balancer = LeastConnectionsBalancer()
        # 模拟不同活跃数
        balancer.on_request_start("i1")
        balancer.on_request_start("i1")
        balancer.on_request_start("i1")  # i1: 3
        balancer.on_request_start("i2")  # i2: 1
        balancer.on_request_start("i3")
        balancer.on_request_start("i3")  # i3: 2

        inst = balancer.select(candidates, inference_request)
        assert inst.instance_id == "i2"  # i2 有最少活跃连接

    def test_select_first_when_same_active_count(self, inference_request: InferenceRequest) -> None:
        """活跃数相同时选择 candidates 列表中第一个（活跃数相同的实例）。"""
        balancer = LeastConnectionsBalancer()
        candidates = [
            ModelInstance(instance_id="i1", address="http://a:8000", model="test", engine_type="ollama"),
            ModelInstance(instance_id="i2", address="http://b:8000", model="test", engine_type="vllm"),
        ]
        # 两个实例都无活跃连接
        inst = balancer.select(candidates, inference_request)
        # min() 在活跃数相同时保持 candidates 顺序，所以返回第一个
        assert inst.instance_id == "i1"

    def test_on_request_start_and_end_pairing(self) -> None:
        """on_request_start +1，on_request_end -1 正确配对。"""
        balancer = LeastConnectionsBalancer()
        assert balancer.get_active_count("i1") == 0

        balancer.on_request_start("i1")
        assert balancer.get_active_count("i1") == 1

        balancer.on_request_start("i1")
        assert balancer.get_active_count("i1") == 2

        balancer.on_request_end("i1")
        assert balancer.get_active_count("i1") == 1

        balancer.on_request_end("i1")
        assert balancer.get_active_count("i1") == 0

    def test_on_request_end_does_not_go_below_zero(self) -> None:
        """活跃数为 0 时调用 on_request_end 仍为 0。"""
        balancer = LeastConnectionsBalancer()
        balancer.on_request_end("i1")  # 没有先 start
        balancer.on_request_end("i1")
        assert balancer.get_active_count("i1") == 0

    def test_get_active_count_unknown_instance(self) -> None:
        """未知实例返回 0。"""
        balancer = LeastConnectionsBalancer()
        assert balancer.get_active_count("unknown") == 0


# ── create_balancer 工厂函数 ───────────────────────────────

class TestCreateBalancer:
    """负载均衡器工厂函数测试。"""

    def test_create_round_robin(self) -> None:
        """create_balancer("round_robin") 返回 RoundRobinBalancer 实例。"""
        b = create_balancer("round_robin")
        assert isinstance(b, RoundRobinBalancer)

    def test_create_least_conn(self) -> None:
        """create_balancer("least_conn") 返回 LeastConnectionsBalancer 实例。"""
        b = create_balancer("least_conn")
        assert isinstance(b, LeastConnectionsBalancer)

    def test_create_unknown_strategy_raises_value_error(self) -> None:
        """create_balancer("unknown") 抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unknown load balancer strategy"):
            create_balancer("unknown")
