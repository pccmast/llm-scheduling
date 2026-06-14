"""API Key 鉴权 — FastAPI 依赖注入方式。

通过环境变量 ADMIN_API_KEY 配置管理员密钥。
- 已设置 → 所有 /admin/* 端点需要 X-API-Key header 匹配
- 未设置 → 开发模式，不做限制
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request


def verify_admin_access(request: Request) -> None:
    """FastAPI 依赖函数：验证 admin 访问权限。

    用法：在 admin router 上添加 dependencies=[Depends(verify_admin_access)]
    """
    api_key = os.environ.get("ADMIN_API_KEY")
    if not api_key:
        return  # 开发模式：不限制

    provided = request.headers.get("X-API-Key", "")
    if provided != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")
