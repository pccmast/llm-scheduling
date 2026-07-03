"""实验 B: 真实 GPU 并发探索 - 并发用户数 1→2→3→5 对延迟的影响。

验证 GPU 并发瓶颈和拐点。
"""

import asyncio, json, statistics, time
from pathlib import Path
import httpx

URL = "http://127.0.0.1:14344/v1/chat/completions"
KEY = "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "minicpm-v-4.6"
PROMPT = "Explain quantum computing"
MAX_TOKENS = 50

RESULTS_DIR = Path(__file__).parent.parent / "results" / "experiment_b"
CONCURRENCIES = [1, 2, 3, 5]
N_PER_LEVEL = 20

async def worker(client, sem, results):
    while True:
        try:
            async with sem:
                start = time.perf_counter()
                resp = await client.post(
                    URL,
                    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                    json={"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": MAX_TOKENS},
                    timeout=120,
                )
                elapsed = (time.perf_counter() - start) * 1000
                results.append({"latency_ms": elapsed, "ok": resp.status_code == 200, "status": resp.status_code})
        except asyncio.CancelledError:
            break
        except Exception as e:
            results.append({"latency_ms": 0, "ok": False, "error": str(e)})

async def run_concurrency(concurrency, n_requests):
    print(f"\n[Concurrency={concurrency}] {n_requests} requests total")
    results = []
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        workers = [asyncio.create_task(worker(client, sem, results)) for _ in range(concurrency)]
        await asyncio.sleep(0.5)
        # Wait until we have enough results
        while len(results) < n_requests:
            await asyncio.sleep(0.1)
        # Cancel workers
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    
    lats = [r["latency_ms"] for r in results[:n_requests] if r.get("ok")]
    if not lats:
        print("  All failed!")
        return None
    s = sorted(lats)
    summary = {
        "concurrency": concurrency,
        "n": len(lats),
        "mean_ms": round(statistics.mean(lats), 1),
        "median_ms": round(statistics.median(lats), 1),
        "p50_ms": round(s[int(len(s)*0.50)], 1),
        "p90_ms": round(s[int(len(s)*0.90)], 1),
        "p95_ms": round(s[int(len(s)*0.95)], 1),
        "max_ms": round(max(lats), 1),
        "failures": n_requests - len(lats),
    }
    print(f"  mean={summary['mean_ms']}ms, p90={summary['p90_ms']}ms, p95={summary['p95_ms']}ms, failures={summary['failures']}")
    return summary

async def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_summaries = []
    for c in CONCURRENCIES:
        summary = await run_concurrency(c, N_PER_LEVEL)
        if summary:
            all_summaries.append(summary)
    
    out = RESULTS_DIR / "gpu_concurrency.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_summaries, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out}")

if __name__ == "__main__":
    asyncio.run(main())