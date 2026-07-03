"""LM Studio 实例注册脚本 — 自动发现已加载模型并精确注册到调度器。

用法: uv run python scripts/setup_lmstudio.py

注册规范:
  - model 字段使用 LM Studio 返回的精确模型名, 不使用 "*" 通配符
  - 同一端口可注册多个实例 (每个模型一个), 引擎类型使用 vllm
  - 注册后调度器按 model 字段精确路由请求到对应 GPU 副本

与 README 的 curl 方式等价, 但本脚本自动处理了模型发现。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any

# ── 配置 ──────────────────────────────────────────────────────
LM_STUDIO_HOST = os.environ.get("LM_STUDIO_HOST", "127.0.0.1")
LM_STUDIO_PORT = int(os.environ.get("LM_STUDIO_PORT", "14344"))
DISPATCHER_BASE = os.environ.get("DISPATCHER_URL", "http://127.0.0.1:9090")
AUTH_HEADER = {"Authorization": f"Bearer {os.environ.get('BACKEND_API_KEY', '')}"}  # noqa: S105

LM_STUDIO_BASE = f"http://{LM_STUDIO_HOST}:{LM_STUDIO_PORT}"
INSTANCE_PREFIX = "lm-"  # 实例 ID 前缀 (lm-qwen, lm-cpm, ...)


# ════════════════════════════════════════════════════════════════
# 工具
# ════════════════════════════════════════════════════════════════


def api_get(path: str, base: str = LM_STUDIO_BASE, extra_headers: dict | None = None) -> Any:
    """向指定服务发送 GET 请求。"""
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(f"{base}{path}", headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def api_post(path: str, body: dict, base: str = DISPATCHER_BASE) -> tuple[int, dict]:
    """向调度器发送 POST 请求。"""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def api_delete(path: str, base: str = DISPATCHER_BASE) -> int:
    """向调度器发送 DELETE 请求。"""
    req = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


# ════════════════════════════════════════════════════════════════
# 模型发现
# ════════════════════════════════════════════════════════════════


def discover_models() -> list[str]:
    """从 LM Studio 获取已加载的模型列表。"""
    print(f"[发现] 查询 LM Studio 模型... ({LM_STUDIO_BASE}/v1/models)")
    try:
        data = api_get("/v1/models", extra_headers=AUTH_HEADER)
        models = [m["id"] for m in data.get("data", [])]
        print(f"[发现] 找到 {len(models)} 个模型: {models}")
        return models
    except Exception as e:
        print(f"[错误] 无法连接 LM Studio: {e}")
        sys.exit(1)


# ════════════════════════════════════════════════════════════════
# 实例注册
# ════════════════════════════════════════════════════════════════


def model_to_instance_id(model: str) -> str:
    """将模型名映射为实例 ID。

    规则: 取模型名最后一段, 将 / 和 : 替换为 -。
    示例: qwen/qwen3-1.7b → lm-qwen-qwen3-1.7b
          qwen/qwen3-1.7b:2 → lm-qwen-qwen3-1.7b-2
    """
    # 取最后一段 (qwen3-1.7b 或 qwen3-1.7b:2)
    parts = model.replace("/", "-").replace(":", "-").split("-")
    short = "-".join(parts[-3:]) if len(parts) > 3 else "-".join(parts)
    return f"{INSTANCE_PREFIX}{short}"


def setup_instances() -> None:
    """清理旧实例并注册新发现的模型。"""
    models = discover_models()

    if not models:
        print("[警告] 未发现任何模型, 退出。")
        return

    # ── 1. 列出并注销旧实例 ──
    print("\n[清理] 注销旧实例...")
    try:
        existing = api_get("/admin/instances", base=DISPATCHER_BASE)
        for inst in existing:
            iid = inst.get("instance_id", "")
            code = api_delete(f"/admin/instances/{iid}")
            status = "OK" if code == 200 else f"ERR({code})"
            print(f"  注销 {iid}: {status}")
    except Exception as e:
        print(f"  (无旧实例: {e})")

    # ── 2. 注册每个模型为独立实例 ──
    print(f"\n[注册] 向调度器注册 {len(models)} 个实例...")
    registered = 0
    for model in models:
        iid = model_to_instance_id(model)
        body = {
            "instance_id": iid,
            "address": LM_STUDIO_BASE,
            "model": model,       # ← 精确模型名, 不是 "*"
            "engine_type": "vllm",  # LM Studio 兼容 OpenAI 格式
        }
        code, data = api_post("/admin/instances", body)
        if code == 200:
            print(f"  ✅ {iid:>25s} → model={model}")
            registered += 1
        elif code == 409:
            print(f"  ⚠️  {iid:>25s} 已存在 (跳过)")
            registered += 1
        else:
            print(f"  ❌ {iid:>25s} 失败: {code} {data.get('detail','')})")

    # ── 3. 验证 ──
    print(f"\n[验证] 当前注册表:")
    try:
        instances = api_get("/admin/instances", base=DISPATCHER_BASE)
        for inst in instances:
            print(f"  {inst['instance_id']:>25s} | model={inst['model']:<35s} | {inst['status']}")
        print(f"\n 总计: {len(instances)} 实例, {registered} 个已注册")
    except Exception as e:
        print(f"  验证失败: {e}")

    print(f"\n[完成] run 'uv run python scripts/bench_dual_model.py' to test.")


if __name__ == "__main__":
    setup_instances()
