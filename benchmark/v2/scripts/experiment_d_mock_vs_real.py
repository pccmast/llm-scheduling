"""实验 D: Mock v2 vs Real GPU 分布对比。

启动两个 Mock v2 引擎，与真实 GPU 在相同并发下对比延迟分布。
"""

import asyncio, json, statistics, subprocess, sys, time
from pathlib import Path
import httpx

MOCK_PORTS = [8001, 8002]
GPU_URL = "http://127.0.0.1:14344/v1/chat/completions"
GPU_KEY = "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG"
MODEL = "minicpm-v-4.6"
PROMPT = "Hi"
MAX_TOKENS = 10
CONCURRENCY = 4  # 匹配 LM Studio num_parallel_slots=4
N = 20

RESULTS_DIR = Path(__file__).parent.parent / "results" / "experiment_d"

def start_mock(port, mean_ms=2750, sigma=0.09):
    script = Path(__file__).parent.parent / "mock" / "mock_gpu_server_v2.py"
    cmd = [sys.executable, str(script), "--port", str(port), "--mean-ms", str(mean_ms), "--sigma", str(sigma)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)
    return proc

def stop_mock(proc):
    proc.terminate()
    proc.wait(timeout=5)

async def call_gpu(client):
    start = time.perf_counter()
    resp = await client.post(
        GPU_URL,
        headers={"Authorization": f"Bearer {GPU_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": MAX_TOKENS},
        timeout=60,
    )
    return (time.perf_counter() - start) * 1000, resp.status_code

async def call_mock(client, port):
    start = time.perf_counter()
    resp = await client.post(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        headers={"Authorization": f"Bearer {GPU_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": MAX_TOKENS},
        timeout=60,
    )
    return (time.perf_counter() - start) * 1000, resp.status_code

async def run_concurrent_test(client, target_fn, n, label):
    """并发发送 n 个请求。"""
    sem = asyncio.Semaphore(CONCURRENCY)
    results = []
    async def worker():
        for _ in range(n // CONCURRENCY):
            async with sem:
                t, code = await target_fn(client)
                results.append({"latency_ms": t, "ok": code == 200})
    await asyncio.gather(*[worker() for _ in range(CONCURRENCY)])
    
    lats = [r["latency_ms"] for r in results if r["ok"]]
    s = sorted(lats)
    summary = {
        "label": label,
        "n": len(lats),
        "mean_ms": round(statistics.mean(lats), 1),
        "median_ms": round(statistics.median(lats), 1),
        "p50_ms": round(s[int(len(s)*0.50)], 1),
        "p90_ms": round(s[int(len(s)*0.90)], 1),
        "p95_ms": round(s[int(len(s)*0.95)], 1),
        "std_ms": round(statistics.stdev(lats) if len(lats) > 1 else 0, 1),
    }
    print(f"  [{label}] mean={summary['mean_ms']}ms, p90={summary['p90_ms']}ms, p95={summary['p95_ms']}ms, std={summary['std_ms']}ms")
    return summary

async def main():
    print("[Experiment D] Mock v2 vs Real GPU comparison")
    print("=" * 60)
    
    # Start mocks
    procs = []
    for port in MOCK_PORTS:
        p = start_mock(port)
        procs.append(p)
    
    try:
        async with httpx.AsyncClient() as client:
            # Test GPU
            print("\n[Real GPU] concurrent=2, 20 requests...")
            gpu_summary = await run_concurrent_test(client, lambda c: call_gpu(c), N, "real_gpu")
            
            # Test Mock (round-robin between 2 mocks)
            mock_port_idx = [0]
            def mock_fn(c):
                port = MOCK_PORTS[mock_port_idx[0] % len(MOCK_PORTS)]
                mock_port_idx[0] += 1
                return call_mock(c, port)
            
            print("\n[Mock v2] concurrent=2, 20 requests...")
            mock_summary = await run_concurrent_test(client, mock_fn, N, "mock_v2")
            
            # Compare
            print("\n[Comparison]")
            mean_err = abs(mock_summary["mean_ms"] - gpu_summary["mean_ms"]) / gpu_summary["mean_ms"] * 100
            p90_err = abs(mock_summary["p90_ms"] - gpu_summary["p90_ms"]) / gpu_summary["p90_ms"] * 100
            p95_err = abs(mock_summary["p95_ms"] - gpu_summary["p95_ms"]) / gpu_summary["p95_ms"] * 100
            print(f"  Mean error: {mean_err:.1f}%")
            print(f"  P90 error:  {p90_err:.1f}%")
            print(f"  P95 error:  {p95_err:.1f}%")
            
            comparison = {
                "real_gpu": gpu_summary,
                "mock_v2": mock_summary,
                "errors": {
                    "mean_percent": round(mean_err, 1),
                    "p90_percent": round(p90_err, 1),
                    "p95_percent": round(p95_err, 1),
                },
                "config": {"mean_ms": 2750, "sigma": 0.09, "concurrency": CONCURRENCY, "n": N},
            }
            
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            out = RESULTS_DIR / "comparison.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(comparison, f, indent=2, ensure_ascii=False)
            print(f"\nResults saved to {out}")
    finally:
        for p in procs:
            stop_mock(p)

if __name__ == "__main__":
    asyncio.run(main())
