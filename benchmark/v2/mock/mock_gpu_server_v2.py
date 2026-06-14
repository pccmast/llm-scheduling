"""Mock GPU 推理引擎 v2 - 基于真实 GPU 延迟分布校准。

支持: numpy lognormal 延迟分布、长尾延迟注入、动态调参、并发限制。
使用方式:
    uv run python benchmark/v2/mock/mock_gpu_server_v2.py --port=8001
    uv run python benchmark/v2/mock/mock_gpu_server_v2.py --port=8002 --mean-ms=5800 --sigma=0.05
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock GPU Engine v2", version="2.0.0")

# -- 全局配置（基于真实 GPU 实测数据: minicpm-v-4.6, GTX1650, 1.92G) --
_CONFIG: dict[str, Any] = {
    "mean_ms": 3000.0,          # 短请求均值 (10-50 tokens ~ 2.8s)
    "sigma": 0.08,             # 对数标准差 (实测波动 ~5-10%)
    "long_tail_prob": 0.02,    # 长尾概率 (2%)
    "long_tail_mult": 3.0,     # 长尾倍数 (3x mean ~ 9s, 模拟 GPU swap)
    "max_concurrent": 4,       # 并发上限 (匹配 LM Studio num_parallel_slots=4)
    "engine_type": "ollama",
    "port": 8001,
}

_SEM: asyncio.Semaphore = asyncio.Semaphore(_CONFIG["max_concurrent"])


def _load_config(path: str | None = None) -> None:
    """从 YAML 加载配置，命令行参数覆盖。"""
    if path is None:
        path = Path(__file__).parent / "mock_config.yaml"
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data:
            _CONFIG.update(data)


async def _generate_delay() -> float:
    """基于 lognormal 分布生成延迟（毫秒）。
    
    2% 概率注入长尾延迟，模拟 GPU OOM/swap。
    """
    mean_ms = _CONFIG["mean_ms"]
    sigma = _CONFIG["sigma"]
    # lognormal: mean of log-space = ln(mean_ms) - sigma^2/2
    mu = np.log(mean_ms) - sigma**2 / 2.0
    delay_ms = float(np.random.lognormal(mu, sigma))
    if random.random() < _CONFIG["long_tail_prob"]:
        delay_ms *= _CONFIG["long_tail_mult"]
    return max(50.0, delay_ms)  # 最小 50ms


def _build_response(body: dict, latency_ms: float) -> dict[str, Any]:
    """构建模拟响应。"""
    prompt = ""
    for msg in body.get("messages", []):
        if msg.get("role") == "user":
            prompt = msg.get("content", "")
            break
    max_tokens = body.get("max_tokens", 50)
    model = body.get("model", "minicpm-v-4.6")
    
    content = f"Mock reply to: {prompt[:40]}... (latency={latency_ms:.0f}ms, port={_CONFIG['port']})"
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(content) // 4)
    
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@app.get("/health")
async def health() -> dict:
    """健康检查。"""
    return {"status": "ok", "engine": _CONFIG["engine_type"], "port": _CONFIG["port"]}


@app.post("/api/tags")
async def ollama_tags() -> dict:
    """Ollama 风格模型列表。"""
    return {"models": [{"name": "minicpm-v-4.6", "modified_at": "2026-01-01T00:00:00Z"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    """OpenAI 兼容 chat completions。"""
    body = await request.json()
    is_stream = body.get("stream", False)
    if is_stream:
        return StreamingResponse(_sse_stream(body), media_type="text/event-stream")

    delay_ms = await _generate_delay()
    async with _SEM:
        await asyncio.sleep(delay_ms / 1000.0)
    
    resp = _build_response(body, delay_ms)
    return JSONResponse(resp, headers={"X-Mock-Latency-Ms": f"{delay_ms:.1f}"})


@app.post("/v1/completions")
async def completions(request: Request) -> JSONResponse:
    """OpenAI 兼容 completions。"""
    body = await request.json()
    delay_ms = await _generate_delay()
    async with _SEM:
        await asyncio.sleep(delay_ms / 1000.0)
    
    prompt = body.get("prompt", "")
    content = f"Mock completion: {prompt[:40]}... (latency={delay_ms:.0f}ms)"
    return JSONResponse({
        "id": f"cmpl-{uuid.uuid4().hex[:12]}",
        "object": "text_completion",
        "created": int(time.time()),
        "choices": [{"text": content, "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": max(1, len(prompt)//4), "completion_tokens": max(1, len(content)//4)},
    }, headers={"X-Mock-Latency-Ms": f"{delay_ms:.1f}"})


@app.post("/admin/config")
async def update_config(request: Request) -> dict:
    """运行时动态修改延迟参数。"""
    body = await request.json()
    for key in ("mean_ms", "sigma", "long_tail_prob", "long_tail_mult", "max_concurrent"):
        if key in body:
            _CONFIG[key] = body[key]
    return {"status": "ok", "config": {k: _CONFIG[k] for k in ("mean_ms", "sigma", "long_tail_prob", "long_tail_mult", "max_concurrent")}}


@app.get("/admin/config")
async def get_config() -> dict:
    """查看当前配置。"""
    return {"status": "ok", "config": {k: _CONFIG[k] for k in ("mean_ms", "sigma", "long_tail_prob", "long_tail_mult", "max_concurrent")}}


async def _sse_stream(body: dict):
    """SSE 流式响应。"""
    delay_ms = await _generate_delay()
    async with _SEM:
        await asyncio.sleep(delay_ms / 1000.0)
    
    content = "Mock stream response"
    model = body.get("model", "minicpm-v-4.6")
    rid = uuid.uuid4().hex[:12]
    for char in content:
        chunk = {
            "id": f"chatcmpl-{rid}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"content": char}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        await asyncio.sleep(0.01)
    
    final = {"id": f"chatcmpl-{rid}", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock GPU Engine v2")
    parser.add_argument("--port", type=int, default=8001, help="监听端口")
    parser.add_argument("--mean-ms", type=float, default=None, help="延迟均值 (ms)")
    parser.add_argument("--sigma", type=float, default=None, help="对数标准差")
    parser.add_argument("--config", type=str, default=None, help="YAML 配置文件路径")
    args = parser.parse_args()
    
    _load_config(args.config)
    if args.mean_ms is not None:
        _CONFIG["mean_ms"] = args.mean_ms
    if args.sigma is not None:
        _CONFIG["sigma"] = args.sigma
    _CONFIG["port"] = args.port
    
    # 重新初始化信号量
    global _SEM
    _SEM = asyncio.Semaphore(_CONFIG["max_concurrent"])
    
    print(f"[Mock GPU v2] port={args.port}, mean={_CONFIG['mean_ms']:.0f}ms, sigma={_CONFIG['sigma']}, "
          f"long_tail={_CONFIG['long_tail_prob']*100:.0f}%x{_CONFIG['long_tail_mult']:.0f}x, "
          f"max_concurrent={_CONFIG['max_concurrent']}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    np.random.seed(42)  # 可复现
    random.seed(42)
    main()
