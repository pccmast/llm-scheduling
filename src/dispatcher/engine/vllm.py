"""Module 5: VLLMAdapter — 规格书 §4 Module 5.

实现 vLLM 推理引擎的适配器，处理 OpenAI 兼容格式的请求构建和响应解析。
vLLM 完全兼容 OpenAI 格式。
"""

from __future__ import annotations

from src.shared.models import EngineAdapter, InferenceRequest, InferenceResponse, ModelInstance, TokenUsage


class VLLMAdapter(EngineAdapter):
    """vLLM 推理引擎适配器。vLLM 提供 OpenAI 兼容的 API。"""

    @property
    def engine_type(self) -> str:
        return "vllm"

    def build_request(self, instance: ModelInstance, request: InferenceRequest) -> tuple[str, dict, dict]:
        """构建 vLLM /v1/chat/completions 请求（OpenAI 兼容格式）。

        Returns:
            (url, headers, body)
        """
        url = instance.address.rstrip("/") + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        body: dict = {
            "model": request.model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": request.stream,
        }
        return url, headers, body

    def parse_response(self, raw: dict) -> InferenceResponse:
        """解析 OpenAI 兼容响应格式。"""
        try:
            choices = raw.get("choices", [])
            content = ""
            if choices:
                message = choices[0].get("message", {})
                content = message.get("content", "")

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
        return instance.address.rstrip("/") + "/health"
