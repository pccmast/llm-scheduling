FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache -r <(uv pip compile pyproject.toml -o -)

COPY config/ ./config/
COPY src/ ./src/

EXPOSE 9090

CMD ["python", "-m", "uvicorn", "src.dispatcher.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "9090"]
