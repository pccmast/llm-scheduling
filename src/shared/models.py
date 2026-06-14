"""Module 1: Shared Models & Config — 规格书 §4 Module 1.

定义项目所有共享的 Pydantic 数据模型、异常类型和核心抽象基类。
本模块只包含数据结构定义，不包含任何业务逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field

# ── 状态枚举 ──────────────────────────────────────────────


class InstanceStatus(str, Enum):
    """推理引擎实例的状态枚举。"""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"  # 缩容中，不再接收新请求


# ── 核心数据模型 ───────────────────────────────────────────


class TokenUsage(BaseModel):
    """token 使用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @property
    def total(self) -> int:
        """返回 prompt_tokens + completion_tokens。"""
        return self.prompt_tokens + self.completion_tokens


class ModelInstance(BaseModel):
    """一个推理引擎实例的完整描述。"""

    instance_id: str
    address: str  # "http://gpu-server-1:8000"
    model: str  # "llama-3-8b" | "gpt-4o-mini"
    engine_type: str  # "ollama" | "vllm" | "tgi"
    max_concurrent: int = 10  # 最大并发请求数
    capacity_factor: float = 1.0  # 容量因子（大 GPU > 1.0）
    status: InstanceStatus = InstanceStatus.HEALTHY
    metadata: dict[str, str] = Field(default_factory=dict)


class InferenceRequest(BaseModel):
    """统一的推理请求格式。"""

    request_id: str  # 唯一请求 ID
    model: str  # 目标模型
    messages: list[dict]  # OpenAI 格式消息列表
    max_tokens: int = 1024
    temperature: float = 0.7
    stream: bool = False
    priority: int = 5  # 1 (最高) ~ 10 (最低)
    raw_body: dict = Field(default_factory=dict)

    @property
    def estimated_input_tokens(self) -> int:
        """估算输入 token 数（字符数 / 4），最小返回 1。"""
        total_chars = sum(len(m.get("content", "")) for m in self.messages)
        return max(1, total_chars // 4)

    @property
    def estimated_weight(self) -> float:
        """估算请求的计算重量。

        输出 token 因自回归生成，计算开销约为输入的 2 倍。
        """
        return self.estimated_input_tokens + self.max_tokens * 2.0


class InferenceResponse(BaseModel):
    """统一的推理响应格式。"""

    request_id: str
    instance_id: str  # 处理该请求的实例 ID
    content: str | None = None
    tool_calls: list[dict] | None = None
    usage: TokenUsage | None = None
    latency_ms: float = 0.0
    ttft_ms: float = 0.0  # 首 token 延迟（流式）
    status: str = "ok"  # "ok" | "error" | "timeout"
    error_message: str | None = None


class ScaleDecision(BaseModel):
    """自动扩缩容的决策输出。"""

    action: str  # "scale_up" | "scale_down" | "none"
    count: int = 0  # 建议增减的实例数
    reason: str = ""


class ScaleConfig(BaseModel):
    """自动扩缩容的配置参数。

    load 阈值使用 balancer.get_load() 的原始分数。
    以 WeightedBalancer 为例：典型请求 estimated_weight ≈ 2000，
    满载实例负载 ≈ 10000-20000。
    """

    scale_up_queue_threshold: int = 10
    scale_up_load_threshold: float = 8000.0
    scale_down_idle_seconds: int = 300
    scale_down_load_threshold: float = 500.0
    min_instances: int = 1
    max_instances: int = 10
    cooldown_seconds: int = 60


class QueueItem(BaseModel):
    """优先级队列中的元素。"""

    priority: int
    enqueue_time: float  # time.monotonic() 时间戳
    request: InferenceRequest

    model_config = {"frozen": True}

    def __lt__(self, other: QueueItem) -> bool:
        """优先级数值越小越优先；优先级相同时先进先出（FIFO）。"""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.enqueue_time < other.enqueue_time


class HealthCheckConfig(BaseModel):
    """健康检查配置。"""

    interval_seconds: float = 10.0
    timeout_seconds: float = 5.0
    unhealthy_threshold: int = 3  # 连续失败 N 次标记为 unhealthy
    healthy_threshold: int = 1  # 成功 1 次恢复为 healthy


# ── 异常类型 ───────────────────────────────────────────────


class NoAvailableInstanceError(Exception):
    """没有可用的健康实例处理请求时抛出。"""

    def __init__(self, model: str) -> None:
        super().__init__(f"No available instance for model: {model}")
        self.model = model


class QueueFullError(Exception):
    """请求队列已满时抛出。"""

    def __init__(self) -> None:
        super().__init__("Request queue is full")


class CircuitOpenError(Exception):
    """实例熔断器处于 OPEN 状态，拒绝请求时抛出。"""

    def __init__(self, instance_id: str) -> None:
        super().__init__(f"Circuit breaker is OPEN for instance: {instance_id}")
        self.instance_id = instance_id


class MissingAdapterError(Exception):
    """未注册对应引擎类型的 EngineAdapter 时抛出（配置错误）。"""

    def __init__(self, engine_type: str) -> None:
        super().__init__(f"No EngineAdapter registered for engine_type: {engine_type}")
        self.engine_type = engine_type


# ── 核心抽象基类（规格书 §2.2）─────────────────────────────


class LoadBalancer(ABC):
    """负载均衡策略的抽象基类。"""

    @abstractmethod
    def select(self, candidates: list[ModelInstance], request: InferenceRequest) -> ModelInstance:
        """从候选实例中选择一个来处理当前请求。

        Args:
            candidates: 非空的健康实例列表（调用方保证 len > 0）。
            request: 待调度的推理请求。

        Returns:
            被选中的 ModelInstance。

        Raises:
            ValueError: candidates 为空时抛出。
        """
        ...

    def on_request_start(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        """请求开始时的回调（用于更新实例负载计数）。

        Args:
            instance_id: 实例 ID。
            request: 可选的推理请求。WeightedBalancer 使用 request.estimated_weight
                     更新负载分数；其他策略可忽略此参数。

        默认空操作。子类可覆写以实现负载跟踪。
        """

    def on_request_end(self, instance_id: str, request: InferenceRequest | None = None) -> None:
        """请求结束时的回调。

        Args:
            instance_id: 实例 ID。
            request: 可选的推理请求。WeightedBalancer 使用 request.estimated_weight
                     减少负载分数；其他策略可忽略此参数。

        默认空操作。子类可覆写以实现负载跟踪。
        """

    def get_load(self, instance_id: str) -> float:
        """返回指定实例的当前负载分数。默认返回 0.0。

        子类（WeightedBalancer / LeastConnectionsBalancer）可覆写以暴露实际负载值，
        供 AutoScaler 等组件使用。
        """
        return 0.0


class EngineAdapter(ABC):
    """推理引擎适配器——抹平不同引擎的 API 差异。"""

    @property
    @abstractmethod
    def engine_type(self) -> str: ...

    @abstractmethod
    def build_request(self, instance: ModelInstance, request: InferenceRequest) -> tuple[str, dict, dict]:
        """构建转发请求。

        Returns:
            (url, headers, json_body) 三元组。
        """
        ...

    @abstractmethod
    def parse_response(self, raw: dict) -> InferenceResponse: ...

    @abstractmethod
    def health_endpoint(self, instance: ModelInstance) -> str: ...


class MetricsRecorder(ABC):
    """指标记录的抽象基类——解耦指标采集和指标存储。"""

    @abstractmethod
    def record_request(
        self,
        instance_id: str,
        latency_ms: float,
        ttft_ms: float,
        tokens: TokenUsage,
        success: bool,
        queued_ms: float = 0,
    ) -> None: ...

    @abstractmethod
    def record_error(self, instance_id: str, error_type: str) -> None: ...

    @abstractmethod
    def record_queue_depth(self, depth: int) -> None: ...

    @abstractmethod
    def get_summary(self) -> dict: ...


class CircuitBreaker(ABC):
    """熔断器的抽象基类——保护系统免受级联故障。"""

    @abstractmethod
    def allow_request(self) -> bool:
        """判断当前是否允许发送请求。

        Returns:
            True 表示允许（CLOSED 或 HALF_OPEN 状态），
            False 表示拒绝（OPEN 状态）。
        """
        ...

    @abstractmethod
    def record_success(self) -> None:
        """记录一次成功的请求。"""
        ...

    @abstractmethod
    def record_failure(self) -> None:
        """记录一次失败的请求。"""
        ...

    @property
    @abstractmethod
    def state(self) -> str:
        """返回当前状态："closed" | "open" | "half_open"。"""
        ...
