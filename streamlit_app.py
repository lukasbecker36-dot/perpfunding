"""Streamlit dashboard for the Spot-Perp Funding Arbitrage Scanner."""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from arb.config import settings
from arb.timeutil import format_utc
from arb.venues import ALL_VENUES

logging.basicConfig(level=logging.WARNING)

st.set_page_config(
    page_title="Spot-Perp Arb Dashboard",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("Arb Scanner Settings")

    top_n = st.slider("Top N opportunities", min_value=5, max_value=100, value=30, step=5)
    min_funding = st.number_input(
        "Min funding avg % (24h)",
        min_value=0.0,
        max_value=5.0,
        value=0.0,
        step=0.05,
        format="%.2f",
    )
    notional_usdt = st.number_input(
        "Notional USDT (for edge estimate)",
        min_value=10.0,
        max_value=100_000.0,
        value=200.0,
        step=50.0,
    )
    selected_venues = st.multiselect(
        "Venues",
        options=ALL_VENUES,
        default=ALL_VENUES,
    )

    refresh = st.button("Refresh", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "df" not in st.session_state:
    st.session_state["df"] = None
if "status" not in st.session_state:
    st.session_state["status"] = None

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("Spot-Perp Funding Arbitrage Dashboard")

if refresh or st.session_state["df"] is None:
    if not selected_venues:
        st.warning("Select at least one venue.")
    else:
        with st.spinner("Fetching funding rates and orderbook data…"):
            try:
                from arb import core
                df, status = core.run(
                    top_n=top_n,
                    min_funding=min_funding,
                    notional_usdt=notional_usdt,
                    venues=selected_venues,
                )
                st.session_state["df"] = df
                st.session_state["status"] = status
            except Exception as exc:
                st.error(f"Error during refresh: {exc}")

df: pd.DataFrame | None = st.session_state["df"]
status: dict | None = st.session_state["status"]

# Status bar
if status:
    col1, col2 = st.columns([3, 1])
    with col1:
        st.caption(f"Last refresh: {format_utc(status['last_refresh'])}")
    if status.get("partial_failures"):
        with col2:
            st.warning(f"{len(status['partial_failures'])} partial failure(s)")
        with st.expander("Partial failure details"):
            for msg in status["partial_failures"]:
                st.text(f"• {msg}")

# Data table
if df is not None and not df.empty:
    # Format funding columns as percentages for readability
    display_df = df.copy()
    for col in ("funding_latest", "funding_avg_24h"):
        if col in display_df.columns:
            display_df[col] = display_df[col].map(
                lambda x: f"{x:.6f}" if x is not None else ""
            )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "rank": st.column_config.NumberColumn("Rank", format="%d"),
            "exchange": st.column_config.TextColumn("Exchange"),
            "symbol": st.column_config.TextColumn("Symbol"),
            "funding_latest": st.column_config.TextColumn("Funding Latest"),
            "funding_avg_24h": st.column_config.TextColumn("Funding Avg 24h"),
            "funding_window_hours": st.column_config.NumberColumn("Window (h)", format="%.1f"),
            "perp_bid": st.column_config.NumberColumn("Perp Bid", format="%.4f"),
            "perp_bid_size_usdt": st.column_config.NumberColumn("Bid Size USDT", format="%.2f"),
            "spot_price": st.column_config.NumberColumn("Spot Price", format="%.4f"),
            "basis_usd": st.column_config.NumberColumn("Basis USD", format="%.4f"),
            "basis_bps": st.column_config.NumberColumn("Basis bps", format="%.2f"),
            "est_gross_edge": st.column_config.NumberColumn("Est. Edge", format="%.4f"),
            "notes": st.column_config.TextColumn("Notes"),
        },
    )

    # Download button
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download CSV",
        data=csv_bytes,
        file_name="arb_opportunities.csv",
        mime="text/csv",
    )
elif df is not None and df.empty:
    st.info("No opportunities found matching the current filters.")
else:
    st.info("Click **Refresh** to load data.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.markdown(
    "Funding rate data provided by [Loris Tools](https://loris.tools)",
    unsafe_allow_html=False,
)
