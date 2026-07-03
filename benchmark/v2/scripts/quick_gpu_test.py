"""快速验证真实 GPU 延迟 - 实验 A 的简化版。"""
import time, json, statistics
import httpx

URL = "http://127.0.0.1:14344/v1/chat/completions"
KEY = "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "minicpm-v-4.6"

PROMPTS = [
    ("hi", 10, "极短"),
    ("Explain quantum computing in one sentence", 50, "短"),
    ("Explain quantum computing", 200, "中"),
]

def call_api(prompt, max_tokens):
    start = time.time()
    resp = httpx.post(
        URL,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        timeout=60,
    )
    elapsed = (time.time() - start) * 1000
    return elapsed, resp.status_code

print("=" * 60)
print("  真实 GPU 延迟快速测试 (minicpm-v-4.6, GTX1650)")
print("=" * 60)

for prompt, max_tokens, label in PROMPTS:
    times = []
    for i in range(3):
        t, code = call_api(prompt, max_tokens)
        times.append(t)
        print(f"  {label} ({max_tokens} tokens) #{i+1}: {t:.1f}ms (HTTP {code})")
    avg = statistics.mean(times)
    p95 = sorted(times)[int(len(times)*0.67)]
    print(f"  --> {label}: avg={avg:.1f}ms, best={min(times):.1f}ms\n")

print("=" * 60)
