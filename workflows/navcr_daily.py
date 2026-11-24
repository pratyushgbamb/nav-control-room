#!/usr/bin/env python3
"""
NAV Control Room — daily data job (v3)
Zensar BFSI Program · Project 1

Modes
  bootstrap  Build a 30-trading-day warm-up for the LIVE sheet from Yahoo Finance and send the reference and
             warm-up files to its web app (then run S1, S2, S3 in the Live sheet).
  run        Build the next NSE trading day after the Live sheet's last processed date: Source A = Yahoo close,
             Sources B/C synthetic (deterministic per date/security), real dividends and splits, fee accruals,
             administrator NAV; optional demo errors (private answer key in live_answer_key.csv, restated next day).
  realfund   Shadow NAV of UTI Nifty 50 Index Fund - Direct Plan - Growth (AMFI 120716) vs the official NAV.
Environment: NAVCR_WEBAPP_URL, NAVCR_SECRET; optional F6_TER_BPS, F6_BREAK_BPS, F6_PORTFOLIO.
Money is integer paise; NAV per unit is an integer in Rs 0.0001; units are milli-units (same as the Sheet).
"""
import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

import numpy as np
import pandas as pd
import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SEED = 42
N_WARMUP = 30
NOISE_SIGMA_BPS, NOISE_CAP_BPS = 4.0, 10.0
DRIFT_PROB, DRIFT_MIN_BPS, DRIFT_MAX_BPS = 0.02, 10, 20
PAY_LAG_DAYS = 21                      # [Estimate] dividend pay date = ex-date + 21 calendar days (next weekday)
LEDGER = 'live_answer_key.csv'         # private answer key for demo errors; never sent to the system

UNIVERSE = [
    ('RELIANCE', 'RELIANCE.NS', 'Reliance Industries', 'Energy'), ('HDFCBANK', 'HDFCBANK.NS', 'HDFC Bank', 'Financials'),
    ('ICICIBANK', 'ICICIBANK.NS', 'ICICI Bank', 'Financials'), ('INFY', 'INFY.NS', 'Infosys', 'IT'),
    ('TCS', 'TCS.NS', 'Tata Consultancy Services', 'IT'), ('BHARTIARTL', 'BHARTIARTL.NS', 'Bharti Airtel', 'Telecom'),
    ('ITC', 'ITC.NS', 'ITC', 'FMCG'), ('LT', 'LT.NS', 'Larsen & Toubro', 'Industrials'),
    ('SBIN', 'SBIN.NS', 'State Bank of India', 'Financials'), ('AXISBANK', 'AXISBANK.NS', 'Axis Bank', 'Financials'),
    ('KOTAKBANK', 'KOTAKBANK.NS', 'Kotak Mahindra Bank', 'Financials'), ('HINDUNILVR', 'HINDUNILVR.NS', 'Hindustan Unilever', 'FMCG'),
    ('BAJFINANCE', 'BAJFINANCE.NS', 'Bajaj Finance', 'Financials'), ('MARUTI', 'MARUTI.NS', 'Maruti Suzuki', 'Auto'),
    ('ASIANPAINT', 'ASIANPAINT.NS', 'Asian Paints', 'Materials'), ('SUNPHARMA', 'SUNPHARMA.NS', 'Sun Pharmaceutical', 'Healthcare'),
    ('TITAN', 'TITAN.NS', 'Titan Company', 'Consumer'), ('ULTRACEMCO', 'ULTRACEMCO.NS', 'UltraTech Cement', 'Materials'),
    ('WIPRO', 'WIPRO.NS', 'Wipro', 'IT'), ('HCLTECH', 'HCLTECH.NS', 'HCL Technologies', 'IT'),
    ('NTPC', 'NTPC.NS', 'NTPC', 'Utilities'), ('POWERGRID', 'POWERGRID.NS', 'Power Grid Corporation', 'Utilities'),
    ('ONGC', 'ONGC.NS', 'Oil & Natural Gas Corporation', 'Energy'), ('TATASTEEL', 'TATASTEEL.NS', 'Tata Steel', 'Materials'),
    ('JSWSTEEL', 'JSWSTEEL.NS', 'JSW Steel', 'Materials'), ('M&M', 'M&M.NS', 'Mahindra & Mahindra', 'Auto'),
    ('NESTLEIND', 'NESTLEIND.NS', 'Nestle India', 'FMCG'), ('TECHM', 'TECHM.NS', 'Tech Mahindra', 'IT'),
    ('CIPLA', 'CIPLA.NS', 'Cipla', 'Healthcare'), ('DRREDDY', 'DRREDDY.NS', "Dr. Reddy's Laboratories", 'Healthcare'),
    ('ADANIPORTS', 'ADANIPORTS.NS', 'Adani Ports & SEZ', 'Industrials'), ('COALINDIA', 'COALINDIA.NS', 'Coal India', 'Energy'),
    ('BRITANNIA', 'BRITANNIA.NS', 'Britannia Industries', 'FMCG'), ('HEROMOTOCO', 'HEROMOTOCO.NS', 'Hero MotoCorp', 'Auto'),
    ('EICHERMOT', 'EICHERMOT.NS', 'Eicher Motors', 'Auto'), ('GRASIM', 'GRASIM.NS', 'Grasim Industries', 'Materials'),
    ('BAJAJ-AUTO', 'BAJAJ-AUTO.NS', 'Bajaj Auto', 'Auto'), ('APOLLOHOSP', 'APOLLOHOSP.NS', 'Apollo Hospitals', 'Healthcare'),
    ('TATACONSUM', 'TATACONSUM.NS', 'Tata Consumer Products', 'FMCG'), ('INDUSINDBK', 'INDUSINDBK.NS', 'IndusInd Bank', 'Financials'),
]
FUNDS = [  # Fund_ID, name, category, mgmt fee bps p.a., size (Rs crore), starting NAV (Rs), first universe index, holdings
    ('F1', 'Pumba Large Cap Bluechip Fund', 'Large Cap', 120, 520, 45.1234, 0, 10),
    ('F2', 'Pumba Flexi Cap Opportunities Fund', 'Flexi Cap', 150, 410, 38.6500, 8, 10),
    ('F3', 'Pumba Equity Income & Dividend Yield Fund', 'Dividend Yield', 135, 360, 27.9800, 16, 10),
    ('F4', 'Pumba Focused Equity Fund', 'Focused', 100, 300, 61.4400, 24, 9),
    ('F5', 'Pumba Mid & Next 50 Growth Fund', 'Large & Mid Cap', 175, 280, 52.7700, 31, 10),
]
CASH_PCT = 0.02
OTHER_LIAB_PAISE = {'F1': 1_500_000_00, 'F2': 1_200_000_00, 'F3': 900_000_00, 'F4': 800_000_00, 'F5': 700_000_00}

F6 = {'code': '120716', 'name': 'UTI Nifty 50 Index Fund - Direct Plan - Growth', 'isin': 'INF789F01XA0'}


def envf(name, default):
    v = os.environ.get(name, '')
    try:
        return float(v) if str(v).strip() else default
    except ValueError:
        return default


F6_TER_BPS = envf('F6_TER_BPS', 20.0)       # [Estimate] set the GitHub variable from UTI's latest TER disclosure
F6_BREAK_BPS = envf('F6_BREAK_BPS', 25.0)   # [Estimate] daily tracking-difference threshold for a "break"
F6_PORTFOLIO = os.environ.get('F6_PORTFOLIO', 'f6_portfolio.csv')

PRICE_COLS = ['Price_Key', 'Date', 'Security_ID', 'Price_A_Paise', 'Price_B_Paise', 'Price_C_Paise', 'Volume_A', 'Feed_File', 'Loaded_At']
POS_COLS = ['Position_Key', 'Date', 'Fund_ID', 'Security_ID', 'Quantity']
BOOK_COLS = ['Booking_ID', 'Date', 'Fund_ID', 'Security_ID', 'CA_ID', 'Booking_Type', 'Amount_Paise', 'Qty_Before', 'Qty_After']
FEE_COLS = ['Accrual_Key', 'Date', 'Fund_ID', 'Accrual_Days', 'Basis_Net_Assets_Paise', 'Mgmt_Fee_Paise', 'Perf_Fee_Paise']
FD_COLS = ['Key', 'Date', 'Fund_ID', 'Cash_Paise', 'Fee_Paid_Paise', 'Other_Liabilities_Paise', 'Units_Milli']
ADM_COLS = ['Key', 'Date', 'Fund_ID', 'Admin_NAV_1e4', 'Admin_Net_Assets_Paise', 'Received_At']
FEED_COLS = {'prices': PRICE_COLS, 'positions': POS_COLS, 'ca_bookings': BOOK_COLS, 'fee_accruals': FEE_COLS,
             'fund_daily': FD_COLS, 'admin_nav': ADM_COLS}
LEDGER_COLS = ['Key_ID', 'Date', 'Type', 'Fund_ID', 'Security_ID', 'Source', 'True_Value', 'Planted_Value',
               'NA_Effect_Paise', 'Restated', 'Clean_Row', 'Run_At']


# ---------------------------------------------------------------------------- helpers
def to_paise(rupees):
    return int(Decimal(str(float(rupees))).scaleb(2).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def rhu_div(a, b):
    """Round-half-up (away from zero) integer division, b > 0."""
    q = (2 * abs(a) + b) // (2 * b)
    return q if a >= 0 else -q


def rhu_mul(x, factor):
    return int((Decimal(int(x)) * Decimal(str(factor))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def seed_for(*parts):
    h = hashlib.sha256('|'.join(str(p) for p in (SEED,) + parts).encode()).digest()
    return int.from_bytes(h[:8], 'big')


def noisy(x, d, s, src):
    """Deterministic vendor noise per (date, security, source): re-runs give identical feeds."""
    rng = np.random.default_rng(seed_for(d, s, src))
    e = float(np.clip(rng.normal(0, NOISE_SIGMA_BPS), -NOISE_CAP_BPS, NOISE_CAP_BPS))
    if rng.random() < DRIFT_PROB:
        e += float(rng.choice([-1, 1]) * rng.uniform(DRIFT_MIN_BPS, DRIFT_MAX_BPS))
    return rhu_mul(x, 1 + e / 10000)


def csv_text(rows, cols):
    return pd.DataFrame(rows, columns=cols).to_csv(index=False, lineterminator='\n')


def api(action, data, timeout=300):
    url, secret = os.environ.get('NAVCR_WEBAPP_URL', ''), os.environ.get('NAVCR_SECRET', '')
    if not url or not secret:
        sys.exit('NAVCR_WEBAPP_URL and NAVCR_SECRET must be set.')
    payload = json.dumps(data, separators=(',', ':'), default=str)
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    sig = hmac.new(secret.encode(), f'{action}|{ts}|{nonce}|{payload}'.encode(), hashlib.sha256).hexdigest()
    body = json.dumps({'action': action, 'ts': ts, 'nonce': nonce, 'payload': payload, 'sig': sig})
    r = requests.post(url, data=body, headers={'Content-Type': 'application/json'}, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    if not out.get('ok'):
        raise RuntimeError(f'{action}: {out.get("error")}')
    return out


def yf_download(tickers, start, end):
    import yfinance as yf
    for attempt in range(3):
        raw = yf.download(tickers, start=start, end=end, auto_adjust=False, actions=True,
                          progress=False, group_by='column', threads=True)
        if raw is not None and len(raw):
            return raw
        time.sleep(15)
    sys.exit('Yahoo Finance returned no data.')


def field(raw, name):
    return raw[name] if name in raw.columns.get_level_values(0) else pd.DataFrame(index=raw.index)


def val(df, d, t):
    try:
        v = df.at[d, t]
        return 0.0 if pd.isna(v) else float(v)
    except (KeyError, ValueError):
        return 0.0


def next_weekday(d):
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


# ---------------------------------------------------------------------------- bootstrap (LIVE warm-up)
def bootstrap():
    state = api('state', {})['state']
    if state['mode'] != 'LIVE':
        sys.exit('bootstrap targets the LIVE sheet only (NAVCR_MODE=LIVE).')
    now = dt.datetime.now(IST)
    tick = [u[1] for u in UNIVERSE]
    raw = yf_download(tick + ['^NSEI'], (now - dt.timedelta(days=80)).strftime('%Y-%m-%d'), (now + dt.timedelta(days=1)).strftime('%Y-%m-%d'))
    close, vol = field(raw, 'Close'), field(raw, 'Volume')
    nifty = close['^NSEI'].dropna()
    if (now.hour, now.minute) < (16, 0):
        nifty = nifty[[x.date() < now.date() for x in nifty.index]]
    closes = close.reindex(columns=tick).loc[nifty.index]
    good = [t for t in tick if closes[t].notna().mean() >= 0.98]
    closes = closes[good].dropna(how='any')
    nifty = nifty.loc[closes.index]
    if len(closes) < N_WARMUP:
        sys.exit(f'Only {len(closes)} complete trading days available.')
    closes, nifty = closes.iloc[-N_WARMUP:], nifty.iloc[-N_WARMUP:]
    vols = vol.reindex(columns=good).loc[closes.index].fillna(0)
    dates = [x.strftime('%Y-%m-%d') for x in closes.index]
    nd = len(dates)
    gap = [1] + [(closes.index[i] - closes.index[i - 1]).days for i in range(1, nd)]
    t2id = {u[1]: u[0] for u in UNIVERSE}
    info = {u[0]: u for u in UNIVERSE}
    secs = [t2id[t] for t in good]
    A = {t2id[t]: [to_paise(v) for v in closes[t].tolist()] for t in good}
    V = {t2id[t]: [int(v) for v in vols[t].tolist()] for t in good}

    rng = np.random.default_rng(SEED)
    hold = {f[0]: [secs[(f[6] + k) % len(secs)] for k in range(f[7])] for f in FUNDS}
    qty, cash0, units = {}, {}, {}
    for f in FUNDS:
        fid, size = f[0], f[4] * 10 ** 7 * 100
        w = rng.dirichlet(np.ones(len(hold[fid])) * 6)
        qty[fid] = {s: int(w[k] * size * (1 - CASH_PCT) // A[s][0]) for k, s in enumerate(hold[fid])}
        cash0[fid] = int(size * CASH_PCT)
        na0 = sum(qty[fid][s] * A[s][0] for s in hold[fid]) + cash0[fid] - OTHER_LIAB_PAISE[fid]
        units[fid] = int(Decimal(na0) / 100 / Decimal(str(f[5])) * 1000)
    B = {s: [noisy(A[s][i], dates[i], s, 'B') for i in range(nd)] for s in secs}
    C = {s: [noisy(A[s][i], dates[i], s, 'C') for i in range(nd)] for s in secs}
    bps = {f[0]: f[3] for f in FUNDS}

    def book(prices):
        out = {}
        for fid in hold:
            payable, prev, rows = 0, None, []
            for i in range(nd):
                fee = rhu_div(prev * bps[fid] * gap[i], 10000 * 365) if prev is not None else 0
                payable += fee
                na = sum(qty[fid][s] * prices[s][i] for s in hold[fid]) + cash0[fid] - payable - OTHER_LIAB_PAISE[fid]
                rows.append({'fee': fee, 'basis': prev or 0, 'na': na, 'nav': rhu_div(na * 100000, units[fid])})
                prev = na
            out[fid] = rows
        return out

    clean, admin = book(A), book(B)
    nret = [None] + [(float(nifty.iloc[i]) / float(nifty.iloc[i - 1]) - 1) * 10000 for i in range(1, nd)]
    beta, sig = {}, {}
    for fid in hold:
        y = np.array([(clean[fid][i]['nav'] / clean[fid][i - 1]['nav'] - 1) * 10000 for i in range(1, nd)])
        x = np.array(nret[1:])
        b, a = np.polyfit(x, y, 1)
        resid = y - (a + b * x)
        beta[fid], sig[fid] = round(float(b), 4), round(float(np.std(resid, ddof=2)), 2)

    files = {
        'reference/calendar.csv': csv_text(
            [[dates[i], 'TRUE', 'WARMUP', dates[i - 1] if i else '', gap[i], round(float(nifty.iloc[i]), 2),
              '' if i == 0 else round(nret[i], 2)] for i in range(nd)],
            ['Date', 'Is_Business_Day', 'Day_Role', 'Prev_Business_Day', 'Accrual_Days', 'Nifty_Close', 'Nifty_Return_bps']),
        'reference/funds.csv': csv_text(
            [[f[0], f[1], f[2], f[3], 0, 'NONE', units[f[0]], beta[f[0]], sig[f[0]]] for f in FUNDS],
            ['Fund_ID', 'Fund_Name', 'Category', 'Mgmt_Fee_bps_PA', 'Perf_Fee_bps', 'Perf_Hurdle', 'Units_Milli_Start', 'Beta', 'Resid_Sigma_bps']),
        'reference/securities.csv': csv_text([[s, '', info[s][2], info[s][3], info[s][1]] for s in secs],
                                             ['Security_ID', 'ISIN', 'Company_Name', 'Sector', 'Yahoo_Ticker']),
        'reference/corp_actions.csv': csv_text([], ['CA_ID', 'Security_ID', 'CA_Type', 'Ex_Date', 'Pay_Date', 'DPS_Paise',
                                                    'Ratio_New', 'Ratio_Old', 'Origin']),
    }
    warm = {k: [] for k in FEED_COLS}
    for i, d in enumerate(dates):
        for s in secs:
            warm['prices'].append([f'{d}_{s}', d, s, A[s][i], B[s][i], C[s][i], V[s][i], f'LIVE_WARMUP_{d.replace("-", "")}', f'{d} 16:30:00'])
        for fid in hold:
            for s in hold[fid]:
                warm['positions'].append([f'{d}_{fid}_{s}', d, fid, s, qty[fid][s]])
            warm['fee_accruals'].append([f'{d}_{fid}', d, fid, gap[i], clean[fid][i]['basis'], clean[fid][i]['fee'], 0])
            warm['fund_daily'].append([f'{d}_{fid}', d, fid, cash0[fid], 0, OTHER_LIAB_PAISE[fid], units[fid]])
            warm['admin_nav'].append([f'{d}_{fid}', d, fid, admin[fid][i]['nav'], admin[fid][i]['na'], f'{d} 18:30:00'])
    for k, rows in warm.items():
        files[f'warmup/warmup_{k}.csv'] = csv_text(rows, FEED_COLS[k])
    out = api('bootstrap', {'files': files})
    print(f'Bootstrap sent: {out.get("files")} files, warm-up {dates[0]} to {dates[-1]}, {len(secs)} securities.')
    print('Beta:', beta, '| Residual sigma (bps):', sig)
    print('Next, in the Live sheet: S1, S2, S3, then L1.')


# ---------------------------------------------------------------------------- daily live run
def load_ledger():
    if os.path.exists(LEDGER):
        df = pd.read_csv(LEDGER, dtype=str).fillna('')
        df['Restated'] = df['Restated'].replace('', '0').astype(int)
        return df
    return pd.DataFrame(columns=LEDGER_COLS)


def run(inject=0):
    state = api('state', {})['state']
    if state['mode'] != 'LIVE':
        sys.exit('run targets the LIVE sheet only.')
    last = state['last_processed_date']
    if not last:
        sys.exit('The Live sheet has no processed date: run bootstrap, then S1-S3.')
    secs = [s['Security_ID'] for s in state['securities']]
    tick = {s['Security_ID']: s['Yahoo_Ticker'] for s in state['securities']}
    now = dt.datetime.now(IST)
    raw = yf_download(list(tick.values()) + ['^NSEI'], (pd.Timestamp(last) - pd.Timedelta(days=10)).strftime('%Y-%m-%d'),
                      (now + dt.timedelta(days=1)).strftime('%Y-%m-%d'))
    close, vol, divs, spl = (field(raw, k) for k in ('Close', 'Volume', 'Dividends', 'Stock Splits'))
    nifty = close['^NSEI'].dropna()
    cand = [x for x in nifty.index if x.strftime('%Y-%m-%d') > last]
    if cand and cand[0].date() == now.date() and (now.hour, now.minute) < (16, 0):
        cand = []
    if not cand:
        print(f'No new NSE trading day after {last} (holiday, weekend or data not yet final).')
        return
    D = cand[0]
    d = D.strftime('%Y-%m-%d')
    miss = [s for s in secs if pd.isna(close.at[D, tick[s]])]
    if miss:
        sys.exit(f'Yahoo has no close for {miss} on {d}; the 20:15 run will retry.')
    gap = (D - pd.Timestamp(last)).days

    funds = [f['Fund_ID'] for f in state['funds']]
    bps = {f['Fund_ID']: int(f['Mgmt_Fee_bps_PA']) for f in state['funds']}
    pos_prev = {(p['Fund_ID'], p['Security_ID']): int(p['Quantity']) for p in state['positions']}
    fd_prev = {x['Fund_ID']: x for x in state['fund_daily']}
    nav_prev = {x['Fund_ID']: x for x in state['nav']}
    price_prev = {p['Security_ID']: p for p in state['prices']}

    # restatement of yesterday's demo errors (vendor corrections)
    ledger = load_ledger()
    pend = ledger[(ledger['Date'] == last) & (ledger['Restated'] == 0)]
    na_fix, clean_prev, feeds = defaultdict(int), {}, {k: [] for k in FEED_COLS}
    for _, r in pend.iterrows():
        cr = json.loads(r['Clean_Row'])
        if r['Type'] == 'FEE_TWICE':
            na_fix[r['Fund_ID']] += int(r['NA_Effect_Paise'])
            feeds['fee_accruals'].append([f"{last}_{r['Fund_ID']}", last, r['Fund_ID'], cr['Accrual_Days'], cr['Basis'], cr['Mgmt'], 0])
        else:
            clean_prev[r['Security_ID']] = cr
            feeds['prices'].append([f"{last}_{r['Security_ID']}", last, r['Security_ID'], cr['A'], cr['B'], cr['C'], cr['V'],
                                    'VENDOR_RESTATEMENT', f'{d} 16:30:00'])

    def prev_px(s, src):
        if s in clean_prev:
            return int(clean_prev[s][src])
        v = price_prev.get(s, {}).get(f'Price_{src}_Paise')
        return int(v) if v not in (None, '') else None

    A = {s: to_paise(close.at[D, tick[s]]) for s in secs}
    V = {s: int(val(vol, D, tick[s])) for s in secs}
    B = {s: noisy(A[s], d, s, 'B') for s in secs}
    C = {s: noisy(A[s], d, s, 'C') for s in secs}
    holders = defaultdict(list)
    for (f, s) in pos_prev:
        holders[s].append(f)

    # real corporate actions going ex today
    qty, ca_rows, new_recv = dict(pos_prev), [], []
    pay = next_weekday(D.date() + dt.timedelta(days=PAY_LAG_DAYS)).strftime('%Y-%m-%d')
    for s in secs:
        if not holders[s]:
            continue
        sp, dv = val(spl, D, tick[s]), val(divs, D, tick[s])
        if sp and abs(sp - 1.0) > 1e-9:
            fr = Fraction(sp).limit_denominator(10)
            cid = f'CA-L-{d.replace("-", "")}-{s}-S'
            ca_rows.append({'CA_ID': cid, 'Security_ID': s, 'CA_Type': 'SPLIT', 'Ex_Date': d, 'Pay_Date': d, 'DPS_Paise': 0,
                            'Ratio_New': fr.numerator, 'Ratio_Old': fr.denominator, 'Origin': 'REAL'})
            for f in holders[s]:
                q0 = pos_prev[(f, s)]
                qty[(f, s)] = q0 * fr.numerator // fr.denominator
                feeds['ca_bookings'].append([f'BK-{cid}-{f}', d, f, s, cid, 'SPLIT', 0, q0, qty[(f, s)]])
        if dv > 0:
            dps = to_paise(dv)
            cid = f'CA-L-{d.replace("-", "")}-{s}-D'
            ca_rows.append({'CA_ID': cid, 'Security_ID': s, 'CA_Type': 'DIVIDEND', 'Ex_Date': d, 'Pay_Date': pay, 'DPS_Paise': dps,
                            'Ratio_New': 1, 'Ratio_Old': 1, 'Origin': 'REAL'})
            for f in holders[s]:
                amt = pos_prev[(f, s)] * dps
                feeds['ca_bookings'].append([f'BK-{cid}-{f}', d, f, s, cid, 'DIVIDEND', amt, pos_prev[(f, s)], pos_prev[(f, s)]])
                new_recv.append({'Fund_ID': f, 'Amount_Paise': amt, 'Pay_Date': pay})

    # cash, receivables, fees, administrator NAV (clean book)
    old_recv = state['receivables']
    cash, recv, basis, fee_clean, payable_clean = {}, {}, {}, {}, {}
    for f in funds:
        cash[f] = int(fd_prev[f]['Cash_Paise']) + sum(int(b['Amount_Paise']) for b in old_recv if b['Fund_ID'] == f and b['Pay_Date'] <= d)
        recv[f] = sum(int(b['Amount_Paise']) for b in old_recv + new_recv if b['Fund_ID'] == f and b['Pay_Date'] > d)
        na_state = nav_prev.get(f, {}).get('Net_Assets_Paise')
        if na_state in (None, ''):       # previous NAV not computable: estimate from yesterday's clean prices
            mv = sum(q * (prev_px(s, 'A') or A[s]) for (ff, s), q in pos_prev.items() if ff == f)
            na_state = mv + int(fd_prev[f]['Cash_Paise']) - int(nav_prev.get(f, {}).get('Fee_Payable_Paise') or 0) - int(fd_prev[f]['Other_Liabilities_Paise'])
        basis[f] = int(na_state) - na_fix[f]
        fee_clean[f] = rhu_div(basis[f] * bps[f] * gap, 10000 * 365)
        payable_clean[f] = int(nav_prev.get(f, {}).get('Fee_Payable_Paise') or 0) + na_fix[f] + fee_clean[f]

    # optional demo errors (private answer key)
    fee_booked = dict(fee_clean)
    px = {s: {'A': A[s], 'B': B[s], 'C': C[s]} for s in secs}
    planted = []
    if inject > 0:
        rng = np.random.default_rng(seed_for(d, 'inject'))
        kinds = [str(k) for k in rng.permutation(['STALE_B', 'FAT_C', 'VARIANCE_B', 'MISSING_C', 'FEE_TWICE'])][:min(inject, 5)]
        ca_secs = {r['Security_ID'] for r in ca_rows}
        used = set()
        for k in kinds:
            if k == 'FEE_TWICE':
                f = funds[int(rng.integers(len(funds)))]
                fee_booked[f] = 2 * fee_clean[f]
                planted.append({'Type': k, 'Fund_ID': f, 'Security_ID': '', 'Source': '', 'True_Value': json.dumps({'Mgmt': fee_clean[f]}),
                                'Planted_Value': json.dumps({'Mgmt': fee_booked[f]}), 'NA_Effect_Paise': -fee_clean[f],
                                'Clean_Row': json.dumps({'Accrual_Days': gap, 'Basis': basis[f], 'Mgmt': fee_clean[f]})})
                continue
            pool = [s for s in secs if holders[s] and s not in ca_secs and s not in used]
            if k == 'STALE_B':
                pool = [s for s in pool if prev_px(s, 'A') and prev_px(s, 'B') and abs(A[s] / prev_px(s, 'A') - 1) >= 0.01]
            if not pool:
                continue
            s = pool[int(rng.integers(len(pool)))]
            used.add(s)
            src = k[-1]
            true = px[s][src]
            bad = {'STALE_B': prev_px(s, 'B'), 'FAT_C': true * 10, 'VARIANCE_B': rhu_mul(true, 1.006), 'MISSING_C': ''}[k]
            px[s][src] = bad
            planted.append({'Type': k, 'Fund_ID': ','.join(holders[s]), 'Security_ID': s, 'Source': src,
                            'True_Value': json.dumps({src: true}), 'Planted_Value': json.dumps({src: bad}), 'NA_Effect_Paise': 0,
                            'Clean_Row': json.dumps({'A': A[s], 'B': B[s], 'C': C[s], 'V': V[s]})})

    for s in secs:
        feeds['prices'].append([f'{d}_{s}', d, s, px[s]['A'], px[s]['B'], px[s]['C'], V[s], f'LIVE_{d.replace("-", "")}', f'{d} 16:30:00'])
    for (f, s), q in sorted(qty.items()):
        feeds['positions'].append([f'{d}_{f}_{s}', d, f, s, q])
    for f in funds:
        units, liab = int(fd_prev[f]['Units_Milli']), int(fd_prev[f]['Other_Liabilities_Paise'])
        feeds['fee_accruals'].append([f'{d}_{f}', d, f, gap, basis[f], fee_booked[f], 0])
        feeds['fund_daily'].append([f'{d}_{f}', d, f, cash[f], 0, liab, units])
        adm_na = sum(q * B[s] for (ff, s), q in qty.items() if ff == f) + recv[f] + cash[f] - payable_clean[f] - liab
        feeds['admin_nav'].append([f'{d}_{f}', d, f, rhu_div(adm_na * 100000, units), adm_na, f'{d} 18:30:00'])

    cal_last = state.get('calendar_last') or {}
    prev_close = float(cal_last['nifty_close']) if cal_last.get('nifty_close') else float(nifty[nifty.index < D].iloc[-1])
    nc = float(nifty.loc[D])
    cal_row = {'Date': d, 'Is_Business_Day': 'TRUE', 'Day_Role': 'TEST', 'Prev_Business_Day': last, 'Accrual_Days': gap,
               'Nifty_Close': round(nc, 2), 'Nifty_Return_bps': round((nc / prev_close - 1) * 10000, 2)}
    out = api('intake', {'date': d, 'calendar_row': cal_row, 'corp_actions': ca_rows,
                         'feeds': {k: csv_text(v, FEED_COLS[k]) for k, v in feeds.items()}})

    stamp = dt.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
    if len(pend):
        ledger.loc[pend.index, 'Restated'] = 1
    new = pd.DataFrame([{**p, 'Key_ID': f'LK-{d.replace("-", "")}-{i + 1:02d}', 'Date': d, 'Restated': 0, 'Run_At': stamp}
                        for i, p in enumerate(planted)], columns=LEDGER_COLS)
    pd.concat([ledger, new], ignore_index=True)[LEDGER_COLS].to_csv(LEDGER, index=False)
    print(f'{d}: feeds accepted ({out.get("files")} files, {out.get("corporate_actions_added")} corporate action(s)); '
          f'{len(pend)} restatement(s); {len(planted)} demo error(s) planted. The pipeline starts automatically.')


# ---------------------------------------------------------------------------- real fund (F6)
def official_navs():
    navs = {}
    try:
        r = requests.get(f"https://api.mfapi.in/mf/{F6['code']}", timeout=60)
        r.raise_for_status()
        for x in r.json().get('data', []):
            navs[dt.datetime.strptime(x['date'], '%d-%m-%Y').strftime('%Y-%m-%d')] = (Decimal(str(x['nav'])), 'mfapi.in (third-party mirror of AMFI)')
    except Exception as e:  # noqa: BLE001
        print('mfapi.in not available:', e)
    try:
        r = requests.get('https://www.amfiindia.com/spages/NAVAll.txt', timeout=60)
        r.raise_for_status()
        for line in r.text.splitlines():
            p = [x.strip() for x in line.split(';')]
            if len(p) >= 6 and p[0] == F6['code']:
                try:
                    navs[dt.datetime.strptime(p[5], '%d-%b-%Y').strftime('%Y-%m-%d')] = (Decimal(p[4]), 'AMFI NAVAll.txt')
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        print('AMFI NAVAll.txt not available:', e)
    return navs


def load_portfolio():
    if not os.path.exists(F6_PORTFOLIO):
        return None
    df = pd.read_csv(F6_PORTFOLIO, dtype={'Symbol': str, 'As_Of': str})
    df['Weight_Pct'] = pd.to_numeric(df['Weight_Pct'], errors='coerce').fillna(0.0)
    df = df[df['Weight_Pct'] > 0]
    return df if len(df) else None


def port_return(port, close, divs, D, Dp, nifty):
    if port is None:
        return float(nifty.loc[D] / nifty.loc[Dp] - 1), 'INDEX (Nifty 50 price return; dividends excluded)', '', 100.0
    snaps = sorted(port['As_Of'].unique())
    asof = max([a for a in snaps if pd.Timestamp(a) <= Dp], default=snaps[0])
    snap = port[port['As_Of'] == asof]
    base_idx = close.index[close.index <= pd.Timestamp(asof)]
    base = base_idx[-1] if len(base_idx) else close.index[0]
    parts, missing, total = [], 0.0, float(snap['Weight_Pct'].sum())
    for _, h in snap.iterrows():
        w0, sym = float(h['Weight_Pct']) / 100, str(h['Symbol'])
        if sym == 'CASH':
            parts.append((w0, 0.0))
            continue
        if sym not in close.columns:
            missing += w0
            continue
        p0, p1, p2 = close.at[base, sym], close.at[Dp, sym], close.at[D, sym]
        if any(pd.isna(x) for x in (p0, p1, p2)) or p0 == 0 or p1 == 0:
            missing += w0
            continue
        parts.append((w0 * float(p1) / float(p0), (float(p2) + val(divs, D, sym)) / float(p1) - 1))
    tw = sum(w for w, _ in parts)
    r = sum(w * x for w, x in parts) / tw if tw else float(nifty.loc[D] / nifty.loc[Dp] - 1)
    return r, 'HOLDINGS (monthly portfolio, drift-adjusted)', asof, round(100 * (1 - missing / (total / 100)), 2) if total else 0.0


def realfund(start='', end=''):
    state = api('state', {})['state']
    navs = official_navs()
    if not navs:
        sys.exit('No official NAV source reachable.')
    now = dt.datetime.now(IST)
    last_final = state.get('realfund_last_final') or ''
    s0 = pd.Timestamp(start) if start else (pd.Timestamp(last_final) + pd.Timedelta(days=1) if last_final
                                            else pd.Timestamp(now.date()) - pd.Timedelta(days=45))
    e0 = pd.Timestamp(end) if end else pd.Timestamp(now.date())
    port = load_portfolio()
    syms = [x for x in (sorted(port['Symbol'].unique()) if port is not None else []) if x != 'CASH']
    dl0 = min([s0] + ([pd.Timestamp(port['As_Of'].min())] if port is not None else [])) - pd.Timedelta(days=15)
    raw = yf_download(['^NSEI'] + syms, dl0.strftime('%Y-%m-%d'), (e0 + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
    close, divs = field(raw, 'Close'), field(raw, 'Dividends')
    nifty = close['^NSEI'].dropna()
    idx = list(nifty.index)
    rows, stamp = [], now.strftime('%Y-%m-%d %H:%M:%S')
    for k in range(1, len(idx)):
        D, Dp = idx[k], idx[k - 1]
        if D < s0 or D > e0 or (D.date() == now.date() and (now.hour, now.minute) < (16, 0)):
            continue
        d, dp = D.strftime('%Y-%m-%d'), Dp.strftime('%Y-%m-%d')
        if dp not in navs:
            continue
        o_prev = navs[dp][0]
        r, method, asof, cov = port_return(port, close, divs, D, Dp, nifty)
        g = (D - Dp).days
        shadow = (o_prev * (Decimal(1) + Decimal(str(r)) - Decimal(str(F6_TER_BPS)) / Decimal(10000) * Decimal(g) / Decimal(365))
                  ).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
        if d in navs:
            o = navs[d][0]
            td = float((o / shadow - 1) * 10000)
            status, src, o_txt, td_txt = ('BREAK' if abs(td) > F6_BREAK_BPS else 'MATCH'), navs[d][1], str(o), round(td, 2)
        else:
            status, src, o_txt, td_txt = 'PENDING_OFFICIAL', '', '', ''
        rows.append({'RF_Key': f"{d}_{F6['code']}", 'Date': d, 'Scheme_Code': F6['code'], 'Scheme_Name': F6['name'],
                     'Official_NAV': o_txt, 'Prev_Official_NAV': str(o_prev), 'Shadow_NAV': str(shadow), 'Tracking_Diff_bps': td_txt,
                     'Portfolio_Return_bps': round(r * 10000, 2), 'Method': method, 'Weights_As_Of': asof, 'Coverage_Pct': cov,
                     'TER_bps': F6_TER_BPS, 'Break_Threshold_bps': F6_BREAK_BPS, 'Status': status, 'Official_Source': src,
                     'Computed_At': stamp})
    if not rows:
        print('No new real-fund dates to compute.')
        return
    api('realfund', {'rows': rows})
    print(f'Real fund: {len(rows)} row(s) sent ({rows[0]["Date"]} to {rows[-1]["Date"]}).')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['bootstrap', 'run', 'realfund'])
    ap.add_argument('--inject', default='0')
    ap.add_argument('--start', default='')
    ap.add_argument('--end', default='')
    a = ap.parse_args()
    inject = int(a.inject) if str(a.inject).strip().isdigit() else 0
    if a.mode == 'bootstrap':
        bootstrap()
    elif a.mode == 'run':
        run(min(inject, 3))
    else:
        realfund(a.start.strip(), a.end.strip())


if __name__ == '__main__':
    main()
