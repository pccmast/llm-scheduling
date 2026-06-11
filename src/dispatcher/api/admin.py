"""Module 12: 管理端点（Sprint 1 简化版）— 规格书 §4 Module 12.

POST /admin/instances — 注册推理引擎实例
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from src.shared.models import ModelInstance

admin_router = APIRouter(prefix="/admin")


@admin_router.post("/instances")
async def register_instance(request: Request) -> dict:
    """注册一个新的推理引擎实例。

    Args:
        request body: ModelInstance 格式的 JSON。

    Returns:
        {"status": "ok", "instance_id": "..."}

    Raises:
        HTTPException(409): instance_id 已存在。
        HTTPException(422): 数据格式错误。
    """
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
