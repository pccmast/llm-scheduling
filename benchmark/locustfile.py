"""Locust  - LLM Dispatcher 


    #  mock 
    uv run locust -f benchmark/locustfile.py --host=http://127.0.0.1:9090 --users=50 --spawn-rate=10 --run-time=2m --headless

    # 
    uv run locust -f benchmark/locustfile.py --host=http://127.0.0.1:9090 --tags=baseline --users=50 --spawn-rate=10 --run-time=2m --headless


    BALANCER_STRATEGY: 
    MOCK_LATENCY: mock 
"""

from __future__ import annotations

import os
import random
import time
from typing import Any

from locust import HttpUser, between, events, tag, task

#   

BALANCER_STRATEGY = os.environ.get("BALANCER_STRATEGY", "weighted")
MOCK_LATENCY_MS = float(os.environ.get("MOCK_LATENCY", "0.1")) * 1000  #  100ms

# 
SHORT_REQUEST_RATIO = 0.8
LONG_REQUEST_RATIO = 0.2

# 
SHORT_PROMPT = "hi"
SHORT_MAX_TOKENS = 50

#  prompt
LONG_PROMPT = "" * 10
LONG_MAX_TOKENS = 1024


#   

class GlobalStats:
    """ locust  user  greenlet"""

    request_count = 0
    success_count = 0
    failure_count = 0
    latency_sum = 0.0
    latencies: list[float] = []
    instance_distribution: dict[str, int] = {}
    status_distribution: dict[int, int] = {}

    @classmethod
    def reset(cls):
        cls.request_count = 0
        cls.success_count = 0
        cls.failure_count = 0
        cls.latency_sum = 0.0
        cls.latencies = []
        cls.instance_distribution = {}
        cls.status_distribution = {}

    @classmethod
    def record(cls, latency_ms: float, status_code: int, instance_port: str | None = None):
        cls.request_count += 1
        cls.latency_sum += latency_ms
        cls.latencies.append(latency_ms)
        cls.status_distribution[status_code] = cls.status_distribution.get(status_code, 0) + 1

        if status_code == 200:
            cls.success_count += 1
        else:
            cls.failure_count += 1

        if instance_port:
            cls.instance_distribution[instance_port] = cls.instance_distribution.get(instance_port, 0) + 1


#  Locust  

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    GlobalStats.reset()
    print(f"\n{'='*60}")
    print(f"  ")
    print(f"  : {BALANCER_STRATEGY}")
    print(f"  Mock : {MOCK_LATENCY_MS:.0f}ms")
    print(f"  : {environment.host}")
    print(f"{'='*60}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print(f"\n{'='*60}")
    print(f"  ")
    print(f"  : {GlobalStats.request_count}")
    print(f"  : {GlobalStats.success_count}")
    print(f"  : {GlobalStats.failure_count}")

    if GlobalStats.latencies:
        latencies_sorted = sorted(GlobalStats.latencies)
        n = len(latencies_sorted)
        p50 = latencies_sorted[int(n * 0.50)]
        p95 = latencies_sorted[int(n * 0.95)]
        p99 = latencies_sorted[min(int(n * 0.99), n - 1)]
        avg = GlobalStats.latency_sum / n

        print(f"  : {avg:.1f}ms")
        print(f"  P50: {p50:.1f}ms")
        print(f"  P95: {p95:.1f}ms")
        print(f"  P99: {p99:.1f}ms")

        # 
        if MOCK_LATENCY_MS > 0:
            overhead_p99 = p99 - MOCK_LATENCY_MS
            print(f"  (P99): {overhead_p99:.1f}ms")

    if GlobalStats.instance_distribution:
        print(f"\n  :")
        total = sum(GlobalStats.instance_distribution.values())
        for port, count in sorted(GlobalStats.instance_distribution.items()):
            pct = count / total * 100
            print(f"     {port}: {count}  ({pct:.1f}%)")

    if GlobalStats.status_distribution:
        print(f"\n  :")
        for code, count in sorted(GlobalStats.status_distribution.items()):
            pct = count / GlobalStats.request_count * 100
            print(f"    HTTP {code}: {count} ({pct:.1f}%)")

    print(f"{'='*60}\n")


#   

class DispatcherUser(HttpUser):
    """ LLM Dispatcher """

    #  0.1-0.5 
    wait_time = between(0.1, 0.5)

    def _extract_instance_port(self, response) -> str | None:
        """ mock """
        try:
            if response.status_code == 200:
                data = response.json()
                content = ""
                if "choices" in data:
                    content = data["choices"][0].get("message", {}).get("content", "")
                elif "response" in data:
                    content = data["response"]
                elif "generated_text" in data:
                    content = data["generated_text"]

                # mock  "Mock engine  8001"
                for port in ["8001", "8002", "8003"]:
                    if f" {port}" in content or f"port {port}" in content or f"on port {port}" in content:
                        return port
        except Exception:
            pass
        return None

    def _make_request(self, is_long: bool) -> None:
        """"""
        if is_long:
            payload = {
                "model": "test-model",
                "messages": [{"role": "user", "content": LONG_PROMPT}],
                "max_tokens": LONG_MAX_TOKENS,
            }
        else:
            payload = {
                "model": "test-model",
                "messages": [{"role": "user", "content": SHORT_PROMPT}],
                "max_tokens": SHORT_MAX_TOKENS,
            }

        start = time.time()
        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            catch_response=True,
            timeout=30,
        ) as response:
            latency_ms = (time.time() - start) * 1000
            instance_port = self._extract_instance_port(response)
            GlobalStats.record(latency_ms, response.status_code, instance_port)

            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")

    @task(8)
    @tag("short", "baseline", "strategy", "circuit", "queue", "overhead")
    def short_request(self):
        """ - 80%"""
        self._make_request(is_long=False)

    @task(2)
    @tag("long", "baseline", "strategy", "circuit", "queue", "overhead")
    def long_request(self):
        """ - 20%"""
        self._make_request(is_long=True)

    @task(0)
    @tag("admin", "circuit")
    def check_status(self):
        """"""
        with self.client.get("/admin/status", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
