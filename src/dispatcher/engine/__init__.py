"""Engine adapters — 规格书 §4 Module 5.

提供 create_adapter_registry() 工厂函数，用于获取所有引擎适配器。
"""

from __future__ import annotations

from src.shared.models import EngineAdapter


def create_adapter_registry() -> dict[str, EngineAdapter]:
    """创建包含所有内置适配器的注册表。

    Returns:
        {"ollama": OllamaAdapter(), "vllm": VLLMAdapter(), "tgi": TGIAdapter()}
    """
    from src.dispatcher.engine.ollama import OllamaAdapter
    from src.dispatcher.engine.vllm import VLLMAdapter
    from src.dispatcher.engine.tgi import TGIAdapter

    return {
        "ollama": OllamaAdapter(),
        "vllm": VLLMAdapter(),
        "tgi": TGIAdapter(),
    }
