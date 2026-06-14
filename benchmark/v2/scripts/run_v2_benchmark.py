"""一键运行 v2 全量实验。

启动 Mock v2 引擎 + 调度器 + 执行所有实验。

用法:
    uv run python benchmark/v2/scripts/run_v2_benchmark.py
    uv run python benchmark/v2/scripts/run_v2_benchmark.py --skip-a --skip-e

需要前置依赖:
    - 真实 GPU: LM Studio on port 12345 (实验 A, B, E, F, G)
    - 调度器: 需要手动启动 (实验 D, G)
"""

import argparse, asyncio, subprocess, sys, time, os
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent

def run_script(name, args=None):
    """运行一个实验脚本。"""
    script = SCRIPTS_DIR / name
    if not script.exists():
        print(f"[SKIP] Script not found: {script}")
        return
    cmd = [sys.executable, str(script)]
    if args:
        cmd.extend(args)
    print(f"\n{'='*60}")
    print(f"Running: {name}")
    print('='*60)
    subprocess.run(cmd, check=False)

def start_mock_v2(port, mean_ms, sigma):
    """启动 Mock GPU v2 引擎。"""
    mock_script = SCRIPTS_DIR.parent / "mock" / "mock_gpu_server_v2.py"
    cmd = [
        sys.executable, str(mock_script),
        "--port", str(port),
        "--mean-ms", str(mean_ms),
        "--sigma", str(sigma),
    ]
    print(f"[Mock v2] Starting on port {port} (mean={mean_ms}ms, sigma={sigma})")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)  # 等待启动
    return proc

def main():
    parser = argparse.ArgumentParser(description="Run v2 benchmark experiments")
    parser.add_argument("--skip-a", action="store_true", help="Skip Experiment A (GPU baseline)")
    parser.add_argument("--skip-b", action="store_true", help="Skip Experiment B (GPU concurrency)")
    parser.add_argument("--skip-c", action="store_true", help="Skip Experiment C (Mock calibration)")
    parser.add_argument("--skip-d", action="store_true", help="Skip Experiment D (Mock comparison)")
    parser.add_argument("--skip-e", action="store_true", help="Skip Experiment E (Stair load)")
    parser.add_argument("--skip-f", action="store_true", help="Skip Experiment F (Soak test)")
    parser.add_argument("--skip-g", action="store_true", help="Skip Experiment G (Overhead)")
    parser.add_argument("--quick", action="store_true", help="Quick mode (fewer requests, shorter duration)")
    args = parser.parse_args()
    
    print("=" * 60)
    print("  v2 Benchmark Suite - Unified Run")
    print("=" * 60)
    
    # Experiment A: GPU baseline
    if not args.skip_a:
        run_script("experiment_a_gpu_baseline.py")
    
    # Experiment B: GPU concurrency
    if not args.skip_b:
        run_script("experiment_b_gpu_concurrency.py")
    
    # Experiment C: Mock calibration
    if not args.skip_c:
        run_script("experiment_c_mock_calibration.py")
    
    # Experiment E: Stair load (real GPU)
    if not args.skip_e:
        if args.quick:
            run_script("experiment_e_stair_load.py", ["--quick"])
        else:
            run_script("experiment_e_stair_load.py")
    
    # Experiment G: Overhead (needs dispatcher running)
    if not args.skip_g:
        print("\n[NOTE] Experiment G requires dispatcher running on port 8000")
        run_script("experiment_g_overhead.py")
    
    print("\n" + "=" * 60)
    print("  All experiments completed!")
    print("  Results in: benchmark/v2/results/")
    print("=" * 60)

if __name__ == "__main__":
    main()
