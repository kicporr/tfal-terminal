# -*- coding: utf-8 -*-
"""Wyniki portfela: krzywa wartości w PLN, benchmarki, statystyki.

Portfel liczony jest przy dzisiejszym składzie (stała liczba akcji), więc pokazuje,
jak zachowywałby się obecny portfel w przeszłości — bez zgadywania dawnych transakcji.
"""
import bisect
import json
import math
import os
import statistics as st
import time
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "_cache")

BENCH = {
    "sp500":  {"symbol": "^GSPC",   "name": "S&P 500",     "cur": "USD"},
    "ndx":    {"symbol": "^NDX",    "name": "Nasdaq 100",  "cur": "USD"},
    "world":  {"symbol": "EUNL.DE", "name": "MSCI World",  "cur": "EUR"},
    # WIG20 przez ETF (Beta ETF WIG20TR) — sam indeks w Yahoo nie ma historii,
    # a WIG20TR z dywidendami jest uczciwszą miarą niż indeks cenowy
    "wig20":  {"symbol": "ETFBW20TR.WA", "name": "WIG20TR (ETF)", "cur": "PLN"},
}


def _path(name):
    return os.path.join(CACHE, name)


def _fresh(p, hours):
    return os.path.exists(p) and time.time() - os.path.getmtime(p) < hours * 3600


def fx_history(cur, start, end):
    """Kursy NBP dzień po dniu; cache 7 dni. Zwraca {date: rate}."""
    if (cur or "PLN").upper() == "PLN":
        return {}
    cur = cur.upper()
    p = _path(f"fxhist_{cur}_{start}_{end}.json")
    if _fresh(p, 24 * 7):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    out = {}
    s = datetime.fromisoformat(start).date()
    e = datetime.fromisoformat(end).date()
    while s <= e:
        chunk_end = min(e, s + timedelta(days=360))
        url = (f"https://api.nbp.pl/api/exchangerates/rates/A/{cur}/"
               f"{s.isoformat()}/{chunk_end.isoformat()}/?format=json")
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                j = json.loads(r.read().decode("utf-8"))
            for row in j.get("rates", []):
                out[row["effectiveDate"]] = float(row["mid"])
        except Exception:
            pass
        s = chunk_end + timedelta(days=1)
    if out:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f)
    return out


def _ffill_map(d):
    """{date: x} -> funkcja lookup(date) po najbliższej wcześniejszej dacie."""
    keys = sorted(d)
    vals = [d[k] for k in keys]
    if not keys:
        return lambda _d: None
    def f(day):
        i = bisect.bisect_right(keys, day) - 1
        return vals[i] if i >= 0 else None
    return f


def bench_series(key, start, end):
    """Seria benchmarku w PLN (kurs NBP dla waluty indeksu)."""
    meta = BENCH.get(key)
    if not meta:
        return {}, None
    p = _path(f"bench_{key}_{start}_{end}.json")
    if _fresh(p, 12):
        try:
            j = json.load(open(p, encoding="utf-8"))
            if j.get("series"):
                return {k: v for k, v in j["series"].items()}, meta
        except Exception:
            pass
    import yfinance as yf
    h = yf.Ticker(meta["symbol"]).history(start=start, end=end, auto_adjust=True)
    base = {}
    for ts, close in zip(h.index, h["Close"]):
        if close and close == close:
            base[str(ts.date())] = float(close)
    fx = _ffill_map(fx_history(meta["cur"], start, end))
    series = {}
    for day, v in base.items():
        f = 1.0 if meta["cur"] == "PLN" else fx(day)
        if f:
            series[day] = v * f
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"series": series, "name": meta["name"]}, f)
    except Exception:
        pass
    return series, meta


def portfolio_series(tickers, shares, cur_of, start):
    """Wartość dzisiejszego portfela w czasie (stała liczba akcji, kursy NBP)."""
    import scrab_client as sc
    grouped = sc.post(tickers, ["price"])
    prices = {}
    for s in grouped:
        t = (s.get("ticker") or "").upper()
        dp = s.get("datapoints") or []
        if t and dp:
            prices[t] = {str(datetime.fromtimestamp(ts, None).date()): float(v)
                         for ts, v in dp if isinstance(v, (int, float))}
    if not prices:
        return {}
    per_cur = {}
    for t in prices:
        per_cur.setdefault(cur_of(t), []).append(t)
    fx_maps = {c: _ffill_map(fx_history(c, start, date.today().isoformat())) for c in per_cur}
    all_days = sorted({d for m in prices.values() for d in m})
    lookups = {t: _ffill_map(m) for t, m in prices.items()}
    out = {}
    for day in all_days:
        if day < start:
            continue
        total = 0.0
        ok = False
        for t, m in prices.items():
            px = lookups[t](day)
            if px is None:
                continue
            f = 1.0 if cur_of(t) == "PLN" else fx_maps[cur_of(t)](day)
            if not f:
                continue
            total += float(shares.get(t, 0) or 0) * px * f
            ok = True
        if ok:
            out[day] = total
    return out


def _returns(series):
    days = sorted(series)
    r = []
    for i in range(1, len(days)):
        p0, p1 = series[days[i - 1]], series[days[i]]
        if p0 > 0 and p1 > 0:
            r.append(p1 / p0 - 1)
    return r


def stats(port, bench=None):
    days = sorted(set(port) & set(bench)) if bench else sorted(port)
    if len(days) < 30:
        return {"error": "za mało wspólnych dni"}
    p = [port[d] for d in days]
    n = len(p)
    ret_p = _returns({d: port[d] for d in days})
    total = p[-1] / p[0] - 1
    years = n / 252
    cagr = (p[-1] / p[0]) ** (1 / years) - 1 if years > 0 else None
    vol = st.stdev(ret_p) * math.sqrt(252) if len(ret_p) > 2 else None
    peak, mdd = p[0], 0.0
    for v in p:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    out = {
        "days": n, "start": days[0], "end": days[-1],
        "start_value": round(p[0], 2), "end_value": round(p[-1], 2),
        "total": round(total, 4), "cagr": round(cagr, 4) if cagr is not None else None,
        "vol": round(vol, 4) if vol else None,
        "maxdd": round(mdd, 4),
        "best_day": round(max(ret_p), 4) if ret_p else None,
        "worst_day": round(min(ret_p), 4) if ret_p else None,
        "hit": round(sum(1 for x in ret_p if x > 0) / len(ret_p), 3) if ret_p else None,
        "sharpe": round((cagr or 0) / vol, 2) if vol else None,
    }
    if bench:
        b = [bench[d] for d in days]
        ret_b = _returns({d: bench[d] for d in days})
        btotal = b[-1] / b[0] - 1
        bvol = st.stdev(ret_b) * math.sqrt(252) if len(ret_b) > 2 else None
        bcagr = (b[-1] / b[0]) ** (1 / years) - 1 if years > 0 else None
        mean_p, mean_b = st.mean(ret_p), st.mean(ret_b)
        cov = sum((x - mean_p) * (y - mean_b) for x, y in zip(ret_p, ret_b)) / max(len(ret_p) - 1, 1)
        var_b = st.variance(ret_b) if len(ret_b) > 2 else 0
        beta = cov / var_b if var_b else None
        corr = None
        if var_b and vol:
            corr = cov / (st.stdev(ret_p) * st.stdev(ret_b))
        te = st.stdev([x - y for x, y in zip(ret_p, ret_b)]) * math.sqrt(252) if len(ret_p) > 2 else None
        out["bench"] = {
            "total": round(btotal, 4), "cagr": round(bcagr, 4) if bcagr is not None else None,
            "vol": round(bvol, 4) if bvol else None, "maxdd": None,
            "beta": round(beta, 3) if beta is not None else None,
            "corr": round(corr, 3) if corr is not None else None,
            "te": round(te, 4) if te else None,
            "alpha": round((cagr or 0) - (beta or 1) * (bcagr or 0), 4) if cagr is not None else None,
        }
        peakb, mddb = b[0], 0.0
        for v in b:
            peakb = max(peakb, v)
            mddb = min(mddb, v / peakb - 1)
        out["bench"]["maxdd"] = round(mddb, 4)
    return out


def normalize(series, days):
    if not days:
        return []
    base = series[days[0]]
    return [round(series[d] / base * 100, 2) if base else None for d in days]
