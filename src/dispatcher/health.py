"""Module 3: Health Checker — 规格书 §4 Module 3.

定期探测所有已注册实例的健康状态。连续失败达到阈值标记为 UNHEALTHY，
恢复后自动标记为 HEALTHY。DRAINING 状态的实例跳过检查。
"""

from __future__ import annotations

import asyncio
import os

import httpx

from src.dispatcher.registry import InstanceRegistry
from src.shared.models import EngineAdapter, HealthCheckConfig, InstanceStatus, ModelInstance


class HealthChecker:
    """实例健康检查器。"""

    def __init__(
        self, registry: InstanceRegistry, engine_adapters: dict[str, EngineAdapter], config: HealthCheckConfig
    ) -> None:
        """
        Args:
            registry: 实例注册表（引用，非拷贝）。
            engine_adapters: 引擎类型 → 适配器的映射。
            config: 健康检查配置。

        Precondition:
            engine_adapters 包含所有已注册实例的 engine_type 对应的适配器。
        """
        self._registry = registry
        self._engine_adapters = engine_adapters
        self._config = config
        self._failure_counts: dict[str, int] = {}
        self._success_counts: dict[str, int] = {}
        self._stopped = asyncio.Event()
        self._client: httpx.AsyncClient | None = None

    async def check_one(self, instance: ModelInstance) -> bool:
        """检查单个实例是否健康。

        通过 HTTP GET 请求实例的 health endpoint。

        Args:
            instance: 要检查的实例。

        Returns:
            True 表示健康，False 表示不健康。
            网络错误或超时返回 False（不抛出异常）。
        """
        adapter = self._engine_adapters.get(instance.engine_type)
        if adapter is None:
            return False

        url = adapter.health_endpoint(instance)
        timeout = self._config.timeout_seconds
        headers: dict[str, str] = {}

        # 后端需要鉴权时携带 API Key（如 LM Studio）
        api_key = os.environ.get("BACKEND_API_KEY", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers, trust_env=False) as client:
                response = await client.get(url)
                return response.status_code < 500
        except Exception:
            return False

    async def run_forever(self) -> None:
        """启动健康检查的后台循环。

        每 interval_seconds 秒检查一次所有实例。
        连续失败 >= unhealthy_threshold 次标记为 UNHEALTHY。
        成功 >= healthy_threshold 次恢复为 HEALTHY。
        该方法不会返回（设计为在 asyncio.Task 中运行）。

        Postcondition:
            registry 中的实例状态会根据探测结果自动更新。
        """
        self._client = httpx.AsyncClient(trust_env=False)
        try:
            while not self._stopped.is_set():
                await self._check_all_instances()
                # 等待间隔，但允许被 stop() 中断
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(),
                        timeout=self._config.interval_seconds,
                    )
                    break  # stop signal received
                except asyncio.TimeoutError:
                    pass  # interval elapsed, continue checking
        finally:
            await self._client.aclose()

    async def _check_all_instances(self) -> None:
        """并行检查所有实例的健康状态。"""
        instances = self._registry.list_all()
        if not instances:
            return

        # 并行检查所有非 DRAINING 实例
        await asyncio.gather(
            *(self._check_and_update(inst) for inst in instances if inst.status != InstanceStatus.DRAINING),
            return_exceptions=True,
        )

    async def _check_and_update(self, instance: ModelInstance) -> None:
        """检查单个实例并更新状态。"""
        is_healthy = await self.check_one(instance)

        if is_healthy:
            self._failure_counts[instance.instance_id] = 0
            self._success_counts[instance.instance_id] = self._success_counts.get(instance.instance_id, 0) + 1
            # 连续成功达到阈值则恢复
            if self._success_counts[instance.instance_id] >= self._config.healthy_threshold:
                self._registry.mark_healthy(instance.instance_id)
        else:
            self._success_counts[instance.instance_id] = 0
            self._failure_counts[instance.instance_id] = self._failure_counts.get(instance.instance_id, 0) + 1
            # 连续失败达到阈值则标记为 unhealthy
            if self._failure_counts[instance.instance_id] >= self._config.unhealthy_threshold:
                self._registry.mark_unhealthy(instance.instance_id)

    def stop(self) -> None:
        """停止健康检查循环。"""
        self._stopped.set()

    def get_failure_count(self, instance_id: str) -> int:
        """获取指定实例的连续失败次数。"""
        return self._failure_counts.get(instance_id, 0)
