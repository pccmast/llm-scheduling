# syntax=docker/dockerfile:1

FROM python:3.11-slim

WORKDIR /app

# Install uv and project dependencies
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv pip compile pyproject.toml -o /tmp/requirements.txt && \
    uv pip install --system --no-cache -r /tmp/requirements.txt

COPY config/ ./config/
COPY src/ ./src/
COPY scripts/ ./scripts/

EXPOSE 9090

# 端口由 DISPATCHER_PORT 环境变量控制（默认 9090）
# 使用 exec 确保 uvicorn 成为 PID 1 以正确接收 SIGTERM
CMD ["sh", "-c", "exec python -m uvicorn src.dispatcher.main:create_app --factory --host 0.0.0.0 --port ${DISPATCHER_PORT:-9090}"]
