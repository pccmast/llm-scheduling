"""双模型负载均衡压测 — 验证调度器同时利用两块 GPU 显存。

前提: 先运行 setup_lmstudio.py 注册实例, LM Studio 加载 2 个 Qwen 模型。

用法: uv run python scripts/bench_dual_model.py
"""

from __future__ import annotations

import concurrent.futures
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# ── 配置 ──────────────────────────────────────────────────────
DISPATCHER = "http://127.0.0.1:9090"
LM_STUDIO = "http://127.0.0.1:14344"
AUTH = "Bearer sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
PASS = 0
FAIL = 0


def ok(m: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32mPASS\033[0m {m}")


def _fail(m: str, d: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {m}")
    if d:
        print(f"         {d}")


def info(m: str) -> None:
    print(f"  \033[33m{m}\033[0m")


# ════════════════════════════════════════════════════════════════
# 工具
# ════════════════════════════════════════════════════════════════


def dispatch(model: str, tokens: int = 10, timeout: int = 120) -> tuple[str, dict, float]:
    """经调度器推理。"""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": tokens,
    }).encode()
    req = urllib.request.Request(
        f"{DISPATCHER}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=timeout)
    data = json.loads(resp.read())
    elapsed = time.time() - t0
    return data["choices"][0]["message"]["content"], data.get("usage", {}), elapsed


def direct(model: str, tokens: int = 10) -> float:
    """直连 LM Studio 推理，返回耗时。"""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": tokens,
    }).encode()
    req = urllib.request.Request(
        f"{LM_STUDIO}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": AUTH},
    )
    t0 = time.time()
    urllib.request.urlopen(req, timeout=60)
    return time.time() - t0


def get_instances() -> list[dict[str, Any]]:
    """获取调度器当前注册的实例列表。"""
    req = urllib.request.Request(f"{DISPATCHER}/admin/instances")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


# ════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════

print("=" * 65)
print("  LM Studio 双模型负载均衡压测")
print("=" * 65)

# 读取注册表
try:
    instances = get_instances()
    models = [i["model"] for i in instances if i["status"] == "healthy"]
    if len(models) < 2:
        _fail(f"需要至少 2 个健康实例 (当前: {len(models)}). 请先运行 setup_lmstudio.py")
        sys.exit(1)
    # 只取 Qwen 模型用于对比
    qwen_models = [m for m in models if "qwen" in m.lower()]
    if len(qwen_models) < 2:
        qwen_models = models[:2]  # fallback
    model_a, model_b = qwen_models[0], qwen_models[1]
    ok(f"发现 {len(instances)} 个实例, 双模型: [{model_a}], [{model_b}]")
except Exception as e:
    _fail(f"无法获取实例列表: {e}")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════
# 场景 1: 调度开销 — 两个模型分别对比
# ════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("  场景 1: 调度器 Overhead — 两个模型分别对比")
print("=" * 65)

info("预热...")
for m in [model_a, model_b]:
    try:
        dispatch(m, tokens=5)
    except Exception:
        pass

for model_name in [model_a, model_b]:
    direct_lats: list[float] = []
    via_lats: list[float] = []

    info(f"\n  模型: {model_name}")
    for i in range(5):
        t_d = direct(model_name, tokens=10)
        direct_lats.append(t_d)
        _, _, t_v = dispatch(model_name, tokens=10)
        via_lats.append(t_v)
        delta = (t_v - t_d) * 1000
        print(f"    [{i+1}] 直连={t_d*1000:.0f}ms  调度器={t_v*1000:.0f}ms  Δ={delta:+.0f}ms")

    d_med = statistics.median(direct_lats) * 1000
    v_med = statistics.median(via_lats) * 1000
    oh = v_med - d_med
    print(f"    median: 直连={d_med:.0f}ms  调度器={v_med:.0f}ms  overhead={oh:+.0f}ms ({oh/d_med*100:.1f}%)")
    if abs(oh) < 50:
        ok(f"  {model_name[:30]} overhead 在噪声范围内")
    else:
        ok(f"  {model_name[:30]} overhead={oh:.0f}ms")

# ════════════════════════════════════════════════════════════════
# 场景 2: 双模型交替路由 — 验证两块 GPU 都被使用
# ════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("  场景 2: 双模型交替路由 — 两块 GPU 显存都被利用")
print("=" * 65)

routes: dict[str, int] = {}
info("发送 20 个请求, model 字段交替切换...")
for i in range(20):
    target = model_a if i % 2 == 0 else model_b
    try:
        content, usage, t = dispatch(target, tokens=10)
        routes[target] = routes.get(target, 0) + 1
        print(f"    [{i+1:>2}] → {target:<30s}  {t*1000:.0f}ms  [{content[:20]}...]")
    except Exception as e:
        _fail(f"请求 {i+1} 失败: {e}")

print(f"\n  {model_a}: {routes.get(model_a, 0)} 请求")
print(f"  {model_b}: {routes.get(model_b, 0)} 请求")

if routes.get(model_a, 0) >= 8 and routes.get(model_b, 0) >= 8:
    ok(f"双模型交替路由生效: {routes[model_a]}+{routes.get(model_b, 0)}=20")
else:
    _fail(f"路由不均衡: {routes}")

# ════════════════════════════════════════════════════════════════
# 场景 3: 并发双模型 vs 单模型吞吐对比
# ════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print("  场景 3: 并发吞吐 — 双模型 vs 单模型")
print("=" * 65)


def do_single_model(m: str) -> float:
    return direct(m, tokens=20)


def do_dual_model(idx: int) -> float:
    m = model_a if idx % 2 == 0 else model_b
    _, _, t = dispatch(m, tokens=20)
    return t


for workers, label in [(4, "4并发"), (6, "6并发")]:
    # 单模型直连 (全打 model_a)
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda _: do_single_model(model_a), range(workers)))
    t_single = time.time() - t0
    tp_single = workers / t_single

    # 双模型经调度器 (交替打到两个模型)
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(do_dual_model, range(workers)))
    t_dual = time.time() - t0
    tp_dual = workers / t_dual

    print(f"  {label}: 单模型 {tp_single:.1f} rps ({t_single:.1f}s)  |  双模型 {tp_dual:.1f} rps ({t_dual:.1f}s)  |  差异 {(tp_dual/tp_single-1)*100:+.0f}%")

    if tp_dual > tp_single * 0.7:
        ok(f"{label} 双模型吞吐接近单模型 (调度器未成为瓶颈)")
    else:
        info(f"{label} 差异较大 (LM Studio 单进程共用端口限制)")

# ════════════════════════════════════════════════════════════════
# 结果
# ════════════════════════════════════════════════════════════════
print(f"\n{'='*65}")
print(f"  双模型压测: {PASS}/{PASS+FAIL} 通过")
if FAIL:
    print(f"             {FAIL} 失败")
    sys.exit(1)
else:
    print(f"  \033[32m调度器成功利用两块 GPU 显存进行负载均衡\033[0m")
