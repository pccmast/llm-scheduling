"""LLM Dispatcher Dashboard — Streamlit 入口。

提供全局概览、实例详情和扩缩容历史页面的 Web 界面。
启动方式: uv run streamlit run dashboard/app.py
"""

import streamlit as st

st.set_page_config(page_title="LLM Dispatcher", layout="wide")

pages = {
    "Overview": [
        st.Page("dashboard/pages/overview.py", title="全局概览", icon="📊"),
    ],
    "Instances": [
        st.Page("dashboard/pages/instances.py", title="实例详情", icon="🖥️"),
    ],
    "Scaling": [
        st.Page("dashboard/pages/scaling.py", title="扩缩容", icon="📈"),
    ],
}

pg = st.navigation(pages)
pg.run()
