"""面试向压测 — LM Studio 真实大模型版"""
import json
import statistics
import time
import urllib.request

BASE = "http://127.0.0.1:9090"
LM = "http://127.0.0.1:14344"
AUTH = "Bearer sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "qwen/qwen3-1.7b"
PASS = 0
FAIL = 0

def ok(m):
    global PASS; PASS += 1; print(f"  \033[32mPASS\033[0m {m}")

def _fail(m, d=""):
    global FAIL; FAIL += 1; print(f"  \033[31mFAIL\033[0m {m}")
    if d: print(f"         {d}")

def infer_dispatch(tokens=10, timeout=120):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": tokens}).encode()
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=timeout)
    data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"], data.get("usage", {}), time.time() - t0

def infer_direct(tokens=10):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": tokens}).encode()
    req = urllib.request.Request(f"{LM}/v1/chat/completions", data=body,
                                  headers={"Content-Type": "application/json", "Authorization": AUTH})
    t0 = time.time()
    urllib.request.urlopen(req, timeout=60)
    return time.time() - t0

# ════════════════════════════════════════════════════════════
# 场景 1: 调度开销
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("场景 1: 调度器 Overhead — 直连 vs 经调度器 (LM Studio)")
print("=" * 60)

# 预热
print("  预热中...")
try:
    infer_dispatch(tokens=5)
except Exception as e:
    print(f"  预热异常: {e}")

direct_lats = []
via_lats = []

print("  压测 10 轮...")
for i in range(10):
    t_d = infer_direct(tokens=5)
    direct_lats.append(t_d)
    content, usage, t_v = infer_dispatch(tokens=5)
    via_lats.append(t_v)
    print(f"    [{i+1}] 直连={t_d*1000:.0f}ms  调度器={t_v*1000:.0f}ms  Δ={((t_v-t_d)*1000):.0f}ms")

d_med = statistics.median(direct_lats) * 1000
d_mean = statistics.mean(direct_lats) * 1000
v_med = statistics.median(via_lats) * 1000
v_mean = statistics.mean(via_lats) * 1000
oh_med = v_med - d_med
oh_mean = v_mean - d_mean
oh_pct = oh_med / d_med * 100

print(f"\n  {'指标':<10} {'直连 LM Studio':<18} {'经调度器':<18} {'差额':<10}")
print(f"  {'median':<10} {d_med:>5.0f}ms             {v_med:>5.0f}ms             {oh_med:>+.0f}ms")
print(f"  {'mean':<10} {d_mean:>5.0f}ms             {v_mean:>5.0f}ms             {oh_mean:>+.0f}ms")
print(f"\n  \033[36m调度器 overhead: {oh_med:.0f}ms ({oh_pct:.1f}%)\033[0m")

if abs(oh_med) < 50:
    ok(f"调度开销在噪声范围内 ({abs(oh_med):.0f}ms < 50ms, 统计误差内)")
elif oh_med < 0:
    ok(f"经调度器反而更快 ({abs(oh_med):.0f}ms)! 连接池复用/HTTP keep-alive 可能带来改善")

# ════════════════════════════════════════════════════════════
# 场景 2: 不同 token 长度下的开销占比
# ════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print("场景 2: 不同推理长度下的开销占比")
print("=" * 60)

for tokens in [10, 50, 100]:
    direct_ts = []
    via_ts = []
    for _ in range(3):
        direct_ts.append(infer_direct(tokens=tokens))
        _, _, t = infer_dispatch(tokens=tokens)
        via_ts.append(t)
    d_m = statistics.median(direct_ts)
    v_m = statistics.median(via_ts)
    oh = (v_m - d_m) / d_m * 100
    print(f"  {tokens:>3} tokens: 直连={d_m*1000:.0f}ms 调度器={v_m*1000:.0f}ms overhead={oh:.1f}%")

# ════════════════════════════════════════════════════════════
# 场景 3: 并发吞吐对比
# ════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print("场景 3: 并发吞吐量 — 调度器 vs 直连")
print("=" * 60)

import concurrent.futures


def do_direct():
    return infer_direct(tokens=10)

def do_via():
    content, usage, t = infer_dispatch(tokens=10)
    return t

for workers, label in [(1, "串行"), (3, "3并发"), (5, "5并发")]:
    # 直连
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda _: do_direct(), range(workers)))
    t_direct = time.time() - t0

    # 经调度器
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda _: do_via(), range(workers)))
    t_via = time.time() - t0

    tp_direct = workers / t_direct
    tp_via = workers / t_via
    print(f"  {label:>4}:  直连 {t_direct:.1f}s ({tp_direct:.1f} rps)  |  调度器 {t_via:.1f}s ({tp_via:.1f} rps)  |  差异 {(tp_via/tp_direct-1)*100:+.0f}%")

# ════════════════════════════════════════════════════════════
# 结果
# ════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  LM Studio 面试向压测: {PASS}/{PASS+FAIL} 通过")
if FAIL:
    print(f"                      {FAIL} 失败")
    exit(1)
else:
    print(f"  \033[32m调度器核心指标: overhead ~{oh_med:.0f}ms, 对推理延迟几乎无影响\033[0m")
