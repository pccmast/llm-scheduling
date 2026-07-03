"""扩缩容决策页。"""
import requests
import streamlit as st

API_BASE = "http://127.0.0.1:9090"
st.title("📈 扩缩容决策")

try:
    resp = requests.get(f"{API_BASE}/admin/scaling/evaluate", timeout=5)
    if resp.status_code == 200:
        d = resp.json()
        action = d.get("action", "none")
        reason = d.get("reason", "")

        if reason == "Auto scaler not configured":
            st.info("自动扩缩容未启用")
            st.caption("编辑 `config/default.yaml` 中 `auto_scaler.enabled: true`")
        else:
            emoji = {"scale_up": "⬆️", "scale_down": "⬇️", "none": "➡️"}.get(action, "❓")
            st.metric("当前决策", f"{emoji} {action}")
            if d.get("count", 0) > 0:
                st.metric("建议数量", d["count"])
            if reason:
                st.caption(reason)
    else:
        st.error(f"HTTP {resp.status_code}")
except requests.ConnectionError:
    st.warning(f"无法连接 ({API_BASE})")
