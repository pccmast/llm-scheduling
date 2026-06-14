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
                status_icon = {"healthy": "🟢", "unhealthy": "🔴", "draining": "🟡"}.get(
                    inst.get("status", ""), "❓"
                )
                with st.expander(
                    f"{status_icon} {inst['instance_id']} — {inst['model']} ({inst['engine_type']})",
                    expanded=True,
                ):
                    col1, col2 = st.columns(2)
                    col1.write(f"**地址**: `{inst['address']}`")
                    col1.write(f"**引擎**: {inst['engine_type']}")
                    col2.write(f"**状态**: {inst['status']}")
                    col2.write(f"**容量因子**: {inst.get('capacity_factor', 1.0)}")
                    col2.write(f"**最大并发**: {inst.get('max_concurrent', 10)}")
        else:
            st.info("暂未注册实例 — 通过 `POST /admin/instances` 注册")
    else:
        st.error(f"实例接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
except requests.ConnectionError:
    st.warning(f"无法连接调度服务 (`{API_BASE}`) — 请确认服务已启动")
except requests.Timeout:
    st.warning("实例接口请求超时")
