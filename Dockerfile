FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY config/ ./config/
COPY src/ ./src/

EXPOSE 9090

CMD ["uv", "run", "uvicorn", "src.dispatcher.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "9090"]
