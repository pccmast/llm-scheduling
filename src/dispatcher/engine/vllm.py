"""Module 5: VLLMAdapter — 规格书 §4 Module 5.

实现 vLLM 推理引擎的适配器，处理 OpenAI 兼容格式的请求构建和响应解析。
vLLM 完全兼容 OpenAI 格式。同时支持 LM Studio 等 OpenAI 兼容后端。

通过环境变量 BACKEND_API_KEY 可配置后端鉴权密钥（如 LM Studio 需要）。
"""

from __future__ import annotations

import os

from src.shared.models import EngineAdapter, InferenceRequest, InferenceResponse, ModelInstance, TokenUsage


class VLLMAdapter(EngineAdapter):
    """vLLM 推理引擎适配器。vLLM 提供 OpenAI 兼容的 API。

    同时兼容 LM Studio 等任意 OpenAI 格式后端。
    设置 BACKEND_API_KEY 环境变量后自动携带 Authorization header。
    """

    @property
    def engine_type(self) -> str:
        return "vllm"

    def build_request(self, instance: ModelInstance, request: InferenceRequest) -> tuple[str, dict, dict]:
        """构建 vLLM /v1/chat/completions 请求（OpenAI 兼容格式）。

        Returns:
            (url, headers, body)
        """
        url = instance.address.rstrip("/") + "/v1/chat/completions"
        headers: dict = {"Content-Type": "application/json"}

        # 后端 API Key（如 LM Studio 需要 sk-lm-xxx）
        api_key = os.environ.get("BACKEND_API_KEY", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        body: dict = {
            "model": request.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": request.stream,
        }
        return url, headers, body

    def parse_response(self, raw: dict) -> InferenceResponse:
        """解析 OpenAI 兼容响应格式。

        兼容 thinking 模型 (DeepSeek-R1, MiniCPM, Qwen3 等):
        - content 非空 → 直接返回
        - content 空 + reasoning_content 非空 → 返回 reasoning 内容
        - 两者都有 → 合并 (reasoning 在前, content 在后)
        """
        try:
            choices = raw.get("choices", [])
            content = ""
            if choices:
                message = choices[0].get("message", {})
                content = message.get("content", "") or ""
                reasoning = message.get("reasoning_content", "") or ""
                # thinking 模型：content 为空时用 reasoning_content
                if not content.strip() and reasoning:
                    content = reasoning
                elif reasoning:
                    content = f"[reasoning]\n{reasoning}\n\n[answer]\n{content}"

            usage_data = raw.get("usage", {})
            usage = TokenUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

            return InferenceResponse(
                request_id="",
                instance_id="",
                content=content,
                usage=usage,
                status="ok",
            )
        except Exception:
            return InferenceResponse(
                request_id="",
                instance_id="",
                status="error",
                error_message="Failed to parse vLLM response",
            )

    def health_endpoint(self, instance: ModelInstance) -> str:
        """返回 OpenAI 兼容的 /v1/models 端点作为健康检查。

        LM Studio、vLLM 等 OpenAI 兼容后端都原生支持此端点。
        无需 /health（LM Studio 没有该端点会打 ERROR 日志）。
        """
        return instance.address.rstrip("/") + "/v1/models"
