"""实例详情 — 每实例配置、状态、v4 指标。"""
import requests
import streamlit as st

API_BASE = "http://127.0.0.1:9090"
st.title("🖥️ 实例详情")

try:
    resp = requests.get(f"{API_BASE}/admin/instances", timeout=5)
    if resp.status_code != 200:
        st.error(f"HTTP {resp.status_code}")
        st.stop()
    instances = resp.json()
except requests.ConnectionError:
    st.warning(f"无法连接 ({API_BASE})")
    st.stop()

if not instances:
    st.info("暂未注册实例")
    st.stop()

# 获取 balancer 内部状态
try:
    balancer_data = requests.get(f"{API_BASE}/admin/balancer/debug", timeout=5).json()
    balancer_info = balancer_data.get("instances", {})
except Exception:
    balancer_info = {}

for inst in instances:
    iid = inst["instance_id"]
    status = inst.get("status", "healthy")
    icon = {"healthy": "🟢", "unhealthy": "🔴", "draining": "🟡"}.get(status, "❓")
    tier = inst.get("tier", "local")
    tier_icon = "🏠" if tier == "local" else "☁️"

    bi = balancer_info.get(iid, {})
    cooldown_mark = " 🧊 冷却中" if bi.get("in_cooldown") else ""

    with st.expander(
        f"{icon}{tier_icon} {iid} — {inst['model']} ({inst['engine_type']}){cooldown_mark}",
        expanded=True,
    ):
        c1, c2, c3 = st.columns(3)
        c1.write(f"**地址**: `{inst['address']}`")
        c1.write(f"**引擎**: {inst['engine_type']}")
        c1.write(f"**层级**: {tier}")
        c1.write(f"**API Key**: `{inst.get('api_key_env', '(fallback)')}`")

        c2.write(f"**状态**: {status}")
        c2.write(f"**容量因子**: {inst.get('capacity_factor', 1.0)}")
        c2.write(f"**最大并发**: {inst.get('max_concurrent', 10)}")

        # v4 指标
        if bi:
            c3.metric("Speed", f"{bi.get('speed_tok_s', '?')} tok/s")
            c3.metric("Reliability", f"{bi.get('reliability', '?')}")
            c3.metric("Load", f"{bi.get('load', '?')}")
