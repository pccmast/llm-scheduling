"""实验 G: 调度器架构 Overhead 测量。

对比 直连 GPU (100ms timeout) vs 经过 Dispatcher 的延迟。
"""

import asyncio, json, statistics, time
from pathlib import Path
import httpx

# 真实 GPU
GPU_URL = "http://127.0.0.1:14344/v1/chat/completions"
GPU_KEY = "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "minicpm-v-4.6"

# Dispatcher 代理 (需要先启动)
PROXY_URL = "http://127.0.0.1:8000/v1/chat/completions"
PROMPT = "Hi"
MAX_TOKENS = 10
N = 50

RESULTS_DIR = Path(__file__).parent.parent / "results" / "experiment_g"

async def call_direct(client):
    start = time.perf_counter()
    resp = await client.post(
        GPU_URL,
        headers={"Authorization": f"Bearer {GPU_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": MAX_TOKENS},
        timeout=60,
    )
    return (time.perf_counter() - start) * 1000, resp.status_code

async def call_via_proxy(client):
    start = time.perf_counter()
    resp = await client.post(
        PROXY_URL,
        headers={"Authorization": f"Bearer {GPU_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": MAX_TOKENS},
        timeout=60,
    )
    return (time.perf_counter() - start) * 1000, resp.status_code

async def run_test():
    print("[Experiment G] Overhead measurement")
    print("=" * 60)
    
    async with httpx.AsyncClient() as client:
        # Direct
        print(f"\nDirect GPU ({N} calls)...")
        direct_times = []
        for _ in range(N):
            t, code = await call_direct(client)
            direct_times.append(t)
        
        # Proxy
        print(f"Via Dispatcher ({N} calls)...")
        proxy_times = []
        for _ in range(N):
            t, code = await call_via_proxy(client)
            proxy_times.append(t)
    
    d_mean = statistics.mean(direct_times)
    p_mean = statistics.mean(proxy_times)
    overhead = p_mean - d_mean
    overhead_pct = (overhead / d_mean) * 100 if d_mean > 0 else 0
    
    summary = {
        "direct": {
            "mean_ms": round(d_mean, 1),
            "median_ms": round(statistics.median(direct_times), 1),
            "min_ms": round(min(direct_times), 1),
            "max_ms": round(max(direct_times), 1),
        },
        "via_proxy": {
            "mean_ms": round(p_mean, 1),
            "median_ms": round(statistics.median(proxy_times), 1),
            "min_ms": round(min(proxy_times), 1),
            "max_ms": round(max(proxy_times), 1),
        },
        "overhead_ms": round(overhead, 1),
        "overhead_percent": round(overhead_pct, 1),
    }
    
    print(f"\nDirect: mean={summary['direct']['mean_ms']}ms")
    print(f"Proxy:  mean={summary['via_proxy']['mean_ms']}ms")
    print(f"Overhead: {summary['overhead_ms']}ms ({summary['overhead_percent']}%)")
    
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "overhead.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out}")
    return summary

async def main():
    await run_test()

if __name__ == "__main__":
    asyncio.run(main())