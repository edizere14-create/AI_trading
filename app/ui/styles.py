import streamlit as st

def apply_global_styles() -> None:
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root{
  --bg:#06090F;
  --panel:#0D131C;
  --panel-2:#121A26;
  --text:#E8EEF7;
  --muted:#9FB0C7;
  --accent:#00D4FF;
  --profit:#00D084;
  --risk:#FF4D4F;
  --border:rgba(255,255,255,.08);
}

html, body, [data-testid="stAppViewContainer"] {
  background: var(--bg);
  color: var(--text);
  font-family: "Inter", "SF Pro Text", "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.35;
  letter-spacing: 0.1px;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { background: var(--panel); }
.block-container { padding-top: 0.55rem; max-width: 1600px; }

h1,h2,h3 {
  letter-spacing: 0.2px;
  font-weight: 700;
  line-height: 1.15;
  margin-bottom: 0.35rem;
}

small, .stCaption, .stMarkdown p { color: var(--muted); margin-bottom: 0.4rem; }

div[data-testid="stMetric"], .stDataFrame, .stPlotlyChart {
  background: var(--panel);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 12px;
  transition: border-color .2s ease, transform .2s ease;
}
div[data-testid="stMetric"]:hover { border-color: rgba(0,212,255,.45); transform: translateY(-1px); }

/* Numeric clarity */
div[data-testid="stMetricValue"], div[data-testid="stMetricDelta"] {
  font-variant-numeric: tabular-nums;
}

/* Buttons */
.stButton > button {
  border-radius: 10px;
  border: 1px solid rgba(0,212,255,.45);
  color: var(--text);
  background: linear-gradient(180deg, rgba(0,212,255,.14), rgba(0,212,255,.05));
}
.stButton > button:hover { border-color: var(--accent); }

.profit { color: var(--profit); font-weight: 700; }
.risk { color: var(--risk); font-weight: 700; }

/* Subtle only */
@keyframes fadeIn { from {opacity:0; transform: translateY(2px);} to {opacity:1; transform:none;} }
.main * { animation: fadeIn .16s ease; }

/* Compact blotter */
.blotter-wrap{
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 10px;
  background: #0D131C;
}
.blotter-table{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  line-height: 1.2;
}
.blotter-table thead th{
  position: sticky;
  top: 0;
  z-index: 2;
  background: #121A26;
  color: #E8EEF7;
  text-align: left;
  padding: 6px 8px;
  border-bottom: 1px solid rgba(255,255,255,.10);
  white-space: nowrap;
}
.blotter-table tbody td{
  padding: 5px 8px;
  border-bottom: 1px solid rgba(255,255,255,.06);
  color: #CFE0F5;
  white-space: nowrap;
}
.blotter-pos{ color:#00D084; font-weight:700; }
.blotter-neg{ color:#FF4D4F; font-weight:700; }
</style>
""",
        unsafe_allow_html=True,
    )