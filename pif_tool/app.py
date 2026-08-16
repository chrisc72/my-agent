"""PIF 工具入口"""
import streamlit as st

st.set_page_config(page_title="PIF 工具", page_icon="🧪", layout="wide")

pg = st.navigation([
    st.Page("pages/00_raw_materials.py", title="原料資訊", icon="🧪"),
    st.Page("pages/1_PIF文件製作.py", title="PIF 文件製作", icon="📄"),
])
pg.run()
