"""实验 F: 长稳测试 (Soak Test) - 在拐点前 80% 并发下跑 30 分钟。

如果阶梯实验 E 的拐点是 5 并发，则在 4 并发下跑 30 分钟。
"""

import asyncio, json, statistics, time
from pathlib import Path
import httpx

URL = "http://127.0.0.1:14344/v1/chat/completions"
KEY = "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "minicpm-v-4.6"
PROMPT = "Explain quantum computing"
MAX_TOKENS = 50

RESULTS_DIR = Path(__file__).parent.parent / "results" / "experiment_f"
CONCURRENCY = 4  # 根据实验 E 的拐点调整
DURATION_S = 1800  # 30 分钟

async def worker(client, sem, results, stop_event):
    while not stop_event.is_set():
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
                results.append({"latency_ms": elapsed, "ok": resp.status_code == 200, "status": resp.status_code, "ts": time.time()})
        except Exception as e:
            results.append({"latency_ms": 0, "ok": False, "error": str(e), "ts": time.time()})

async def run_soak(concurrency, duration_s):
    print(f"[Soak] concurrency={concurrency}, duration={duration_s//60}min")
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
        print("All failed!")
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
    print(f"RPS={summary['rps']}, mean={summary['mean_ms']}ms, p95={summary['p95_ms']}ms, failures={summary['failures']}")
    return summary

async def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = await run_soak(CONCURRENCY, DURATION_S)
    if summary:
        out = RESULTS_DIR / "soak_test.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Results saved to {out}")

if __name__ == "__main__":
    asyncio.run(main())