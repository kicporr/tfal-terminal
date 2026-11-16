# -*- coding: utf-8 -*-
"""Analiza portfela: ryzyko, backtest scoringu, kalendarz katalizatorów.

Uzycie:
    python analiza.py                 # lista z Portfel.xlsx
    python analiza.py MU AAPL ASML.AS
    python analiza.py --pdf
"""
import math
import os
import statistics as st
import sys
from datetime import datetime, timezone, timedelta, date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import ColorScaleRule

import scrab_client as sc
import scoring
import history as hist

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Analiza.xlsx")
CH = os.path.join(HERE, "_charts")
os.makedirs(CH, exist_ok=True)

INK, MUTE, DARK, WHITE = "1F2933", "6B7280", "1F2933", "FFFFFF"
GREY, YELLOW = "F2F4F7", "FFF2CC"
GREEN_F, GREEN_T = "C6EFCE", "0B6B3A"
RED_F, RED_T = "FFC7CE", "9C1C1C"
THIN = Side(style="thin", color="D0D5DD")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
RISK_LIMIT = 0.15

ALL_METRICS = sc.METRICS + sc.EXTRA_METRICS


# ---------- dane ----------
def load_tickers():
    if not os.path.exists(os.path.join(HERE, "Portfel.xlsx")):
        return []
    try:
        ws = load_workbook(os.path.join(HERE, "Portfel.xlsx"))["Portfel"]
    except Exception:
        return []
    import re
    ok = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
    out = []
    for r in range(5, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if v and ok.match(str(v).strip().upper()):
            out.append(str(v).strip().upper())
    return out


def fetch(tickers):
    data = sc.post(tickers, ALL_METRICS)
    g = {}
    for s in data:
        tk = (s.get("ticker") or "").upper()
        if tk and s.get("datapoints"):
            g.setdefault(tk, {})[s.get("metric")] = s.get("datapoints")
    return g


def meta(tickers):
    """Sektor, branża i kalendarz z Yahoo (uzupełnienie)."""
    out = {}
    try:
        import yfinance as yf
    except Exception:
        return {t: {} for t in tickers}
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            info = tk.info or {}
            cal = {}
            try:
                cal = tk.calendar or {}
            except Exception:
                pass
            out[t] = {"sector": info.get("sector") or "—",
                      "industry": info.get("industry") or "—",
                      "calendar": cal if isinstance(cal, dict) else {}}
        except Exception:
            out[t] = {"sector": "—", "industry": "—", "calendar": {}}
    return out


def price_map(dp):
    return {int(ts): float(v) for ts, v in dp if isinstance(v, (int, float))}


def returns_to_frame(series):
    """Wspólne daty -> macierz dziennych stóp zwrotu."""
    maps = {t: price_map(dp) for t, dp in series.items()}
    common = set.intersection(*[set(m) for m in maps.values()]) if maps else set()
    dates = sorted(common)
    if len(dates) < 30:
        return [], []
    rets = {t: [] for t in maps}
    for i in range(1, len(dates)):
        for t, m in maps.items():
            p0, p1 = m[dates[i - 1]], m[dates[i]]
            rets[t].append(p1 / p0 - 1 if p0 else 0.0)
    return dates[1:], rets


# ---------- styl arkuszy ----------
def put(ws, cell, value, bold=False, size=10, color=INK, fill=None, fmt=None,
        border=False, wrap=False, align="left"):
    cl = ws[cell]
    cl.value = value
    cl.font = Font(bold=bold, size=size, color=color)
    if fill:
        cl.fill = PatternFill("solid", fgColor=fill)
    if fmt:
        cl.number_format = fmt
    if border:
        cl.border = BOX
    cl.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    return cl


def head_row(ws, row, labels, widths=None):
    for i, lab in enumerate(labels, 1):
        cl = ws.cell(row=row, column=i, value=lab)
        cl.font = Font(bold=True, size=9, color=WHITE)
        cl.fill = PatternFill("solid", fgColor=DARK)
        cl.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cl.border = BOX
    if widths:
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w


# ---------- 1. ryzyko ----------
def sheet_risk(wb, tickers, series, weights, m):
    ws = wb.create_sheet("Ryzyko")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "D2453C"

    dates, rets = returns_to_frame(series)
    n = len(tickers)
    if not dates:
        put(ws, "A1", "Brak wystarczającej historii cen do policzenia ryzyka.", color=RED_T)
        return
    win = 750
    rets_w = {t: v[-win:] for t, v in rets.items()}
    R = np.array([rets_w[t] for t in tickers])
    cov = np.cov(R) * 252
    vol = np.sqrt(np.diag(cov))
    w = np.array(weights)
    port_var = float(w @ cov @ w)
    port_vol = math.sqrt(port_var)
    mcr = (cov @ w) / port_vol if port_vol else np.zeros(n)
    rc = (w * mcr) / port_vol if port_vol else np.zeros(n)

    daily_port = (R.T @ w)
    var_hist = float(np.percentile(daily_port, 5))
    var_param = 1.645 * daily_port.std(ddof=1)
    risk_budget = float(np.sum(w * vol))

    for col, wd in zip("ABCDEF", (13, 11, 15, 15, 18, 22)):
        ws.column_dimensions[col].width = wd

    put(ws, "A1", "Ryzyko portfela", bold=True, size=16)
    put(ws, "A2", f"Okno: {len(dates[-win:])} sesji · wagi: równe (możesz wpisać własne w kolumnie C) · "
                  f"dane: Scrab (FactSet) · {datetime.now():%Y-%m-%d %H:%M}", size=9, color=MUTE)

    kpis = [
        ("Zmienność portfela (rok)", f"{port_vol*100:.1f}%", port_vol > 0.35),
        ("VaR 5% (1 dzień)", f"{var_param*100:.2f}%", var_param > 0.03),
        ("VaR 5% (10 dni)", f"{var_param*math.sqrt(10)*100:.1f}%", False),
        ("VaR historyczny (1 dzień)", f"{var_hist*100:.2f}%", False),
        ("Suma w·σ (proxy limitu 15%)", f"{risk_budget*100:.1f}%", risk_budget > RISK_LIMIT),
        ("Limit", f"{RISK_LIMIT*100:.0f}%", False),
    ]
    r = 4
    for lab, val, bad in kpis:
        put(ws, f"A{r}", lab, size=10)
        ws.merge_cells(f"A{r}:C{r}")
        put(ws, f"D{r}", val, bold=True, size=11, border=True,
            color=RED_T if bad else INK, align="right")
        r += 1
    put(ws, "A10", "Interpretacja", bold=True, size=10)
    put(ws, "A11", "VaR 5% = strata, której nie przekroczysz w 95% dni (parametryczny, 1,645·σ). "
                   "Ryzyko w pozycjach to suma ważonych zmienności — u Treli limit 15% całego portfela, "
                   "średnio 1,5% na pozycję.", size=9, color=MUTE, wrap=True)
    ws.merge_cells("A11:F12")

    # tabela pozycji
    start = 15
    put(ws, f"A{start-1}", "POZYCJE", bold=True, size=10)
    head_row(ws, start, ["Ticker", "Waga", "Zmienność (rok)", "Wkład w ryzyko", "Wkład w wariancję", "Sektor"])
    sec = m.get("sectors", {})
    for i, t in enumerate(sorted(tickers, key=lambda x: -w[tickers.index(x)] * vol[tickers.index(x)])):
        idx = tickers.index(t)
        rr = start + 1 + i
        put(ws, f"A{rr}", t, bold=True, border=True)
        put(ws, f"B{rr}", float(w[idx]), fmt="0.0%", border=True, align="right")
        put(ws, f"C{rr}", f"{vol[idx]*100:.1f}%", border=True, align="right")
        put(ws, f"D{rr}", f"{w[idx]*vol[idx]*100:.1f}%", border=True, align="right")
        put(ws, f"E{rr}", f"{rc[idx]*100:.1f}%", border=True, align="right")
        put(ws, f"F{rr}", sec.get(t, "—"), border=True)
    put(ws, f"A{start+len(tickers)+2}", "Suma ryzyka w pozycjach:", bold=True, size=9)
    put(ws, f"D{start+len(tickers)+2}", f"{risk_budget*100:.1f}%", bold=True, size=9,
        color=RED_T if risk_budget > RISK_LIMIT else GREEN_T)

    # koncentracja sektorowa
    r0 = start + len(tickers) + 5
    put(ws, f"A{r0-1}", "KONCENTRACJA SEKTOROWA", bold=True, size=10)
    head_row(ws, r0, ["Sektor", "", "", "Udział", "Spółek"])
    ws.merge_cells(f"A{r0}:C{r0}")
    bysec = {}
    for i, t in enumerate(tickers):
        bysec.setdefault(sec.get(t, "—"), [0.0, 0])
        bysec[sec.get(t, "—")][0] += w[i]
        bysec[sec.get(t, "—")][1] += 1
    rr = r0 + 1
    for sname, (sw, cnt) in sorted(bysec.items(), key=lambda x: -x[1][0]):
        put(ws, f"A{rr}", sname, border=True)
        ws.merge_cells(f"A{rr}:C{rr}")
        put(ws, f"D{rr}", float(sw), fmt="0.0%", border=True, align="right")
        put(ws, f"E{rr}", cnt, border=True, align="right")
        rr += 1
    hhi = sum((sw / sum(w)) ** 2 for sw, _ in bysec.values())
    put(ws, f"A{rr+1}", "HHI (koncentracja)", size=9, color=MUTE)
    ws.merge_cells(f"A{rr+1}:C{rr+1}")
    put(ws, f"D{rr+1}", f"{hhi:.2f}", bold=True, size=9)

    ws.print_area = f"A1:F{rr+3}"
    ws.page_setup.orientation = "portrait"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1


def corr_sheet(wb, tickers, series):
    ws = wb.create_sheet("Korelacje")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "B87A00"
    dates, rets = returns_to_frame(series)
    if not dates:
        put(ws, "A1", "Brak danych.", color=RED_T)
        return
    win = 750
    R = np.array([rets[t][-win:] for t in tickers])
    C = np.corrcoef(R)

    put(ws, "A1", "Macierz korelacji (3 lata, dzienne stopy zwrotu)", bold=True, size=14)
    put(ws, "A2", "1,00 = ruch jeden do jednego · powyżej 0,8 = ta sama ekspozycja, nie dywersyfikacja", size=9, color=MUTE)

    n = len(tickers)
    for j, t in enumerate(tickers, 1):
        put(ws, f"{chr(65+j)}4", t, bold=True, size=8, color=WHITE, fill=DARK, align="center", border=True)
    for i, t in enumerate(tickers):
        r = 5 + i
        put(ws, f"A{r}", t, bold=True, size=8, border=True)
        for j in range(n):
            put(ws, f"{chr(65+j+1)}{r}", round(float(C[i][j]), 2), size=8, border=True,
                align="center", fmt="0.00")
    rng = f"B5:{chr(65+n)}{4+n}"
    ws.conditional_formatting.add(rng, ColorScaleRule(
        start_type="num", start_value=-0.2, start_color="FFFFFF",
        mid_type="num", mid_value=0.5, mid_color="FFEB9C",
        end_type="num", end_value=1.0, end_color="F4B6B0"))

    # heatmapa
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    im = ax.imshow(C, cmap="RdYlGn_r", vmin=-0.2, vmax=1)
    ax.set_xticks(range(n)); ax.set_yticks(range(n))
    ax.set_xticklabels(tickers, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(tickers, fontsize=8)
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{C[i][j]:.2f}", ha="center", va="center", fontsize=7.5,
                    color="white" if C[i][j] > 0.75 else "#1F2933")
    ax.set_title("Korelacja dziennych stóp zwrotu (3 lata)", fontsize=10, loc="left")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    p = os.path.join(CH, "korelacje.png")
    fig.savefig(p, dpi=170); plt.close(fig)
    img = XLImage(p)
    img.width, img.height = 620, 500
    ws.add_image(img, "A6" if n < 6 else f"A{7+n}")

    ws.print_area = "A1:N40"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1


# ---------- 2. backtest ----------
def _val_at(dp, ts):
    return hist._value_at(dp, ts)


def _median_upto(dp, ts, window=3 * 365.25 * 86400, min_obs=60):
    vals = [v for t, v in dp if t <= ts and t >= ts - window and isinstance(v, (int, float))]
    if len(vals) < min_obs:
        return None
    return st.median(vals)


def score_at(t, ser, ts):
    """Ocena spółki na moment ts — tylko dane dostępne wtedy."""
    def series(m):
        return [(x[0], x[1]) for x in (ser.get(m) or []) if isinstance(x[1], (int, float))]
    price = _val_at(series("price"), ts)
    eps = _val_at(series("eps_est_1y"), ts)
    rps = _val_at(series("revenue_per_share_ntm"), ts)
    med_pe = _median_upto(series("forward_pe_ratio"), ts)
    med_ps = _median_upto(series("forward_ps_ratio"), ts)
    if not price:
        return None
    d = {}
    rev = series("revenue_per_share_ntm")
    e = series("eps_est_1y")
    d["rev1y_pct"] = (scoring.series_change([(x[0], x[1]) for x in rev if x[0] <= ts], 1) or 0) * 100 if rev else None
    d["rev3y_pct"] = (scoring.series_change([(x[0], x[1]) for x in rev if x[0] <= ts], 3) or None)
    d["rev3y_pct"] = d["rev3y_pct"] * 100 if d["rev3y_pct"] is not None else None
    d["eps1y_pct"] = (scoring.series_change([(x[0], x[1]) for x in e if x[0] <= ts], 1) or None)
    d["eps1y_pct"] = d["eps1y_pct"] * 100 if d["eps1y_pct"] is not None else None
    d["eps_growth"] = _val_at(series("eps_growth"), ts)
    d["op_margin"] = _val_at(series("operating_margin"), ts)
    d["roa"] = _val_at(series("return_on_assets"), ts)
    d["current_ratio"] = _val_at(series("current_ratio"), ts)
    d["pt_upside"] = _val_at(series("price_target_upside"), ts)
    ebitda = _val_at(series("ebitda"), ts)
    nd = _val_at(series("net_debt"), ts)
    d["net_debt_ebitda"] = (nd / (ebitda * 4)) if (ebitda and ebitda > 0 and nd is not None) else None
    d["pe_rel"] = (price / eps / med_pe) if (eps and eps > 0 and med_pe) else None
    d["ps_rel"] = (price / rps / med_ps) if (rps and rps > 0 and med_ps) else None
    parts = scoring.score_parts(d)
    return scoring.total_score(parts)


def backtest_sheet(wb, tickers, grouped):
    ws = wb.create_sheet("Backtest")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "2F6DF6"

    prices = {t: price_map(grouped[t].get("price") or []) for t in tickers}
    all_dates = sorted(set.intersection(*[set(p) for p in prices.values()])) if prices else []
    if len(all_dates) < 400:
        put(ws, "A1", "Za krótka wspólna historia cen, żeby robić backtest.", color=RED_T)
        return

    start = all_dates[0] + 3 * 365 * 86400
    quarters = {}
    for d in all_dates:
        if d <= start:
            continue
        dt = datetime.fromtimestamp(d, timezone.utc)
        if dt.month in (1, 4, 7, 10):
            quarters.setdefault((dt.year, dt.month), d)   # pierwsza sesja kwartału
    rebals = [quarters[k] for k in sorted(quarters)]
    if len(rebals) < 4:
        put(ws, "A1", "Za mało okresów do backtestu.", color=RED_T)
        return

    top_curve, bench_curve, rows = [1.0], [1.0], []
    ic_list = []
    for i in range(len(rebals) - 1):
        d0, d1 = rebals[i], rebals[i + 1]
        scores = {}
        for t in tickers:
            s = score_at(t, grouped[t], d0)
            if s is not None:
                scores[t] = s
        if len(scores) < 3:
            continue
        fwd = {t: prices[t][d1] / prices[t][d0] - 1 for t in scores if prices[t].get(d0) and prices[t].get(d1)}
        if not fwd:
            continue
        k = max(1, round(len(fwd) / 3))
        ranked = sorted(fwd.items(), key=lambda x: -scores[x[0]])
        top = [x[0] for x in ranked[:k]]
        r_top = sum(fwd[t] for t in top) / len(top)
        r_bench = sum(fwd.values()) / len(fwd)
        top_curve.append(top_curve[-1] * (1 + r_top))
        bench_curve.append(bench_curve[-1] * (1 + r_bench))

        # IC: korelacja rangi oceny i przyszłej stopy zwrotu (Spearman)
        xs = [scores[t] for t in fwd]
        ys = [fwd[t] for t in fwd]
        try:
            rx = [sorted(xs).index(v) for v in xs]
            ry = [sorted(ys).index(v) for v in ys]
            mx, my = st.mean(rx), st.mean(ry)
            num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
            den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
            ic_list.append(num / den if den else None)
        except Exception:
            pass

        rows.append([datetime.fromtimestamp(d0, timezone.utc).strftime("%Y-%m-%d"),
                     datetime.fromtimestamp(d1, timezone.utc).strftime("%Y-%m-%d"),
                     round(r_top, 4), round(r_bench, 4), round(r_top - r_bench, 4),
                     ", ".join(sorted(top)[:5]), len(fwd)])

    if not rows:
        put(ws, "A1", "Backtest nie miał z czego policzyć okresów.", color=RED_T)
        return

    def stats(curve):
        n = len(curve) - 1
        if n <= 0:
            return (0, 0, 0)
        cagr = (curve[-1] ** (4 / n) - 1) if curve[-1] > 0 else -1     # kwartały
        rets_q = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
        vol = (st.stdev(rets_q) * math.sqrt(4)) if len(rets_q) > 1 else 0
        peak, mdd = curve[0], 0
        for v in curve:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        return cagr, vol, mdd

    c_top, v_top, dd_top = stats(top_curve)
    c_ben, v_ben, dd_ben = stats(bench_curve)
    hit = sum(1 for r in rows if r[4] > 0) / len(rows)

    put(ws, "A1", "Backtest scoringu — czy wysoka ocena bila rynek", bold=True, size=15)
    put(ws, "A2", f"Uniwersum: {len(tickers)} spółek z Twojej listy · rebalans kwartalny · "
                  f"long: górna tercja oceny, równa waga · benchmark: równa waga wszystkich · "
                  f"{rows[0][0]} → {rows[-1][1]}", size=9, color=MUTE)

    kpis = [("CAGR — portfel scoringowy", f"{c_top*100:+.1f}%"),
            ("CAGR — benchmark", f"{c_ben*100:+.1f}%"),
            ("Różnica (CAGR)", f"{(c_top-c_ben)*100:+.1f} pkt"),
            ("Trafność kwartałów", f"{hit*100:.0f}%"),
            ("Średni IC (ranga)", f"{st.mean([x for x in ic_list if x is not None]):+.2f}" if any(ic_list) else "—"),
            ("Maks. obsunięcie — scoring", f"{dd_top*100:.1f}%")]
    for col, wd in zip("ABCDEFG", (16, 10, 14, 11, 11, 34, 5)):
        ws.column_dimensions[col].width = wd
    r = 4
    for lab, val in kpis:
        put(ws, f"A{r}", lab)
        ws.merge_cells(f"A{r}:B{r}")
        put(ws, f"C{r}", val, bold=True, border=True, align="right")
        r += 1
    put(ws, "A11", "IC powyżej 0 = wyższa ocena zapowiadała wyższą stopę zwrotu. 0,1–0,2 to dobry wynik "
                   "w praktyce. Uwaga: uniwersum to dzisiejsza lista spółek (bias przeżywalności), a liczba "
                   "okresów jest mała — to test sanity, nie dowód.", size=9, color=MUTE, wrap=True)
    ws.merge_cells("A11:F12")

    # wykres
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    x = list(range(len(top_curve)))
    ax.plot(x, top_curve, color="#2F6DF6", lw=2.2, label="górna tercja oceny")
    ax.plot(x, bench_curve, color="#6B7280", lw=1.8, ls=(0, (4, 2)), label="benchmark (równa waga)")
    lab = [datetime.fromtimestamp(r, timezone.utc).strftime("%m-%y") for r in rebals[:len(top_curve)]]
    ax.set_xticks(x)
    ax.set_xticklabels(lab, fontsize=8)
    ax.grid(axis="y", color="#E4E7EB")
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0, labelsize=8, colors="#6B7280")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_title("Wartość 1 zł — scoring vs benchmark", fontsize=9, loc="left")
    fig.tight_layout()
    p = os.path.join(CH, "backtest.png")
    fig.savefig(p, dpi=170); plt.close(fig)
    img = XLImage(p); img.width, img.height = 700, 330
    ws.add_image(img, "A14")

    r0 = 33
    head_row(ws, r0, ["Od", "Do", "Scoring", "Benchmark", "Różnica", "Spółki w long", "N"])
    rr = r0 + 1
    for row in rows:
        for j, v in enumerate(row, 1):
            cl = ws.cell(row=rr, column=j, value=v)
            cl.font = Font(size=9)
            cl.border = BOX
            if j in (3, 4, 5):
                cl.number_format = "+0.0%;-0.0%"
        rr += 1
    pass

    ws.print_area = f"A1:G{rr}"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


# ---------- 3. kalendarz ----------
def calendar_sheet(wb, tickers, m, weights=None):
    ws = wb.create_sheet("Kalendarz")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "1C8A5A"

    today = date.today()
    events = []
    for t in tickers:
        cal = (m.get(t, {}) or {}).get("calendar") or {}
        for key, label in (("Earnings Date", "Wyniki"), ("Ex-Dividend Date", "Ex-dividend"),
                           ("Dividend Date", "Dywidenda")):
            v = cal.get(key)
            vals = v if isinstance(v, (list, tuple)) else [v] if v else []
            for item in vals:
                if isinstance(item, datetime):
                    d = item.date()
                elif isinstance(item, date):
                    d = item
                elif isinstance(item, str):
                    try:
                        d = datetime.fromisoformat(item).date()
                    except Exception:
                        continue
                else:
                    continue
                if d >= today:
                    events.append([d, t, label, (d - today).days])

    events.sort(key=lambda x: (x[0], x[1]))
    put(ws, "A1", "Kalendarz katalizatorów", bold=True, size=15)
    put(ws, "A2", f"Wyniki, ex-dividend i dywidendy dla {len(tickers)} spółek · stan na {today} · źródło: Yahoo",
        size=9, color=MUTE)
    head_row(ws, 4, ["Data", "Za dni", "Ticker", "Zdarzenie", "Waga"], widths=[12, 9, 11, 16, 10])

    r = 5
    wmap = dict(zip(tickers, weights)) if weights else {}
    for d, t, label, days in events:
        put(ws, f"A{r}", d.strftime("%Y-%m-%d"), border=True)
        put(ws, f"B{r}", days, border=True, align="right")
        put(ws, f"C{r}", t, bold=True, border=True)
        put(ws, f"D{r}", label, border=True)
        put(ws, f"E{r}", float(wmap.get(t, 0)), fmt="0.0%", border=True, align="center")
        if 0 <= days <= 14:
            ws.cell(row=r, column=2).fill = PatternFill("solid", fgColor="FFEB9C")
        r += 1
    if not events:
        put(ws, "A5", "Brak nadchodzących zdarzeń w danych Yahoo dla tej listy.", color=MUTE)

    put(ws, f"A{r+2}", "Konferencje i dni inwestora nie są dostępne w darmowym źródle — "
                       "uzupełnij ręcznie w kolumnie D, jeśli ich pilnujesz.", size=8, color=MUTE, wrap=True)
    ws.merge_cells(f"A{r+2}:E{r+3}")

    ws.print_area = f"A1:E{r+3}"
    ws.page_setup.orientation = "portrait"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


# ---------- main ----------
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tickers = [a.upper() for a in args] if args else load_tickers()
    if not tickers:
        raise SystemExit("Brak listy. Uzupełnij Portfel.xlsx albo podaj tickery w argumencie.")
    tickers = list(dict.fromkeys(tickers))
    print(f"Pobieram {len(tickers)} spółek ...")
    grouped = fetch(tickers)
    tickers = [t for t in tickers if t in grouped and grouped[t].get("price")]
    if len(tickers) < 2:
        raise SystemExit("Za mało spółek z danymi, żeby policzyć ryzyko portfela.")

    print("Sektory i kalendarz (Yahoo) ...")
    md = meta(tickers)
    sectors = {t: md.get(t, {}).get("sector", "—") for t in tickers}

    series = {t: grouped[t]["price"] for t in tickers}
    w = [1 / len(tickers)] * len(tickers)

    wb = Workbook()
    wb.remove(wb.active)
    sheet_risk(wb, tickers, series, w, {"sectors": sectors})
    corr_sheet(wb, tickers, series)
    backtest_sheet(wb, tickers, grouped)
    calendar_sheet(wb, tickers, md, w)

    wb.calculation.fullCalcOnLoad = True
    wb.save(OUT)
    print(f"  -> {OUT}")

    if "--pdf" in sys.argv:
        try:
            import pdf_export
            pdf_export.export(OUT)
        except Exception as e:
            print(f"  PDF nie wyszedł: {e}")


if __name__ == "__main__":
    main()
