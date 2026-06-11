"""Module 4: Load Balancer 测试 — 规格书 §4 Module 4.

本文件覆盖 RoundRobinBalancer 和 LeastConnectionsBalancer 的单元测试用例。
WeightedBalancer 测试在 Sprint 2 阶段追加。
在 Phase 2 阶段创建骨架，Core 阶段填充实现。
"""

from __future__ import annotations

import pytest


# ── RoundRobinBalancer ──────────────────────────────────────

class TestRoundRobinBalancer:
    """轮询负载均衡策略测试。"""

    def test_round_robin_distribution(self) -> None:
        """3 个实例连续 6 次 select，每个实例被选中 2 次。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")

    def test_empty_candidates_raises_value_error(self) -> None:
        """candidates 为空列表时抛出 ValueError。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")


# ── LeastConnectionsBalancer ────────────────────────────────

class TestLeastConnectionsBalancer:
    """最少连接负载均衡策略测试。"""

    def test_select_least_active_instance(self) -> None:
        """选择活跃连接数最少的实例（i1=3, i2=1, i3=2 → i2）。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")

    def test_select_first_when_same_active_count(self) -> None:
        """活跃数相同时选择 candidates 列表中的第一个。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")

    def test_on_request_start_and_end_pairing(self) -> None:
        """on_request_start +1，on_request_end -1 正确配对标。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")

    def test_on_request_end_does_not_go_below_zero(self) -> None:
        """活跃数为 0 时调用 on_request_end 仍为 0。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")


# ── create_balancer 工厂函数 ───────────────────────────────

class TestCreateBalancer:
    """负载均衡器工厂函数测试。"""

    def test_create_round_robin(self) -> None:
        """create_balancer("round_robin") 返回 RoundRobinBalancer 实例。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")

    def test_create_unknown_strategy_raises_value_error(self) -> None:
        """create_balancer("unknown") 抛出 ValueError。"""
        pytest.skip("未实现 — 将在 Core Step 4 中实现")
