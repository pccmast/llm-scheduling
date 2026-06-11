"""全局概览页 — 实例状态、请求量、错误率概览。"""

import requests
import streamlit as st

API_BASE = "http://localhost:9090"

st.title("📊 LLM Dispatcher — 全局概览")

try:
    resp = requests.get(f"{API_BASE}/admin/status", timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        instances = data.get("instances", {})
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("实例总数", instances.get("total", 0))
        col2.metric("健康", instances.get("healthy", 0), delta=None, delta_color="off")
        col3.metric("不健康", instances.get("unhealthy", 0), delta_color="inverse")
        col4.metric("缩容中", instances.get("draining", 0))

        st.subheader("熔断器状态")
        circuit_states = data.get("circuit_breakers", {})
        if circuit_states:
            for inst_id, state in circuit_states.items():
                color = "🟢" if state == "closed" else ("🟡" if state == "half_open" else "🔴")
                st.write(f"{color} `{inst_id}`: **{state}**")
        else:
            st.info("暂无熔断器数据")

        st.metric("队列深度", data.get("queue_depth", 0))
    else:
        st.error(f"无法连接调度服务: {resp.status_code}")

    # 指标摘要
    resp2 = requests.get(f"{API_BASE}/admin/metrics/summary", timeout=5)
    if resp2.status_code == 200:
        summary = resp2.json()
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("总请求", summary.get("request_count", 0))
        col2.metric("错误数", summary.get("error_count", 0))
        col3.metric("P95 延迟", f"{summary.get('p95_latency_ms', 0):.1f}ms")
        col4.metric("P99 延迟", f"{summary.get('p99_latency_ms', 0):.1f}ms")
except requests.ConnectionError:
    st.warning("无法连接到调度服务。请确保服务运行在 http://localhost:9090")
