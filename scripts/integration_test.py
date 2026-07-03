"""真实后端集成测试 — LM Studio qwen3-1.7b 端到端验证。"""
import json, sys, time, urllib.request, urllib.error

BASE = "http://localhost:9090"
LM_STUDIO = "http://127.0.0.1:12345"
MODEL = "qwen/qwen3-1.7b"

passed = 0
failed = 0
results = []

def ok(m):
    global passed
    passed += 1
    results.append(f"  \033[32mPASS\033[0m {m}")

def fail(m, d=""):
    global failed
    failed += 1
    results.append(f"  \033[31mFAIL\033[0m {m}")
    if d:
        results.append(f"        {d}")

def run(label, fn):
    global results
    print(f"\n{'='*56}")
    print(f"  {label}")
    try:
        fn()
    except Exception as e:
        fail(f"unexpected: {e}")
    for r in results:
        print(r)
    results.clear()

# ════════════════════════════════════════════════════════════════
# Test 1: 注册 LM Studio 实例
# ════════════════════════════════════════════════════════════════
def t1():
    body = json.dumps({"instance_id":"lm","address":LM_STUDIO,"model":MODEL,"engine_type":"vllm"})
    req = urllib.request.Request(f"{BASE}/admin/instances", data=body.encode(), headers={"Content-Type":"application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data["status"] == "ok":
            ok(f"register: {data['instance_id']}")
        else:
            fail("register unexpected", str(data))
    except urllib.error.HTTPError as e:
        if e.code == 409:
            ok("register: already registered (409 = skip)")
        else:
            fail(f"register HTTP {e.code}", e.read().decode()[:200])
run("Test 1: Register instance", t1)

# ════════════════════════════════════════════════════════════════
# Test 2: 健康检查
# ════════════════════════════════════════════════════════════════
def t2():
    time.sleep(5)  # 等 health checker 探测一次
    resp = urllib.request.urlopen(f"{BASE}/admin/status", timeout=10)
    data = json.loads(resp.read())
    inst = data["instances"]
    cb = data.get("circuit_breakers", {})
    if inst["healthy"] >= 1:
        ok(f"health check: {inst['healthy']}/{inst['total']} healthy  CB: {list(cb.keys())}")
    else:
        fail(f"no healthy instances: total={inst['total']} healthy={inst['healthy']}")
run("Test 2: Health check (/v1/models)", t2)

# ════════════════════════════════════════════════════════════════
# Test 3: 推理请求
# ════════════════════════════════════════════════════════════════
def t3():
    body = json.dumps({"model":MODEL,"messages":[{"role":"user","content":"Say hello in one word"}],"max_tokens":5,"temperature":0.3})
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(), headers={"Content-Type":"application/json"})
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        fail(f"inference HTTP {e.code}", (e.read().decode()[:300] if e.fp else ""))
        return
    elapsed = time.time() - t0
    result = json.loads(resp.read())
    content = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    finish = result["choices"][0].get("finish_reason", "?")

    tokens_ok = usage.get("completion_tokens", 0) > 0
    content_ok = len(content.strip()) > 0
    if tokens_ok and content_ok:
        ok(f"inference OK ({elapsed:.1f}s) tokens: p={usage['prompt_tokens']} c={usage['completion_tokens']}")
        results.append(f"    reply: [{content}]")
    elif tokens_ok:
        fail(f"tokens ok but content empty ({elapsed:.1f}s) p={usage.get('prompt_tokens')} c={usage['completion_tokens']} finish={finish}")
        results.append(f"    raw: {json.dumps(result)[:300]}")
    else:
        fail(f"inference no tokens ({elapsed:.1f}s) finish={finish}")
run("Test 3: Inference request", t3)

# ════════════════════════════════════════════════════════════════
# Test 4: Metrics 汇总
# ════════════════════════════════════════════════════════════════
def t4():
    resp = urllib.request.urlopen(f"{BASE}/admin/metrics/summary", timeout=10)
    data = json.loads(resp.read())
    rc = data["request_count"]
    ec = data["error_count"]
    if rc >= 1:
        ok(f"metrics: {rc} requests, {ec} errors, p99={data['p99_latency_ms']:.0f}ms")
    else:
        fail(f"metrics: expected >=1 request, got {rc}")
run("Test 4: Metrics summary", t4)

# ════════════════════════════════════════════════════════════════
# Test 5: 流式推理 SSE
# ════════════════════════════════════════════════════════════════
def t5():
    import urllib.request
    body = json.dumps({"model":MODEL,"messages":[{"role":"user","content":"Say hi"}],"max_tokens":5,"temperature":0.3,"stream":True})
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body.encode(), headers={"Content-Type":"application/json"})
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=300)
    chunk_count = 0
    content_parts = []
    for line_bytes in resp:
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if line.startswith("data: ") and "content" in line:
            chunk_count += 1
    elapsed = time.time() - t0
    if chunk_count > 0:
        ok(f"stream: {chunk_count} chunks ({elapsed:.1f}s)")
    else:
        fail(f"stream: no content chunks ({elapsed:.1f}s)")
run("Test 5: Streaming (SSE)", t5)

# ════════════════════════════════════════════════════════════════
# 总结
# ════════════════════════════════════════════════════════════════
print(f"\n{'='*56}")
print(f"\n  Results: {passed}/{passed+failed} passed")
if failed:
    print(f"           {failed} FAILED")
    sys.exit(1)
else:
    print(f"  \033[32mAll tests passed!\033[0m")
