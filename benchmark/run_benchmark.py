""" - 


    # 
    uv run python benchmark/run_benchmark.py --experiment=baseline --users=50 --time=120

    # 
    uv run python benchmark/run_benchmark.py --experiment=strategy --users=50 --time=120

    # 
    uv run python benchmark/run_benchmark.py --experiment=circuit --users=50 --time=300

    # 
    uv run python benchmark/run_benchmark.py --experiment=queue --users=300 --time=120

    # 
    uv run python benchmark/run_benchmark.py --experiment=overhead --users=50 --time=60

    # 
    uv run python benchmark/run_benchmark.py --all


    baseline   - 
    strategy   - round_robin / least_conn / weighted
    circuit    - 
    queue      - 
    overhead   - mock =0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

#   

MOCK_PORTS = [8001, 8002, 8003]
MOCK_LATENCIES = [0.05, 0.10, 0.20]  # 50ms, 100ms, 200ms
DISPATCHER_PORT = 9090
BENCHMARK_DIR = Path(__file__).parent
RESULTS_DIR = BENCHMARK_DIR / "results"

# 
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
RESET = "\033[0m"


def ok(msg: str) -> str:
    return f"[OK] {msg}"


def fail(msg: str) -> str:
    return f"[FAIL] {msg}"


def info(msg: str) -> str:
    return f"[INFO] {msg}"


def blue(msg: str) -> str:
    return f"[RUN] {msg}"


#   

class ProcessManager:
    """ mock """

    def __init__(self):
        self.mock_processes: list[subprocess.Popen] = []
        self.dispatcher_process: subprocess.Popen | None = None

    def start_mock_engines(self, latencies: list[float] | None = None) -> None:
        """ mock """
        lats = latencies or MOCK_LATENCIES
        print(blue("[] Mock ..."))
        for port, latency in zip(MOCK_PORTS, lats, strict=False):
            cmd = [
                sys.executable,
                str(BENCHMARK_DIR.parent / "scripts" / "mock_engine.py"),
                f"--port={port}",
                f"--latency={latency}",
                "--engine=ollama",
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.mock_processes.append(proc)
            print(f"   {port},  {latency*1000:.0f}ms (PID: {proc.pid})")

    def start_dispatcher(self, strategy: str = "weighted") -> None:
        """"""
        print(blue(f"[]  (: {strategy})..."))

        # 
        config_path = BENCHMARK_DIR.parent / "config" / "default.yaml"
        original_config = config_path.read_text(encoding="utf-8")
        modified_config = original_config.replace(
            'strategy: "weighted"',
            f'strategy: "{strategy}"',
        )
        config_path.write_text(modified_config, encoding="utf-8")

        cmd = [
            sys.executable,
            "-m", "uvicorn",
            "src.dispatcher.main:create_app",
            "--factory",
            f"--port={DISPATCHER_PORT}",
            "--log-level=warning",
        ]
        self.dispatcher_process = subprocess.Popen(
            cmd,
            cwd=str(BENCHMARK_DIR.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        print(f"   {DISPATCHER_PORT} (PID: {self.dispatcher_process.pid})")

        # 
        config_path.write_text(original_config, encoding="utf-8")

    def stop_all(self) -> None:
        """"""
        print(blue("[] ..."))
        for proc in self.mock_processes:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        self.mock_processes.clear()

        if self.dispatcher_process:
            try:
                self.dispatcher_process.terminate()
                self.dispatcher_process.wait(timeout=5)
            except Exception:
                self.dispatcher_process.kill()
            self.dispatcher_process = None

        print(ok("  All services stopped"))


#   

async def wait_for_service(url: str, timeout: float = 30) -> bool:
    """"""
    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(url, timeout=2)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
    return False


async def register_instances() -> bool:
    """ mock """
    print(blue("[] ..."))
    async with httpx.AsyncClient() as client:
        for i, port in enumerate(MOCK_PORTS):
            inst_id = f"mock-{['fast', 'medium', 'slow'][i]}"
            r = await client.post(
                f"http://localhost:{DISPATCHER_PORT}/admin/instances",
                json={
                    "instance_id": inst_id,
                    "address": f"http://localhost:{port}",
                    "model": "test-model",
                    "engine_type": "ollama",
                    "capacity_factor": 1.0,
                },
            )
            status = ok("OK") if r.status_code == 200 else fail("FAIL")
            print(f"  {status} {inst_id} (port {port}): HTTP {r.status_code}")
            if r.status_code != 200:
                return False
    return True


async def inject_fault(port: int, count: int) -> None:
    """ mock """
    async with httpx.AsyncClient() as client:
        r = await client.post(f"http://localhost:{port}/admin/fail/{count}")
        print(info(f"  [] port {port}:  {count}  500"))


async def reset_fault(port: int) -> None:
    """ mock """
    async with httpx.AsyncClient() as client:
        r = await client.post(f"http://localhost:{port}/admin/reset")
        print(info(f"  [] port {port}: "))


#  Locust  

def run_locust(
    users: int,
    run_time: int,
    tags: str | None = None,
    env_vars: dict[str, str] | None = None,
) -> dict:
    """ locust """
    print(blue(f"[] locust: {users} , {run_time}s..."))

    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)

    cmd = [
        sys.executable,
        "-m", "locust",
        "-f", str(BENCHMARK_DIR / "locustfile.py"),
        f"--host=http://localhost:{DISPATCHER_PORT}",
        f"--users={users}",
        "--spawn-rate=10",
        f"--run-time={run_time}s",
        "--headless",
        "--only-summary",
    ]
    if tags:
        cmd.extend(["--tags", tags])

    proc = subprocess.run(
        cmd,
        cwd=str(BENCHMARK_DIR.parent),
        env=env,
        capture_output=True,
        text=True,
    )

    #  locust 
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr and "warning" not in proc.stderr.lower():
        print(proc.stderr)

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


#   

async def run_baseline_experiment(pm: ProcessManager, users: int, run_time: int) -> dict:
    """ - """
    print(f"\n{'='*60}")
    print(f"   (weighted, {users} )")
    print(f"{'='*60}\n")

    pm.start_mock_engines()
    for port in MOCK_PORTS:
        if not await wait_for_service(f"http://localhost:{port}/health"):
            print(fail(f"  Mock  {port} "))
            return {}

    pm.start_dispatcher(strategy="weighted")
    if not await wait_for_service(f"http://localhost:{DISPATCHER_PORT}/health"):
        print(fail("  "))
        return {}

    if not await register_instances():
        return {}

    #  10 
    print(info("  [Warmup] 10 seconds..."))
    await asyncio.sleep(10)

    result = run_locust(
        users=users,
        run_time=run_time,
        env_vars={"BALANCER_STRATEGY": "weighted"},
    )

    return {
        "experiment": "baseline",
        "strategy": "weighted",
        "users": users,
        "run_time": run_time,
        "result": result,
    }


async def run_strategy_experiment(pm: ProcessManager, users: int, run_time: int) -> list[dict]:
    """"""
    print(f"\n{'='*60}")
    print(f"   ({users} )")
    print(f"{'='*60}\n")

    results = []
    strategies = ["round_robin", "least_conn", "weighted"]

    for strategy in strategies:
        print(f"\n{blue(f'[] {strategy}')}")

        pm.start_mock_engines()
        for port in MOCK_PORTS:
            if not await wait_for_service(f"http://localhost:{port}/health"):
                print(fail(f"  Mock  {port} "))
                continue

        pm.start_dispatcher(strategy=strategy)
        if not await wait_for_service(f"http://localhost:{DISPATCHER_PORT}/health"):
            print(fail("  "))
            continue

        if not await register_instances():
            continue

        print(info("  [Warmup] 10 seconds..."))
        await asyncio.sleep(10)

        result = run_locust(
            users=users,
            run_time=run_time,
            env_vars={"BALANCER_STRATEGY": strategy},
        )

        results.append({
            "experiment": "strategy",
            "strategy": strategy,
            "users": users,
            "run_time": run_time,
            "result": result,
        })

        pm.stop_all()
        await asyncio.sleep(2)  # 

    return results


async def run_circuit_experiment(pm: ProcessManager, users: int, run_time: int) -> dict:
    """"""
    print(f"\n{'='*60}")
    print(f"   ({users} , {run_time}s)")
    print(f"{'='*60}\n")

    pm.start_mock_engines()
    for port in MOCK_PORTS:
        if not await wait_for_service(f"http://localhost:{port}/health"):
            print(fail(f"  Mock  {port} "))
            return {}

    pm.start_dispatcher(strategy="weighted")
    if not await wait_for_service(f"http://localhost:{DISPATCHER_PORT}/health"):
        print(fail("  "))
        return {}

    if not await register_instances():
        return {}

    print(info("  [Warmup] 10 seconds..."))
    await asyncio.sleep(10)

    #  locust
    print(blue(f"[]  locust: {users} ..."))
    env = os.environ.copy()
    env["BALANCER_STRATEGY"] = "weighted"

    locust_proc = subprocess.Popen(
        [
            sys.executable,
            "-m", "locust",
            "-f", str(BENCHMARK_DIR / "locustfile.py"),
            f"--host=http://localhost:{DISPATCHER_PORT}",
            f"--users={users}",
            "--spawn-rate=10",
            f"--run-time={run_time}s",
            "--headless",
            "--only-summary",
        ],
        cwd=str(BENCHMARK_DIR.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # 
    await asyncio.sleep(60)   # T=60s: 
    print(info("  [T=60s]  mock-fast (port 8001)..."))
    await inject_fault(8001, 50)

    await asyncio.sleep(60)   # T=120s: 
    print(info("  [T=120s] ..."))

    await asyncio.sleep(60)   # T=180s: 
    print(info("  [T=180s] ..."))
    await reset_fault(8001)

    await asyncio.sleep(60)   # T=240s: 
    print(info("  [T=240s] ..."))

    #  locust 
    remaining = run_time - 240
    if remaining > 0:
        await asyncio.sleep(remaining)

    locust_proc.wait(timeout=30)
    stdout = locust_proc.stdout.read() if locust_proc.stdout else ""
    stderr = locust_proc.stderr.read() if locust_proc.stderr else ""

    if stdout:
        print(stdout)
    if stderr:
        print(stderr)

    return {
        "experiment": "circuit",
        "users": users,
        "run_time": run_time,
        "result": {"returncode": locust_proc.returncode, "stdout": stdout, "stderr": stderr},
    }


async def run_queue_experiment(pm: ProcessManager, users: int, run_time: int) -> dict:
    """"""
    print(f"\n{'='*60}")
    print(f"   ({users} , {run_time}s)")
    print(f"{'='*60}\n")

    # 
    config_path = BENCHMARK_DIR.parent / "config" / "default.yaml"
    original_config = config_path.read_text(encoding="utf-8")
    modified_config = original_config.replace(
        "max_size: 100",
        "max_size: 50",
    )
    config_path.write_text(modified_config, encoding="utf-8")

    pm.start_mock_engines()
    for port in MOCK_PORTS:
        if not await wait_for_service(f"http://localhost:{port}/health"):
            print(fail(f"  Mock  {port} "))
            config_path.write_text(original_config, encoding="utf-8")
            return {}

    pm.start_dispatcher(strategy="weighted")
    if not await wait_for_service(f"http://localhost:{DISPATCHER_PORT}/health"):
        print(fail("  "))
        config_path.write_text(original_config, encoding="utf-8")
        return {}

    if not await register_instances():
        config_path.write_text(original_config, encoding="utf-8")
        return {}

    print(info("  [Warmup] 10 seconds..."))
    await asyncio.sleep(10)

    result = run_locust(
        users=users,
        run_time=run_time,
        env_vars={"BALANCER_STRATEGY": "weighted"},
    )

    # 
    config_path.write_text(original_config, encoding="utf-8")

    return {
        "experiment": "queue",
        "users": users,
        "run_time": run_time,
        "result": result,
    }


async def run_overhead_experiment(pm: ProcessManager, users: int, run_time: int) -> dict:
    """mock =0"""
    print(f"\n{'='*60}")
    print(f"   ({users} , {run_time}s, mock =0)")
    print(f"{'='*60}\n")

    pm.start_mock_engines(latencies=[0, 0, 0])
    for port in MOCK_PORTS:
        if not await wait_for_service(f"http://localhost:{port}/health"):
            print(fail(f"  Mock  {port} "))
            return {}

    pm.start_dispatcher(strategy="weighted")
    if not await wait_for_service(f"http://localhost:{DISPATCHER_PORT}/health"):
        print(fail("  "))
        return {}

    if not await register_instances():
        return {}

    print(info("  [Warmup] 10 seconds..."))
    await asyncio.sleep(10)

    result = run_locust(
        users=users,
        run_time=run_time,
        env_vars={"BALANCER_STRATEGY": "weighted", "MOCK_LATENCY": "0"},
    )

    return {
        "experiment": "overhead",
        "users": users,
        "run_time": run_time,
        "result": result,
    }


#   

async def main():
    parser = argparse.ArgumentParser(description="LLM Dispatcher ")
    parser.add_argument("--experiment", choices=["baseline", "strategy", "circuit", "queue", "overhead"], help="")
    parser.add_argument("--users", type=int, default=50, help="")
    parser.add_argument("--time", type=int, default=120, help="")
    parser.add_argument("--all", action="store_true", help="")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    pm = ProcessManager()

    all_results = []

    try:
        if args.all or args.experiment == "overhead":
            r = await run_overhead_experiment(pm, users=50, run_time=60)
            all_results.append(r)
            pm.stop_all()
            await asyncio.sleep(2)

        if args.all or args.experiment == "baseline":
            for users in [10, 20, 50, 100, 200]:
                r = await run_baseline_experiment(pm, users=users, run_time=120)
                all_results.append(r)
                pm.stop_all()
                await asyncio.sleep(2)

        if args.all or args.experiment == "strategy":
            rs = await run_strategy_experiment(pm, users=args.users, run_time=args.time)
            all_results.extend(rs)

        if args.all or args.experiment == "circuit":
            r = await run_circuit_experiment(pm, users=args.users, run_time=args.time)
            all_results.append(r)
            pm.stop_all()
            await asyncio.sleep(2)

        if args.all or args.experiment == "queue":
            r = await run_queue_experiment(pm, users=300, run_time=120)
            all_results.append(r)
            pm.stop_all()
            await asyncio.sleep(2)

    except KeyboardInterrupt:
        print(info("\n[] "))
    finally:
        pm.stop_all()

    # 
    summary_path = RESULTS_DIR / "benchmark_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n{ok('Benchmark complete!')}")
    print(f"  Summary: {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
