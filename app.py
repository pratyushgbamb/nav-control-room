import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from urllib.parse import quote

# ==============================================================================
# PAGE CONFIGURATION & SAFE NUMBER PARSER
# ==============================================================================
st.set_page_config(
    page_title="The NAV Control Room | Zensar BFSI Project 1",
    page_icon="📊",
    layout="wide",
)

# Uses your live NAV_Control_Room and Answer_Key Google Sheets
SHEET_CSV_BASE = "https://docs.google.com/spreadsheets/d/1akDgZEAl5Bk_kZrvOiBjPXPoAfs_wTJln0xlNggb5gA/gviz/tq?tqx=out:csv&sheet="
SHEET_EDIT_URL = "https://docs.google.com/spreadsheets/d/1akDgZEAl5Bk_kZrvOiBjPXPoAfs_wTJln0xlNggb5gA/edit"
ANSWER_KEY_URL = "https://docs.google.com/spreadsheets/d/11MmIEtS-526idagFkH5nD-wyy1RG4dOHk-_LUj-oa0M/edit"

def safe_float(val, default: float = 0.0) -> float:
    """Safely converts comma-formatted strings (e.g. '727,146'), NaNs, or #N/A into a float."""
    if val is None or pd.isna(val):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("₹", "").replace("%", "").replace("+", "")
    if s in ("", "—", "-", "None", "nan", "NaN", "#N/A", "#VALUE!", "#REF!", "#DIV/0!"):
        return default
    try:
        return float(s)
    except ValueError:
        return default

@st.cache_data(ttl=20)
def load_sheet_tab(tab_name: str) -> pd.DataFrame:
    """Loads a tab live from Google Sheets via CSV export endpoint (20s cache)."""
    url = f"{SHEET_CSV_BASE}{quote(tab_name)}"
    try:
        df = pd.read_csv(url)
        # Drop completely empty padding rows
        df = df.dropna(how="all")
        if not df.empty and len(df.columns) > 0:
            first_col = df.columns[0]
            df = df[df[first_col].notna() & (df[first_col].astype(str).str.strip() != "")]
        return df
    except Exception as e:
        st.error(f"Could not load tab '{tab_name}'. Ensure the Google Sheet is shared as 'Anyone with the link -> Viewer'. Error: {e}")
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
    st.markdown(f"📄 **[Open NAV_Control_Room Sheet]({SHEET_EDIT_URL})**")
    st.markdown(f"🎯 **[Open Answer_Key Scorecard]({ANSWER_KEY_URL})**")

# Load primary tabs
funds_df = load_sheet_tab("Funds")
nav_runs_df = load_sheet_tab("NAV_Runs")
prices_df = load_sheet_tab("Prices")
positions_df = load_sheet_tab("Positions")
fees_df = load_sheet_tab("Fee_Accruals")
cases_df = load_sheet_tab("Cases")
ai_df = load_sheet_tab("AI_Decisions")

if nav_runs_df.empty or "Date" not in nav_runs_df.columns:
    st.warning("No rows in NAV_Runs yet. Run the pipeline in Google Sheets first.")
    st.stop()

nav_runs_df = nav_runs_df[nav_runs_df["Date"].notna() & nav_runs_df["Fund_ID"].notna()].copy()
available_dates = sorted(nav_runs_df["Date"].astype(str).str.strip().unique(), reverse=True)
fund_list = funds_df["Fund_ID"].dropna().astype(str).tolist() if not funds_df.empty else ["F1", "F2", "F3", "F4", "F5"]

with st.sidebar:
    selected_date = st.selectbox("📅 Select Valuation Date (T)", available_dates, index=0)
    default_fund_idx = 3 if len(fund_list) > 3 else 0
    selected_fund = st.selectbox("💼 Select Mutual Fund for Waterfall", fund_list, index=default_fund_idx)

day_navs = nav_runs_df[nav_runs_df["Date"].astype(str).str.strip() == selected_date].copy()
if not funds_df.empty and "Fund_Name" in funds_df.columns:
    day_navs = day_navs.merge(funds_df[["Fund_ID", "Fund_Name", "Category"]], on="Fund_ID", how="left")
else:
    day_navs["Fund_Name"] = day_navs["Fund_ID"]
    day_navs["Category"] = "Equity"

# ==============================================================================
# 1. EXECUTIVE KPI STRIP & 5 FUND STATUS CARDS
# ==============================================================================
col1, col2, col3, col4, col5 = st.columns(5)
statuses = day_navs["Fund_Day_Status"].astype(str).str.strip().str.upper()
blocked_count = int((statuses == "BLOCKED").sum())
review_count = int((statuses.isin(["UNDER_REVIEW", "IN_REVIEW"])).sum())
ready_count = int((statuses.isin(["READY", "RELEASED"])).sum())

col1.metric("Valuation Date (T)", selected_date)
col2.metric("Funds Ready / Released", f"{ready_count} / {len(day_navs)}")
col3.metric("Funds Under Review", review_count)
col4.metric("Funds Hard-Blocked", blocked_count)
col5.metric("Precision Standard", "Integer Paise (1e-4 ₹)")

st.subheader(f"🚦 Daily Fund Release Gate Status ({selected_date})")
f_cols = st.columns(max(1, len(day_navs)))
status_colors = {
    "READY": "🟢 READY",
    "RELEASED": "✅ RELEASED",
    "UNDER_REVIEW": "🟡 UNDER REVIEW",
    "IN_REVIEW": "🟡 IN REVIEW",
    "BLOCKED": "🔴 BLOCKED"
}

for idx, (_, row) in enumerate(day_navs.sort_values("Fund_ID").iterrows()):
    with f_cols[idx % len(f_cols)]:
        raw_status = str(row.get("Fund_Day_Status", "UNKNOWN")).strip().upper()
        status_badge = status_colors.get(raw_status, raw_status)
        nav_rs = safe_float(row.get("NAV_1e4")) / 10000.0
        exp_rs = safe_float(row.get("Expected_NAV_1e4")) / 10000.0
        adm_rs = safe_float(row.get("Admin_NAV_1e4")) / 10000.0
        nav_chg = safe_float(row.get("NAV_Change_bps"))
        dev_exp = safe_float(row.get("Dev_vs_Expected_bps"))
        adm_brk = safe_float(row.get("Admin_Break_bps"))

        st.markdown(f"**{row['Fund_ID']} — {row.get('Fund_Name', row['Fund_ID'])}**")
        st.markdown(f"Status: **{status_badge}**")
        st.metric(
            label="Official NAV (₹)",
            value=f"₹{nav_rs:.4f}",
            delta=f"{nav_chg:+.2f} bps vs T-1"
        )
        st.caption(
            f"Expected: ₹{exp_rs:.4f} ({dev_exp:+.1f} bps)  \n"
            f"Admin NAV: ₹{adm_rs:.4f} (Break: {adm_brk:.1f} bps)  \n"
            f"Gates: N01=`{row.get('N01')}` | N02=`{row.get('N02')}` | N03=`{row.get('N03')}` | N04=`{row.get('N04')}`"
        )

st.markdown("---")

# ==============================================================================
# 2. PLOTLY NAV ATTRIBUTION WATERFALL CHART (SECTION 4.6)
# ==============================================================================
fund_subset = day_navs[day_navs["Fund_ID"].astype(str) == selected_fund]
if not fund_subset.empty:
    fund_row = fund_subset.iloc[0]
    prev_nav = safe_float(fund_row.get("Prev_NAV_1e4")) / 10000.0
    mkt_attr = safe_float(fund_row.get("Attr_Market_1e4")) / 10000.0
    inc_attr = safe_float(fund_row.get("Attr_Income_1e4")) / 10000.0
    fee_attr = safe_float(fund_row.get("Attr_Fees_1e4")) / 10000.0
    res_attr = safe_float(fund_row.get("Attr_Residual_1e4")) / 10000.0
    curr_nav = safe_float(fund_row.get("NAV_1e4")) / 10000.0
    exp_nav = safe_float(fund_row.get("Expected_NAV_1e4")) / 10000.0
    adm_nav = safe_float(fund_row.get("Admin_NAV_1e4")) / 10000.0

    w_col1, w_col2 = st.columns([2, 1])

    with w_col1:
        st.subheader(f"📉 NAV Attribution Waterfall: {selected_fund} ({fund_row.get('Fund_Name', '')}) on {selected_date}")
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
        if exp_nav > 0:
            fig.add_hline(
                y=exp_nav, line_dash="dot", line_color="#9333ea",
                annotation_text=f"Frozen Expected NAV: ₹{exp_nav:.4f}"
            )
        if adm_nav > 0:
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
        * **Expected Frozen At:** `{fund_row.get('Expected_Frozen_At', '—')}`
        * **Holdings Market Value:** `₹{safe_float(fund_row.get('Holdings_MV_Paise'))/100:,.2f}`
        * **Dividend Receivable:** `₹{safe_float(fund_row.get('Div_Receivable_Paise'))/100:,.2f}`
        * **Cash Balance:** `₹{safe_float(fund_row.get('Cash_Paise'))/100:,.2f}`
        * **Accrued Fee Payable:** `₹{safe_float(fund_row.get('Fee_Payable_Paise'))/100:,.2f}`
        * **Net Assets:** `₹{safe_float(fund_row.get('Net_Assets_Paise'))/100:,.2f}`
        * **Units Outstanding:** `{safe_float(fund_row.get('Units_Milli'))/1000:,.3f}`
        * **Top Contributors:** `{fund_row.get('Top_Contributors', '—')}`
        """)
        raw_res_1e4 = safe_float(fund_row.get("Attr_Residual_1e4"))
        if abs(raw_res_1e4) > 1:
            st.error(
                f"🚨 **Rule N04 Triggered:** Unexplained attribution residual of "
                f"`{raw_res_1e4:+.0f}` (₹{res_attr:+.4f}/unit) exceeds 1 bp tolerance!"
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
        st.info("No exception cases generated yet.")
    else:
        st.dataframe(cases_df, use_container_width=True)
    if not ai_df.empty:
        st.subheader("Gemini AI Decisions & Post-AI Guardrail Audit Log (G1–G6)")
        st.dataframe(ai_df, use_container_width=True)

with tab2:
    st.subheader(f"Price Feeds & Selection Waterfall ({selected_date})")
    if not prices_df.empty and "Date" in prices_df.columns:
        day_prices = prices_df[prices_df["Date"].astype(str).str.strip() == selected_date]
        st.dataframe(day_prices, use_container_width=True)

with tab3:
    st.subheader(f"Fund Positions & Corporate Action Checks ({selected_date})")
    if not positions_df.empty and "Date" in positions_df.columns:
        day_pos = positions_df[positions_df["Date"].astype(str).str.strip() == selected_date]
        st.dataframe(day_pos, use_container_width=True)

with tab4:
    st.subheader(f"Management & Performance Fee Recalculation Audit ({selected_date})")
    if not fees_df.empty and "Date" in fees_df.columns:
        day_fees = fees_df[fees_df["Date"].astype(str).str.strip() == selected_date]
        st.dataframe(day_fees, use_container_width=True)
