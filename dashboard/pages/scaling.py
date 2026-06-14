"""扩缩容页 — 评估结果和决策日志。"""

import requests
import streamlit as st

API_BASE = "http://localhost:9090"

st.title("📈 扩缩容决策")

try:
    resp = requests.get(f"{API_BASE}/admin/scaling/evaluate", timeout=5)
    if resp.status_code == 200:
        decision = resp.json()
        action = decision.get("action", "none")
        emoji = {"scale_up": "⬆️", "scale_down": "⬇️", "none": "➡️"}.get(action, "❓")
        st.metric("当前决策", f"{emoji} {action}")

        count = decision.get("count", 0)
        if count > 0:
            st.metric("建议数量", count)

        reason = decision.get("reason", "")
        if reason:
            st.info(reason)
    else:
        st.error(f"扩缩容接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
except requests.ConnectionError:
    st.warning(f"无法连接调度服务 (`{API_BASE}`) — 请确认服务已启动")
except requests.Timeout:
    st.warning("扩缩容接口请求超时")
