import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from urllib.parse import quote

# ==============================================================================
# PAGE CONFIGURATION & STYLING
# ==============================================================================
st.set_page_config(
    page_title="The NAV Control Room | Zensar BFSI Project 1",
    page_icon="📊",
    layout="wide",
)

SHEET_ID = "1akDgZEAl5Bk_kZrvOiBjPXPoAfs_wTJln0xlNggb5gA"
ANSWER_KEY_ID = "11MmIEtS-526idagFkH5nD-wyy1RG4dOHk-_LUj-oa0M"

@st.cache_data(ttl=30)
def load_sheet_tab(spreadsheet_id: str, tab_name: str) -> pd.DataFrame:
    """Loads a tab live from Google Sheets via CSV export endpoint (30s cache)."""
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={quote(tab_name)}"
    try:
        df = pd.read_csv(url)
        return df
    except Exception as e:
        st.error(f"Could not load tab {tab_name}: {e}")
        return pd.DataFrame()

# ==============================================================================
# HEADER & SIDEBAR CONTROLS
# ==============================================================================
st.title("🏦 The NAV Control Room — Fund Accounting & Daily NAV Production")
st.caption("Zensar BFSI Program • PUMBA MBA Finance • Team: Akshay Jadhav, Deepa Pandey, Pratyush Bamb, Vikram Shetty")

with st.sidebar:
    st.header("⚙️ Live Control Panel")
    if st.button("🔄 Refresh Live Google Sheet Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown(f"📄 **[Open NAV_Control_Room Sheet](https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit)**")
    st.markdown(f"🎯 **[Open Answer_Key Scorecard](https://docs.google.com/spreadsheets/d/{ANSWER_KEY_ID}/edit)**")

# Load primary tabs
funds_df = load_sheet_tab(SHEET_ID, "Funds")
nav_runs_df = load_sheet_tab(SHEET_ID, "NAV_Runs")
prices_df = load_sheet_tab(SHEET_ID, "Prices")
positions_df = load_sheet_tab(SHEET_ID, "Positions")
fees_df = load_sheet_tab(SHEET_ID, "Fee_Accruals")
cases_df = load_sheet_tab(SHEET_ID, "Cases")
ai_df = load_sheet_tab(SHEET_ID, "AI_Decisions")

if nav_runs_df.empty:
    st.warning("No rows in NAV_Runs yet. Run the pipeline in Google Sheets first.")
    st.stop()

available_dates = sorted(nav_runs_df["Date"].astype(str).unique(), reverse=True)
with st.sidebar:
    selected_date = st.selectbox("📅 Select Valuation Date (T)", available_dates, index=0)
    selected_fund = st.selectbox("💼 Select Mutual Fund for Waterfall", funds_df["Fund_ID"].tolist(), index=3)

day_navs = nav_runs_df[nav_runs_df["Date"].astype(str) == selected_date].copy()
day_navs = day_navs.merge(funds_df[["Fund_ID", "Fund_Name", "Category"]], on="Fund_ID", how="left")

# ==============================================================================
# 1. EXECUTIVE KPI STRIP & 5 FUND STATUS CARDS
# ==============================================================================
col1, col2, col3, col4, col5 = st.columns(5)
blocked_count = int((day_navs["Fund_Day_Status"] == "BLOCKED").sum())
review_count = int((day_navs["Fund_Day_Status"].isin(["UNDER_REVIEW", "IN_REVIEW"])).sum())
ready_count = int((day_navs["Fund_Day_Status"].isin(["READY", "RELEASED"])).sum())

col1.metric("Valuation Date (T)", selected_date)
col2.metric("Funds Ready / Released", f"{ready_count} / 5")
col3.metric("Funds Under Review", review_count)
col4.metric("Funds Hard-Blocked", blocked_count)
col5.metric("Precision Standard", "Integer Paise (1e-4 ₹)")

st.subheader(f"🚦 Daily Fund Release Gate Status ({selected_date})")
f_cols = st.columns(5)
status_colors = {
    "READY": "🟢 READY",
    "RELEASED": "✅ RELEASED",
    "UNDER_REVIEW": "🟡 UNDER REVIEW",
    "IN_REVIEW": "🟡 IN REVIEW",
    "BLOCKED": "🔴 BLOCKED"
}

for idx, (_, row) in enumerate(day_navs.sort_values("Fund_ID").iterrows()):
    with f_cols[idx % 5]:
        status_badge = status_colors.get(str(row["Fund_Day_Status"]), str(row["Fund_Day_Status"]))
        nav_rs = float(row["NAV_1e4"]) / 10000.0
        exp_rs = float(row["Expected_NAV_1e4"]) / 10000.0
        adm_rs = float(row["Admin_NAV_1e4"]) / 10000.0
        st.markdown(f"**{row['Fund_ID']} — {row['Fund_Name']}**")
        st.markdown(f"Status: **{status_badge}**")
        st.metric(
            label="Official NAV (₹)",
            value=f"₹{nav_rs:.4f}",
            delta=f"{float(row['NAV_Change_bps']):+.2f} bps vs T-1"
        )
        st.caption(
            f"Expected: ₹{exp_rs:.4f} ({float(row['Dev_vs_Expected_bps']):+.1f} bps)  \n"
            f"Admin NAV: ₹{adm_rs:.4f} (Break: {float(row['Admin_Break_bps']):.1f} bps)  \n"
            f"Gates: N01=`{row['N01']}` | N02=`{row['N02']}` | N03=`{row['N03']}` | N04=`{row['N04']}`"
        )

st.markdown("---")

# ==============================================================================
# 2. PLOTLY NAV ATTRIBUTION WATERFALL CHART (SECTION 4.6)
# ==============================================================================
fund_row = day_navs[day_navs["Fund_ID"] == selected_fund].iloc[0]
prev_nav = float(fund_row["Prev_NAV_1e4"]) / 10000.0
mkt_attr = float(fund_row["Attr_Market_1e4"]) / 10000.0
inc_attr = float(fund_row["Attr_Income_1e4"]) / 10000.0
fee_attr = float(fund_row["Attr_Fees_1e4"]) / 10000.0
res_attr = float(fund_row["Attr_Residual_1e4"]) / 10000.0
curr_nav = float(fund_row["NAV_1e4"]) / 10000.0
exp_nav = float(fund_row["Expected_NAV_1e4"]) / 10000.0
adm_nav = float(fund_row["Admin_NAV_1e4"]) / 10000.0

w_col1, w_col2 = st.columns([2, 1])

with w_col1:
    st.subheader(f"📉 NAV Attribution Waterfall: {selected_fund} ({fund_row['Fund_Name']}) on {selected_date}")
    fig = go.Figure(go.Waterfall(
        name="NAV Attribution",
        orientation="v",
        measure=["absolute", "relative", "relative", "relative", "relative", "total"],
        x=[
            "Prior NAV (T-1)",
            "Market Move",
            "Dividend Income",
            "Fee Accrual",
            "Unexplained Residual (N04)",
            "Actual NAV (T)"
        ],
        textposition="outside",
        text=[
            f"₹{prev_nav:.4f}",
            f"{mkt_attr:+.4f}",
            f"{inc_attr:+.4f}",
            f"{fee_attr:+.4f}",
            f"{res_attr:+.4f}",
            f"₹{curr_nav:.4f}"
        ],
        y=[prev_nav, mkt_attr, inc_attr, fee_attr, res_attr, curr_nav],
        connector={"line": {"color": "rgb(63, 63, 63)"}},
        increasing={"marker": {"color": "#16a34a"}},
        decreasing={"marker": {"color": "#dc2626"}},
        totals={"marker": {"color": "#2563eb"}}
    ))
    fig.add_hline(
        y=exp_nav, line_dash="dot", line_color="#9333ea",
        annotation_text=f"Frozen Expected NAV: ₹{exp_nav:.4f}"
    )
    fig.add_hline(
        y=adm_nav, line_dash="dash", line_color="#d97706",
        annotation_text=f"Admin Shadow NAV: ₹{adm_nav:.4f}"
    )
    fig.update_layout(
        yaxis_title="NAV per Unit (₹)",
        height=430,
        margin=dict(l=20, r=20, t=30, b=20)
    )
    st.plotly_chart(fig, use_container_width=True)

with w_col2:
    st.subheader("🔍 Balance Sheet & Attribution Audit")
    st.markdown(f"""
    * **Expected Frozen At:** `{fund_row['Expected_Frozen_At']}`
    * **Holdings Market Value:** `₹{float(fund_row['Holdings_MV_Paise'])/100:,.2f}`
    * **Dividend Receivable:** `₹{float(fund_row['Div_Receivable_Paise'])/100:,.2f}`
    * **Cash Balance:** `₹{float(fund_row['Cash_Paise'])/100:,.2f}`
    * **Accrued Fee Payable:** `₹{float(fund_row['Fee_Payable_Paise'])/100:,.2f}`
    * **Net Assets:** `₹{float(fund_row['Net_Assets_Paise'])/100:,.2f}`
    * **Units Outstanding:** `{float(fund_row['Units_Milli'])/1000:,.3f}`
    * **Top Contributors:** `{fund_row['Top_Contributors']}`
    """)
    if abs(float(fund_row["Attr_Residual_1e4"])) > 1:
        st.error(
            f"🚨 **Rule N04 Triggered:** Unexplained attribution residual of "
            f"`{float(fund_row['Attr_Residual_1e4']):+.0f}` (₹{res_attr:+.4f}/unit) exceeds 1 bp tolerance!"
        )
    else:
        st.success("✅ **Rule N04 Clean:** Attribution explains 100% of NAV movement within rounding.")

st.markdown("---")

# ==============================================================================
# 3. LIVE DIAGNOSTIC TABS: CASES & AI, PRICES (P01-P07), POSITIONS & FEES
# ==============================================================================
tab1, tab2, tab3, tab4 = st.tabs([
    "🤖 Cases, Gemini AI & Guardrails",
    "📈 Multi-Source Price Validation (P01–P07)",
    "🏢 Positions & Corporate Actions (C01–C04)",
    "🧾 Independent Fee Recalculation (F01–F03)"
])

with tab1:
    st.subheader("Exception Cases & Gemini Structured JSON Classifications")
    if cases_df.empty:
        st.info("No exception cases generated yet. In Google Sheets, click `NAV Control Room -> 2. Run Specific Date (Live Demo)` for `2026-09-15`.")
    else:
        st.dataframe(cases_df, use_container_width=True)
    if not ai_df.empty:
        st.subheader("Gemini AI Decisions & Post-AI Guardrail Audit Log (G1–G6)")
        st.dataframe(ai_df, use_container_width=True)

with tab2:
    st.subheader(f"Price Feeds & Selection Waterfall ({selected_date})")
    day_prices = prices_df[prices_df["Date"].astype(str) == selected_date]
    st.dataframe(day_prices, use_container_width=True)

with tab3:
    st.subheader(f"Fund Positions & Corporate Action Checks ({selected_date})")
    day_pos = positions_df[positions_df["Date"].astype(str) == selected_date]
    st.dataframe(day_pos, use_container_width=True)

with tab4:
    st.subheader(f"Management & Performance Fee Recalculation Audit ({selected_date})")
    day_fees = fees_df[fees_df["Date"].astype(str) == selected_date]
    st.dataframe(day_fees, use_container_width=True)
