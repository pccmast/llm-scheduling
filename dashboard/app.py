"""LLM Dispatcher Dashboard v4 — Streamlit 入口。"""
import streamlit as st

st.set_page_config(page_title="LLM Dispatcher v4", layout="wide")

pages = {
    "Overview": [
        st.Page("pages/overview.py", title="全局概览", icon="📊"),
    ],
    "Instances": [
        st.Page("pages/instances.py", title="实例详情", icon="🖥️"),
    ],
    "Balancer": [
        st.Page("pages/balancer.py", title="调度器内部", icon="⚖️"),
    ],
    "Scaling": [
        st.Page("pages/scaling.py", title="扩缩容", icon="📈"),
    ],
}

pg = st.navigation(pages)
pg.run()
