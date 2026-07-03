# syntax=docker/dockerfile:1

FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── 依赖安装层 (利用 Docker 层缓存 + BuildKit cache mount 持久化 uv 缓存) ──
# 先复制依赖清单文件，这样只要 pyproject.toml / uv.lock 不变，该层就不会重建
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ── 源码层 (频繁变动，放在最后) ──
COPY config/ ./config/
COPY src/ ./src/
COPY scripts/ ./scripts/

EXPOSE 9090

# 端口由 DISPATCHER_PORT 环境变量控制（默认 9090）
# 使用 exec 确保 uvicorn 成为 PID 1 以正确接收 SIGTERM
CMD ["sh", "-c", "exec python -m uvicorn src.dispatcher.main:create_app --factory --host 0.0.0.0 --port ${DISPATCHER_PORT:-9090}"]
