# LLM Inference Dispatcher

> 部署在多个 LLM 推理引擎实例之上的智能调度层

接收推理请求，根据模型类型、实例负载、请求权重和优先级，将请求路由到最优的推理实例。支持通配符路由、重试+fallback、cooldown 冷却期、性能反馈 EWMA、分层健康检查、per-instance API Key 等生产级能力。

**v4 核心升级**：感知实例速度 + 可靠性差异，自动偏好快实例、降权慢实例。

## 架构

```
客户端 → FastAPI
           │
    ┌──────▼──────┐
    │ 四层过滤     │ Drain → Registry → CircuitBreaker → Balancer
    │ + cooldown  │ 冷却中实例暂时跳过
    └──────┬──────┘
           ▼
    RoutingProxy (重试 + tier 耗尽: LOCAL → REMOTE)
           ▼
    EngineAdapter → vLLM/Ollama/TGI/LM Studio
           │
    ┌──────▼──────┐
    │ 性能反馈     │ EWMA speed + reliability 回写 balancer
    │ 分层健康检查 │ GET /v1/models (轻) + POST chat (深检)
    └─────────────┘
```

## 快速开始

### 本地 LM Studio (真实模型)

```bash
# 1. 复制环境变量
cp .env.example .env
# 编辑 .env: BACKEND_API_KEY=sk-lm-xxx

# 2. 启动调度服务
uv run uvicorn src.dispatcher.main:create_app --factory --port 9090

# 3. 注册 LM Studio 实例 (通配符, 可处理任意模型)
curl -X POST http://127.0.0.1:9090/admin/instances \
  -H "Content-Type: application/json" \
  -d '{"instance_id":"qwen-A","address":"http://127.0.0.1:14344",
       "model":"*","engine_type":"vllm","capacity_factor":1.5}'

# 4. 发送推理请求
curl -X POST http://127.0.0.1:9090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen/qwen3-1.7b","messages":[{"role":"user","content":"你好"}]}'
```

### Mock 引擎 (开发测试)

```bash
uv run python scripts/mock_engine.py --port 8001
uv run uvicorn src.dispatcher.main:create_app --factory --port 9090

curl -X POST http://127.0.0.1:9090/admin/instances \
  -d '{"instance_id":"mock-1","address":"http://127.0.0.1:8001","model":"test-model","engine_type":"ollama"}'
```

## 部署拓扑

| 场景 | model 字段 | 示例 |
|------|-----------|------|
| 单端点单模型 (Ollama) | `"llama3"` | 精确模型匹配 |
| 单端点多模型 (LM Studio) | `"*"` | 通配符, 后端自行路由 |
| 多端点同模型 (3×vLLM) | 同一 model | 负载均衡分发 |
| 远程 API (OpenAI) | `"gpt-4o"` | `api_key_env: "OPENAI_API_KEY"` |
| 混合本地+远程 | `tier: "local"/"remote"` | LOCAL 优先, 全失败 fallback REMOTE |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DISPATCHER_PORT` | 调度器端口 | `9090` |
| `DISPATCHER_LOG_LEVEL` | 日志级别 | `info` |
| `BACKEND_API_KEY` | 全局后端 API Key (deprecated, fallback) | — |
| `ADMIN_API_KEY` | Admin 鉴权密钥 | (无, 开发模式) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTel 导出端点 | (控制台) |

**v4 per-instance Key**: 注册实例时传 `api_key_env: "MY_KEY"`，从指定环境变量读取，不传则 fallback 到 `BACKEND_API_KEY`。

## 实例管理

调度器本身不做模型发现——实例由运维层（YAML 配置 / API / 脚本）注册。

### 注册实例

```bash
# 方式 1: API 注册 (推荐)
curl -X POST http://127.0.0.1:9090/admin/instances \
  -H "Content-Type: application/json" \
  -d '{
    "instance_id": "qwen-1",
    "address": "http://127.0.0.1:14344",
    "model": "qwen/qwen3-1.7b",
    "engine_type": "vllm"
  }'
```

```bash
# 方式 2: LM Studio 一键注册 (自动发现已加载模型)
uv run python scripts/setup_lmstudio.py
```

```bash
# 方式 3: YAML 静态配置 (生产环境)
# 编辑 config/default.yaml:
instances:
  - instance_id: gpu-1
    address: http://192.168.1.10:8000
    model: qwen3-1.7b
    engine_type: vllm
```

### 注销实例

```bash
curl -X DELETE http://127.0.0.1:9090/admin/instances/qwen-1
```

### `model` 字段规范

| 值 | 含义 | 适用场景 |
|----|------|---------|
| `"qwen/qwen3-1.7b"` | 精确模型名 | 该实例明确只服务此模型 |
| `"*"` | 通配符 | 后端能处理任意模型（如 OpenAI API 代理） |

> ⚠️ 同一 `address` + 同一 `model` 不能重复注册。`model="*"` 与精确模型名并存会触发警告。

### 管理端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/admin/instances` | 注册实例 |
| `DELETE` | `/admin/instances/{id}` | 注销实例 + 清理熔断器 |
| `GET` | `/admin/instances` | 列出所有实例 |
| `GET` | `/admin/status` | 全局状态 |
| `GET` | `/admin/balancer/debug` | 每个实例的速度/可靠性评分 |

## 配置

编辑 `config/default.yaml`：

```yaml
# 路由重试
routing:
  max_retries: 2                # 最多重试 2 次 (总尝试 3 实例)
  retry_on_timeout: true
  retry_on_5xx: true
  retry_on_connection: true

# 负载均衡
load_balancer:
  strategy: "weighted"
  weights:                      # v4: 可调权重
    speed: 1.0                  # >1 偏好快实例
    reliability: 1.0            # >1 偏好稳定实例
    load: 1.0                   # 0=忽略负载
    cooldown_seconds: 5.0       # 失败后冷却秒数

# 分层健康检查
health_check:
  interval_seconds: 10.0
  timeout_seconds: 5.0
  unhealthy_threshold: 3
  deep_check_interval: 30       # 每 30 次轻检 +1 次推理心跳
```

## v4 核心能力

| 能力 | 说明 |
|------|------|
| **通配符路由** | `model="*"` 的实例可处理任意模型 |
| **性能反馈** | EWMA speed + reliability 自动回写 balancer |
| **动态权重** | speed/reliability/load 权重可配置 |
| **cooldown 冷却** | 失败后 N 秒不选同一实例 |
| **重试+fallback** | 瞬态错误换实例重试, max 3 次 |
| **tier 优先级** | LOCAL 耗尽后自动 fallback REMOTE |
| **分层健康检查** | 轻检 (0 token) + 深检 (推理心跳) |
| **per-instance Key** | 每实例独立 API Key, fallback 全局 |
| **thinking 模型兼容** | 自动合并 reasoning_content |
| **429 感知** | 限流响应触发 reliability 降权 |
| **流式重试** | forward_stream 也支持重试循环 |

## API 端点

| 端点 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/health` | GET | 无 | 调度器健康检查 |
| `/ready` | GET | 无 | 就绪检查 (draining 时 503) |
| `/v1/chat/completions` | POST | 无 | OpenAI 兼容聊天推理 |
| `/v1/completions` | POST | 无 | OpenAI 兼容文本补全 |
| `/metrics` | GET | 无 | Prometheus 指标 |
| `/admin/instances` | GET/POST/DELETE | API Key | 实例 CRUD |
| `/admin/status` | GET | API Key | 全局状态 (实例/熔断器/队列) |
| `/admin/metrics/summary` | GET | API Key | 指标汇总 (含 P95/P99) |
| `/admin/scaling/evaluate` | GET | API Key | 扩缩容评估 |
| `/admin/circuit-breakers/{id}/reset` | POST | API Key | 熔断器重置 |

## 运行测试

```bash
uv run pytest tests/ -v -m "not e2e"   # 183 单元+集成测试
uv run pytest tests/ -v -m "e2e"        # 14 端到端测试 (需 mock 引擎)
uv run ruff check src/ tests/           # Lint
uv run mypy                             # 类型检查
```

## 真实后端集成测试

```bash
# 需 LM Studio 运行中 + dispatcher 已启动
uv run python scripts/integration_test.py              # 单模型 5 项测试
uv run python scripts/integration_scheduling_test.py   # 双模型调度 7 项测试

# 压测
uv run locust -f benchmark/v2/locust/locustfile_v2.py \
  --host=http://127.0.0.1:9090 --headless --users=10 --run-time=30s
```

## Docker

```bash
docker compose build
docker compose up -d          # dispatcher + 2 mock 引擎 + 自动注册 + 冒烟测试
docker compose logs init      # 确认 "Smoke Test Passed"
docker compose down
```

## 技术栈

- **Runtime**: Python >=3.11
- **Web**: FastAPI + uvicorn
- **Scheduling**: WeightedBalancer (speed + reliability + load 线性权重)
- **Resilience**: CircuitBreaker + Cooldown + Retry/Fallback + HealthChecker
- **Metrics**: prometheus-client + PrometheusRule 告警
- **Tracing**: OpenTelemetry (OTLP / Console)
- **Testing**: pytest + pytest-asyncio (183 用例) + locust (压测)
- **Package**: uv
