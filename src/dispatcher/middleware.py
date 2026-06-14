"""API Key 鉴权中间件。

通过环境变量 ADMIN_API_KEY 配置管理员密钥。
所有 /admin/* 端点需要携带 X-API-Key header 匹配该密钥。
未配置环境变量时，admin 端点仅允许本地回环地址访问。
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class AdminAuthMiddleware(BaseHTTPMiddleware):
    """Admin API 鉴权中间件。

    鉴权逻辑（按优先级）：
    1. 环境变量 ADMIN_API_KEY 已设置 → 检查 X-API-Key header 是否匹配
    2. 未设置 → 只允许 127.0.0.1 / ::1 访问（开发模式）
    """

    _ADMIN_PREFIX: str = "/admin"

    def __init__(self, app, api_key: str | None = None) -> None:
        super().__init__(app)
        self._api_key = api_key or os.environ.get("ADMIN_API_KEY")

    async def dispatch(self, request: Request, call_next):
        # 只拦截 /admin 路径
        if not request.url.path.startswith(self._ADMIN_PREFIX):
            return await call_next(request)

        if self._api_key:
            # 生产模式：需要 API Key
            provided = request.headers.get("X-API-Key", "")
            if provided != self._api_key:
                raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")
        else:
            # 开发模式：仅允许本地回环或测试客户端访问
            client_host = request.client.host if request.client else ""
            if client_host not in ("127.0.0.1", "::1", "localhost", "testclient", ""):
                raise HTTPException(
                    status_code=403,
                    detail="Admin API only accessible from localhost (set ADMIN_API_KEY for remote access)",
                )

        return await call_next(request)
