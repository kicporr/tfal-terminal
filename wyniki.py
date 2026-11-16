# -*- coding: utf-8 -*-
"""Wyniki portfela liczone z dziennika transakcji.

W przeciwieństwie do `perf.py` (dzisiejszy skład cofnięty w czasie) tutaj portfel
zmienia się wraz z transakcjami: kupno dodaje akcje, sprzedaż je zdejmuje, wpłata
zasila gotówkę, dywidenda ją zwiększa.

Dwie miary:
  * TWR (time-weighted) — wynik strategii, odporny na wielkość i moment wpłat,
  * MWR (money-weighted, XIRR) — wynik Twoich pieniędzy, zależy od wpłat.
"""
import math
from datetime import date


def _ffill(d):
    keys = sorted(d)

    def f(day):
        lo, hi, ans = 0, len(keys) - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if keys[mid] <= day:
                ans, lo = mid, mid + 1
            else:
                hi = mid - 1
        return d[keys[ans]] if ans >= 0 else None
    return f


def _xirr(flows):
    """Stopa, przy której NPV przepływów = 0. flows: [(date, amount)]."""
    if not flows:
        return None
    t0 = min(d for d, _ in flows)
    pts = [((date.fromisoformat(d) - date.fromisoformat(t0)).days / 365.25, a) for d, a in flows]

    def npv(r):
        return sum(a / (1 + r) ** t for t, a in pts if t >= 0)

    lo, hi = -0.95, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        v = npv(mid)
        if abs(v) < 1e-7:
            return mid
        if npv(lo) * v <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def compute(txs, prices, ccy_of, fx_of, end=None):
    """prices: {ticker: {data: close}} w walucie instrumentu; fx_of(ccy) -> lookup(data)."""
    end = end or date.today().isoformat()
    txs = [t for t in txs if t.get("date")]
    if not txs:
        return {"error": "dziennik jest pusty"}
    txs.sort(key=lambda t: (t["date"], t.get("seq", 0)))

    tickers = sorted({t["ticker"] for t in txs
                      if t.get("side") in ("KUP", "SPRZEDAJ") and t.get("ticker")})
    px_maps = {t: (prices.get(t) or {}) for t in tickers}
    missing = [t for t in tickers if not px_maps[t]]
    lookups = {t: _ffill(m) for t, m in px_maps.items()}
    fxsl = {c: fx_of(c) for c in {ccy_of(t) for t in tickers}}

    first_day = txs[0]["date"]
    days_all = sorted({d for m in px_maps.values() for d in m if first_day <= d <= end})
    if not days_all:
        return {"error": "brak notowań dla spółek z dziennika"}
    # dołóż dni z transakcjami, których nie ma w notowaniach (weekendy, święta):
    # inaczej wpłata z soboty przepadała z modelu
    days_all = sorted(set(days_all) | {t["date"] for t in txs if first_day <= t["date"] <= end})

    # kolejność w ciągu dnia: najpierw wpłata (zasila gotówkę), potem transakcje,
    # na końcu dywidenda i wypłata — inaczej chwilowy debet robi sztuczne wpłaty
    prio = {"WPŁATA": 0, "KUP": 1, "SPRZEDAJ": 1, "DYWIDENDA": 2, "WYPŁATA": 3}
    by_day = {}
    for t in txs:
        by_day.setdefault(t["date"], []).append(t)
    for day in by_day:
        by_day[day].sort(key=lambda x: (prio.get(x.get("side"), 1), x.get("seq", 0)))

    shares = {t: 0.0 for t in tickers}
    cash = 0.0
    invested = 0.0          # wpłaty netto narastająco
    flows, series, drawdown, wealth = [], [], [], []
    implicit = 0.0
    idx = 100.0
    prev_value = None
    peak = 100.0
    warned = set()

    for day in days_all:
        flow = 0.0
        for tx in by_day.get(day, []):
            side = tx.get("side")
            amt = float(tx.get("amount") or 0)
            if side == "WPŁATA":
                cash += amt
                flow += amt
                invested += amt
                flows.append((day, -amt))
            elif side == "WYPŁATA":
                cash -= amt
                flow -= amt
                invested -= amt
                flows.append((day, amt))
            elif side == "DYWIDENDA":
                # dywidenda przychodzi w walucie spółki — do gotówki wchodzi po kursie z dnia
                tk = tx.get("ticker")
                fd = 1.0 if (not tk or ccy_of(tk) == "PLN") else (fxsl[ccy_of(tk)](day) or 0.0)
                cash += amt * fd
            elif side in ("KUP", "SPRZEDAJ"):
                tk = tx.get("ticker")
                qty = float(tx.get("qty") or 0)
                px = float(tx.get("price") or 0)
                fee = float(tx.get("fee") or 0)
                f = 1.0 if ccy_of(tk) == "PLN" else (fxsl[ccy_of(tk)](day) or 0.0)
                if side == "KUP":
                    shares[tk] = shares.get(tk, 0.0) + qty
                    cash -= qty * px * f + fee * f
                else:
                    shares[tk] = shares.get(tk, 0.0) - qty
                    cash += qty * px * f - fee * f

        # brak wpłat w dzienniku → uzupełniam gotówkę, żeby nie zaniżać wyceny
        if cash < -0.01:
            need = -cash
            cash = 0.0
            implicit += need
            flow += need
            flows.append((day, -need))

        value = cash
        for t in tickers:
            q = shares.get(t, 0.0)
            if abs(q) < 1e-9:
                continue
            px = lookups[t](day)
            if px is None:
                continue
            f = 1.0 if ccy_of(t) == "PLN" else (fxsl[ccy_of(t)](day) or 0.0)
            value += q * px * f

        if prev_value and prev_value + flow > 0:
            r = value / (prev_value + flow) - 1
            idx *= (1 + r)
        peak = max(peak, idx)
        drawdown.append([day, round(idx / peak - 1, 4)])
        series.append([day, round(idx, 4), round(value, 2)])
        wealth.append([day, round(value, 2), round(invested, 2)])
        prev_value = value

    if len(series) < 5:
        return {"error": "za mało dni z wyceną portfela"}

    days = len(series)
    years = days / 365.25
    total = series[-1][1] / 100 - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else None
    rets = [series[i][1] / series[i - 1][1] - 1 for i in range(1, len(series)) if series[i - 1][1] > 0]
    vol = None
    if len(rets) > 2:
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        vol = math.sqrt(var) * math.sqrt(252)
    maxdd = min([d[1] for d in drawdown] or [0])

    final_value = series[-1][2]
    mwr = _xirr(flows + [(series[-1][0], final_value)])
    deposits = sum(float(t.get("amount") or 0) for t in txs if t.get("side") == "WPŁATA")
    if implicit > max(100.0, 0.01 * deposits):
        warned.add(f"w gotówce brakowało {implicit:.0f} zł (np. przewalutowania) — doliczone jako wpłata")
    withdrawals = sum(float(t.get("amount") or 0) for t in txs if t.get("side") == "WYPŁATA")
    def _div_pln(t):
        tk, day = t.get("ticker"), t.get("date")
        if not tk or ccy_of(tk) == "PLN" or not day:
            return float(t.get("amount") or 0)
        return float(t.get("amount") or 0) * (fxsl[ccy_of(tk)](day) or 0.0)

    dividends = sum(_div_pln(t) for t in txs if t.get("side") == "DYWIDENDA")

    monthly = {}
    for i in range(1, len(series)):
        k = series[i][0][:7]
        monthly[k] = monthly.get(k, 1.0) * (series[i][1] / series[i - 1][1])

    return {
        "start": series[0][0], "end": series[-1][0], "days": days,
        "value_end": round(final_value, 2),
        "cash_end": round(cash, 2),
        "net_invested": round(deposits - withdrawals, 2),
        "profit_pln": round(final_value - (deposits - withdrawals), 2),
        "deposits": round(deposits, 2), "withdrawals": round(withdrawals, 2),
        "dividends": round(dividends, 2),
        "twr": {"total": round(total, 4),
                "cagr": round(cagr, 4) if cagr is not None else None,
                "vol": round(vol, 4) if vol else None,
                "sharpe": round((cagr or 0) / vol, 2) if vol else None,
                "maxdd": round(maxdd, 4)},
        "mwr": {"annual": round(mwr, 4) if mwr is not None else None},
        "series": series,
        "wealth": wealth,
        "drawdown": drawdown,
        "monthly": [[k, round(v - 1, 4)] for k, v in sorted(monthly.items())],
        "missing_prices": missing,
        "implicit_deposits": round(implicit, 2),
        "warnings": sorted(warned),
    }
