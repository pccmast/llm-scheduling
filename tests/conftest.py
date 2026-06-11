"""共享测试 fixtures — 用于所有测试模块。

本文件为 LLM 推理调度平台的所有测试提供公共 fixtures。
在 Phase 2 (Tests First) 阶段创建骨架，在 Core 阶段逐步填充实际数据。
"""

from __future__ import annotations

import pytest


# ── 数据模型 fixtures ──────────────────────────────────────

@pytest.fixture
def sample_instance() -> dict:
    """返回一个标准的 ModelInstance 配置字典（用于测试 fixture 创建）。"""
    return {
        "instance_id": "test-instance-1",
        "address": "http://localhost:8001",
        "model": "llama-3",
        "engine_type": "ollama",
        "max_concurrent": 10,
        "capacity_factor": 1.0,
        "metadata": {},
    }


@pytest.fixture
def sample_request() -> dict:
    """返回一个标准的 InferenceRequest 配置字典。"""
    return {
        "request_id": "req-001",
        "model": "llama-3",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
        ],
        "max_tokens": 1024,
        "temperature": 0.7,
        "stream": False,
        "priority": 5,
    }


@pytest.fixture
def sample_response() -> dict:
    """返回一个标准的 OpenAI 兼容响应字典（模拟上游引擎返回）。"""
    return {
        "id": "chatcmpl-abc123",
        "object": "chat.completion",
        "created": 1718000000,
        "model": "llama-3",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "I'm doing well, thank you!",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 8,
            "total_tokens": 28,
        },
    }


# ── 实例列表 fixtures ──────────────────────────────────────

@pytest.fixture
def healthy_instances() -> list[dict]:
    """返回多个健康实例的配置字典列表。"""
    return [
        {
            "instance_id": "i1",
            "address": "http://gpu-1:8000",
            "model": "llama-3",
            "engine_type": "ollama",
            "max_concurrent": 10,
            "capacity_factor": 1.0,
        },
        {
            "instance_id": "i2",
            "address": "http://gpu-2:8000",
            "model": "llama-3",
            "engine_type": "vllm",
            "max_concurrent": 8,
            "capacity_factor": 2.0,
        },
        {
            "instance_id": "i3",
            "address": "http://gpu-3:8000",
            "model": "llama-3",
            "engine_type": "tgi",
            "max_concurrent": 6,
            "capacity_factor": 1.5,
        },
    ]
