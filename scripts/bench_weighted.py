"""速度感知调度验证 — 两副本来一场竞速赛。

前提: LM Studio 加载两个 Qwen 副本, 调度器运行中。

用法: uv run python scripts/bench_weighted.py
"""

import json, sys, time, urllib.request, statistics

DISPATCHER = "http://127.0.0.1:9090"
LM_STUDIO = "http://127.0.0.1:14344"
AUTH = "Bearer sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"

def api(path, method="GET", body=None):
    url = f"{DISPATCHER}{path}"
    d = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=d, headers={"Content-Type":"application/json"}, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def dispatch(model, tokens=10):
    t0 = time.time()
    body = json.dumps({"model":model,"messages":[{"role":"user","content":"Hello"}],"max_tokens":tokens}).encode()
    req = urllib.request.Request(f"{DISPATCHER}/v1/chat/completions", data=body, headers={"Content-Type":"application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return data["choices"][0]["message"]["content"], data.get("usage",{}), time.time()-t0

# ── 1. 获取已注册实例 ──
instances = api("/admin/instances")
healthy = [i for i in instances if i["status"]=="healthy"]
if len(healthy) < 2:
    print("需要至少 2 个健康实例, 先运行 setup_lmstudio.py")
    sys.exit(1)

models = [i["model"] for i in healthy]
m1, m2 = models[0], models[1]
print(f"实例: [{m1}], [{m2}]")

# ── 2. 预热 + 竞速: 各跑 10 轮测速度 ──
print("\n=== 竞速预热 ===")
for m in [m1, m2]:
    try: dispatch(m, tokens=5)
    except: pass

print("\n=== 两副本 10 轮速度对比 ===")
speeds = {m1: [], m2: []}
for i in range(10):
    for m in [m1, m2]:
        _, usage, elapsed = dispatch(m, tokens=30)
        tok_s = usage.get("completion_tokens", 0) / elapsed if elapsed > 0 else 0
        speeds[m].append(tok_s)
        print(f"  [{i+1}] {m:<30s} {elapsed*1000:.0f}ms  {tok_s:.1f} tok/s")

print("\n=== 速度统计 ===")
for m in [m1, m2]:
    s = speeds[m]
    print(f"  {m:<30s} median={statistics.median(s):.1f} tok/s  mean={statistics.mean(s):.1f} tok/s  std={statistics.stdev(s):.1f}")

# ── 3. 查看调度器内部评分 ──
print("\n=== 调度器内部状态 (balancer debug) ===")
debug = api("/admin/balancer/debug")
for iid, info in debug.get("instances", {}).items():
    print(f"  {iid:>25s} | speed={info.get('speed_tok_s',0):>6.1f} tok/s | reliability={info.get('reliability',0):.4f} | load={info.get('load',0):.1f} | cooldown={info.get('in_cooldown',False)}")

# ── 4. 速度感知: 并发 20 请求, 观察流量分配 ──
print("\n=== 速度感知路由: 20 并发请求, 观察分配 ===")
from concurrent.futures import ThreadPoolExecutor

route_count = {m1: 0, m2: 0}
def send_one(idx):
    m = m1 if idx % 2 == 0 else m2
    try:
        _, _, _ = dispatch(m, tokens=20)
        route_count[m] += 1
    except: pass

t0 = time.time()
with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(send_one, range(20)))
elapsed = time.time() - t0

print(f"  {m1}: {route_count[m1]} 请求")
print(f"  {m2}: {route_count[m2]} 请求")
print(f"  总耗时: {elapsed:.1f}s  (并发 4 workers)")

# ── 5. 再次查看调度器内部评分 (跑完后速度应该更新了) ──
print("\n=== 调度器内部状态 (压测后) ===")
debug = api("/admin/balancer/debug")
for iid, info in debug.get("instances", {}).items():
    print(f"  {iid:>25s} | speed={info.get('speed_tok_s',0):>6.1f} tok/s | reliability={info.get('reliability',0):.4f} | load={info.get('load',0):.1f} | cooldown={info.get('in_cooldown',False)}")

# 判断快慢
speeds_after = {iid: info.get("speed_tok_s", 0) for iid, info in debug.get("instances", {}).items()}
if len(speeds_after) >= 2:
    sorted_ids = sorted(speeds_after, key=speeds_after.get, reverse=True)
    faster, slower = sorted_ids[0], sorted_ids[1]
    print(f"\n  ⚡ 更快: {faster} ({speeds_after[faster]:.1f} tok/s)")
    print(f"  🐢 较慢: {slower} ({speeds_after[slower]:.1f} tok/s)")
    if speeds_after[faster] / max(speeds_after[slower], 0.1) > 1.1:
        print(f"  ✅ 速度差异 > 10%, 调度器将会偏向快实例 (speed_bonus 生效)")
    else:
        print(f"  ℹ️  速度差异 < 10%, 调度器视为相近 (均衡分配)")
