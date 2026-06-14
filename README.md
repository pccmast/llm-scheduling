# LLM Inference Dispatcher

> 部署在多个 LLM 推理引擎实例之上的智能调度层

接收推理请求，根据模型类型、实例负载、请求权重和优先级，将请求路由到最优的推理实例。提供优先级队列、动态批处理、健康检查、三态熔断、加权负载均衡、弹性伸缩决策、分布式追踪、优雅停机等生产级能力。

## 架构

```
客户端 → FastAPI → [PriorityQueue → DynamicBatcher]
                       ↓
              [LoadBalancer → RoutingProxy → EngineAdapter] → vLLM/Ollama/TGI
                 ↑        ↑         ↑              ↑
           InstanceRegistry  │   CircuitBreaker  MetricsCollector
                 ↑           │         ↑              ↑
            HealthChecker ───┘    AutoScaler ←─┘      │
                 ↓                                    ↓
          [Prometheus /metrics]           [OTel → Jaeger/Tempo]
```

## 快速开始

```bash
# 1. 安装依赖
uv sync --extra dev

# 2. 启动 mock 引擎（终端 1）
uv run python scripts/mock_engine.py --port 8001

# 3. 启动调度服务（终端 2）
#    默认无 API Key 鉴权，仅允许 localhost 访问 admin 端点
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

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_API_KEY` | Admin API 鉴权密钥。未设置时仅允许 localhost 访问 | (无，开发模式) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTel 导出端点 (如 `http://jaeger:4318/v1/traces`)。未设置时打印至 stdout | (控制台) |
| `DISPATCHER_PORT` | 覆盖调度服务端口 | `9090` |
| `DISPATCHER_LOG_LEVEL` | 日志级别 | `info` |

## API 端点

| 端点 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/health` | GET | 无 | 调度器健康检查 |
| `/ready` | GET | 无 | 就绪检查 (draining 时返回 503) |
| `/v1/chat/completions` | POST | 无 | OpenAI 兼容聊天推理 |
| `/v1/completions` | POST | 无 | OpenAI 兼容文本补全 |
| `/metrics` | GET | 无 | Prometheus 指标 |
| `/admin/instances` | GET/POST/DELETE | API Key | 实例 CRUD |
| `/admin/status` | GET | API Key | 全局状态 (实例/熔断器/队列) |
| `/admin/metrics/summary` | GET | API Key | 指标汇总 (含 P95/P99) |
| `/admin/scaling/evaluate` | GET | API Key | 扩缩容评估 |
| `/admin/circuit-breakers/{id}/reset` | POST | API Key | 熔断器重置 |

> **Admin 端点鉴权**: 设置 `ADMIN_API_KEY` 环境变量后，admin 端点需携带 `X-API-Key: <your-key>` header。未设置时仅允许 `127.0.0.1` 访问。

## 配置

编辑 `config/default.yaml`：

```yaml
dispatcher:
  host: "0.0.0.0"
  port: 9090
  upstream_timeout: 120.0

load_balancer:
  strategy: "weighted"         # round_robin | least_conn | weighted

queue:
  max_size: 100                # 最大排队请求数
  max_wait_seconds: 30.0       # 请求最大等待时间

batcher:
  enabled: false               # 启用动态攒批
  max_batch_size: 8
  max_wait_ms: 50

circuit_breaker:
  failure_threshold: 5         # 连续失败 N 次 → OPEN
  recovery_timeout: 30         # OPEN → HALF_OPEN 等待秒数
  half_open_max_calls: 1       # HALF_OPEN 试探请求数

auto_scaler:
  enabled: false
  scale_up_queue_threshold: 10
  scale_up_load_threshold: 8000.0
  scale_down_idle_seconds: 300
  scale_down_load_threshold: 500.0
  min_instances: 1
  max_instances: 10
  cooldown_seconds: 60

health_check:
  interval_seconds: 10.0       # 探测间隔
  timeout_seconds: 5.0          # 单次超时
  unhealthy_threshold: 3        # 连续失败 N 次标记 UNHEALTHY
```

## Prometheus 告警

预置告警规则位于 `deploy/alerts.yaml`，可直接导入 PrometheusRule：

| 告警 | 级别 | 触发条件 |
|------|------|---------|
| `DispatcherNoHealthyInstances` | critical | 5 分钟内无成功请求 |
| `DispatcherQueueDepthHigh` | warning | 队列深度 > 50 持续 5 分钟 |
| `DispatcherQueueDepthCritical` | critical | 队列深度 > 100 持续 2 分钟 |
| `DispatcherP99LatencyHigh` | warning | P99 > 10s 持续 5 分钟 |
| `DispatcherP99LatencyCritical` | critical | P99 > 30s |
| `DispatcherCircuitBreakerOpen` | warning | 持续报错 (熔断器可能 OPEN) |
| `DispatcherErrorRateHigh` | critical | 错误率 > 10% |

## 优雅停机

调度器收到 SIGTERM 后执行三步优雅停机：

1. **Drain**: 停止接受新请求 (HTTP 推理返回 503，`/ready` 返回 503)
2. **Wait**: 等待所有 in-flight 请求完成 (默认超时 30s)
3. **Close**: 关闭连接池 + 刷新 OTel span

配合 K8s `terminationGracePeriodSeconds` + preStop hook 使用。

## 运行测试

```bash
uv run pytest tests/ -v              # 168 测试
uv run ruff check src/ tests/        # Lint
```

## Docker

```bash
# 启动完整环境（dispatcher + 2 个 mock 引擎，自动注册）
docker compose up -d

# 等待 init 容器完成注册（约 15s），然后测试推理
curl -X POST http://localhost:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test-model","messages":[{"role":"user","content":"Hello Docker!"}]}'

# 查看状态
curl http://localhost:9090/admin/status
```

## 技术栈

- **Runtime**: Python >=3.11
- **Web**: FastAPI + uvicorn
- **Scheduling**: asyncio.PriorityQueue (优先级队列)
- **Load Balancing**: RoundRobin / LeastConn / Weighted (token 感知)
- **Resilience**: CircuitBreaker (三态惰性转换) + HealthChecker (并行探测)
- **Metrics**: prometheus-client (7 个指标) + PrometheusRule 告警
- **Tracing**: OpenTelemetry (OTLP / Console exporter)
- **Auth**: AdminAuthMiddleware (API Key / localhost)
- **Dashboard**: Streamlit
- **Testing**: pytest + pytest-asyncio (168 用例)
- **Package**: uv
