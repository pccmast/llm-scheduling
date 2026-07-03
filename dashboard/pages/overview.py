"""全局概览 — 实例状态、请求量、错误率、熔断器。"""
import requests
import streamlit as st

API_BASE = "http://127.0.0.1:9090"

def safe_get(endpoint: str) -> dict | None:
    try:
        resp = requests.get(f"{API_BASE}{endpoint}", timeout=5)
        if resp.status_code == 200:
            return resp.json()
        st.error(f"`{endpoint}` HTTP {resp.status_code}")
    except requests.ConnectionError:
        st.warning(f"无法连接 ({API_BASE}) — 请确认服务已启动")
    except requests.Timeout:
        st.warning(f"`{endpoint}` 超时")
    return None

st.title("📊 LLM Dispatcher v4 — 全局概览")

# ── 实例状态 ──
data = safe_get("/admin/status")
if data:
    inst = data.get("instances", {})
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("实例总数", inst.get("total", 0))
    c2.metric("健康", inst.get("healthy", 0))
    c3.metric("不健康", inst.get("unhealthy", 0))
    c4.metric("缩容中", inst.get("draining", 0))
    c5.metric("队列深度", data.get("queue_depth", 0))

    # 熔断器
    st.subheader("熔断器状态")
    cb = data.get("circuit_breakers", {})
    if cb:
        cols = st.columns(len(cb))
        for i, (iid, state) in enumerate(cb.items()):
            icon = {"closed": "🟢", "half_open": "🟡", "open": "🔴"}.get(state, "❓")
            cols[i].metric(iid, f"{icon} {state}")
    else:
        st.info("暂无熔断器数据")

# ── 指标摘要 ──
summary = safe_get("/admin/metrics/summary")
if summary:
    st.subheader("请求指标")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("总请求", summary.get("request_count", 0))
    c2.metric("错误数", summary.get("error_count", 0))
    c3.metric("P95 延迟", f"{summary.get('p95_latency_ms', 0):.0f} ms")
    c4.metric("P99 延迟", f"{summary.get('p99_latency_ms', 0):.0f} ms")

    # 每实例指标
    pi = summary.get("per_instance", {})
    if pi:
        st.subheader("每实例请求分布")
        cols = st.columns(len(pi))
        for i, (iid, s) in enumerate(pi.items()):
            cols[i].metric(iid, f"{s['request_count']} req", f"avg {s['avg_latency_ms']:.0f}ms")
