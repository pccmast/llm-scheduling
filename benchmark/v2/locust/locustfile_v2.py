"""Locust v2 - 负载测试脚本，支持真实 GPU 和 Mock 引擎。

用法:
    # 直连真实 GPU
    uv run locust -f benchmark/v2/locust/locustfile_v2.py --host=http://127.0.0.1:14344

    # 通过 Dispatcher (先注册实例)
    uv run locust -f benchmark/v2/locust/locustfile_v2.py --host=http://127.0.0.1:9090

环境变量 (优先级高于默认值):
    BACKEND_API_KEY  -- 后端 API Key (默认 sk-lm-xxx)
    BENCH_MODEL      -- 压测目标模型名 (默认 qwen/qwen3-1.7b)
"""

from locust import HttpUser, task, between, events
import random, time, json, os
from pathlib import Path

# ── 配置 (环境变量可覆盖) ────────────────────────────────────
GPU_KEY = os.environ.get("BACKEND_API_KEY", "sk-lm-cR1GVaJg:W0pVeY0MwNSkiaJY3llG")
MODEL = os.environ.get("BENCH_MODEL", "qwen/qwen3-1.7b")

# GPU 模式: 200-token 长请求 (需要 GPU, ~2-5s 延迟)
PROMPTS_GPU = [
    ("Hi", 10, 0.5),
    ("Hello, how are you?", 20, 0.3),
    ("Explain quantum computing in one sentence", 50, 0.15),
    ("Explain quantum computing and its real-world applications", 200, 0.05),
]

# CPU 模式: 短请求为主, max 20 tokens (CPU ~2-3 token/s)
PROMPTS_CPU = [
    ("Hi", 5, 0.4),
    ("Say hello world", 8, 0.3),
    ("What is 1+1? Answer briefly", 12, 0.2),
    ("Name three colors", 20, 0.1),
]

# 根据 BENCH_CPU_MODE=1 切换 prompt 集
if os.environ.get("BENCH_CPU_MODE", "").lower() in ("1", "true", "yes"):
    PROMPTS = PROMPTS_CPU
else:
    PROMPTS = PROMPTS_GPU

class GPUUser(HttpUser):
    """Locust user 模拟真实用户。"""
    wait_time = between(1, 3)  # 1-3秒思考时间
    
    def on_start(self):
        self.client.headers["Authorization"] = f"Bearer {GPU_KEY}"
        self.client.headers["Content-Type"] = "application/json"
    
    @task(10)
    def short_request(self):
        self._make_request(0)
    
    @task(6)
    def medium_request(self):
        self._make_request(1)
    
    @task(3)
    def long_request(self):
        self._make_request(2)
    
    @task(1)
    def very_long_request(self):
        self._make_request(3)
    
    def _make_request(self, idx):
        prompt, max_tokens, _ = PROMPTS[idx]
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        with self.client.post("/v1/chat/completions", json=payload, catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Status {resp.status_code}")


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, context, exception, **kwargs):
    """记录每次请求的数据。"""
    pass  # 默认使用 Locust 自带的统计


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """退出时保存结果。"""
    stats = environment.stats
    summary = {
        "total_requests": stats.total.num_requests,
        "failures": stats.total.num_failures,
        "mean_response_time": stats.total.avg_response_time,
        "median_response_time": stats.total.median_response_time,
        "p95_response_time": stats.total.get_response_time_percentile(0.95),
        "p99_response_time": stats.total.get_response_time_percentile(0.99),
        "rps": round(stats.total.num_requests / max(1, stats.total.last_request_timestamp - stats.total.start_time), 2),
    }
    
    results_dir = Path(__file__).parent.parent / "results" / "locust"
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / "locust_summary.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nLocust results saved to {out}")
