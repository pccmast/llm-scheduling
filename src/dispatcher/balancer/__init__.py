"""Load balancer strategies — 规格书 §4 Module 4.

提供 create_balancer() 工厂函数，根据配置创建负载均衡策略实例。
"""

from __future__ import annotations

from src.shared.models import LoadBalancer


def create_balancer(strategy: str) -> LoadBalancer:
    """根据策略名称创建负载均衡器。

    Args:
        strategy: "round_robin" | "least_conn" | "weighted"

    Returns:
        对应的 LoadBalancer 实例。

    Raises:
        ValueError: strategy 不在支持列表中。
    """
    if strategy == "round_robin":
        from src.dispatcher.balancer.round_robin import RoundRobinBalancer
        return RoundRobinBalancer()
    elif strategy == "least_conn":
        from src.dispatcher.balancer.least_conn import LeastConnectionsBalancer
        return LeastConnectionsBalancer()
    elif strategy == "weighted":
        from src.dispatcher.balancer.weighted import WeightedBalancer
        return WeightedBalancer()
    else:
        raise ValueError(f"Unknown load balancer strategy: {strategy}. "
                         f"Supported: round_robin, least_conn, weighted")
