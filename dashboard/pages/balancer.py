"""调度器内部状态 — speed/reliability/cooldown/load 实时面板。"""
import requests
import streamlit as st

API_BASE = "http://localhost:9090"
st.title("⚖️ 调度器内部状态 (v4)")

try:
    resp = requests.get(f"{API_BASE}/admin/balancer/debug", timeout=5)
    if resp.status_code != 200:
        st.error(f"HTTP {resp.status_code}")
        st.stop()
    data = resp.json().get("instances", {})
except requests.ConnectionError:
    st.warning(f"无法连接 ({API_BASE})")
    st.stop()

if not data:
    st.info("暂未注册实例")
    st.stop()

# ── Speed 排行 ──
st.subheader("🚀 Speed (tok/s)")
sorted_by_speed = sorted(data.items(), key=lambda x: x[1].get("speed_tok_s", 0), reverse=True)
cols = st.columns(len(sorted_by_speed))
for i, (iid, s) in enumerate(sorted_by_speed):
    speed = s.get("speed_tok_s", 0)
    cols[i].metric(iid, f"{speed} tok/s")

# ── Reliability + Cooldown ──
st.subheader("🛡️ Reliability & Cooldown")
for iid, s in data.items():
    rel = s.get("reliability", 1.0)
    cooldown = "🧊 冷却中" if s.get("in_cooldown") else "✅"
    color = "green" if rel > 0.8 else ("orange" if rel > 0.5 else "red")
    st.markdown(
        f"**{iid}** | reliability: :{color}[{rel:.3f}] | cooldown: {cooldown} | "
        f"tier: {s.get('tier','?')} | load: {s.get('load',0):.0f}"
    )
    st.progress(min(rel, 1.0))

# ── 负载分布 ──
st.subheader("📊 负载分布")
total_load = sum(s.get("load", 0) for s in data.values()) or 1
for iid, s in data.items():
    load = s.get("load", 0)
    pct = load / total_load * 100
    st.text(f"{iid}: {load:.0f} ({pct:.0f}%)")
