"""实例详情页 — 各实例的负载、延迟、token 消耗。"""

import requests
import streamlit as st

API_BASE = "http://localhost:9090"

st.title("🖥️ 实例详情")

try:
    resp = requests.get(f"{API_BASE}/admin/instances", timeout=5)
    if resp.status_code == 200:
        instances = resp.json()
        if instances:
            for inst in instances:
                with st.expander(f"{inst['instance_id']} — {inst['model']} ({inst['engine_type']})", expanded=True):
                    st.write(f"**地址**: {inst['address']}")
                    st.write(f"**状态**: {inst['status']}")
                    st.write(f"**引擎**: {inst['engine_type']}")
                    st.write(f"**容量因子**: {inst.get('capacity_factor', 1.0)}")
                    st.write(f"**最大并发**: {inst.get('max_concurrent', 10)}")
        else:
            st.info("暂未注册实例")
except requests.ConnectionError:
    st.warning("无法连接调度服务")
