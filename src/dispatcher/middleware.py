"""API Key 鉴权 — FastAPI 依赖注入方式。

通过环境变量 ADMIN_API_KEY 配置管理员密钥。
所有 /admin/* 端点需要携带 X-API-Key header 匹配该密钥。
未配置环境变量时仅允许本地回环地址访问。
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request


def verify_admin_access(request: Request) -> None:
    """FastAPI 依赖函数：验证 admin 访问权限。

    用法：在 admin router 或单个端点上添加 Depends(verify_admin_access)

    鉴权逻辑：
    1. ADMIN_API_KEY 已设置 → 检查 X-API-Key header
    2. 未设置 → 仅允许 127.0.0.1 / ::1 / localhost
    """
    api_key = os.environ.get("ADMIN_API_KEY")

    if api_key:
        provided = request.headers.get("X-API-Key", "")
        if provided != api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")
    else:
        client_host = request.client.host if request.client else ""
        if client_host not in ("127.0.0.1", "::1", "localhost", "testclient", ""):
            raise HTTPException(
                status_code=403,
                detail="Admin API only accessible from localhost (set ADMIN_API_KEY for remote access)",
            )
