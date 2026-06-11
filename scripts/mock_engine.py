"""Mock 推理引擎 — 模拟 Ollama/vLLM/TGI 的 HTTP API。

用于本地开发和集成测试，无需真实 GPU 推理引擎。
支持三种引擎格式、可配置延迟、SSE 流式响应。

使用方式：
    uv run python scripts/mock_engine.py --port 8001
    uv run python scripts/mock_engine.py --port 8001 --latency 0.5 --engine ollama
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock LLM Engine", version="1.0.0")

# ── 全局配置 ───────────────────────────────────────────────
ENGINE_TYPE: str = "ollama"  # "ollama" | "vllm" | "tgi"
SIMULATED_LATENCY: float = 0.1  # 秒
PORT: int = 8001


def maybe_delay(default: float | None = None) -> None:
    """非异步版本的延迟（仅在非 async 上下文中使用）。"""
    delay = default if default is not None else SIMULATED_LATENCY
    if delay > 0:
        time.sleep(delay)


async def maybe_async_delay() -> None:
    """异步延迟，模拟 GPU 推理耗时。"""
    if SIMULATED_LATENCY > 0:
        await asyncio.sleep(SIMULATED_LATENCY)


def generate_mock_response(messages: list[dict], model: str = "mock-model") -> dict[str, Any]:
    """生成模拟的推理响应内容。"""
    user_content = ""
    for msg in messages:
        if msg.get("role") == "user":
            user_content = msg.get("content", "")
            break

    if user_content:
        assistant_content = (
            f"这是对「{user_content[:50]}」的模拟回复。Mock engine 运行在端口 {PORT}，使用 {ENGINE_TYPE} 引擎格式。"
        )
    else:
        assistant_content = "Mock engine 模拟回复 — 没有收到用户消息。"

    prompt_tokens = max(1, sum(len(m.get("content", "")) for m in messages) // 4)
    completion_tokens = max(10, len(assistant_content) // 3)

    return {
        "content": assistant_content,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model": model,
    }


# ── 健康检查 ───────────────────────────────────────────────


@app.get("/health")
async def health_check() -> dict:
    """健康检查端点 — 所有引擎共用。"""
    await maybe_async_delay()
    return {"status": "ok", "engine": ENGINE_TYPE, "port": PORT}


@app.get("/api/tags")
async def ollama_health() -> dict:
    """Ollama 风格的健康检查端点。"""
    await maybe_async_delay()
    return {"models": [{"name": "mock-model", "modified_at": "2026-01-01T00:00:00Z"}]}


# ── Ollama 格式 ────────────────────────────────────────────


@app.post("/api/chat")
async def ollama_chat(request: Request) -> dict:
    """Ollama /api/chat 端点 — 非流式。"""
    body = await request.json()
    await maybe_async_delay()

    model = body.get("model", "mock-model")
    messages = body.get("messages", [])
    is_stream = body.get("stream", False)

    if is_stream:
        # 流式请求返回非流式响应（简单处理）
        pass

    mock = generate_mock_response(messages, model)

    return {
        "model": model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "message": {
            "role": "assistant",
            "content": mock["content"],
        },
        "done": True,
        "total_duration": int(SIMULATED_LATENCY * 1e9),
        "load_duration": 100_000_000,
        "prompt_eval_count": mock["prompt_tokens"],
        "prompt_eval_duration": 50_000_000,
        "eval_count": mock["completion_tokens"],
        "eval_duration": int(SIMULATED_LATENCY * 1e9 * 0.7),
    }


@app.post("/api/generate")
async def ollama_generate(request: Request) -> dict:
    """Ollama /api/generate 端点 — 补全模式。"""
    body = await request.json()
    await maybe_async_delay()

    prompt = body.get("prompt", "")
    model = body.get("model", "mock-model")

    assistant_content = f"这是对「{prompt[:50]}」的模拟补全。Mock engine ({ENGINE_TYPE}) on port {PORT}."

    return {
        "model": model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "response": assistant_content,
        "done": True,
        "total_duration": int(SIMULATED_LATENCY * 1e9),
        "prompt_eval_count": max(1, len(prompt) // 4),
        "eval_count": max(10, len(assistant_content) // 3),
    }


# ── OpenAI/vLLM 格式 ───────────────────────────────────────


@app.post("/v1/chat/completions", response_model=None)
async def openai_chat_completions(request: Request):
    """OpenAI 兼容的 /v1/chat/completions 端点。

    同时被 vLLM 和 TGI（当 TGI 使用 OpenAI 兼容模式时）使用。
    """
    body = await request.json()
    is_stream = body.get("stream", False)

    if is_stream:
        return StreamingResponse(
            _sse_stream(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    await maybe_async_delay()

    model = body.get("model", "mock-model")
    messages = body.get("messages", [])
    mock = generate_mock_response(messages, model)
    request_id = str(uuid.uuid4())

    return {
        "id": f"chatcmpl-{request_id[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": mock["content"],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": mock["prompt_tokens"],
            "completion_tokens": mock["completion_tokens"],
            "total_tokens": mock["prompt_tokens"] + mock["completion_tokens"],
        },
    }


@app.post("/v1/completions", response_model=None)
async def openai_completions(request: Request):
    """OpenAI 兼容的 /v1/completions 端点。"""
    body = await request.json()
    is_stream = body.get("stream", False)

    if is_stream:
        return StreamingResponse(
            _sse_completion_stream(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    await maybe_async_delay()

    prompt = body.get("prompt", "")
    model = body.get("model", "mock-model")
    assistant_content = f"这是对「{prompt[:50]}」的模拟补全。"

    return {
        "id": f"cmpl-{uuid.uuid4().hex[:12]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "text": assistant_content,
                "index": 0,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": max(1, len(prompt) // 4),
            "completion_tokens": max(10, len(assistant_content) // 3),
            "total_tokens": max(1, len(prompt) // 4) + max(10, len(assistant_content) // 3),
        },
    }


async def _sse_stream(body: dict):
    """生成 SSE 流式响应（chat completions 格式）。"""
    model = body.get("model", "mock-model")
    messages = body.get("messages", [])
    mock = generate_mock_response(messages, model)
    content = mock["content"]
    request_id = str(uuid.uuid4())

    # 按字符逐个发送，模拟真实的流式输出
    for i, char in enumerate(content):
        chunk = {
            "id": f"chatcmpl-{request_id[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": char} if i > 0 else {"role": "assistant", "content": char},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.01)  # 模拟 token 间的小延迟

    # 发送结束标记
    final_chunk = {
        "id": f"chatcmpl-{request_id[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": mock["prompt_tokens"],
            "completion_tokens": mock["completion_tokens"],
            "total_tokens": mock["prompt_tokens"] + mock["completion_tokens"],
        },
    }
    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


async def _sse_completion_stream(body: dict):
    """生成 SSE 流式响应（completions 格式）。"""
    prompt = body.get("prompt", "")
    model = body.get("model", "mock-model")
    content = f"这是对「{prompt[:50]}」的模拟补全。"
    request_id = str(uuid.uuid4())

    for i, char in enumerate(content):
        chunk = {
            "id": f"cmpl-{request_id[:12]}",
            "object": "text_completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "text": char,
                    "index": 0,
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.01)

    final_chunk = {
        "id": f"cmpl-{request_id[:12]}",
        "object": "text_completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "text": "",
                "index": 0,
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


# ── TGI 格式 ───────────────────────────────────────────────


@app.post("/generate")
async def tgi_generate(request: Request) -> dict:
    """TGI /generate 端点。"""
    body = await request.json()
    await maybe_async_delay()

    inputs = body.get("inputs", "")

    assistant_content = f"这是对「{inputs[:50]}」的 TGI 模拟生成。"

    return {
        "generated_text": assistant_content,
        "details": {
            "finish_reason": "length",
            "generated_tokens": max(10, len(assistant_content) // 3),
            "prefill": [{"text": inputs, "tokens": [1] * (max(1, len(inputs) // 4))}],
            "tokens": [[1] for _ in range(max(10, len(assistant_content) // 3))],
        },
    }


# ── 健康检查（带故障模拟） ──────────────────────────────────

_fail_count: dict[str, int] = {}
_fail_until_success: dict[str, int] = {}


@app.post("/admin/fail/{count}")
async def set_fail_count(count: int) -> dict:
    """管理端点：设置接下来连续失败的次数（用于测试熔断器）。"""
    _fail_count["remaining"] = count
    return {"status": "ok", "fail_remaining": count, "message": f"接下来 {count} 个请求将返回 500"}


@app.post("/admin/fail-until-success/{count}")
async def set_fail_until_success(count: int) -> dict:
    """管理端点：设置必须连续成功多少次后才恢复正常。"""
    _fail_until_success["remaining"] = count
    _fail_count.clear()
    return {"status": "ok", "required_successes": count, "message": f"需要 {count} 次连续成功后才恢复正常"}


@app.post("/admin/reset")
async def reset_fail() -> dict:
    """管理端点：重置故障模拟状态。"""
    _fail_count.clear()
    _fail_until_success.clear()
    return {"status": "ok", "message": "故障模拟已重置"}


# ── 全局中间件：故障注入 ────────────────────────────────────


@app.middleware("http")
async def fault_injection_middleware(request: Request, call_next):
    """在 /admin 以外的路径注入故障，模拟引擎异常。"""
    path = request.url.path

    # 管理端点不受故障影响
    if path.startswith("/admin"):
        return await call_next(request)

    # 连续失败模式
    if "remaining" in _fail_count and _fail_count["remaining"] > 0:
        _fail_count["remaining"] -= 1
        return JSONResponse(
            status_code=500,
            content={"error": f"Simulated failure (injected). {_fail_count['remaining']} failures remaining."},
        )

    # 需要连续成功后恢复的模式
    if "remaining" in _fail_until_success:
        if _fail_until_success["remaining"] > 0:
            _fail_until_success["remaining"] = 0
            return JSONResponse(
                status_code=500,
                content={"error": "Simulated failure (still in fail mode)."},
            )
        else:
            return JSONResponse(
                status_code=500,
                content={"error": "Simulated failure — need more successes to recover."},
            )

    return await call_next(request)


# ── CLI 入口 ────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock LLM Engine — 模拟推理引擎 HTTP 服务")
    parser.add_argument("--port", type=int, default=8001, help="监听端口（默认: 8001）")
    parser.add_argument("--latency", type=float, default=0.1, help="模拟推理延迟（秒，默认: 0.1）")
    parser.add_argument(
        "--engine", choices=["ollama", "vllm", "tgi"], default="ollama", help="模拟的引擎类型（默认: ollama）"
    )
    args = parser.parse_args()

    global ENGINE_TYPE, SIMULATED_LATENCY, PORT
    ENGINE_TYPE = args.engine
    SIMULATED_LATENCY = args.latency
    PORT = args.port

    print(f"[Mock Engine] 启动 {ENGINE_TYPE} 模拟引擎")
    print(f"[Mock Engine] 监听 0.0.0.0:{PORT}")
    print(f"[Mock Engine] 模拟延迟: {SIMULATED_LATENCY}s")
    print("[Mock Engine] 端点:")
    print(f"  - GET  http://localhost:{PORT}/health")
    if ENGINE_TYPE == "ollama":
        print(f"  - POST http://localhost:{PORT}/api/chat")
        print(f"  - GET  http://localhost:{PORT}/api/tags")
    print(f"  - POST http://localhost:{PORT}/v1/chat/completions")
    print(f"  - POST http://localhost:{PORT}/v1/completions")
    print(f"  - POST http://localhost:{PORT}/generate")
    print(f"  - POST http://localhost:{PORT}/admin/fail/N")
    print(f"  - POST http://localhost:{PORT}/admin/reset")
    print()

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
