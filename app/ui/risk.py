import pandas as pd
import streamlit as st

def render_risk_panel(port_data: dict, api_url: str, api_request_fn):
    st.subheader("🌐 Portfolio Risk")
    port_df = pd.DataFrame(port_data.get("positions", []))

    if not port_df.empty:
        for c in ["notional", "vol_contrib_pct", "asset_dd_pct", "avg_corr"]:
            if c in port_df.columns:
                port_df[c] = pd.to_numeric(port_df[c], errors="coerce").fillna(0.0)

        def highlight_risk(row):
            styles = [""] * len(row)
            cols = list(row.index)
            if "avg_corr" in cols and float(row["avg_corr"]) > 0.7:
                styles[cols.index("avg_corr")] = "background-color:#FF4D4F22;color:#FF4D4F;font-weight:700;"
            if "asset_dd_pct" in cols and float(row["asset_dd_pct"]) > 5:
                styles[cols.index("asset_dd_pct")] = "background-color:#FF4D4F22;color:#FF4D4F;font-weight:700;"
            return styles

        show_cols = [c for c in ["symbol", "notional", "vol_contrib_pct", "asset_dd_pct", "avg_corr"] if c in port_df.columns]
        st.dataframe(
            port_df[show_cols].style.apply(highlight_risk, axis=1),
            column_config={
                "avg_corr": st.column_config.NumberColumn("avg_corr", format="%.2f"),
                "asset_dd_pct": st.column_config.NumberColumn("asset_dd_pct", format="%.2f%%"),
                "vol_contrib_pct": st.column_config.NumberColumn("vol_contrib_pct", format="%.2f%%"),
                "notional": st.column_config.NumberColumn("notional", format="$%.2f"),
            },
            use_container_width=True,
        )

    total_dd = float(port_data.get("total_dd", 0.0))
    corr_avg = float(port_data.get("corr_avg", 0.0))

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Portfolio DD", f"{total_dd:.1f}%")
        st.markdown("<span class='risk'>DD breach (> 5%)</span>" if total_dd > 5 else "<span class='profit'>DD OK</span>", unsafe_allow_html=True)
    with col2:
        st.metric("Avg Corr", f"{corr_avg:.2f}")
        st.markdown("<span class='risk'>Corr breach (> 0.70)</span>" if corr_avg > 0.7 else "<span class='profit'>Correlation OK</span>", unsafe_allow_html=True)

    if st.button("🚨 Enforce Limits", use_container_width=True):
        api_request_fn("POST", f"{api_url}/risk/enforce")
        st.rerun()