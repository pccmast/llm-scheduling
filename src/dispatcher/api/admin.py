"""Module 12: 管理端点（完整版）— 规格书 §4 Module 12.

POST /admin/instances — 注册实例
DELETE /admin/instances/{id} — 注销实例
GET /admin/instances — 列出实例（支持 model 过滤）
GET /admin/status — 全局状态概览
GET /admin/metrics/summary — 指标汇总
GET /admin/scaling/evaluate — 扩缩容评估
POST /admin/circuit-breakers/{id}/reset — 熔断器重置
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from src.shared.models import ModelInstance

admin_router = APIRouter(prefix="/admin")


# ── 实例 CRUD ──────────────────────────────────────────────


@admin_router.post("/instances")
async def register_instance(request: Request) -> dict:
    """注册一个新的推理引擎实例。"""
    registry = request.app.state.registry
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body")
    try:
        instance = ModelInstance(**body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid instance data: {e}")
    try:
        registry.register(instance)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "ok", "instance_id": instance.instance_id}


@admin_router.delete("/instances/{instance_id}")
async def deregister_instance(instance_id: str, request: Request) -> dict:
    """注销一个推理引擎实例。"""
    registry = request.app.state.registry
    try:
        registry.deregister(instance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Instance '{instance_id}' not found")
    # 同时清理熔断器
    cb_registry = request.app.state.circuit_breakers
    if cb_registry:
        cb_registry.remove(instance_id)
    return {"status": "ok"}


@admin_router.get("/instances")
async def list_instances(model: str | None = None, request: Request = None) -> list[dict]:  # type: ignore
    """列出所有已注册的实例，支持 model 参数过滤。"""
    registry = request.app.state.registry
    if model:
        instances = registry.list_by_model(model)
    else:
        instances = registry.list_all()
    return [inst.model_dump() for inst in instances]


# ── 状态与指标 ─────────────────────────────────────────────


@admin_router.get("/status")
async def get_status(request: Request) -> dict:
    """获取调度器全局状态概览。"""
    registry = request.app.state.registry
    cb_registry = request.app.state.circuit_breakers

    all_instances = registry.list_all()
    healthy = sum(1 for i in all_instances if i.status.value == "healthy")
    unhealthy = sum(1 for i in all_instances if i.status.value == "unhealthy")
    draining = sum(1 for i in all_instances if i.status.value == "draining")

    circuit_states = cb_registry.get_all_states() if cb_registry else {}

    queue = request.app.state.request_queue
    queue_depth = queue.size if queue else 0

    return {
        "instances": {
            "total": len(all_instances),
            "healthy": healthy,
            "unhealthy": unhealthy,
            "draining": draining,
        },
        "circuit_breakers": circuit_states,
        "queue_depth": queue_depth,
    }


@admin_router.get("/metrics/summary")
async def get_metrics_summary(request: Request) -> dict:
    """获取指标汇总数据。"""
    metrics = request.app.state.metrics
    if metrics is None:
        return {"error": "Metrics not configured"}
    return metrics.get_summary()


@admin_router.get("/scaling/evaluate")
async def evaluate_scaling(request: Request) -> dict:
    """手动触发一次扩缩容评估。"""
    scaler = request.app.state.auto_scaler
    if scaler is None:
        return {"action": "none", "reason": "Auto scaler not configured"}
    decision = scaler.evaluate()
    return decision.model_dump()


# ── 熔断器管理 ─────────────────────────────────────────────


@admin_router.post("/circuit-breakers/{instance_id}/reset")
async def reset_circuit_breaker(instance_id: str, request: Request) -> dict:
    """手动重置指定实例的熔断器。"""
    cb_registry = request.app.state.circuit_breakers
    if cb_registry is None:
        raise HTTPException(status_code=404, detail="Circuit breaker registry not configured")
    try:
        cb = cb_registry.get_or_create(instance_id)
        cb.reset()
        return {"status": "ok", "instance_id": instance_id, "new_state": cb.state}
    except Exception:
        raise HTTPException(status_code=404, detail=f"Instance '{instance_id}' not found")
