"""Engine adapters — 规格书 §4 Module 5.

提供 create_adapter_registry() 工厂函数，用于获取所有引擎适配器。
"""

from __future__ import annotations

from src.shared.models import EngineAdapter


def create_adapter_registry() -> dict[str, EngineAdapter]:
    """创建包含所有内置适配器的注册表。

    Sprint 1 仅包含 OllamaAdapter；
    Sprint 3 将追加 VLLMAdapter 和 TGIAdapter。

    Returns:
        {"ollama": OllamaAdapter()}
    """
    from src.dispatcher.engine.ollama import OllamaAdapter

    return {"ollama": OllamaAdapter()}
