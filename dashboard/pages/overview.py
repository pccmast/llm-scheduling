"""全局概览页 — 实例状态、请求量、错误率、熔断器概览。"""

import requests
import streamlit as st

API_BASE = "http://localhost:9090"

st.title("📊 LLM Dispatcher — 全局概览")

# ── 辅助函数 ────────────────────────────────────────────────


def safe_get(endpoint: str) -> dict | None:
    """调用 dispatcher API，返回 JSON 或 None（附带错误显示）。"""
    try:
        resp = requests.get(f"{API_BASE}{endpoint}", timeout=5)
        if resp.status_code == 200:
            return resp.json()
        st.error(f"`{endpoint}` 返回 HTTP {resp.status_code}: {resp.text[:200]}")
    except requests.ConnectionError:
        st.warning(f"无法连接调度服务 (`{API_BASE}`) — 请确认服务已启动")
    except requests.Timeout:
        st.warning(f"`{endpoint}` 请求超时")
    return None


# ── 实例状态 ────────────────────────────────────────────────

data = safe_get("/admin/status")
if data:
    instances = data.get("instances", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("实例总数", instances.get("total", 0))
    col2.metric("健康", instances.get("healthy", 0))
    col3.metric("不健康", instances.get("unhealthy", 0))
    col4.metric("缩容中", instances.get("draining", 0))

    # 熔断器状态
    st.subheader("熔断器状态")
    circuit_states = data.get("circuit_breakers", {})
    if circuit_states:
        for inst_id, state in circuit_states.items():
            icon = {"closed": "🟢", "half_open": "🟡", "open": "🔴"}.get(state, "❓")
            st.write(f"{icon} `{inst_id}`: **{state}**")
    else:
        st.info("暂无熔断器数据")

    st.metric("队列深度", data.get("queue_depth", 0))

# ── 指标摘要 ────────────────────────────────────────────────

summary = safe_get("/admin/metrics/summary")
if summary:
    st.subheader("请求指标")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总请求", summary.get("request_count", 0))
    col2.metric("错误数", summary.get("error_count", 0))
    col3.metric("P95 延迟", f"{summary.get('p95_latency_ms', 0):.1f} ms")
    col4.metric("P99 延迟", f"{summary.get('p99_latency_ms', 0):.1f} ms")
