"""实验 E: 阶梯负载测试 - 快速版 (30s per step).

1→2→5→10→20 users, 每阶梯 30 秒。
"""

import asyncio, json, statistics, time
from pathlib import Path
import httpx

URL = "http://localhost:12345/v1/chat/completions"
KEY = "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "minicpm-v-4.6"
PROMPT = "Hi"
MAX_TOKENS = 10

RESULTS_DIR = Path(__file__).parent.parent / "results" / "experiment_e"

STEPS = [1, 2, 5, 10, 20]
DURATION_S = 30  # 快速版: 30 秒每阶梯

async def worker(client, sem, results, stop_event):
    while not stop_event.is_set():
        try:
            async with sem:
                start = time.perf_counter()
                resp = await client.post(
                    URL,
                    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                    json={"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": MAX_TOKENS},
                    timeout=60,
                )
                elapsed = (time.perf_counter() - start) * 1000
                results.append({"latency_ms": elapsed, "ok": resp.status_code == 200, "status": resp.status_code, "ts": time.time()})
        except Exception as e:
            results.append({"latency_ms": 0, "ok": False, "error": str(e), "ts": time.time()})
        await asyncio.sleep(0.05)

async def run_step(concurrency, duration_s):
    print(f"\n[Step] concurrency={concurrency}, duration={duration_s}s")
    results = []
    sem = asyncio.Semaphore(concurrency)
    stop_event = asyncio.Event()
    
    async with httpx.AsyncClient() as client:
        workers = [asyncio.create_task(worker(client, sem, results, stop_event)) for _ in range(concurrency)]
        await asyncio.sleep(duration_s)
        stop_event.set()
        await asyncio.gather(*workers, return_exceptions=True)
    
    lats = [r["latency_ms"] for r in results if r.get("ok")]
    if not lats:
        print("  All failed!")
        return None
    
    s = sorted(lats)
    summary = {
        "concurrency": concurrency,
        "duration_s": duration_s,
        "total_requests": len(results),
        "successful": len(lats),
        "failures": len(results) - len(lats),
        "rps": round(len(results) / duration_s, 2),
        "mean_ms": round(statistics.mean(lats), 1),
        "median_ms": round(statistics.median(lats), 1),
        "p50_ms": round(s[int(len(s)*0.50)], 1),
        "p90_ms": round(s[int(len(s)*0.90)], 1),
        "p95_ms": round(s[int(len(s)*0.95)], 1),
        "p99_ms": round(s[int(len(s)*0.99)], 1),
        "max_ms": round(max(lats), 1),
    }
    print(f"  RPS={summary['rps']}, mean={summary['mean_ms']}ms, p95={summary['p95_ms']}ms, p99={summary['p99_ms']}ms, failures={summary['failures']}")
    return summary

async def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_summaries = []
    for c in STEPS:
        summary = await run_step(c, DURATION_S)
        if summary:
            all_summaries.append(summary)
    
    out = RESULTS_DIR / "stair_load_quick.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out}")

if __name__ == "__main__":
    asyncio.run(main())
