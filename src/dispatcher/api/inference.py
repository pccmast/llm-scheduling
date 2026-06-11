"""Module 12: 推理入口端点 — 规格书 §4 Module 12.

POST /v1/chat/completions — OpenAI 兼容的聊天补全
POST /v1/completions — OpenAI 兼容的文本补全
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.shared.models import (
    InferenceRequest,
    NoAvailableInstanceError,
    QueueFullError,
)

inference_router = APIRouter()


@inference_router.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request):
    """OpenAI 兼容的聊天补全端点。

    流程：
    1. 从 request body 解析为 InferenceRequest（生成 request_id）
    2. 如果 stream=True，调用 proxy.forward_stream() 返回 SSE 流
    3. 否则调用 proxy.forward() 返回非流式响应

    Raises:
        HTTPException(503): 无可用实例（NoAvailableInstanceError）。
        HTTPException(503): 队列已满（QueueFullError）。
        HTTPException(422): 请求格式错误。
    """
    proxy = request.app.state.proxy

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body")

    try:
        inference_req = _build_inference_request(body)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid request format: {e}")

    try:
        if inference_req.stream:
            # 流式响应
            stream_gen = proxy.forward_stream(inference_req)
            return StreamingResponse(
                stream_gen,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
        else:
            # 非流式响应
            resp = await proxy.forward(inference_req)
            return _to_openai_format(resp, body.get("model", "unknown"))

    except NoAvailableInstanceError:
        raise HTTPException(status_code=503, detail="No available instance for the requested model")
    except QueueFullError:
        raise HTTPException(status_code=503, detail="Request queue is full")


@inference_router.post("/v1/completions", response_model=None)
async def completions(request: Request):
    """OpenAI 兼容的文本补全端点。

    将 completion 格式转换为内部 chat 格式后走相同流程。
    """
    proxy = request.app.state.proxy

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body")

    # 将 completion prompt 转为 chat messages 格式
    prompt = body.get("prompt", "")
    messages = [{"role": "user", "content": prompt}]

    try:
        inference_req = InferenceRequest(
            request_id=str(uuid.uuid4()),
            model=body.get("model", ""),
            messages=messages,
            max_tokens=body.get("max_tokens", 1024),
            temperature=body.get("temperature", 0.7),
            stream=body.get("stream", False),
            priority=body.get("priority", 5),
            raw_body=body,
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid request format: {e}")

    try:
        resp = await proxy.forward(inference_req)
        return _to_completion_format(resp, body.get("model", "unknown"))
    except NoAvailableInstanceError:
        raise HTTPException(status_code=503, detail="No available instance for the requested model")
    except QueueFullError:
        raise HTTPException(status_code=503, detail="Request queue is full")


def _build_inference_request(body: dict) -> InferenceRequest:
    """从 OpenAI 格式请求体构建 InferenceRequest。"""
    return InferenceRequest(
        request_id=str(uuid.uuid4()),
        model=body.get("model", ""),
        messages=body.get("messages", []),
        max_tokens=body.get("max_tokens", 1024),
        temperature=body.get("temperature", 0.7),
        stream=body.get("stream", False),
        priority=body.get("priority", 5),
        raw_body=body,
    )


def _to_openai_format(resp, model: str) -> dict:
    """将 InferenceResponse 转换为 OpenAI chat completions 格式。"""
    return {
        "id": f"chatcmpl-{resp.request_id[:12]}",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": resp.content or "",
                },
                "finish_reason": "stop" if resp.status == "ok" else "error",
            }
        ],
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            "total_tokens": resp.usage.total_tokens if resp.usage else 0,
        },
    }


def _to_completion_format(resp, model: str) -> dict:
    """将 InferenceResponse 转换为 OpenAI completions 格式。"""
    return {
        "id": f"cmpl-{resp.request_id[:12]}",
        "object": "text_completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "text": resp.content or "",
                "index": 0,
                "finish_reason": "stop" if resp.status == "ok" else "error",
            }
        ],
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            "total_tokens": resp.usage.total_tokens if resp.usage else 0,
        },
    }
