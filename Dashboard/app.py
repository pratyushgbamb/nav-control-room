"""
THE NAV CONTROL ROOM — Streamlit dashboard v3.0
Zensar BFSI Program · Project 1 · Team: Akshay Jadhav, Deepa Pandey, Pratyush Bamb, Vikram Shetty

v3: multi-page control room · Google sign-in · maker-checker decisions with evidence upload (sent to the Apps Script
web app, which validates them and writes the audit trail; the dashboard never writes to the Sheet) · Replay and Live
environments · fund performance analytics · real-fund (UTI Nifty 50 Index Fund) NAV check · data-quality and audit views.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO
from urllib.parse import quote

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="NAV Control Room", page_icon="🏦", layout="wide")

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_SHEET_ID = "159qLct6HLJG_qAqFAfPn9LOc2bUoog7x3tSHT51mE_k"
LABELS = ["STALE_PRICE", "FAT_FINGER", "SOURCE_VARIANCE", "MISSING_PRICE", "UNPROCESSED_DIVIDEND", "UNPROCESSED_SPLIT",
          "FEE_ERROR", "GENUINE_MARKET_MOVE", "BENIGN_NOISE", "UNEXPLAINED"]
ACTIONS = ["USE_SELECTED_PRICE", "CONFIRM_PRICE_MANUALLY", "BOOK_CORPORATE_ACTION", "RECALCULATE_FEE", "NO_ACTION", "INVESTIGATE"]
REASONS = ["VENDOR_ERROR", "CA_MISSED", "FEE_SETUP", "GENUINE_MOVE", "AI_MISREAD_EVIDENCE", "NEEDS_SENIOR", "CLEAN_RELEASE", "OTHER"]
FUND_STATUS_COLOR = {"RELEASED": "#15803d", "READY": "#16a34a", "IN_REVIEW": "#d97706", "BLOCKED": "#dc2626"}
CASE_STATUS_COLOR = {"NEW": "#64748b", "AI_DONE": "#2563eb", "PENDING_CHECK": "#7c3aed", "ESCALATED": "#ea580c", "CLOSED": "#15803d"}
SERIES_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2"]
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "csv": "text/csv",
        "txt": "text/plain", "eml": "message/rfc822",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xls": "application/vnd.ms-excel"}
MAX_FILE_MB = 10
TABS = {"Funds": "Fund_ID", "NAV_Runs": "Run_Key", "Cases": "Case_ID", "AI_Decisions": "AI_Call_ID", "Reviews": "Review_ID",
        "Release_Log": "Release_ID", "Reviewers": "Reviewer_Email", "NAV_Snapshots": "Snapshot_Key", "Price_Overrides": "Price_Key",
        "Calendar": "Date", "Run_Log": "Run_ID", "Flags": "Flag_ID", "RealFund_NAV": "RF_Key", "Prices": "Price_Key",
        "Positions": "Position_Key", "CorpActions": "CA_ID", "CA_Bookings": "Booking_ID", "Fee_Accruals": "Accrual_Key",
        "Rule_Catalogue": "Rule_ID"}
DATE_COLS = {"Date", "Prev_Business_Day", "Ex_Date", "Pay_Date"}
TS_COLS = {"Timestamp", "Approved_At", "Start_Time", "End_Time", "Created_At", "Closed_At", "Called_At", "Captured_At",
           "Confirmed_At", "Computed_At", "Expected_Frozen_At"}

st.markdown("""
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {padding-top: 3.4rem; padding-bottom: .5rem; max-width: 100%;}
.hero h1 {font-size: 1.5rem; margin: 0;}
.hero p {opacity: .7; margin: .1rem 0 .8rem 0;}
.topbar {display: flex; gap: 14px; align-items: center; flex-wrap: wrap; padding: 8px 12px; border-radius: 10px;
         background: rgba(127,127,127,.08); border: 1px solid rgba(127,127,127,.18); margin-bottom: 10px; font-size: .85rem;}
.topbar .dl {font-weight: 700;}
.tile {border-radius: 10px; padding: 10px 12px; background: rgba(127,127,127,.08); border: 1px solid rgba(127,127,127,.16);}
.tile .l {font-size: .68rem; text-transform: uppercase; letter-spacing: .06em; opacity: .7;}
.tile .v {font-size: 1.45rem; font-weight: 800; line-height: 1.2;}
.tile .s {font-size: .72rem; opacity: .7;}
.kpi {border-radius: 10px; padding: 10px 12px; background: rgba(127,127,127,.08); border: 1px solid rgba(127,127,127,.18);}
.kpi .l {font-size: .68rem; text-transform: uppercase; letter-spacing: .05em; opacity: .7;}
.kpi .v {font-size: 1.4rem; font-weight: 700; line-height: 1.25;}
.kpi .s {font-size: .75rem; opacity: .75;}
.chip {display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: .7rem; font-weight: 700;}
.pipe {display: flex; gap: 6px; flex-wrap: wrap;}
.pipe span {font-size: .72rem; padding: 2px 8px; border-radius: 6px; background: rgba(127,127,127,.12);}
.card {border-radius: 10px; padding: 10px 14px; background: rgba(127,127,127,.07); border: 1px solid rgba(127,127,127,.18); margin-bottom: .5rem;}
.small {font-size: .78rem; opacity: .8;}
.fund {border-radius: 10px; padding: 10px 12px; background: rgba(127,127,127,.07); border: 1px solid rgba(127,127,127,.18); border-left: 6px solid #64748b;}
.fund .fn {font-size: .75rem; opacity: .7;} .fund .nav {font-size: 1.25rem; font-weight: 700;} .fund .meta {font-size: .74rem; opacity: .8;}
.steps {display: flex; gap: 8px; flex-wrap: wrap;} .step {flex: 1 1 150px; border-radius: 10px; padding: 8px 10px; background: rgba(127,127,127,.07);}
.step .t {font-weight: 700; font-size: .8rem;} .step .d {font-size: .72rem; opacity: .8;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------- configuration & data access
def _secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def env_config():
    try:
        e = st.secrets.get("envs", {})
        out = {k: dict(v) for k, v in dict(e).items()}
    except Exception:
        out = {}
    return out or {"replay": {"label": "Replay", "sheet_id": DEFAULT_SHEET_ID, "webapp_url": ""}}


def _has_sa():
    try:
        return "gcp_service_account" in st.secrets
    except Exception:
        return False


def _serial_date(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(v))).strftime("%Y-%m-%d")
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        return pd.to_datetime(s, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return s


def _serial_ts(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=float(v))).strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


@st.cache_data(ttl=30, show_spinner=False)
def load_tab(sheet_id, tab, key_col):
    try:
        if _has_sa():
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]),
                                                          scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
            ws = gspread.authorize(creds).open_by_key(sheet_id).worksheet(tab)
            vals = ws.get_all_values(value_render_option="UNFORMATTED_VALUE", date_time_render_option="SERIAL_NUMBER")
            if not vals:
                return pd.DataFrame()
            df = pd.DataFrame(vals[1:], columns=[str(h).strip() for h in vals[0]])
        else:
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&headers=1&sheet={quote(tab)}"
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text), dtype=str, keep_default_na=False)
            df.columns = [str(c).strip() for c in df.columns]
        df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
        if key_col not in df.columns:
            return pd.DataFrame()
        for c in df.columns:
            if c in DATE_COLS:
                df[c] = df[c].apply(_serial_date)
            elif c in TS_COLS:
                df[c] = df[c].apply(_serial_ts)
        df = df[df[key_col].astype(str).str.strip() != ""].reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def T(tab):
    return load_tab(ENV["sheet_id"], tab, TABS[tab])


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, np.integer, np.floating)):
        return None if pd.isna(v) else float(v)
    s = str(v).strip().replace(",", "").replace("₹", "")
    if s in ("", "—", "-", "nan", "NaN", "None") or s.startswith("#") or s.upper() == "NOT_COMPUTABLE":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def numcol(df, c):
    return pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.Series(np.nan, index=df.index)


def truthy(v):
    return v is True or str(v).strip().upper() == "TRUE"


def nav_text(v1e4):
    x = num(v1e4)
    return "NOT COMPUTABLE" if x is None else f"₹{x / 10000:,.4f}"


def fmt_bps(v, signed=True):
    x = num(v)
    return "—" if x is None else (f"{x:+.1f} bps" if signed else f"{x:.1f} bps")


def chip(text, color):
    return f'<span class="chip" style="background:{color}22;color:{color};border:1px solid {color}55">{text}</span>'


def kpis(items):
    cols = st.columns(len(items))
    for col, (label, value, sub) in zip(cols, items):
        col.markdown(f'<div class="kpi"><div class="l">{label}</div><div class="v">{value}</div><div class="s">{sub}</div></div>',
                     unsafe_allow_html=True)


def header(title, subtitle):
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)


def flash():
    f = st.session_state.pop("flash", None)
    if f:
        (st.success if f[0] == "ok" else st.error)(f[1])


def finish(resp, ok_msg=None):
    if resp.get("ok"):
        st.session_state["flash"] = ("ok", ok_msg or resp.get("message") or "Done.")
    else:
        st.session_state["flash"] = ("err", resp.get("error") or "Request failed.")
    st.cache_data.clear()
    st.rerun()


def api(action, data, timeout=120):
    url, secret = ENV.get("webapp_url"), _secret("navcr_secret")
    if not url or not secret:
        return {"ok": False, "error": "Web app URL or shared secret is not configured for this environment."}
    payload = json.dumps(data, separators=(",", ":"), default=str)
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    sig = hmac.new(secret.encode(), f"{action}|{ts}|{nonce}|{payload}".encode(), hashlib.sha256).hexdigest()
    try:
        r = requests.post(url, data=json.dumps({"action": action, "ts": ts, "nonce": nonce, "payload": payload, "sig": sig}),
                          headers={"Content-Type": "application/json"}, timeout=timeout)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------- identity
def auth_configured():
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def me():
    if not auth_configured():
        return None
    try:
        if not st.user.is_logged_in:
            return None
        return {"email": str(st.user.get("email") or "").strip().lower(), "name": str(st.user.get("name") or "")}
    except Exception:
        return None


def reviewer_map():
    rv = T("Reviewers")
    if rv.empty:
        return {}
    return {str(r["Reviewer_Email"]).strip().lower(): {"name": str(r.get("Name", "")), "role": str(r.get("Role", "")).upper()}
            for _, r in rv.iterrows()}


def role_of(u):
    return reviewer_map().get(u["email"], {}).get("role") if u else None


def who(email):
    e = str(email or "").strip().lower()
    return reviewer_map().get(e, {}).get("name") or ("Unregistered" if e else "—")


# ---------------------------------------------------------------------------- shared data helpers
def ai_rows():
    ai = T("AI_Decisions")
    return {str(r["AI_Call_ID"]): r for _, r in ai.iterrows()} if not ai.empty else {}


def ai_coverage(exc):
    if exc.empty:
        return 0, 0
    m = ai_rows()
    return int(sum(str(m.get(str(x), {}).get("AI_Status", "")) in ("OK", "CACHED") for x in exc["Latest_AI_Call_ID"])), len(exc)


def age_text(ts):
    try:
        t = pd.Timestamp(ts).tz_localize(IST) if pd.Timestamp(ts).tzinfo is None else pd.Timestamp(ts)
        mins = (pd.Timestamp.now(tz=IST) - t).total_seconds() / 60
        return f"{mins:.0f} min" if mins < 120 else f"{mins / 60:.1f} h"
    except Exception:
        return "—"


def events():
    ev = []
    rl = T("Run_Log")
    for _, r in rl.iterrows():
        ev.append({"Time": r.get("End_Time") or r.get("Start_Time"), "Type": "RUN", "Actor": "System",
                   "Object": f"{r.get('Date', '')} · {r.get('Step', '')}", "Detail": f"{r.get('Status', '')} — {r.get('Message', '')}"})
    for _, r in T("AI_Decisions").iterrows():
        ev.append({"Time": r.get("Called_At"), "Type": "AI", "Actor": str(r.get("Model", ""))[:40], "Object": r.get("Case_ID"),
                   "Detail": f"{r.get('Classification', '')} · conf {r.get('Confidence', '')} · {r.get('Confidence_Band', '')} · {r.get('AI_Status', '')}"})
    for _, r in T("Reviews").iterrows():
        ok = truthy(r.get("Valid"))
        ev.append({"Time": r.get("Timestamp"), "Type": f"REVIEW · {r.get('Stage', 'MAKER') or 'MAKER'}", "Actor": who(r.get("Reviewer_Email")),
                   "Object": r.get("Case_ID"), "Detail": f"{r.get('Decision', '')} {r.get('Final_Classification', '')}"
                   + ("" if ok else f" — REJECTED: {r.get('Invalid_Reason', '')}")})
    for _, r in T("Release_Log").iterrows():
        ev.append({"Time": r.get("Approved_At"), "Type": "RELEASE", "Actor": who(r.get("Approved_By")),
                   "Object": f"{r.get('Date', '')} · {r.get('Fund_ID', '')}", "Detail": f"{r.get('Decision', '')} · NAV {nav_text(r.get('NAV_1e4_Released'))}"})
    for _, r in T("Price_Overrides").iterrows():
        ev.append({"Time": r.get("Confirmed_At"), "Type": "PRICE CONFIRMED", "Actor": who(r.get("Confirmed_By")),
                   "Object": f"{r.get('Date', '')} · {r.get('Security_ID', '')}", "Detail": f"₹{(num(r.get('Confirmed_Price_Paise')) or 0) / 100:,.2f}"})
    df = pd.DataFrame(ev)
    if df.empty:
        return df
    df["Time"] = df["Time"].astype(str)
    return df.sort_values("Time", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------- sidebar: environment, identity, date
ENVS = env_config()
with st.sidebar:
    env_key = st.selectbox("Environment", list(ENVS), format_func=lambda k: ENVS[k].get("label", k.title()), key="env")
ENV = ENVS[env_key]
ENV_MODE = "live" if "live" in env_key.lower() else "replay"

with st.sidebar:
    u = me()
    if not auth_configured():
        st.caption("Sign-in is not configured: read-only mode.")
    elif u is None:
        st.button("🔐 Sign in with Google", on_click=st.login, use_container_width=True)
    else:
        role = role_of(u)
        st.markdown(f"**{u['name'] or u['email']}**  \n" + (chip(role, "#2563eb") if role else chip("NOT REGISTERED", "#dc2626")),
                    unsafe_allow_html=True)
        st.button("Sign out", on_click=st.logout, use_container_width=True)
    nav_all = T("NAV_Runs")
    if nav_all.empty:
        st.warning("NAV_Runs is empty for this environment.")
        dates = []
    else:
        frozen = nav_all[nav_all.get("Expected_Frozen_At", pd.Series("", index=nav_all.index)).astype(str).str.strip() != ""]
        dates = sorted((frozen if not frozen.empty else nav_all)["Date"].dropna().unique(), reverse=True)
    SEL_DATE = st.selectbox("Valuation date", dates, index=0, key=f"date_{env_key}") if dates else ""
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Data refreshed at most every 30 s · {datetime.now(IST):%d %b %Y %H:%M} IST")
    st.markdown(f"[Open the Sheet](https://docs.google.com/spreadsheets/d/{ENV['sheet_id']}/edit)")


# ---------------------------------------------------------------------------- reusable panels
def mini_waterfall(r, height=300):
    prev, cur = num(r.get("Prev_NAV_1e4")), num(r.get("NAV_1e4"))
    comps = [num(r.get(c)) for c in ("Attr_Market_1e4", "Attr_Income_1e4", "Attr_Fees_1e4", "Attr_Residual_1e4")]
    if prev is None or cur is None or any(c is None for c in comps):
        st.info("Attribution not available (NAV not computable or no previous NAV).")
        return
    p, c = prev / 10000, cur / 10000
    m, inc, fee, res = (x / 10000 for x in comps)
    fig = go.Figure(go.Waterfall(measure=["absolute", "relative", "relative", "relative", "relative", "total"],
                                 x=["Previous", "Market", "Income", "Fees", "Unexplained", "Today"], y=[p, m, inc, fee, res, c],
                                 text=[f"₹{p:.4f}", f"{m:+.4f}", f"{inc:+.4f}", f"{fee:+.4f}", f"{res:+.4f}", f"₹{c:.4f}"],
                                 textposition="outside", increasing={"marker": {"color": "#16a34a"}},
                                 decreasing={"marker": {"color": "#dc2626"}}, totals={"marker": {"color": "#2563eb"}}))
    levels = [p, p + m, p + m + inc, p + m + inc + fee, c]
    pad = max((max(levels) - min(levels)) * 0.4, p * 0.001)
    fig.update_yaxes(range=[min(levels) - pad, max(levels) + pad], title="₹ per unit")
    style(fig, height)
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


def evidence_panel(c, f):
    d, sec = c["Date"], str(c.get("Security_ID", "ALL"))
    funds = [x for x in str(c.get("Fund_IDs", "")).split(",") if x and x != "ALL"]
    if str(c.get("Case_Type")) == "RELEASE":
        ai = ai_rows().get(str(c.get("Latest_AI_Call_ID")), {})
        st.markdown(f'<div class="card"><b>Release recommendation:</b> {c.get("Final_Recommendation", "")}</div>', unsafe_allow_html=True)
        if ai:
            st.caption(f"AI memo confidence {ai.get('Confidence', '—')} · guardrail {ai.get('Guardrail_Applied', 'NONE')} {ai.get('Guardrail_Reason', '')}")
        cases = T("Cases")
        closed = cases[(cases["Date"] == d) & (cases["Case_Type"] == "EXCEPTION") &
                       cases["Fund_IDs"].astype(str).str.split(",").apply(lambda x: funds[0] in x if funds else False)]
        st.markdown("**Exception cases on this fund-day**")
        if closed.empty:
            st.caption("None: clean fund-day (rule R02).")
        else:
            st.dataframe(closed[[x for x in ["Case_ID", "Root_Rule_ID", "Max_Severity", "Status", "Final_Recommendation"] if x in closed.columns]],
                         hide_index=True, use_container_width=True)
    if not f.empty:
        rc = T("Rule_Catalogue")
        names = dict(zip(rc["Rule_ID"], rc["Rule_Name"])) if not rc.empty else {}
        show = f[[x for x in ["Rule_ID", "Severity", "Fund_ID", "Source", "Observed_Value", "Threshold_Value", "Est_NAV_Impact_bps"] if x in f.columns]].copy()
        show.insert(1, "Rule", show["Rule_ID"].map(names))
        st.markdown("**Why it was flagged**")
        st.dataframe(show, hide_index=True, use_container_width=True)
    if sec and sec != "ALL":
        pr = T("Prices")
        h = pr[(pr["Security_ID"] == sec) & (pr["Date"] <= d)].sort_values("Date").tail(8) if not pr.empty else pr
        if not h.empty:
            fig = go.Figure()
            for src, color in (("A", "#2563eb"), ("B", "#16a34a"), ("C", "#d97706")):
                fig.add_trace(go.Scatter(x=h["Date"], y=numcol(h, f"Price_{src}_Paise") / 100, mode="lines+markers",
                                         name=f"Source {src}", line=dict(color=color)))
            fig.add_trace(go.Scatter(x=h["Date"], y=numcol(h, "Selected_Price_Paise") / 100, mode="markers", name="Selected",
                                     marker=dict(symbol="diamond-open", size=13, color="#111827", line=dict(width=2))))
            style(fig, 280, f"{sec}: three sources, last {len(h)} days")
            fig.update_yaxes(title="₹ per share")
            st.plotly_chart(fig, use_container_width=True)
            today = h[h["Date"] == d]
            if not today.empty:
                t = today.iloc[0]
                st.caption(f"Selected source: **{t.get('Selected_Source', '—')}** · {t.get('Selection_Reason', '')} · "
                           f"volume {t.get('Volume_A', '—')}")
        ca = T("CorpActions")
        if not ca.empty:
            near = ca[(ca["Security_ID"] == sec)]
            near = near[pd.to_datetime(near["Ex_Date"], errors="coerce").sub(pd.Timestamp(d)).abs() <= pd.Timedelta(days=7)]
            if not near.empty:
                st.markdown("**Corporate actions within 7 days**")
                st.dataframe(near, hide_index=True, use_container_width=True)
                bk = T("CA_Bookings")
                if not bk.empty:
                    st.dataframe(bk[bk["CA_ID"].isin(near["CA_ID"])], hide_index=True, use_container_width=True)
    if not f.empty and f["Rule_ID"].astype(str).str.startswith("F").any():
        fe = T("Fee_Accruals")
        rows = fe[(fe["Date"] == d) & fe["Fund_ID"].isin(funds)] if not fe.empty else fe
        if not rows.empty:
            st.markdown("**Booked vs recalculated fee**")
            st.dataframe(rows[[x for x in ["Fund_ID", "Accrual_Days", "Exp_Accrual_Days", "Basis_Net_Assets_Paise", "Prev_Net_Assets_Paise",
                                           "Mgmt_Fee_Paise", "Recalc_Mgmt_Paise", "Diff_Mgmt_Paise"] if x in rows.columns]],
                         hide_index=True, use_container_width=True)
    nav = T("NAV_Runs")
    ctx = nav[(nav["Date"] == d) & nav["Fund_ID"].isin(funds)] if funds else nav.iloc[0:0]
    if not ctx.empty:
        st.markdown("**NAV context**")
        view = pd.DataFrame({"Fund": ctx["Fund_ID"], "NAV": ctx["NAV_1e4"].apply(nav_text),
                             "Expected": ctx["Expected_NAV_1e4"].apply(nav_text), "Dev vs expected": ctx["Dev_vs_Expected_bps"].apply(fmt_bps),
                             "Band": ctx["Band_bps"].apply(lambda v: fmt_bps(v, False)), "Admin break": ctx["Admin_Break_bps"].apply(lambda v: fmt_bps(v, False)),
                             "Status": ctx["Fund_Day_Status"]})
        st.dataframe(view, hide_index=True, use_container_width=True)
        mini_waterfall(ctx.iloc[0], height=280)


def ai_panel(ai):
    if not ai or str(ai.get("Classification", "")) in ("", "NO_AI"):
        st.warning("No AI recommendation for this case (guardrail G4). Decide from the evidence and use OVERRIDE.")
        if ai:
            st.caption(f"Attempt detail: HTTP {ai.get('HTTP_Status_History') or ai.get('HTTP_Status', '')} · {ai.get('Rationale', '')}")
        return
    band = str(ai.get("Confidence_Band", ""))
    bcol = {"HIGH": "#16a34a", "MEDIUM": "#d97706", "LOW": "#dc2626"}.get(band, "#64748b")
    conf = num(ai.get("Confidence")) or 0
    c1, c2, c3 = st.columns(3)
    c1.markdown(f'<div class="kpi"><div class="l">Classification</div><div class="v" style="font-size:1.1rem">{ai.get("Classification")}</div>'
                f'<div class="s">is_error = {ai.get("Is_Error")}</div></div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="kpi"><div class="l">Recommended action</div><div class="v" style="font-size:1.1rem">{ai.get("Recommended_Action")}</div>'
                f'<div class="s">source {ai.get("Recommended_Source", "—")} · impact {ai.get("AI_Impact_bps", "—")} bps</div></div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="kpi"><div class="l">Confidence</div><div class="v">{conf:.2f}</div><div class="s">{chip(band or "—", bcol)}</div></div>',
                unsafe_allow_html=True)
    st.progress(min(max(conf, 0.0), 1.0))
    if band == "LOW":
        st.error("AI uncertain or in disagreement with the rules: verify the evidence before approving.")
    st.markdown(f'<div class="card"><b>Rationale.</b> {ai.get("Rationale", "")}<br><span class="small">Evidence used: '
                f'{ai.get("Evidence_Used", "")}</span></div>', unsafe_allow_html=True)
    st.caption(f"Guardrails: {ai.get('Guardrail_Applied', 'NONE')} — {ai.get('Guardrail_Reason', '')} · Final recommendation: "
               f"{ai.get('Final_Recommendation', '')} · Model {ai.get('Model', '')} · Prompt {ai.get('Prompt_Version', '')} · "
               f"HTTP {ai.get('HTTP_Status_History') or ai.get('HTTP_Status', '')}")


def evidence_payload(files):
    out = []
    for up in files or []:
        b = up.getvalue()
        if len(b) > MAX_FILE_MB * 1024 * 1024:
            raise ValueError(f"{up.name} is larger than {MAX_FILE_MB} MB")
        ext = up.name.rsplit(".", 1)[-1].lower()
        out.append({"name": up.name, "mime": MIME.get(ext, up.type or "application/octet-stream"), "b64": base64.b64encode(b).decode()})
    return out


def maker_form(c, rules, ai):
    u = me()
    role = role_of(u)
    cid, status = c["Case_ID"], str(c["Status"])
    is_release = str(c.get("Case_Type")) == "RELEASE"
    if not u:
        st.info("Sign in to propose a decision.")
        return
    if not role:
        st.warning("Your account is not registered in the Reviewers tab.")
        return
    if status == "ESCALATED" and role != "SENIOR":
        st.info("This case was escalated: only a SENIOR can act as maker.")
        return
    needs_price = any(r in ("P02", "P07") for r in rules)
    st.markdown(f"**Maker proposal** · you are {chip(role, '#2563eb')} · a different SENIOR must confirm it", unsafe_allow_html=True)
    with st.form(f"maker_{cid}"):
        if is_release:
            decision = st.radio("Proposal", ["RELEASE", "HOLD"], horizontal=True, index=None)
            action = st.selectbox("Action taken", ["RELEASE_NAV", "HOLD_NAV"], index=None)
            final_class = ""
        else:
            decision = st.radio("Decision", ["APPROVE", "OVERRIDE", "ESCALATE"], horizontal=True, index=None,
                                help="APPROVE = accept the AI classification · OVERRIDE = your own classification · ESCALATE = send to a SENIOR")
            final_class = st.selectbox("Final classification (required for OVERRIDE)", LABELS, index=None)
            action = st.selectbox("Action taken (required for APPROVE / OVERRIDE)", ACTIONS, index=None)
        reason_code = st.selectbox("Reason code (required for OVERRIDE / ESCALATE / HOLD)", REASONS, index=None)
        reason_text = st.text_area("Reason (at least 20 characters for OVERRIDE / ESCALATE / HOLD)", height=90)
        corrected = st.text_input("Confirmed price in paise (required: no corroborated price, rule P02/P07)") if needs_price else ""
        files = st.file_uploader("Attach evidence (vendor e-mail, exchange screenshot, calculation)", accept_multiple_files=True,
                                 type=list(MIME.keys()))
        ack = st.checkbox("I reviewed the evidence, not only the AI recommendation")
        submit = st.form_submit_button("Submit for checker approval", type="primary")
    if submit:
        if not decision:
            st.error("Choose a decision.")
            return
        try:
            ev = evidence_payload(files)
        except ValueError as e:
            st.error(str(e))
            return
        with st.spinner("Recording the proposal…"):
            resp = api("decide", {"caseId": cid, "decision": decision, "finalClass": final_class or "", "action": action or "",
                                  "reasonCode": reason_code or "", "reasonText": reason_text, "correctedPrice": corrected,
                                  "evidenceChecked": ack, "evidence": ev, "email": u["email"], "channel": "DASHBOARD"}, timeout=180)
        finish(resp, f"Proposal {resp.get('reviewId', '')} recorded: case {cid} is now {resp.get('status', '')}.")


def checker_panel(c):
    rv = T("Reviews")
    m = rv[rv["Review_ID"] == str(c.get("Pending_Review_ID", ""))] if not rv.empty else rv
    if m.empty:
        st.error("The maker proposal for this case was not found.")
        return
    m = m.iloc[0]
    links = [x for x in str(m.get("Evidence_Links", "")).split() if x.startswith("http")]
    ev_html = " · ".join(f'<a href="{x}" target="_blank">evidence {i + 1}</a>' for i, x in enumerate(links)) or "none attached"
    st.markdown(
        f'<div class="card"><b>Maker proposal {m["Review_ID"]}</b> by {who(m.get("Reviewer_Email"))} at {m.get("Timestamp")}<br>'
        f'Decision <b>{m.get("Decision")}</b> · classification <b>{m.get("Final_Classification") or "—"}</b> · '
        f'action {m.get("Action_Taken") or "—"} · confirmed price {m.get("Corrected_Price_Paise") or "—"}<br>'
        f'Reason: {m.get("Reason_Code") or ""} {m.get("Reason_Text") or ""}<br>Evidence: {ev_html}</div>', unsafe_allow_html=True)
    u = me()
    role = role_of(u)
    if not u:
        st.info("Sign in as a SENIOR to check this proposal.")
        return
    if role != "SENIOR":
        st.info("Only a SENIOR can act as checker.")
        return
    if u["email"] == str(m.get("Reviewer_Email", "")).strip().lower():
        st.warning("Segregation of duties: you made this proposal, so another SENIOR must check it.")
        return
    with st.form(f"checker_{c['Case_ID']}"):
        decision = st.radio("Checker decision", ["CONFIRM", "RETURN"], horizontal=True, index=None)
        reason_code = st.selectbox("Reason code (required for RETURN)", REASONS, index=None)
        reason_text = st.text_area("Reason (at least 20 characters for RETURN)", height=80)
        files = st.file_uploader("Attach supporting evidence (optional)", accept_multiple_files=True, type=list(MIME.keys()))
        ack = st.checkbox("I reviewed the maker's evidence and the case evidence")
        submit = st.form_submit_button("Record checker decision", type="primary")
    if submit:
        if not decision:
            st.error("Choose CONFIRM or RETURN.")
            return
        try:
            ev = evidence_payload(files)
        except ValueError as e:
            st.error(str(e))
            return
        with st.spinner("Recording the checker decision…"):
            resp = api("decide", {"caseId": c["Case_ID"], "decision": decision, "reasonCode": reason_code or "",
                                  "reasonText": reason_text, "evidenceChecked": ack, "evidence": ev,
                                  "email": u["email"], "channel": "DASHBOARD"}, timeout=180)
        finish(resp, f"Checker decision recorded: case {c['Case_ID']} is now {resp.get('status', '')}.")


def history_panel(cid):
    rv = T("Reviews")
    h = rv[rv["Case_ID"] == cid].copy() if not rv.empty else rv
    if h.empty:
        st.caption("No decisions recorded yet.")
        return
    h.insert(1, "Reviewer", h["Reviewer_Email"].apply(who))
    cols = [x for x in ["Timestamp", "Reviewer", "Stage", "Decision", "Final_Classification", "Action_Taken", "Reason_Code",
                        "Reason_Text", "Valid", "Invalid_Reason", "Evidence_Count", "Evidence_SHA256", "Channel", "Review_ID"] if x in h.columns]
    st.dataframe(h[cols].iloc[::-1], hide_index=True, use_container_width=True)


def render_case(cid):
    cases = T("Cases")
    row = cases[cases["Case_ID"] == cid]
    if row.empty:
        st.error("Case not found.")
        return
    c = row.iloc[0]
    status = str(c["Status"])
    fl = T("Flags")
    f = fl[fl["Case_ID"] == cid] if not fl.empty else fl
    rules = sorted(set(f["Rule_ID"].astype(str))) if not f.empty else []
    ai = ai_rows().get(str(c.get("Latest_AI_Call_ID")), {})
    sev = str(c.get("Max_Severity", ""))
    st.markdown(f'<div class="card"><b style="font-size:1.1rem">{cid}</b> &nbsp; {chip(status, CASE_STATUS_COLOR.get(status, "#64748b"))} '
                f'{chip(sev, "#dc2626" if sev == "HARD" else "#d97706")} &nbsp; {c.get("Date")} · {c.get("Level")} · funds '
                f'{c.get("Fund_IDs")} · security {c.get("Security_ID")} · root rule {c.get("Root_Rule_ID")} · '
                f'est. impact {c.get("Est_NAV_Impact_bps")} bps · age {age_text(c.get("Created_At"))}</div>', unsafe_allow_html=True)
    t1, t2, t3, t4 = st.tabs(["🔎 Evidence", "🤖 AI recommendation", "✍️ Decision", "🕘 History"])
    with t1:
        evidence_panel(c, f)
    with t2:
        ai_panel(ai)
    with t3:
        if status == "CLOSED":
            st.success(f"Closed at {c.get('Closed_At')}: maker review {c.get('Latest_Review_ID')}, checker review "
                       f"{c.get('Checker_Review_ID', '—')}.")
        elif status == "PENDING_CHECK":
            checker_panel(c)
        else:
            maker_form(c, rules, ai)
    with t4:
        history_panel(cid)


def select_and_render(table, cols, key):
    if table.empty:
        st.info("Nothing to show for this selection.")
        return
    ev = st.dataframe(table[cols], hide_index=True, use_container_width=True, on_select="rerun",
                      selection_mode="single-row", key=key)
    rows = ev.selection.rows if ev is not None and hasattr(ev, "selection") else []
    if rows:
        render_case(table.iloc[rows[0]]["Case_ID"])
    else:
        st.caption("Select a row to open the case.")


def with_ai_columns(df):
    m = ai_rows()
    df = df.copy()
    df["AI_Class"] = df["Latest_AI_Call_ID"].map(lambda x: m.get(str(x), {}).get("Classification", ""))
    df["AI_Conf"] = df["Latest_AI_Call_ID"].map(lambda x: m.get(str(x), {}).get("Confidence", ""))
    df["AI_Band"] = df["Latest_AI_Call_ID"].map(lambda x: m.get(str(x), {}).get("Confidence_Band", ""))
    return df

def case_icon(r):
    s = str(r.get("Status", ""))
    if s == "CLOSED":
        return "🟢"
    if s == "PENDING_CHECK":
        return "🟣"
    if s == "ESCALATED":
        return "🟠"
    return "🔴" if str(r.get("Max_Severity", "")) == "HARD" else "🟡"

# ---------------------------------------------------------------------------- dashboard helpers (Control Tower v3.1)
MATRIX_SCALE = [[0.0, "#15803d"], [0.25, "#15803d"], [0.25, "#1d4ed8"], [0.5, "#1d4ed8"],
                [0.5, "#b45309"], [0.75, "#b45309"], [0.75, "#b91c1c"], [1.0, "#b91c1c"]]


def style(fig, height, title=None):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=34 if title else 8, b=8), font=dict(size=11),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", legend=dict(orientation="h", y=-0.18))
    if title:
        fig.update_layout(title=dict(text=title, font=dict(size=13), x=0.01))
    return fig


def tiles(items):
    cols = st.columns(len(items))
    for col, (label, value, sub, color) in zip(cols, items):
        col.markdown(f'<div class="tile" style="border-top:3px solid {color}"><div class="l">{label}</div>'
                     f'<div class="v">{value}</div><div class="s">{sub}</div></div>', unsafe_allow_html=True)


def pipeline_html(day, exc, cday):
    rl, fl = T("Run_Log"), T("Flags")
    run = rl[(rl["Date"] == SEL_DATE) & (rl["Step"] == "FULL_PIPELINE")] if not rl.empty else rl
    ran = (not run.empty) and (run["Status"] == "SUCCESS").any()
    nfl = int((fl["Date"] == SEL_DATE).sum()) if not fl.empty else 0
    ok, n = ai_coverage(exc)
    closed = int((exc["Status"] == "CLOSED").sum()) if not exc.empty else 0
    pending = int((cday["Status"] == "PENDING_CHECK").sum()) if not cday.empty else 0
    rel = int((day["Fund_Day_Status"].astype(str).str.upper() == "RELEASED").sum())
    steps = [("Feeds", "✅" if ran else "▫️"), (f"Rules {nfl} flags", "✅" if ran else "▫️"),
             (f"AI {ok}/{n}", "✅" if ok == n else "⏳"), (f"Maker-checker {closed}/{n} · {pending} pending", "✅" if closed == n else "⏳"),
             (f"Released {rel}/{len(day)}", "✅" if rel == len(day) else "⏳")]
    return '<div class="pipe">' + "".join(f"<span>{i} {t}</span>" for t, i in steps) + "</div>"


def control_matrix(day, exc):
    controls = ["Prices", "Corp. actions", "Fees", "Expected NAV", "Administrator", "Attribution", "Release"]
    z, txt = [], []
    for _, r in day.iterrows():
        f = r["Fund_ID"]
        mine = exc[exc["Fund_IDs"].astype(str).str.split(",").apply(lambda x: f in x)] if not exc.empty else exc

        def case_cell(prefix):
            m = mine[mine["Root_Rule_ID"].astype(str).str.startswith(prefix)] if not mine.empty else mine
            if m.empty:
                return 0, "OK"
            op = m[m["Status"] != "CLOSED"]
            if op.empty:
                return 1, f"{len(m)} resolved"
            return (3 if (op["Max_Severity"] == "HARD").any() else 2), f"{len(op)} open"

        cells = [case_cell("P"), case_cell("C"), case_cell("F")]
        dev = num(r.get("Dev_vs_Expected_bps"))
        cells.append((3, "n/a") if dev is None else ((2 if truthy(r.get("N01")) else 0), f"{dev:+.0f} bps"))
        brk = num(r.get("Admin_Break_bps"))
        cells.append((2, "missing") if brk is None else ((3 if truthy(r.get("N03")) else 2 if truthy(r.get("N02")) else 0), f"{brk:.1f} bps"))
        res, prev = num(r.get("Attr_Residual_1e4")), num(r.get("Prev_NAV_1e4"))
        rb = None if res is None or not prev else res / prev * 10000
        cells.append((3, "n/a") if rb is None else ((2 if truthy(r.get("N04")) else 0), f"{rb:+.1f} bps"))
        s = str(r.get("Fund_Day_Status", "")).upper()
        cells.append({"RELEASED": (0, "Released"), "READY": (1, "Ready"), "IN_REVIEW": (2, "In review"), "BLOCKED": (3, "Blocked")}.get(s, (2, s or "—")))
        z.append([c[0] for c in cells])
        txt.append([c[1] for c in cells])
    fig = go.Figure(go.Heatmap(z=z, x=controls, y=list(day["Fund_ID"]), text=txt, texttemplate="%{text}", zmin=0, zmax=3,
                               colorscale=MATRIX_SCALE, showscale=False, xgap=3, ygap=3, textfont=dict(color="white", size=11),
                               hovertemplate="%{y} · %{x}: %{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(side="top")
    style(fig, 320)
    fig.update_layout(margin=dict(l=8, r=8, t=40, b=8))   # room for the column labels at the top
    return fig


def deviation_chart(day):
    dev, band, y = numcol(day, "Dev_vs_Expected_bps"), numcol(day, "Band_bps"), day["Fund_ID"]
    colors = ["#64748b" if pd.isna(d) else ("#dc2626" if abs(d) > b else "#16a34a") for d, b in zip(dev, band.fillna(0))]
    fig = go.Figure(go.Bar(x=dev.fillna(0), y=y, orientation="h", marker_color=colors, name="Actual − expected",
                           text=["n/a" if pd.isna(d) else f"{d:+.0f}" for d in dev], textposition="outside"))
    for sign, show in ((1, True), (-1, False)):
        fig.add_trace(go.Scatter(x=sign * band, y=y, mode="markers", name="± band", showlegend=show,
                                 marker=dict(symbol="line-ns-open", size=22, color="#94a3b8", line=dict(width=2))))
    fig.add_vline(x=0, line_color="#94a3b8", line_width=1)
    fig.update_yaxes(autorange="reversed")
    return style(fig, 320)


def trend_chart(nav):
    df = nav[nav["Date"] <= SEL_DATE].copy()
    df["NAV"] = numcol(df, "NAV_1e4") / 10000
    piv = df.pivot_table(index="Date", columns="Fund_ID", values="NAV", aggfunc="last").sort_index().tail(20)
    reb = piv.apply(lambda s: s / s.dropna().iloc[0] * 100 if s.notna().any() else s)
    fig = go.Figure([go.Scatter(x=reb.index, y=reb[f], name=f, mode="lines", line=dict(width=2, color=SERIES_COLORS[i % 6]))
                     for i, f in enumerate(reb.columns)])
    return style(fig, 260, "NAV trend · last 20 days (rebased to 100)")


def donut(cday):
    if cday.empty:
        labels, values, colors, total = ["No cases"], [1], ["#334155"], 0
    else:
        vc = cday["Status"].value_counts()
        labels, values, total = list(vc.index), list(vc.values), int(vc.sum())
        colors = [CASE_STATUS_COLOR.get(s, "#64748b") for s in labels]
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.62, marker=dict(colors=colors), textinfo="value", sort=False))
    fig.add_annotation(text=f"<b>{total}</b><br>cases", showarrow=False, font=dict(size=14))
    return style(fig, 260, "Cases by status")


def gauge(ok, n):
    pct = ok / n * 100 if n else 100.0
    fig = go.Figure(go.Indicator(mode="gauge+number", value=pct, number={"suffix": "%"},
                                 gauge={"axis": {"range": [0, 100]}, "bar": {"color": "#3b82f6"},
                                        "steps": [{"range": [0, 60], "color": "rgba(220,38,38,.25)"},
                                                  {"range": [60, 90], "color": "rgba(217,119,6,.25)"},
                                                  {"range": [90, 100], "color": "rgba(22,163,74,.25)"}]}))
    return style(fig, 260, f"AI coverage · {ok}/{n} cases" if n else "AI coverage · no cases")


def top_bar(day, exc, cday):
    now = datetime.now(IST)
    if SEL_DATE == now.strftime("%Y-%m-%d"):
        secs = (now.replace(hour=23, minute=0, second=0, microsecond=0) - now).total_seconds()
        dl = f"⏱ {int(secs // 3600)}h {int(secs % 3600 // 60)}m to the 11:00 PM NAV deadline" if secs > 0 else "⚠ Past the 11:00 PM NAV deadline"
    else:
        dl = "Historical valuation date"
    env_col = "#16a34a" if ENV_MODE == "live" else "#7c3aed"
    st.markdown(f'<div class="topbar">{chip(ENV.get("label", env_key).upper(), env_col)}<b>NAV date {SEL_DATE}</b>'
                f'<span class="dl">{dl}</span>{pipeline_html(day, exc, cday)}'
                f'<span class="small">updated {now:%H:%M:%S} IST</span></div>', unsafe_allow_html=True)


def operate_panel():
    u = me()
    can = bool(u and role_of(u))
    if ENV_MODE == "live":
        n_inj = st.number_input("Demo errors to plant (private answer key)", min_value=0, max_value=3, value=0, step=1)
        if st.button("⚡ Fetch & process latest trading day", type="primary", disabled=not can, use_container_width=True):
            finish(api("dispatch_live", {"inject": int(n_inj)}))
    elif st.button("▶ Process next replay date", type="primary", disabled=not can, use_container_width=True):
        finish(api("run_next", {}))
    if st.button("🤖 Retry AI on failed cases", disabled=not can, use_container_width=True):
        finish(api("retry_ai", {}))
    if not can:
        st.caption("Sign in with a registered reviewer account to operate the pipeline.")


def control_tower_body():
    nav, funds, cases = T("NAV_Runs"), T("Funds"), T("Cases")
    day = nav[nav["Date"] == SEL_DATE].copy() if (not nav.empty and SEL_DATE) else pd.DataFrame()
    if day.empty:
        st.info("No NAV rows for the selected date.")
        return
    if not funds.empty:
        day = day.merge(funds[["Fund_ID", "Fund_Name"]], on="Fund_ID", how="left")
    day = day.sort_values("Fund_ID").reset_index(drop=True)
    cday = cases[cases["Date"] == SEL_DATE] if not cases.empty else cases
    exc = cday[cday["Case_Type"] == "EXCEPTION"] if not cday.empty else cday
    top_bar(day, exc, cday)
    if "Expected_Frozen_At" in day and (day["Expected_Frozen_At"].astype(str).str.strip() == "").all():
        st.info("Warm-up date: clean history used to estimate beta and the expected-NAV band. It is not checked by the rules. "
                + ("Use ⚙ Operate → Fetch & process latest trading day to run the first live day." if ENV_MODE == "live"
                   else "Select a test date in the sidebar."))
    status = day["Fund_Day_Status"].astype(str).str.upper()
    ok, n = ai_coverage(exc)
    open_m = (exc["Status"] != "CLOSED") if not exc.empty else pd.Series(dtype=bool)
    hard_open = int((open_m & (exc["Max_Severity"] == "HARD")).sum()) if not exc.empty else 0
    pending = int((cday["Status"] == "PENDING_CHECK").sum()) if not cday.empty else 0
    dev, band, brk = numcol(day, "Dev_vs_Expected_bps"), numcol(day, "Band_bps"), numcol(day, "Admin_Break_bps")
    tiles([("Released", f"{int((status == 'RELEASED').sum())}/{len(day)}", f"{int((status == 'READY').sum())} ready for release", "#16a34a"),
           ("Blocked", int((status == "BLOCKED").sum()), "hard rule or NAV not computable", "#dc2626"),
           ("Open exceptions", int(open_m.sum()) if not exc.empty else 0, f"{hard_open} HARD · {len(exc)} raised", "#d97706"),
           ("Awaiting checker", pending, "maker proposals", "#7c3aed"),
           ("Outside band", int((dev.abs() > band).sum()), "actual vs expected NAV", "#0891b2"),
           ("Max admin break", "—" if brk.dropna().empty else f"{brk.max():.1f} bps", f"{int((brk > 10).sum())} above 10 bps", "#3b82f6")])
    c1, c2 = st.columns([6, 4])
    c1.markdown("**Control matrix** &nbsp; " + chip("OK", "#15803d") + " " + chip("Resolved / ready", "#1d4ed8") + " "
                + chip("Review", "#b45309") + " " + chip("Critical", "#b91c1c"), unsafe_allow_html=True)
    c1.plotly_chart(control_matrix(day, exc), use_container_width=True, config={"displayModeBar": False})
    c2.markdown("**Actual NAV vs frozen expected NAV (bps)** &nbsp; " + chip("inside band", "#16a34a") + " "
                + chip("outside band", "#dc2626"), unsafe_allow_html=True)
    c2.plotly_chart(deviation_chart(day), use_container_width=True, config={"displayModeBar": False})
    c3, c4, c5 = st.columns([5, 2.5, 2.5])
    c3.plotly_chart(trend_chart(nav), use_container_width=True, config={"displayModeBar": False})
    c4.plotly_chart(donut(cday), use_container_width=True, config={"displayModeBar": False})
    c5.plotly_chart(gauge(ok, n), use_container_width=True, config={"displayModeBar": False})
    with st.expander("Latest activity", expanded=False):
        ev = events()
        if ev.empty:
            st.caption("No activity yet.")
        else:
            st.dataframe(ev.head(15), hide_index=True, use_container_width=True)
# ---------------------------------------------------------------------------- pages
def page_control_tower():
    flash()
    left, right = st.columns([5, 1])
    auto = left.toggle("Live auto-refresh (30 s)", value=True, key="ct_auto")
    with right.popover("⚙ Operate", use_container_width=True):
        operate_panel()

    @st.fragment(run_every=30 if auto else None)
    def body():
        control_tower_body()

    body()

def page_cases():
    flash()
    cases = T("Cases")
    if cases.empty:
        st.info("No cases yet.")
        return
    exc_all = cases[cases["Case_Type"] == "EXCEPTION"].copy()
    fids = sorted(T("Funds")["Fund_ID"].tolist()) if not T("Funds").empty else []
    fb = st.columns([1.6, 3, 1.4, 1.2])
    scope = fb[0].segmented_control("Dates", ["Selected date", "All dates"], default="Selected date") or "Selected date"
    statuses = fb[1].multiselect("Status", list(CASE_STATUS_COLOR), default=["NEW", "AI_DONE", "ESCALATED", "PENDING_CHECK"])
    sev = fb[2].multiselect("Severity", ["HARD", "WARN"], default=["HARD", "WARN"])
    fund = fb[3].selectbox("Fund", ["All"] + fids)

    base = exc_all[exc_all["Date"] == SEL_DATE] if scope == "Selected date" else exc_all
    open_ = base[base["Status"] != "CLOSED"]
    ok, n = ai_coverage(base)
    created = pd.to_datetime(open_["Created_At"], errors="coerce") if not open_.empty else pd.Series(dtype="datetime64[ns]")
    imp_open = numcol(open_, "Est_NAV_Impact_bps")
    tiles([("Open cases", len(open_), f"{len(base)} in scope", "#d97706"),
           ("HARD open", int((open_["Max_Severity"] == "HARD").sum()) if not open_.empty else 0, "block release", "#dc2626"),
           ("Awaiting checker", int((base["Status"] == "PENDING_CHECK").sum()) if not base.empty else 0, "maker proposals", "#7c3aed"),
           ("No AI result", n - ok, "decide from evidence (G4)", "#64748b"),
           ("Oldest open", age_text(created.min()) if created.notna().any() else "—", "since case creation", "#0891b2"),
           ("Largest impact", "—" if imp_open.dropna().empty else f"{imp_open.max():.1f} bps", "open cases", "#3b82f6")])

    q = base[base["Status"].isin(statuses) & base["Max_Severity"].isin(sev)].copy()
    if fund != "All" and not q.empty:
        q = q[q["Fund_IDs"].astype(str).str.split(",").apply(lambda x: fund in x)]
    left, right = st.columns([4, 6])
    sel_id = None
    with left:
        st.markdown(f"**Queue** · {len(q)} case(s)")
        if q.empty:
            st.info("No cases match these filters.")
        else:
            q = with_ai_columns(q)
            q["●"] = q.apply(case_icon, axis=1)
            q["Impact"] = numcol(q, "Est_NAV_Impact_bps")
            q["AI conf"] = pd.to_numeric(q["AI_Conf"], errors="coerce")
            q["_hard_first"] = (q["Max_Severity"] != "HARD").astype(int)
            q = q.sort_values(["_hard_first", "Impact"], ascending=[True, False]).reset_index(drop=True)
            cols = ["●", "Case_ID"] + (["Date"] if scope == "All dates" else []) + \
                   ["Root_Rule_ID", "Fund_IDs", "Security_ID", "Impact", "AI_Class", "AI conf", "Status"]
            ev = st.dataframe(
                q[cols], hide_index=True, use_container_width=True, height=560, on_select="rerun",
                selection_mode="single-row", key="wb_tbl",
                column_config={
                    "●": st.column_config.TextColumn("", width="small"),
                    "Case_ID": st.column_config.TextColumn("Case"),
                    "Root_Rule_ID": st.column_config.TextColumn("Rule", width="small"),
                    "Fund_IDs": st.column_config.TextColumn("Funds"),
                    "Security_ID": st.column_config.TextColumn("Security"),
                    "Impact": st.column_config.NumberColumn("Impact (bps)", format="%.1f"),
                    "AI_Class": st.column_config.TextColumn("AI label"),
                    "AI conf": st.column_config.ProgressColumn("AI conf.", min_value=0.0, max_value=1.0, format="%.2f"),
                })
            rows = ev.selection.rows if ev is not None and hasattr(ev, "selection") else []
            sel_id = q.iloc[rows[0]]["Case_ID"] if rows else None
            st.caption("🔴 HARD open · 🟡 WARN open · 🟠 escalated · 🟣 awaiting checker · 🟢 closed")
    with right:
        if sel_id:
            render_case(sel_id)
        else:
            st.markdown('<div class="card">Select a case in the queue to open its evidence, AI recommendation and decision form.</div>',
                        unsafe_allow_html=True)

def page_checker():
    header("Checker Queue", "Second approval: a SENIOR other than the maker confirms or returns each proposal")
    flash()
    cases = T("Cases")
    q = cases[cases["Status"] == "PENDING_CHECK"].copy() if not cases.empty else cases
    if q.empty:
        st.success("No proposals awaiting a checker.")
        return
    rv = T("Reviews")
    rmap = {str(r["Review_ID"]): r for _, r in rv.iterrows()} if not rv.empty else {}
    q["Maker"] = q.get("Pending_Review_ID", "").map(lambda x: who(rmap.get(str(x), {}).get("Reviewer_Email")))
    q["Proposal"] = q.get("Pending_Review_ID", "").map(lambda x: rmap.get(str(x), {}).get("Decision", ""))
    q["Proposed_At"] = q.get("Pending_Review_ID", "").map(lambda x: rmap.get(str(x), {}).get("Timestamp", ""))
    q = q.sort_values("Proposed_At").reset_index(drop=True)
    cols = [x for x in ["Case_ID", "Case_Type", "Date", "Fund_IDs", "Security_ID", "Max_Severity", "Maker", "Proposal", "Proposed_At"] if x in q.columns]
    select_and_render(q, cols, "checker_tbl")


def page_release():
    header("Release Desk", f"Fund-day release decisions for {SEL_DATE or '—'} (maker proposes, a different SENIOR confirms)")
    flash()
    nav, cases, rel = T("NAV_Runs"), T("Cases"), T("Release_Log")
    day = nav[nav["Date"] == SEL_DATE].copy() if not nav.empty else nav
    rc = cases[(cases["Date"] == SEL_DATE) & (cases["Case_Type"] == "RELEASE")].copy() if not cases.empty else cases
    if not day.empty:
        latest = rc.sort_values("Created_At").groupby("Fund_IDs").tail(1).set_index("Fund_IDs") if not rc.empty else pd.DataFrame()
        view = pd.DataFrame({"Fund": day["Fund_ID"], "NAV": day["NAV_1e4"].apply(nav_text), "Status": day["Fund_Day_Status"],
                             "Release case": day["Fund_ID"].map(lambda f: latest.loc[f, "Case_ID"] if f in latest.index else "— (exceptions open)"),
                             "Case status": day["Fund_ID"].map(lambda f: latest.loc[f, "Status"] if f in latest.index else ""),
                             "Recommendation": day["Fund_ID"].map(lambda f: str(latest.loc[f, "Final_Recommendation"])[:90] if f in latest.index else "")})
        st.dataframe(view, hide_index=True, use_container_width=True)
    st.markdown("#### Act on a release case")
    if not rc.empty:
        rc = with_ai_columns(rc).sort_values("Case_ID").reset_index(drop=True)
        select_and_render(rc, [x for x in ["Case_ID", "Fund_IDs", "Status", "Final_Recommendation", "AI_Conf"] if x in rc.columns], "rel_tbl")
    else:
        st.caption("Release cases appear once every exception case on a fund-day is closed.")
    st.markdown("#### Release log")
    if rel.empty:
        st.caption("No releases recorded.")
    else:
        r = rel.copy()
        r["Checker"] = r["Approved_By"].apply(who)
        r["Maker"] = r.get("Maker_Email", pd.Series("", index=r.index)).apply(who)
        r["NAV released"] = r["NAV_1e4_Released"].apply(nav_text)
        st.dataframe(r[[x for x in ["Release_ID", "Date", "Fund_ID", "Decision", "NAV released", "Maker", "Checker", "Approved_At",
                                    "Four_Eyes", "Reason"] if x in r.columns]].iloc[::-1], hide_index=True, use_container_width=True)


def page_performance():
    flash()
    nav, funds, cal = T("NAV_Runs"), T("Funds"), T("Calendar")
    if nav.empty:
        st.info("No NAV history yet.")
        return
    df = nav.copy()
    df["NAV"] = numcol(df, "NAV_1e4") / 10000
    dates = sorted(df.loc[df["NAV"].notna(), "Date"].unique())
    if len(dates) < 3:
        st.info("At least three dates are needed.")
        return
    ids = sorted(df["Fund_ID"].unique())
    names = dict(zip(funds["Fund_ID"], funds["Fund_Name"])) if not funds.empty else {}
    betas = dict(zip(funds["Fund_ID"], funds["Beta"])) if (not funds.empty and "Beta" in funds) else {}

    c1, c2, c3 = st.columns([4, 4, 2])
    d0, d1 = c1.select_slider("Period", options=dates, value=(dates[0], dates[-1]))
    sel = c2.multiselect("Funds", ids, default=ids) or ids
    one = c3.selectbox("Detail fund", sel)
    sub = df[(df["Date"] >= d0) & (df["Date"] <= d1) & df["Fund_ID"].isin(sel)]
    piv = sub.pivot_table(index="Date", columns="Fund_ID", values="NAV", aggfunc="last").sort_index()

    rows = []
    for f in piv.columns:
        s = piv[f].dropna()
        if len(s) < 3:
            continue
        r = s.pct_change().dropna()
        rows.append({"Fund": f, "Name": names.get(f, ""), "Return %": round((s.iloc[-1] / s.iloc[0] - 1) * 100, 2),
                     "Volatility %": round(r.std(ddof=1) * np.sqrt(252) * 100, 2),
                     "Max drawdown %": round((s / s.cummax() - 1).min() * 100, 2),
                     "Beta": num(betas.get(f)), "Days": len(s)})
    stats = pd.DataFrame(rows)
    n01 = int(sub["N01"].apply(truthy).sum()) if "N01" in sub else 0
    brk = numcol(sub, "Admin_Break_bps")
    if not stats.empty:
        best, worst = stats.loc[stats["Return %"].idxmax()], stats.loc[stats["Return %"].idxmin()]
        dd = stats.loc[stats["Max drawdown %"].idxmin()]
        tiles([("Best fund", f"{best['Return %']:+.2f}%", f"{best['Fund']} · {d0} → {d1}", "#16a34a"),
               ("Worst fund", f"{worst['Return %']:+.2f}%", worst["Fund"], "#dc2626"),
               ("Avg volatility", f"{stats['Volatility %'].mean():.1f}%", "annualised", "#3b82f6"),
               ("Worst drawdown", f"{dd['Max drawdown %']:.2f}%", dd["Fund"], "#d97706"),
               ("Band breaches", n01, "fund-days outside expected band (N01)", "#0891b2"),
               ("Avg admin break", "—" if brk.dropna().empty else f"{brk.mean():.1f} bps",
                "—" if brk.dropna().empty else f"max {brk.max():.1f} bps", "#7c3aed")])

    g1, g2 = st.columns([6, 4])
    reb = piv.apply(lambda s: s / s.dropna().iloc[0] * 100 if s.notna().any() else s)
    fr = go.Figure([go.Scatter(x=reb.index, y=reb[f], name=f, line=dict(color=SERIES_COLORS[i % 6], width=2))
                    for i, f in enumerate(reb.columns)])
    nf = cal[(cal["Date"] >= d0) & (cal["Date"] <= d1)].sort_values("Date") if not cal.empty else cal
    if not nf.empty:
        nc = numcol(nf, "Nifty_Close")
        if nc.notna().any():
            fr.add_trace(go.Scatter(x=nf["Date"], y=nc / nc.dropna().iloc[0] * 100, name="Nifty 50",
                                    line=dict(color="#e5e7eb", dash="dash", width=1.5)))
        test = cal.loc[cal.get("Day_Role", pd.Series("", index=cal.index)) == "TEST", "Date"]
        if not test.empty and d0 <= test.min() <= d1:
            fr.add_vline(x=test.min(), line_dash="dot", line_color="#94a3b8")
            fr.add_annotation(x=test.min(), y=1, yref="paper", text="test window", showarrow=False, yanchor="bottom", font=dict(size=10))
    g1.plotly_chart(style(fr, 330, "NAV rebased to 100 vs Nifty 50"), use_container_width=True, config={"displayModeBar": False})
    ddf = piv.apply(lambda s: s / s.cummax() - 1) * 100
    fd = go.Figure([go.Scatter(x=ddf.index, y=ddf[f], name=f, fill="tozeroy", line=dict(color=SERIES_COLORS[i % 6], width=1))
                    for i, f in enumerate(ddf.columns)])
    g2.plotly_chart(style(fd, 330, "Drawdown %"), use_container_width=True, config={"displayModeBar": False})

    g3, g4 = st.columns([4, 6])
    with g3:
        st.markdown("**Risk and return by fund**")
        if stats.empty:
            st.caption("Not enough data in this period.")
        else:
            st.dataframe(stats, hide_index=True, use_container_width=True, height=300,
                         column_config={"Return %": st.column_config.NumberColumn(format="%+.2f"),
                                        "Volatility %": st.column_config.NumberColumn(format="%.2f"),
                                        "Max drawdown %": st.column_config.NumberColumn(format="%.2f"),
                                        "Beta": st.column_config.NumberColumn(format="%.2f")})
    o = sub[sub["Fund_ID"] == one].sort_values("Date")
    exp, band = numcol(o, "Expected_NAV_1e4") / 10000, numcol(o, "Band_bps")
    breach = o["N01"].apply(truthy) if "N01" in o else pd.Series(False, index=o.index)
    fbd = go.Figure()
    fbd.add_trace(go.Scatter(x=o["Date"], y=exp * (1 + band / 10000), line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fbd.add_trace(go.Scatter(x=o["Date"], y=exp * (1 - band / 10000), fill="tonexty", fillcolor="rgba(59,130,246,.15)",
                             line=dict(width=0), name="Expected band"))
    fbd.add_trace(go.Scatter(x=o["Date"], y=exp, name="Expected (frozen)", line=dict(color="#3b82f6", dash="dot")))
    fbd.add_trace(go.Scatter(x=o["Date"], y=o["NAV"], name="Actual", mode="lines+markers", line=dict(color="#e5e7eb"),
                             marker=dict(color=np.where(breach, "#dc2626", "#e5e7eb"), size=np.where(breach, 10, 4))))
    g4.plotly_chart(style(fbd, 330, f"{one}: actual NAV vs expected band (red = breach)"),
                    use_container_width=True, config={"displayModeBar": False})

    g5, g6 = st.columns([6, 4])
    prev = numcol(o, "Prev_NAV_1e4")
    fa = go.Figure([go.Bar(x=o["Date"], y=numcol(o, c) / prev * 10000, name=nm, marker_color=col)
                    for c, nm, col in (("Attr_Market_1e4", "Market", "#3b82f6"), ("Attr_Income_1e4", "Income", "#16a34a"),
                                       ("Attr_Fees_1e4", "Fees", "#d97706"), ("Attr_Residual_1e4", "Unexplained", "#dc2626"))])
    fa.update_layout(barmode="relative")
    g5.plotly_chart(style(fa, 300, f"{one}: daily NAV move by driver (bps)"), use_container_width=True, config={"displayModeBar": False})
    fk = go.Figure(go.Scatter(x=o["Date"], y=numcol(o, "Admin_Break_bps"), mode="lines+markers", line=dict(color="#a78bfa")))
    fk.add_hline(y=10, line_dash="dot", line_color="#d97706")
    fk.add_hline(y=50, line_dash="dot", line_color="#dc2626")
    g6.plotly_chart(style(fk, 300, f"{one}: break vs administrator (bps)"), use_container_width=True, config={"displayModeBar": False})


def page_realfund():
    header("Real-Fund Check · UTI Nifty 50 Index Fund (Direct, Growth)",
           "AMFI scheme code 120716 · shadow NAV from public prices compared with the official NAV")
    rf = T("RealFund_NAV")
    if rf.empty:
        st.info("No rows yet. Run the GitHub workflow in mode 'realfund' for this environment (see the setup guide).")
        return
    rf = rf.sort_values("Date")
    for c in ("Official_NAV", "Shadow_NAV", "Tracking_Diff_bps", "Break_Threshold_bps", "Portfolio_Return_bps", "TER_bps", "Coverage_Pct"):
        rf[c] = numcol(rf, c)
    fin = rf[rf["Status"].isin(["MATCH", "BREAK"])]
    last = rf.iloc[-1]
    thr = float(last["Break_Threshold_bps"]) if pd.notna(last["Break_Threshold_bps"]) else 25.0
    kpis([("Latest date", last["Date"], str(last["Status"])),
          ("Official NAV", "—" if pd.isna(last["Official_NAV"]) else f"₹{last['Official_NAV']:,.4f}", str(last.get("Official_Source", ""))[:40]),
          ("Shadow NAV", f"₹{last['Shadow_NAV']:,.4f}", str(last["Method"])[:40]),
          ("Mean |difference|", f"{fin['Tracking_Diff_bps'].abs().mean():.1f} bps" if len(fin) else "—", f"{len(fin)} compared days"),
          ("Max |difference|", f"{fin['Tracking_Diff_bps'].abs().max():.1f} bps" if len(fin) else "—", f"threshold ±{thr:.0f} bps"),
          ("Breaks", int((fin["Status"] == "BREAK").sum()), "days above threshold")])
    f1 = go.Figure()
    f1.add_trace(go.Scatter(x=rf["Date"], y=rf["Official_NAV"], name="Official NAV (AMFI)", line=dict(color="#111827", width=2)))
    f1.add_trace(go.Scatter(x=rf["Date"], y=rf["Shadow_NAV"], name="Shadow NAV (our calculation)", line=dict(color="#2563eb", dash="dot")))
    f1.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=-0.2), yaxis_title="₹ per unit")
    st.plotly_chart(f1, use_container_width=True)
    f2 = go.Figure(go.Bar(x=fin["Date"], y=fin["Tracking_Diff_bps"],
                          marker_color=np.where(fin["Status"] == "BREAK", "#dc2626", "#16a34a")))
    f2.add_hrect(y0=-thr, y1=thr, fillcolor="rgba(22,163,74,.08)", line_width=0)
    f2.update_layout(height=300, title="Official minus shadow (bps); green band = tolerance", margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(f2, use_container_width=True)
    st.dataframe(rf.iloc[::-1], hide_index=True, use_container_width=True)
    with st.expander("Method and limitations"):
        st.markdown(
            "- **Shadow NAV(t)** = official NAV(t−1) × (1 + portfolio return(t) − TER × days / 365). It is re-anchored to the official NAV daily, so errors do not accumulate.\n"
            "- **HOLDINGS method**: weights from the AMC's monthly portfolio (file `f6_portfolio.csv`), drift-adjusted with Yahoo prices, plus dividends going ex. "
            "**INDEX method** (no portfolio file): Nifty 50 price return, which excludes dividends, so ex-dividend days show larger differences.\n"
            "- This is a reasonableness check, not a reconstruction: units, cash and daily flows are not public, and Yahoo closes can differ from NSE's official close.\n"
            "- The TER is a configurable input; confirm it from the AMC's latest TER disclosure.")


def page_quality():
    header("Data Quality", "Where the exceptions come from and how prices were selected")
    fl, pr, ai, po = T("Flags"), T("Prices"), T("AI_Decisions"), T("Price_Overrides")
    if not fl.empty:
        pv = fl.pivot_table(index="Rule_ID", columns="Date", values="Flag_ID", aggfunc="count", fill_value=0)
        fh = go.Figure(go.Heatmap(z=pv.values, x=list(pv.columns), y=list(pv.index), colorscale="Reds", hoverongaps=False))
        fh.update_layout(height=420, title="Rule hits by date", margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fh, use_container_width=True)
    c1, c2 = st.columns(2)
    if not pr.empty and "Selected_Source" in pr:
        mix = pr[pr["Selected_Source"].astype(str) != "A"].groupby(["Date", "Selected_Source"]).size().reset_index(name="n")
        fm = go.Figure([go.Bar(x=g["Date"], y=g["n"], name=k) for k, g in mix.groupby("Selected_Source")])
        fm.update_layout(barmode="stack", height=300, title="Securities not priced from Source A", margin=dict(l=10, r=10, t=50, b=10))
        c1.plotly_chart(fm, use_container_width=True)
    if not ai.empty:
        case_ai = ai[ai["Call_Type"] == "CASE"]
        col = "HTTP_Status_History" if "HTTP_Status_History" in case_ai else "HTTP_Status"
        codes = case_ai[col].astype(str).str.replace("|", ",", regex=False).str.split(",").explode().str.strip()
        codes = codes[codes != ""].value_counts()
        fc = go.Figure(go.Bar(x=codes.index.astype(str), y=codes.values, marker_color="#7c3aed"))
        fc.update_layout(height=300, title="AI call outcomes (HTTP status per attempt)", margin=dict(l=10, r=10, t=50, b=10))
        c2.plotly_chart(fc, use_container_width=True)
    st.markdown("#### Reviewer-confirmed prices (G2)")
    if po.empty:
        st.caption("None.")
    else:
        p = po.copy()
        p["Confirmed_By"] = p["Confirmed_By"].apply(who)
        st.dataframe(p.iloc[::-1], hide_index=True, use_container_width=True)


def page_audit():
    header("Audit Trail", "Who did what, when and why: runs, AI calls, maker-checker decisions, confirmed prices and releases")
    ev = events()
    if ev.empty:
        st.info("No events yet.")
        return
    c1, c2 = st.columns([2, 3])
    types = c1.multiselect("Event types", sorted(ev["Type"].unique()), default=sorted(ev["Type"].unique()))
    q = c2.text_input("Search (case, fund, security, person)")
    v = ev[ev["Type"].isin(types)]
    if q:
        v = v[v.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)]
    st.dataframe(v, hide_index=True, use_container_width=True, height=560)
    st.download_button("Download as CSV", v.to_csv(index=False).encode(), file_name=f"navcr_audit_{env_key}.csv", mime="text/csv")


# ---------------------------------------------------------------------------- navigation
pg = st.navigation({
    "Operate": [st.Page(page_control_tower, title="Control Tower", icon="🛰️", default=True),
                st.Page(page_cases, title="Case Workbench", icon="🧾"),
                st.Page(page_checker, title="Checker Queue", icon="✅"),
                st.Page(page_release, title="Release Desk", icon="🚦")],
    "Analyse": [st.Page(page_performance, title="Fund Performance", icon="📈"),
                st.Page(page_realfund, title="Real-Fund Check", icon="🏛️"),
                st.Page(page_quality, title="Data Quality", icon="🧪")],
    "Govern": [st.Page(page_audit, title="Audit Trail", icon="🗂️")],
})
pg.run()
