"""Module 12: 健康检查端点 — 规格书 §4 Module 12.

GET /health — 调度器自身健康检查
GET /ready — 就绪检查（判断是否可以接收流量）
"""

from __future__ import annotations

from fastapi import APIRouter, Request

health_router = APIRouter()


@health_router.get("/health")
async def health_check(request: Request) -> dict:
    """调度器自身的健康检查端点。

    Returns:
        {"status": "ok" | "degraded", "instances_healthy": N, "instances_total": M}

    Postcondition:
        如果注册表中有至少一个健康实例，status 为 "ok"；
        否则 status 为 "degraded"（但 HTTP 仍返回 200）。
    """
    registry = request.app.state.registry
    all_instances = registry.list_all()
    healthy_count = sum(1 for inst in all_instances if inst.status.value == "healthy")

    return {
        "status": "ok" if healthy_count > 0 else "degraded",
        "instances_healthy": healthy_count,
        "instances_total": len(all_instances),
    }


@health_router.get("/ready", response_model=None)
async def readiness_check(request: Request):
    """就绪检查——判断调度器是否可以接收流量。

    Returns:
        {"ready": true/false}
        不 ready 时返回 HTTP 503：
        - 调度器正在 draining（优雅停机）
        - 注册表中没有健康实例
    """
    from fastapi.responses import JSONResponse

    # 优雅停机期间拒绝流量
    proxy = getattr(request.app.state, "proxy", None)
    if proxy is not None and getattr(proxy, "_draining", False):
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reason": "Server is draining"},
        )

    registry = request.app.state.registry
    all_instances = registry.list_all()
    healthy_count = sum(1 for inst in all_instances if inst.status.value == "healthy")

    ready = healthy_count > 0
    if not ready:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "reason": "No healthy instances registered"},
        )
    return {"ready": True}
