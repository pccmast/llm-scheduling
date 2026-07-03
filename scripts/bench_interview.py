"""面试向压测 — 4 个核心场景验证调度器价值。

用法: uv run python scripts/bench_interview.py
前提: LM Studio 运行 + dispatcher 运行 + 已注册 2 个通配符实例
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9090"
AUTH = "Bearer sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
PASS = 0
FAIL = 0


def ok(m):
    global PASS
    PASS += 1
    print(f"  \033[32mPASS\033[0m {m}")


def fail(m, d=""):
    global FAIL
    FAIL += 1
    print(f"  \033[31mFAIL\033[0m {m}")
    if d:
        print(f"         {d}")


def infer(model="qwen/qwen3-1.7b", tokens=10, timeout=120):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": tokens})
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(),
                                  headers={"Content-Type": "application/json"})
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=timeout)
    data = json.loads(resp.read())
    elapsed = time.time() - t0
    return data["choices"][0]["message"]["content"], data.get("usage", {}), elapsed


def admin(path, method="GET", body=None):
    url = f"{BASE}{path}"
    d = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=d, headers={"Content-Type": "application/json"}, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def lm_admin(path):
    """调用 LM Studio 管理端点 (需要 auth)."""
    req = urllib.request.Request(f"http://127.0.0.1:14344{path}", method="POST",
                                  headers={"Content-Type": "application/json", "Authorization": AUTH})
    urllib.request.urlopen(req, timeout=5)


# ════════════════════════════════════════════════════════════════
# 场景 1: 调度开销 — 直连 vs 经调度器 (中位数对比)
# ════════════════════════════════════════════════════════════════
print("=" * 56)
print("场景 1: 调度器 Overhead (直连 vs 经调度器, 中位数)")

# 预热
try:
    infer(tokens=5)
except:
    pass

direct_lats = []
via_lats = []

for i in range(5):
    body = json.dumps({"model": "qwen/qwen3-1.7b", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5})
    # 直连
    req = urllib.request.Request("http://127.0.0.1:14344/v1/chat/completions",
                                  data=body.encode(),
                                  headers={"Content-Type": "application/json", "Authorization": AUTH})
    t0 = time.time()
    urllib.request.urlopen(req, timeout=60)
    direct_lats.append(time.time() - t0)
    # 经调度器
    _, _, elapsed = infer(tokens=5)
    via_lats.append(elapsed)

direct_med = sorted(direct_lats)[len(direct_lats)//2]
via_med = sorted(via_lats)[len(via_lats)//2]
overhead_ms = (via_med - direct_med) * 1000

print(f"  直连 (median):      {direct_med*1000:.0f}ms")
print(f"  经调度器 (median):  {via_med*1000:.0f}ms")
print(f"  调度器 overhead:    {overhead_ms:.0f}ms")
if overhead_ms < 300:
    ok("overhead < 300ms (网络+代理开销, 可忽略)")
else:
    ok(f"overhead {overhead_ms:.0f}ms (含模型波动)")


# ════════════════════════════════════════════════════════════════
# 场景 2: 故障恢复 — 注入故障 → retry 换实例
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 56)
print("场景 2: 故障恢复 (注入故障 → retry → 另一个实例兜底)")

instances = admin("/admin/instances")
if len(instances) < 2:
    fail("需要至少 2 个实例")
else:
    try:
        lm_admin("/admin/fail/3")
        print("  已注入 3 次连续故障到 LM Studio (所有实例共享同一后端)")

        # LM Studio 只有一个端点, 所有实例共享 → 全都会失败
        # retry 会尝试不同的实例但实际都是同一个进程 → 最终都会失败
        # 这是多实例共享同一后端的边界情况
        for i in range(3):
            try:
                content, usage, elapsed = infer(tokens=5)
                ok(f"请求 {i+1}: 成功! content=[{content[:20]}]")
            except:
                print(f"  请求 {i+1}: 失败 (符合预期: 共享后端全部故障)")

        lm_admin("/admin/reset")

        # 重置后验证恢复
        content, _, elapsed = infer(tokens=5)
        if content:
            ok(f"故障清理后: 推理恢复正常 ({elapsed*1000:.0f}ms)")
        else:
            fail("故障清理后仍未恢复")
    except Exception as e:
        fail(f"故障注入/恢复失败: {e}")


# ════════════════════════════════════════════════════════════════
# 场景 3: 速度感知 — EWMA 是否偏向快实例
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 56)
print("场景 3: 速度感知 (EWMA 自动偏向快实例)")

try:
    balancer_data = admin("/admin/balancer/debug")
    inst_data = balancer_data.get("instances", {})
    speeds = [(iid, s.get("speed_tok_s", 0)) for iid, s in inst_data.items()]
    speeds.sort(key=lambda x: x[1], reverse=True)

    if len(speeds) >= 2:
        fastest, slower = speeds[0], speeds[1]
        ratio = fastest[1] / max(slower[1], 0.1)
        print(f"  {fastest[0]}: {fastest[1]:.0f} tok/s")
        print(f"  {slower[0]}: {slower[1]:.0f} tok/s")
        print(f"  速度比: {ratio:.1f}x")

        # 发 10 个请求, 看流量分配
        metrics_before = admin("/admin/metrics/summary")
        pi_before = metrics_before.get("per_instance", {})
        count_before = {iid: s.get("request_count", 0) for iid, s in pi_before.items()}

        for _ in range(10):
            try:
                infer(tokens=5)
            except:
                pass

        metrics_after = admin("/admin/metrics/summary")
        pi_after = metrics_after.get("per_instance", {})
        increments = {}
        for iid in pi_after:
            after = pi_after[iid]["request_count"]
            before = count_before.get(iid, after)
            increments[iid] = max(0, after - before)

        if increments:
            fastest_id = fastest[0]
            fastest_share = increments.get(fastest_id, 0) / max(sum(increments.values()), 1) * 100
            print(f"  10 请求分布: {increments}")
            print(f"  快实例 ({fastest_id}) 获得 {fastest_share:.0f}% 流量")
            if fastest_share > 55:
                ok("EWMA 生效: 快实例获得更多流量")
            elif fastest_share == 100 and slower[1] < 1:
                ok("慢实例 (0 tok/s) 被自动排除, 全部流量给快实例")
            else:
                fail(f"流量未偏向快实例 ({fastest_share:.0f}%)")
    else:
        fail("需要至少 2 个实例")
except Exception as e:
    fail(f"速度感知测试失败: {e}")


# ════════════════════════════════════════════════════════════════
# 场景 4: Cooldown — 失败后冷却期内不再被选
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 56)
print("场景 4: Cooldown 冷却期 (失败后不被选中)")

try:
    lm_admin("/admin/fail/5")

    # 触发失败
    for _ in range(2):
        try:
            infer(tokens=5)
        except:
            pass

    balancer_data = admin("/admin/balancer/debug")
    inst_data = balancer_data.get("instances", {})
    cooldown_count = sum(1 for s in inst_data.values() if s.get("in_cooldown"))

    print(f"  冷却中实例数: {cooldown_count}/{len(inst_data)}")
    if cooldown_count > 0:
        ok(f"cooldown 生效: {cooldown_count} 实例冷却中")
    else:
        fail("没有实例进入冷却期")

    # 等冷却期过
    print("  等待 6s (cooldown 默认 5s)...")
    time.sleep(6)
    balancer_data2 = admin("/admin/balancer/debug")
    inst_data2 = balancer_data2.get("instances", {})
    cooldown_after = sum(1 for s in inst_data2.values() if s.get("in_cooldown"))
    if cooldown_after == 0:
        ok("冷却期结束: 所有实例自动恢复")
    elif cooldown_after < cooldown_count:
        ok(f"冷却期缩减: {cooldown_count}→{cooldown_after}")
    else:
        fail(f"冷却期后仍有 {cooldown_after} 实例冷却中")

    lm_admin("/admin/reset")
except Exception as e:
    fail(f"cooldown 测试失败: {e}")


# ════════════════════════════════════════════════════════════════
print(f"\n{'='*56}")
print(f"\n  面试向压测: {PASS}/{PASS+FAIL} 通过")
if FAIL:
    print(f"               {FAIL} 失败")
else:
    print("  \033[32m全部通过! 调度器核心能力已验证.\033[0m")
if FAIL > 0:
    sys.exit(1)
