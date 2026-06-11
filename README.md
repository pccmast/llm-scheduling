# LLM Inference Dispatcher

> 部署在多个 LLM 推理引擎实例之上的智能调度层

接收推理请求，根据模型类型、实例负载、请求权重和优先级，将请求路由到最优的推理实例。

## 架构

```
客户端 → FastAPI → [LoadBalancer → RoutingProxy → EngineAdapter] → vLLM/Ollama/TGI
                       ↑              ↑               ↑
                  InstanceRegistry  CircuitBreaker  MetricsCollector
                       ↑
                  HealthChecker ← AutoScaler
```

## 快速开始

```bash
# 1. 安装依赖
uv sync --extra dev

# 2. 启动 mock 引擎（终端 1）
uv run python scripts/mock_engine.py --port 8001

# 3. 启动调度服务（终端 2）
uv run uvicorn src.dispatcher.main:create_app --factory --port 9090

# 4. 注册实例
curl -X POST http://localhost:9090/admin/instances \
  -H "Content-Type: application/json" \
  -d '{"instance_id":"mock-1","address":"http://localhost:8001","model":"test-model","engine_type":"ollama"}'

# 5. 发送推理请求
curl -X POST http://localhost:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test-model","messages":[{"role":"user","content":"Hello!"}]}'
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/ready` | GET | 就绪检查 |
| `/v1/chat/completions` | POST | OpenAI 兼容推理 |
| `/v1/completions` | POST | OpenAI 兼容补全 |
| `/admin/instances` | GET/POST/DELETE | 实例 CRUD |
| `/admin/status` | GET | 全局状态 |
| `/admin/metrics/summary` | GET | 指标汇总 |
| `/admin/scaling/evaluate` | GET | 扩缩容评估 |
| `/admin/circuit-breakers/{id}/reset` | POST | 熔断器重置 |
| `/metrics` | GET | Prometheus 指标 |

## 运行测试

```bash
uv run pytest tests/ -v          # 144 tests
uv run ruff check src/ tests/    # Lint
uv run mypy --ignore-missing-imports  # Type check
```

## 技术栈

- **Runtime**: Python >=3.11
- **Web**: FastAPI + uvicorn
- **Scheduling**: asyncio.PriorityQueue
- **Metrics**: prometheus-client
- **Dashboard**: Streamlit
- **Testing**: pytest + pytest-asyncio
