"""
THE NAV CONTROL ROOM — Streamlit dashboard (v2.0, Phase 3 rebuild)
Zensar BFSI Program · Project 1.

Reads the SAME Google Sheet the Apps Script writes to (read-only). Two connection modes:
  1. Service account (recommended): add to .streamlit/secrets.toml (or Streamlit Cloud secrets)
        sheet_id = "<NAV_Control_Room sheet id>"
        [gcp_service_account]
        ...the service-account JSON fields...
     and share the Sheet with the service-account email as Viewer.
  2. Fallback: public "Anyone with the link — Viewer" CSV export (gviz). Dates must be formatted
     yyyy-mm-dd in the Sheet (Code.gs menu S1 sets this).

Fixes: D-1 (N04 from the Sheet), D-2 (NOT COMPUTABLE, never Rs 0), D-3 (legible waterfall),
D-4 (Reviews + Release_Log shown), D-5 (no answer-key link), D-6 (no static KPI tiles).
"""
from io import StringIO
from urllib.parse import quote

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="NAV Control Room | Zensar BFSI Project 1", page_icon="📊", layout="wide")

DEFAULT_SHEET_ID = "159qLct6HLJG_qAqFAfPn9LOc2bUoog7x3tSHT51mE_k"
def _secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:  # no secrets.toml configured
        return default


SHEET_ID = _secret("sheet_id", DEFAULT_SHEET_ID)
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
DATE_COLS = {"Date", "Prev_Business_Day", "Ex_Date", "Pay_Date"}
NON_ERROR_LABELS = {"GENUINE_MARKET_MOVE", "BENIGN_NOISE"}
STATUS_BADGE = {"RELEASED": "✅ RELEASED", "READY": "🟢 READY", "IN_REVIEW": "🟡 IN REVIEW",
                "UNDER_REVIEW": "🟡 IN REVIEW", "BLOCKED": "🔴 BLOCKED"}


# ----------------------------------------------------------------------------
# DATA ACCESS
# ----------------------------------------------------------------------------
def _has_service_account() -> bool:
    try:
        return "gcp_service_account" in st.secrets
    except Exception:
        return False


def _serial_to_date(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(v))).strftime("%Y-%m-%d")
    return _norm_date_text(v)


def _norm_date_text(v):
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        return pd.to_datetime(s, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return s


@st.cache_data(ttl=30, show_spinner=False)
def load_tab(tab: str) -> pd.DataFrame:
    try:
        if _has_service_account():
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_info(
                dict(st.secrets["gcp_service_account"]),
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
            ws = gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(tab)
            vals = ws.get_all_values(value_render_option="UNFORMATTED_VALUE", date_time_render_option="SERIAL_NUMBER")
            if not vals:
                return pd.DataFrame()
            df = pd.DataFrame(vals[1:], columns=[str(h).strip() for h in vals[0]])
            for c in df.columns:
                if c in DATE_COLS:
                    df[c] = df[c].apply(_serial_to_date)
        else:
            url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&headers=1&sheet={quote(tab)}"
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text), dtype=str, keep_default_na=False)
            df.columns = [str(c).strip() for c in df.columns]
            for c in df.columns:
                if c in DATE_COLS:
                    df[c] = df[c].apply(_norm_date_text)
        df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
        if df.empty:
            return df
        first = df.columns[0]
        return df[df[first].astype(str).str.strip() != ""].reset_index(drop=True)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read tab '{tab}': {e}")
        return pd.DataFrame()


def num(v):
    """Number or None. Never converts a blank/error into zero (rule: a missing value is never zero)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    s = str(v).strip().replace(",", "").replace("₹", "")
    if s in ("", "—", "-", "nan", "NaN", "None") or s.startswith("#"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def truthy(v) -> bool:
    return v is True or str(v).strip().upper() == "TRUE"


def nav_text(v1e4) -> str:
    x = num(v1e4)
    return "NOT COMPUTABLE" if x is None else f"₹{x / 10000:,.4f}"


def fmt_bps(v, signed=True) -> str:
    x = num(v)
    if x is None:
        return "—"
    return f"{x:+.1f} bps" if signed else f"{x:.1f} bps"


# ----------------------------------------------------------------------------
# HEADER / SIDEBAR
# ----------------------------------------------------------------------------
st.title("🏦 The NAV Control Room — Daily NAV Production & Release")
st.caption("Zensar BFSI Program · Project 1 · Team: Akshay Jadhav, Deepa Pandey, Pratyush Bamb, Vikram Shetty · "
           "Data: Source A and Nifty 50 from Yahoo Finance; Sources B/C, bookings, fees and administrator NAV synthetic")

with st.sidebar:
    st.header("⚙️ Controls")
    if st.button("🔄 Refresh from Google Sheet"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data source: " + ("service account (read-only)" if _has_service_account() else "public CSV export (gviz)") + " · cache 30 s")
    st.markdown(f"📄 [Open NAV_Control_Room Sheet]({SHEET_URL})")

funds_df = load_tab("Funds")
nav_df = load_tab("NAV_Runs")
cases_df = load_tab("Cases")
ai_df = load_tab("AI_Decisions")
reviews_df = load_tab("Reviews")
release_df = load_tab("Release_Log")
cal_df = load_tab("Calendar")

if nav_df.empty or "Date" not in nav_df.columns:
    st.warning("NAV_Runs is empty. Run S2 → S3 → Run next date in the Sheet first.")
    st.stop()

processed_dates = set(cases_df["Date"]) | set(release_df.get("Date", pd.Series(dtype=str))) if not cases_df.empty else set()
all_dates = sorted({d for d in nav_df["Date"] if d}, reverse=True)
default_dates = [d for d in all_dates if d in processed_dates] or all_dates
fund_list = funds_df["Fund_ID"].astype(str).tolist() if not funds_df.empty else sorted(nav_df["Fund_ID"].unique())

with st.sidebar:
    show_all = st.checkbox("Include warm-up dates", value=False)
    date_options = all_dates if show_all else default_dates
    sel_date = st.selectbox("📅 Valuation date (T)", date_options, index=0)
    sel_fund = st.selectbox("💼 Fund for waterfall", fund_list, index=0)

day = nav_df[nav_df["Date"] == sel_date].copy()
if not funds_df.empty:
    day = day.merge(funds_df[["Fund_ID", "Fund_Name"]], on="Fund_ID", how="left")
day = day.sort_values("Fund_ID")

# ----------------------------------------------------------------------------
# KPI STRIP
# ----------------------------------------------------------------------------
status = day["Fund_Day_Status"].astype(str).str.upper()
case_calls = ai_df[ai_df.get("Call_Type", pd.Series(dtype=str)).astype(str) == "CASE"] if not ai_df.empty else ai_df
ai_ok = int(case_calls["AI_Status"].isin(["OK", "CACHED"]).sum()) if "AI_Status" in case_calls else 0
ai_fail = int((case_calls["AI_Status"] == "FAILED").sum()) if "AI_Status" in case_calls else 0
open_cases = cases_df[cases_df["Status"].isin(["NEW", "AI_DONE", "ESCALATED"])] if not cases_df.empty else cases_df

k = st.columns(6)
k[0].metric("Valuation date", sel_date)
k[1].metric("Released", int((status == "RELEASED").sum()))
k[2].metric("Ready for Senior", int((status == "READY").sum()))
k[3].metric("In review", int(status.isin(["IN_REVIEW", "UNDER_REVIEW"]).sum()))
k[4].metric("Blocked", int((status == "BLOCKED").sum()))
k[5].metric("AI calls OK / failed", f"{ai_ok} / {ai_fail}")
if ai_fail:
    st.warning(f"{ai_fail} AI call(s) failed (guardrail G4). Those cases show 'NO_AI' and must be decided from the evidence (OVERRIDE). "
               "Check Config AI_MODEL with menu S4 in the Sheet.")

# ----------------------------------------------------------------------------
# FUND CARDS
# ----------------------------------------------------------------------------
st.subheader(f"🚦 Release gate by fund — {sel_date}")
cols = st.columns(max(1, len(day)))
for col, (_, r) in zip(cols, day.iterrows()):
    with col:
        s = str(r.get("Fund_Day_Status", "")).upper()
        st.markdown(f"**{r['Fund_ID']} — {r.get('Fund_Name', '')}**")
        st.markdown(f"Status: **{STATUS_BADGE.get(s, s or '—')}**")
        chg = num(r.get("NAV_Change_bps"))
        st.metric("NAV per unit", nav_text(r.get("NAV_1e4")), delta=None if chg is None else f"{chg:+.2f} bps vs T-1")
        st.caption(
            f"Expected (frozen): {nav_text(r.get('Expected_NAV_1e4'))} ({fmt_bps(r.get('Dev_vs_Expected_bps'))}, band ±{fmt_bps(r.get('Band_bps'), False)})  \n"
            f"Administrator: {nav_text(r.get('Admin_NAV_1e4'))} (break {fmt_bps(r.get('Admin_Break_bps'), False)})  \n"
            + " · ".join(f"{n}={'🔴' if truthy(r.get(n)) else '🟢'}" for n in ("N01", "N02", "N03", "N04")))

st.divider()

# ----------------------------------------------------------------------------
# ATTRIBUTION WATERFALL
# ----------------------------------------------------------------------------
fr = day[day["Fund_ID"] == sel_fund]
if not fr.empty:
    r = fr.iloc[0]
    prev, cur = num(r.get("Prev_NAV_1e4")), num(r.get("NAV_1e4"))
    comps = [num(r.get(c)) for c in ("Attr_Market_1e4", "Attr_Income_1e4", "Attr_Fees_1e4", "Attr_Residual_1e4")]
    w1, w2 = st.columns([2, 1])
    with w1:
        st.subheader(f"📉 NAV attribution — {sel_fund} on {sel_date}")
        if prev is None or cur is None or any(c is None for c in comps):
            st.info("Waterfall not available: NAV is not computable for this fund-day, or there is no previous NAV (first warm-up day).")
        else:
            p, c = prev / 10000, cur / 10000
            m, inc, fee, res = (x / 10000 for x in comps)
            fig = go.Figure(go.Waterfall(
                orientation="v", measure=["absolute", "relative", "relative", "relative", "relative", "total"],
                x=["Previous NAV", "Market", "Income (calendar)", "Fees (recalculated)", "Unexplained", "Today's NAV"],
                y=[p, m, inc, fee, res, c],
                text=[f"₹{p:.4f}", f"{m:+.4f}", f"{inc:+.4f}", f"{fee:+.4f}", f"{res:+.4f}", f"₹{c:.4f}"],
                textposition="outside", connector={"line": {"color": "rgb(120,120,120)"}},
                increasing={"marker": {"color": "#16a34a"}}, decreasing={"marker": {"color": "#dc2626"}},
                totals={"marker": {"color": "#2563eb"}}))
            exp = num(r.get("Expected_NAV_1e4"))
            adm = num(r.get("Admin_NAV_1e4"))
            levels = [p, c] + ([exp / 10000] if exp else []) + ([adm / 10000] if adm else [])
            running = [p, p + m, p + m + inc, p + m + inc + fee, c]
            lo, hi = min(levels + running), max(levels + running)
            pad = max((hi - lo) * 0.35, p * 0.001)
            fig.update_yaxes(range=[lo - pad, hi + pad], title="NAV per unit (₹)")
            if exp:
                fig.add_hline(y=exp / 10000, line_dash="dot", line_color="#9333ea", annotation_text=f"Expected (frozen) ₹{exp / 10000:.4f}")
            if adm:
                fig.add_hline(y=adm / 10000, line_dash="dash", line_color="#d97706", annotation_text=f"Administrator ₹{adm / 10000:.4f}",
                              annotation_position="bottom right")
            fig.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10), showlegend=False)
            st.plotly_chart(fig, width="stretch")
    with w2:
        st.subheader("🔍 Balance sheet & controls")
        def rs(col):
            x = num(r.get(col))
            return "—" if x is None else f"₹{x / 100:,.2f}"
        st.markdown(
            f"- **Expected NAV frozen at:** `{r.get('Expected_Frozen_At', '') or '—'}`\n"
            f"- **Holdings market value:** {rs('Holdings_MV_Paise')}\n"
            f"- **Dividend receivable:** {rs('Div_Receivable_Paise')}\n"
            f"- **Cash:** {rs('Cash_Paise')}\n"
            f"- **Fee payable (cumulative):** {rs('Fee_Payable_Paise')}\n"
            f"- **Other liabilities:** {rs('Other_Liab_Paise')}\n"
            f"- **Net assets:** {rs('Net_Assets_Paise')}\n"
            f"- **Top contributors (₹0.0001/unit):** {r.get('Top_Contributors', '') or '—'}")
        resid, prevn = num(r.get("Attr_Residual_1e4")), num(r.get("Prev_NAV_1e4"))
        resid_bps = None if resid is None or not prevn else resid / prevn * 10000
        if truthy(r.get("N04")):
            st.error(f"N04 fired: unexplained residual {resid:+.0f} (×₹0.0001) = {resid_bps:+.2f} bps of NAV.")
        elif resid_bps is not None:
            st.success(f"N04 clean: residual {resid_bps:+.2f} bps of NAV (tolerance per Config RESID_TOL_BPS).")

st.divider()

# ----------------------------------------------------------------------------
# DETAIL TABS
# ----------------------------------------------------------------------------
t1, t2, t3, t4, t5, t6 = st.tabs(["🤖 Cases, AI & guardrails", "🧑‍⚖️ Human decisions & releases", "📈 Prices (P01–P07)",
                                  "🏢 Positions & corporate actions", "🧾 Fees (F01–F03)", "🗂️ Run log"])

with t1:
    scope = st.radio("Cases shown", ["Selected date", "All open cases", "All dates"], horizontal=True)
    cdf = cases_df.copy()
    if not cdf.empty:
        if scope == "Selected date":
            cdf = cdf[cdf["Date"] == sel_date]
        elif scope == "All open cases":
            cdf = cdf[cdf["Status"].isin(["NEW", "AI_DONE", "ESCALATED"])]
        if not ai_df.empty:
            ai_cols = [c for c in ["AI_Call_ID", "Model", "Classification", "Is_Error", "Confidence", "Confidence_Band", "Rationale",
                                   "AI_Status", "HTTP_Status"] if c in ai_df.columns]
            cdf = cdf.merge(ai_df[ai_cols].rename(columns={"AI_Call_ID": "Latest_AI_Call_ID"}), on="Latest_AI_Call_ID", how="left")
        show = [c for c in ["Case_ID", "Case_Type", "Date", "Level", "Fund_IDs", "Security_ID", "Root_Rule_ID", "Max_Severity",
                            "Est_NAV_Impact_bps", "Status", "Classification", "Is_Error", "Confidence", "Confidence_Band",
                            "Guardrail_Status", "Final_Recommendation", "AI_Status", "Model", "Rationale"] if c in cdf.columns]
        st.dataframe(cdf[show], width="stretch", hide_index=True)
    else:
        st.info("No cases yet.")

with t2:
    if reviews_df.empty:
        st.info("No reviewer decisions recorded yet (Google Form → Reviews tab).")
    else:
        v = reviews_df[reviews_df["Valid"].apply(truthy)]
        m = st.columns(4)
        m[0].metric("Valid decisions", len(v))
        m[1].metric("Rejected submissions", len(reviews_df) - len(v))
        agreed = v["Agreed_With_AI"].astype(str).str.upper()
        decided = agreed.isin(["TRUE", "FALSE"])
        m[2].metric("AI–human agreement", f"{(agreed[decided] == 'TRUE').mean():.0%}" if decided.any() else "—")
        secs = pd.to_numeric(v["Seconds_To_Decision"], errors="coerce").dropna()
        m[3].metric("Median time to decision", f"{secs.median() / 60:.1f} min" if len(secs) else "—")
        st.dataframe(reviews_df.iloc[::-1], width="stretch", hide_index=True)
    st.markdown("**Release log**")
    if release_df.empty:
        st.info("No fund-day has been released or held yet.")
    else:
        st.dataframe(release_df.iloc[::-1], width="stretch", hide_index=True)

with t3:
    pdf = load_tab("Prices")
    if not pdf.empty:
        pdf = pdf[pdf["Date"] == sel_date]
        rules = [c for c in ["P01", "P02", "P03", "P04", "P05", "P06", "P07"] if c in pdf.columns]
        only = st.checkbox("Only securities with a rule hit", value=True)
        if only and rules:
            pdf = pdf[pdf[rules].apply(lambda row: any(truthy(x) for x in row), axis=1)]
        show = [c for c in ["Security_ID", "Price_A_Paise", "Price_B_Paise", "Price_C_Paise", "Prev_A", "Prev_B", "Prev_C",
                            "Chg_A_bps", "Chg_B_bps", "Chg_C_bps", "Median_Paise", "Clean_Median"] + rules +
                ["Selected_Price_Paise", "Selected_Source", "Selection_Reason"] if c in pdf.columns]
        st.dataframe(pdf[show], width="stretch", hide_index=True)

with t4:
    pos = load_tab("Positions")
    if not pos.empty:
        pos = pos[pos["Date"] == sel_date]
        rules = [c for c in ["C01", "C02", "C03", "C04"] if c in pos.columns]
        only = st.checkbox("Only positions with a rule hit", value=False, key="pos_only")
        if only and rules:
            pos = pos[pos[rules].apply(lambda row: any(truthy(x) for x in row), axis=1)]
        st.dataframe(pos, width="stretch", hide_index=True)
    ca = load_tab("CorpActions")
    if not ca.empty:
        st.markdown("**Corporate-action calendar (all SYNTHETIC)**")
        st.dataframe(ca, width="stretch", hide_index=True)

with t5:
    fee = load_tab("Fee_Accruals")
    if not fee.empty:
        st.dataframe(fee[fee["Date"] == sel_date], width="stretch", hide_index=True)

with t6:
    rl = load_tab("Run_Log")
    if not rl.empty:
        st.dataframe(rl.iloc[::-1], width="stretch", hide_index=True)

st.caption("Every NAV, price and fee is stored as an integer (paise, ₹0.0001 per unit, milli-units). "
           "The AI classifies and explains; it never calculates, changes data, closes a case or releases a NAV.")
