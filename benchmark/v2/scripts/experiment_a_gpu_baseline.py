"""实验 A: 真实 GPU 基线延迟 - 单请求直连。

验证 1.92G 模型在 GTX1650 上的延迟分布。
"""

import asyncio, json, statistics, time, sys
from pathlib import Path
import httpx

URL = "http://localhost:12345/v1/chat/completions"
KEY = "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "minicpm-v-4.6"

RESULTS_DIR = Path(__file__).parent.parent / "results" / "experiment_a"

SCENARIOS = [
    ("short_10", "Hi", 10, 20),
    ("short_50", "Explain quantum computing in one sentence", 50, 20),
    ("med_200", "Explain quantum computing and its applications", 200, 10),
]

async def call_one(client, prompt, max_tokens):
    start = time.perf_counter()
    try:
        resp = await client.post(
            URL,
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=120,
        )
        elapsed = (time.perf_counter() - start) * 1000
        return {"latency_ms": elapsed, "status": resp.status_code, "ok": resp.status_code == 200}
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return {"latency_ms": elapsed, "status": 0, "ok": False, "error": str(e)}

async def run_scenario(name, prompt, max_tokens, n):
    print(f"\n[{name}] {n} requests (sequential), max_tokens={max_tokens}")
    async with httpx.AsyncClient() as client:
        results = []
        for i in range(n):
            r = await call_one(client, prompt, max_tokens)
            results.append(r)
            if (i + 1) % 5 == 0:
                print(f"  {i+1}/{n} done...")
            await asyncio.sleep(0.5)  # 串行间隔, 避免 GPU 缓存预热效应
    
    lats = [r["latency_ms"] for r in results if isinstance(r, dict) and r.get("ok")]
    if not lats:
        print("  All failed!")
        return None
    
    s = sorted(lats)
    summary = {
        "scenario": name,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "n": len(lats),
        "mean_ms": round(statistics.mean(lats), 1),
        "median_ms": round(statistics.median(lats), 1),
        "std_ms": round(statistics.stdev(lats) if len(lats) > 1 else 0, 1),
        "min_ms": round(min(lats), 1),
        "max_ms": round(max(lats), 1),
        "p50_ms": round(s[int(len(s)*0.50)], 1),
        "p90_ms": round(s[int(len(s)*0.90)], 1),
        "p95_ms": round(s[int(len(s)*0.95)], 1),
        "p99_ms": round(s[int(len(s)*0.99)], 1),
        "failures": len([r for r in results if isinstance(r, dict) and not r.get("ok")]),
    }
    print(f"  mean={summary['mean_ms']}ms, p50={summary['p50_ms']}ms, p90={summary['p90_ms']}ms, p95={summary['p95_ms']}ms, p99={summary['p99_ms']}ms")
    return summary

async def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_summaries = []
    for name, prompt, max_tokens, n in SCENARIOS:
        summary = await run_scenario(name, prompt, max_tokens, n)
        if summary:
            all_summaries.append(summary)
    
    out = RESULTS_DIR / "gpu_baseline.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out}")

if __name__ == "__main__":
    asyncio.run(main())
