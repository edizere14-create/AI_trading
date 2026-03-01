from __future__ import annotations

import streamlit as st


def apply_theme() -> None:
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
:root{
  --bg:#070B12; --panel:#0F1723; --text:#E8EEF7; --muted:#A8B8CC;
  --accent:#00D4FF; --profit:#00D084; --risk:#FF4D4F;
}
html,body,[data-testid="stAppViewContainer"]{
  background:var(--bg); color:var(--text);
  font-family:Inter,"SF Pro Text","Segoe UI",sans-serif;
}
[data-testid="stSidebar"]{background:var(--panel);}
.block-container{padding-top:.7rem; max-width:1700px;}
.metric-card{background:var(--panel); border:1px solid rgba(255,255,255,.08); border-radius:12px; padding:.6rem .8rem;}
.p-profit{color:var(--profit); font-weight:700;}
.p-risk{color:var(--risk); font-weight:700;}
.p-accent{color:var(--accent); font-weight:700;}

@media (max-width: 980px) {
  .block-container {padding-left: .8rem; padding-right: .8rem;}
  [data-testid="stSidebar"] {min-width: 78vw; max-width: 78vw;}
  [data-testid="column"] {min-width: 100% !important; flex: 1 1 100% !important;}
  button[kind], .stButton button {min-height: 44px; font-size: 1rem;}
  .stSlider [role="slider"] {min-width: 26px; min-height: 26px;}
  .stTextInput input {min-height: 42px; font-size: 1rem;}
}
</style>
""",
        unsafe_allow_html=True,
    )