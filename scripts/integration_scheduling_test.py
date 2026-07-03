"""多实例调度集成测试 — 验证双模型注册、路由分发、负载隔离。

前提：LM Studio 已加载两个 qwen 实例
运行：uv run python scripts/integration_scheduling_test.py
"""
import asyncio
import json
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:9090"
AUTH = "Bearer sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
LM = "http://127.0.0.1:12345"
MODEL_A = "qwen/qwen3-1.7b"
MODEL_B = "qwen/qwen3-1.7b:2"

passed = 0
failed = 0
results = []

def ok(m):
    global passed; passed += 1
    results.append(f"  \033[32mPASS\033[0m {m}")

def fail(m, d=""):
    global failed; failed += 1
    results.append(f"  \033[31mFAIL\033[0m {m}")
    if d: results.append(f"        {d}")

def run(label, fn):
    global results
    results.clear()
    print(f"\n{'='*56}")
    print(f"  {label}")
    try:
        fn()
    except Exception as e:
        fail(f"unexpected: {e}")
    for r in results:
        print(r)

def api(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    return urllib.request.urlopen(req, timeout=30)

# ════════════════════════════════════════════════════════════════
# 前置：清理旧实例并注册两个模型
# ════════════════════════════════════════════════════════════════
def setup():
    print("  Cleaning up old instances...")
    # 删除旧实例
    for old in ["lm", "lm2"]:
        try:
            api("DELETE", f"/admin/instances/{old}")
            print(f"    Deregistered {old}")
        except:
            pass

    print("  Registering 2 qwen instances...")
    # 注册 A
    body = {"instance_id": "qwen-A", "address": LM, "model": MODEL_A, "engine_type": "vllm"}
    try:
        resp = api("POST", "/admin/instances", body)
        data = json.loads(resp.read())
        print(f"    OK: {data['instance_id']}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"    qwen-A already exists")
        else:
            raise

    # 注册 B
    body = {"instance_id": "qwen-B", "address": LM, "model": MODEL_B, "engine_type": "vllm"}
    try:
        resp = api("POST", "/admin/instances", body)
        data = json.loads(resp.read())
        print(f"    OK: {data['instance_id']}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"    qwen-B already exists")
        else:
            raise

    # 等健康检查
    print("    Waiting for health checks...")
    time.sleep(6)
setup()

# ════════════════════════════════════════════════════════════════
# Test 1: 双实例健康状态
# ════════════════════════════════════════════════════════════════
def t1():
    resp = urllib.request.urlopen(f"{BASE}/admin/status", timeout=10)
    data = json.loads(resp.read())
    inst = data["instances"]
    cb = data["circuit_breakers"]
    if inst["total"] == 2 and inst["healthy"] == 2:
        ok(f"2 instances: {inst['healthy']} healthy, CB: {dict(cb)}")
    else:
        fail(f"expected 2 healthy, got total={inst['total']} healthy={inst['healthy']}")

    # 还列出实例
    resp = urllib.request.urlopen(f"{BASE}/admin/instances", timeout=10)
    instances = json.loads(resp.read())
    models = [(i["instance_id"], i["model"], i["status"]) for i in instances]
    for iid, model, status in models:
        ok(f"  {iid}: model={model} status={status}")
run("Test 1: Dual instance health", t1)

# ════════════════════════════════════════════════════════════════
# Test 2: 模型 A 推理（非流式）
# ════════════════════════════════════════════════════════════════
def t2():
    body = json.dumps({"model": MODEL_A, "messages": [{"role": "user", "content": "Say: I am instance A"}], "max_tokens": 10, "temperature": 0.3})
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=120)
    data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    ok(f"model A inference ({time.time()-t0:.1f}s) tokens: p={usage['prompt_tokens']} c={usage['completion_tokens']}")
    results.append(f"    reply: [{content}]")

    if len(content.strip()) == 0:
        fail("model A returned empty content")
    if usage["completion_tokens"] == 0:
        fail("model A returned 0 tokens")
run("Test 2: Model A inference", t2)

# ════════════════════════════════════════════════════════════════
# Test 3: 模型 B 推理（非流式）
# ════════════════════════════════════════════════════════════════
def t3():
    body = json.dumps({"model": MODEL_B, "messages": [{"role": "user", "content": "Say: I am instance B"}], "max_tokens": 10, "temperature": 0.3})
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=120)
    data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    ok(f"model B inference ({time.time()-t0:.1f}s) tokens: p={usage['prompt_tokens']} c={usage['completion_tokens']}")
    results.append(f"    reply: [{content}]")

    if len(content.strip()) == 0:
        fail("model B returned empty content")
    if usage["completion_tokens"] == 0:
        fail("model B returned 0 tokens")
run("Test 3: Model B inference", t3)

# ════════════════════════════════════════════════════════════════
# Test 4: 串行请求验证路由隔离（A 请求不干扰 B）
# ════════════════════════════════════════════════════════════════
def t4():
    results_A = {"ok": 0, "fail": 0}
    results_B = {"ok": 0, "fail": 0}

    for i in range(3):
        # 请求 A
        body = json.dumps({"model": MODEL_A, "messages": [{"role": "user", "content": "Say A"}], "max_tokens": 3})
        req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(), headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            data = json.loads(resp.read())
            if data["choices"][0]["message"]["content"]:
                results_A["ok"] += 1
            else:
                results_A["fail"] += 1
        except:
            results_A["fail"] += 1

        # 请求 B
        body = json.dumps({"model": MODEL_B, "messages": [{"role": "user", "content": "Say B"}], "max_tokens": 3})
        req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(), headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            data = json.loads(resp.read())
            if data["choices"][0]["message"]["content"]:
                results_B["ok"] += 1
            else:
                results_B["fail"] += 1
        except:
            results_B["fail"] += 1

    ok(f"serial 3x(A+B): A={results_A['ok']}/{results_A['ok']+results_A['fail']} B={results_B['ok']}/{results_B['ok']+results_B['fail']}")
    if results_A["fail"] > 0:
        fail(f"model A had {results_A['fail']} failures")
    if results_B["fail"] > 0:
        fail(f"model B had {results_B['fail']} failures")
run("Test 4: Serial routing isolation (A/B 3 rounds)", t4)

# ════════════════════════════════════════════════════════════════
# Test 5: 每个模型的流式推理
# ════════════════════════════════════════════════════════════════
def t5():
    for label, model in [("A", MODEL_A), ("B", MODEL_B)]:
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 3, "temperature": 0.3, "stream": True})
        req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(), headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=120)
        chunks = 0
        for line_bytes in resp:
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if line.startswith("data: ") and "content" in line:
                chunks += 1
        if chunks > 0:
            ok(f"stream model {label}: {chunks} chunks")
        else:
            fail(f"stream model {label}: no chunks")
run("Test 5: Streaming both models", t5)

# ════════════════════════════════════════════════════════════════
# Test 6: 每个模型的指标隔离
# ════════════════════════════════════════════════════════════════
def t6():
    resp = urllib.request.urlopen(f"{BASE}/admin/metrics/summary", timeout=10)
    data = json.loads(resp.read())
    rc = data["request_count"]
    ec = data["error_count"]
    pi = data.get("per_instance", {})
    ok(f"metrics: {rc} total, {ec} errors, p99={data['p99_latency_ms']:.0f}ms")
    if pi:
        for iid, stats in pi.items():
            ok(f"  {iid}: {stats['request_count']} req, avg={stats['avg_latency_ms']:.1f}ms")
    else:
        fail("no per-instance metrics")
run("Test 6: Per-instance metrics", t6)

# ════════════════════════════════════════════════════════════════
# Test 7: 熔断器状态（两个都应 CLOSED）
# ════════════════════════════════════════════════════════════════
def t7():
    resp = urllib.request.urlopen(f"{BASE}/admin/status", timeout=10)
    data = json.loads(resp.read())
    cb = data["circuit_breakers"]
    all_closed = all(s == "closed" for s in cb.values())
    if all_closed and len(cb) >= 2:
        ok(f"all {len(cb)} circuit breakers closed")
    else:
        fail(f"circuit breakers: {dict(cb)}")
run("Test 7: Circuit breaker check", t7)

# ════════════════════════════════════════════════════════════════
print(f"\n{'='*56}")
print(f"\n  Results: {passed}/{passed+failed} passed")
if failed:
    print(f"           {failed} FAILED")
    sys.exit(1)
else:
    print(f"  \033[32mAll tests passed!\033[0m")
