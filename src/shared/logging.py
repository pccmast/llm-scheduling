"""Module 1: 日志配置 — 规格书 §4 Module 1.

基于 structlog 的结构化日志配置。
"""

from __future__ import annotations

import logging
import sys

import structlog


def setup_logging(log_level: str = "info") -> None:
    """初始化 structlog 结构化日志。

    配置标准日志输出到 stdout，格式化为可读的键值对。

    Args:
        log_level: 日志级别（"debug" | "info" | "warning" | "error"）。
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 同时配置标准 logging（供第三方库使用）
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取一个 structlog logger 实例。

    Args:
        name: logger 名称（通常使用 __name__）。

    Returns:
        配置好的 structlog BoundLogger。
    """
    return structlog.get_logger(name or __name__)
