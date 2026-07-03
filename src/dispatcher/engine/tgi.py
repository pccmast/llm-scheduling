"""Module 5: TGIAdapter — 规格书 §4 Module 5.

实现 TGI (Text Generation Inference) 引擎的适配器。
TGI 使用 /generate 端点，需要将 messages 拼接为单个 prompt 字符串。

通过环境变量 BACKEND_API_KEY 可配置后端鉴权密钥。
"""

from __future__ import annotations

from src.shared.models import EngineAdapter, InferenceRequest, InferenceResponse, ModelInstance, TokenUsage


class TGIAdapter(EngineAdapter):
    """TGI 推理引擎适配器。"""

    @property
    def engine_type(self) -> str:
        return "tgi"

    def build_request(self, instance: ModelInstance, request: InferenceRequest) -> tuple[str, dict, dict]:
        """构建 TGI /generate 请求。

        将 messages 列表拼接为带角色标记的 prompt 字符串，
        格式: <|system|>...<|user|>...<|assistant|>...（ChatML 风格）。
        """
        url = instance.address.rstrip("/") + "/generate"
        headers: dict = {"Content-Type": "application/json"}

        # 后端 API Key（优先 instance.api_key, fallback 到全局 BACKEND_API_KEY）
        api_key = instance.api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # 拼接 messages 为 prompt，保留角色信息
        parts: list[str] = []
        for m in request.messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                parts.append(f"<|system|>\n{content}")
            elif role == "assistant":
                parts.append(f"<|assistant|>\n{content}")
            else:
                parts.append(f"<|user|>\n{content}")
        prompt = "\n".join(parts) + "\n<|assistant|>\n"

        body: dict = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }
        return url, headers, body

    def parse_response(self, raw: dict) -> InferenceResponse:
        """解析 TGI 响应格式。"""
        try:
            content = raw.get("generated_text", "")
            details = raw.get("details", {})
            generated_tokens = details.get("generated_tokens", 0)
            # 估算 prompt tokens（TGI 不直接返回）
            prefill = details.get("prefill", [{}])
            prompt_tokens = sum(len(p.get("tokens", [])) for p in prefill)

            usage = TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=generated_tokens,
                total_tokens=prompt_tokens + generated_tokens,
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
                error_message="Failed to parse TGI response",
            )

    def health_endpoint(self, instance: ModelInstance) -> str:
        return instance.address.rstrip("/") + "/health"
