from html import escape

import pandas as pd
import streamlit as st

def render_blotter_compact(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        st.info("No trades yet.")
        return

    cols = [c for c in ["side", "price", "quantity", "pnl", "timestamp"] if c in df.columns]
    d = df[cols].copy()

    for c in ["price", "quantity", "pnl"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)

    header = "".join(f"<th>{escape(c)}</th>" for c in d.columns)
    rows = []
    for _, r in d.iterrows():
        pnl = float(r["pnl"]) if "pnl" in d.columns else 0.0
        pnl_cls = "blotter-pos" if pnl > 0 else ("blotter-neg" if pnl < 0 else "")
        rows.append(
            "<tr>"
            + "".join([
                f"<td>{escape(str(r.get('side','')))}</td>" if "side" in d.columns else "",
                f"<td>{float(r.get('price',0.0)):,.2f}</td>" if "price" in d.columns else "",
                f"<td>{float(r.get('quantity',0.0)):,.4f}</td>" if "quantity" in d.columns else "",
                f"<td class='{pnl_cls}'>{pnl:,.2f}</td>" if "pnl" in d.columns else "",
                f"<td>{escape(str(r.get('timestamp','')))}</td>" if "timestamp" in d.columns else "",
            ])
            + "</tr>"
        )

    st.markdown(
        f"""
        <div class="blotter-wrap">
          <table class="blotter-table">
            <thead><tr>{header}</tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>
        """,
        unsafe_allow_html=True,
    )