"""实验 C: Mock GPU v2 校准 - 调整 mock 分布与真实 GPU 分布相似。

输出校准后的参数，供实验 D 使用。
"""

import json, statistics, time
from pathlib import Path
import httpx
import numpy as np

# 真实 GPU 结果（实验 A 测得，如果还没跑实验 A，先用之前的快速测试数据）
REAL_DATA = {
    "short": {"mean": 2888, "median": 2789, "p90": 3100, "p95": 3200, "std": 150, "n": 6},
    "medium": {"mean": 5818, "median": 5750, "p90": 5980, "p95": 6000, "std": 120, "n": 3},
}

MOCK_URLS = ["http://localhost:8001", "http://localhost:8002"]

def mock_distribution(mean_ms, sigma, n=100):
    """模拟 mock 的延迟分布。"""
    mu = np.log(mean_ms) - sigma**2 / 2.0
    samples = np.random.lognormal(mu, sigma, n)
    # 2% 长尾
    n_long = int(n * 0.02)
    for i in range(n_long):
        samples[i] *= 3.0
    samples = np.maximum(samples, 50.0)
    s = sorted(samples)
    return {
        "mean": round(float(np.mean(samples)), 1),
        "median": round(float(np.median(samples)), 1),
        "p90": round(float(s[int(n*0.90)]), 1),
        "p95": round(float(s[int(n*0.95)]), 1),
        "std": round(float(np.std(samples)), 1),
    }

def calibrate():
    """校准参数: 寻找最佳 mean_ms 和 sigma 使 mock 分布匹配真实数据。"""
    print("Mock GPU v2 Calibration")
    print("=" * 60)
    
    best = None
    best_err = float('inf')
    
    # 搜索参数空间
    for mean_ms in range(2700, 3100, 50):
        for sigma in [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15]:
            mock = mock_distribution(mean_ms, sigma, n=1000)
            # 误差: 主要匹配均值和p90
            err = abs(mock["mean"] - REAL_DATA["short"]["mean"]) / REAL_DATA["short"]["mean"]
            err += abs(mock["p90"] - REAL_DATA["short"]["p90"]) / REAL_DATA["short"]["p90"] * 0.5
            err += abs(mock["p95"] - REAL_DATA["short"]["p95"]) / REAL_DATA["short"]["p95"] * 0.3
            
            if err < best_err:
                best_err = err
                best = {"mean_ms": mean_ms, "sigma": sigma, "mock": mock, "err": err}
    
    print(f"Best match: mean_ms={best['mean_ms']}, sigma={best['sigma']}")
    print(f"  Mock: mean={best['mock']['mean']}ms, p90={best['mock']['p90']}ms, p95={best['mock']['p95']}ms")
    print(f"  Real: mean={REAL_DATA['short']['mean']}ms, p90={REAL_DATA['short']['p90']}ms, p95={REAL_DATA['short']['p95']}ms")
    print(f"  Error: {best['err']*100:.1f}%")
    
    # 保存校准参数
    RESULTS_DIR = Path(__file__).parent.parent / "results" / "experiment_c"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "mean_ms": best["mean_ms"],
        "sigma": best["sigma"],
        "long_tail_prob": 0.02,
        "long_tail_mult": 3.0,
        "max_concurrent": 2,
        "source": "calibrated from real GPU GTX1650 + minicpm-v-4.6",
        "real_data": REAL_DATA,
    }
    out = RESULTS_DIR / "mock_config.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"\nCalibration config saved to {out}")
    return config

if __name__ == "__main__":
    np.random.seed(42)
    calibrate()