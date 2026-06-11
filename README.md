# LLM Inference Dispatcher

> 部署在多个 LLM 推理引擎实例之上的智能调度层

接收推理请求，根据模型类型、实例负载、请求权重和优先级，将请求路由到最优的推理实例。提供批处理、健康检查、熔断保护、弹性伸缩决策等生产级能力。

## 架构

```
客户端 → FastAPI → [LoadBalancer → RoutingProxy → EngineAdapter] → vLLM/Ollama/TGI
                       ↑                  ↑              ↑
                  InstanceRegistry     CircuitBreaker  MetricsCollector
                       ↑
                  HealthChecker ←  AutoScaler
                       ↓
              [Prometheus /metrics]
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

## 完整演示

```bash
# 一键运行端到端演示（自动启动服务、注册实例、熔断测试、指标查看）
uv run python scripts/demo.py

# Dashboard
uv run streamlit run dashboard/app.py
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/ready` | GET | 就绪检查 |
| `/v1/chat/completions` | POST | OpenAI 兼容推理 |
| `/v1/completions` | POST | OpenAI 兼容补全 |
| `/admin/instances` | GET/POST/DELETE | 实例 CRUD |
| `/admin/status` | GET | 全局状态（含熔断器状态） |
| `/admin/metrics/summary` | GET | 指标汇总（含 P95/P99） |
| `/admin/scaling/evaluate` | GET | 扩缩容评估 |
| `/admin/circuit-breakers/{id}/reset` | POST | 熔断器重置 |
| `/metrics` | GET | Prometheus 指标 |

## 运行测试

```bash
uv run pytest tests/ -v          # 168 测试
uv run ruff check src/ tests/    # Lint
uv run mypy --ignore-missing-imports  # Type check
```

## 配置

编辑 `config/default.yaml`：

```yaml
dispatcher:
  host: "0.0.0.0"
  port: 9090

load_balancer:
  strategy: "weighted"   # round_robin | least_conn | weighted

circuit_breaker:
  failure_threshold: 5
  recovery_timeout: 30

auto_scaler:
  enabled: false         # 启用扩缩容
```

## Docker

```bash
docker compose up -d
curl http://localhost:9090/health
```

## 技术栈

- **Runtime**: Python >=3.11
- **Web**: FastAPI + uvicorn
- **Scheduling**: asyncio.PriorityQueue
- **Load Balancing**: RoundRobin / LeastConn / Weighted
- **Resilience**: CircuitBreaker (三态) + HealthChecker
- **Metrics**: prometheus-client
- **Dashboard**: Streamlit
- **Testing**: pytest + pytest-asyncio
- **Package**: uv
