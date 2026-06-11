"""Module 5: OllamaAdapter — 规格书 §4 Module 5.

实现 Ollama 推理引擎的适配器，处理 Ollama /api/chat 格式的请求构建和响应解析。
"""

from __future__ import annotations

from src.shared.models import EngineAdapter, InferenceRequest, InferenceResponse, ModelInstance, TokenUsage


class OllamaAdapter(EngineAdapter):
    """Ollama 推理引擎适配器。

    处理 Ollama 专有格式的请求构建和响应解析。
    """

    @property
    def engine_type(self) -> str:
        return "ollama"

    def build_request(self, instance: ModelInstance, request: InferenceRequest) -> tuple[str, dict, dict]:
        """构建 Ollama /api/chat 请求。

        Precondition:
            instance.engine_type == "ollama"。

        Returns:
            (url, headers, body) 其中：
            - url = instance.address + "/api/chat"
            - headers = {"Content-Type": "application/json"}
            - body = {"model": request.model, "messages": request.messages,
                      "stream": request.stream, "options": {"temperature": ...}}
        """
        url = instance.address.rstrip("/") + "/api/chat"
        headers = {"Content-Type": "application/json"}
        body: dict = {
            "model": request.model,
            "messages": request.messages,
            "stream": request.stream,
            "options": {"temperature": request.temperature},
        }
        if request.max_tokens:
            body["options"]["num_predict"] = request.max_tokens
        return url, headers, body

    def parse_response(self, raw: dict) -> InferenceResponse:
        """解析 Ollama 响应格式。

        Ollama 响应格式：{"message": {"role": "assistant", "content": "..."},
                          "eval_count": N, "prompt_eval_count": M}

        Postcondition:
            返回的 InferenceResponse 包含正确提取的 content 和 usage。
            如果 raw 格式异常，返回 InferenceResponse(status="error")。
        """
        try:
            message = raw.get("message", {})
            content = message.get("content", "")

            prompt_tokens = raw.get("prompt_eval_count", 0)
            completion_tokens = raw.get("eval_count", 0)

            usage = TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            )

            return InferenceResponse(
                request_id="",  # 由 RoutingProxy 填充
                instance_id="",  # 由 RoutingProxy 填充
                content=content,
                usage=usage,
                status="ok",
            )
        except Exception:
            return InferenceResponse(
                request_id="",
                instance_id="",
                status="error",
                error_message="Failed to parse Ollama response",
            )

    def health_endpoint(self, instance: ModelInstance) -> str:
        """返回 instance.address + "/api/tags"。

        Ollama 的 /api/tags 端点返回模型列表，可作为健康检查使用。
        """
        return instance.address.rstrip("/") + "/api/tags"
