"""Module 1: 配置加载 — 规格书 §4 Module 1.

通过 Pydantic Settings 加载调度器全局配置，支持 YAML 文件和环境变量覆盖。
配置优先级：环境变量 DISPATCHER_* > YAML 文件 > 默认值。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic_settings import BaseSettings


class DispatcherSettings(BaseSettings):
    """调度器全局配置。

    通过环境变量 DISPATCHER_<FIELD> 可覆盖 YAML 和默认值。
    例如：DISPATCHER_PORT=8080 覆盖 port 配置。
    """

    model_config = {"env_prefix": "DISPATCHER_"}

    host: str = "0.0.0.0"
    port: int = 9090
    config_dir: str = "./config"
    upstream_timeout: float = 120.0
    log_level: str = "info"


def load_config(config_path: str | None = None) -> DispatcherSettings:
    """加载调度器配置。

    加载策略（优先级从低到高）：
    1. 代码中的默认值
    2. YAML 配置文件（dispatcher 段）
    3. 环境变量（DISPATCHER_ 前缀）

    Args:
        config_path: YAML 配置文件路径。None 时使用默认路径 "./config/default.yaml"。

    Returns:
        DispatcherSettings 实例。配置缺失时使用默认值。

    Raises:
        pydantic.ValidationError: 配置格式错误时抛出。
    """
    yaml_values: dict[str, object] = {}  # type: ignore[var-annotated]

    yaml_path = Path(config_path) if config_path else Path("./config/default.yaml")
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data and isinstance(data, dict):
            dispatcher_cfg = data.get("dispatcher", {})
            if isinstance(dispatcher_cfg, dict):
                for key in ("host", "port", "upstream_timeout", "log_level", "config_dir"):
                    if key in dispatcher_cfg:
                        yaml_values[key] = dispatcher_cfg[key]

    # DispatcherSettings 在实例化时会自动读取环境变量（DISPATCHER_ 前缀），
    # 环境变量的优先级最高
    return DispatcherSettings(**yaml_values)  # type: ignore[arg-type]


def load_yaml_config(config_path: str | None = None) -> dict:
    """加载完整的 YAML 配置文件（包含所有模块的配置段）。

    Args:
        config_path: YAML 配置文件路径。None 时使用默认路径。

    Returns:
        完整的配置字典。文件不存在时返回空字典。
    """
    yaml_path = Path(config_path) if config_path else Path("./config/default.yaml")
    if not yaml_path.exists():
        return {}
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}
