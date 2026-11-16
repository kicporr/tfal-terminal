#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cena docelowa - automat dla arkusza Trading For a Living.

Wpisujesz ticker, skrypt pobiera dane (yfinance), liczy mnozniki i buduje
arkusz Cena-docelowa-auto.xlsx z gotowymi formulami + dziennikiem rewizji.

Uzycie:
    python pt.py MU
    python pt.py ALE.WA --open
"""
import sys, os, json, math, statistics as st
from datetime import datetime, timedelta, date, timezone

import yfinance as yf
import pandas as pd
import scrab_client
import history as hist
import charts
from openpyxl.drawing.image import Image as XLImage
from openpyxl import Workbook, load_workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Cena-docelowa-auto.xlsx")
LOG = os.path.join(HERE, "pt_log.json")

# ---------- styl ----------
INK = "1F2933"; MUTE = "6B7280"
YELLOW = "FFF2CC"; GREY = "F2F4F7"; DARK = "1F2933"; WHITE = "FFFFFF"
GREEN_F = "C6EFCE"; GREEN_T = "0B6B3A"; RED_F = "FFC7CE"; RED_T = "9C1C1C"
THIN = Side(style="thin", color="D0D5DD")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def clean(x):
    if x is None:
        return None
    try:
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return None
    except Exception:
        pass
    return x


def fnum(x, nd=2):
    x = clean(x)
    return None if x is None else round(float(x), nd)


# ---------- dane ----------
def fetch_yahoo(ticker):
    t = yf.Ticker(ticker)
    info = t.info or {}
    if not info.get("symbol") and not info.get("shortName"):
        raise SystemExit(f"Nie znalazlem tickera '{ticker}'. Sprawdz symbol (np. MU, AAPL, ALE.WA).")

    hist = t.history(period="12y", auto_adjust=False)
    if hist is None or hist.empty:
        raise SystemExit("Brak historii cen dla tego tickera.")
    px = hist["Close"].copy()
    px.index = px.index.tz_localize(None)

    price = clean(info.get("currentPrice")) or clean(info.get("regularMarketPrice")) or float(px.iloc[-1])

    # konsensus 12 miesiecy do przodu
    fwd_eps = None
    try:
        ee = t.earnings_estimate
        if ee is not None and "+1y" in ee.index:
            fwd_eps = clean(ee.loc["+1y", "avg"])
        if fwd_eps is None and "0y" in ee.index:
            fwd_eps = clean(ee.loc["0y", "avg"])
    except Exception:
        pass
    if fwd_eps is None:
        fwd_eps = clean(info.get("forwardEps"))

    fwd_rev_total = None
    try:
        re_ = t.revenue_estimate
        if re_ is not None and "+1y" in re_.index:
            fwd_rev_total = clean(re_.loc["+1y", "avg"])
        elif re_ is not None and "0y" in re_.index:
            fwd_rev_total = clean(re_.loc["0y", "avg"])
    except Exception:
        pass

    # bilans roczny
    fin = t.income_stmt
    eps_by_fy, rev_by_fy, sh_by_fy = {}, {}, {}
    if fin is not None and not fin.empty:
        for col in fin.columns:
            try:
                if "Diluted EPS" in fin.index: eps_by_fy[col] = clean(fin.loc["Diluted EPS", col])
                if "Total Revenue" in fin.index: rev_by_fy[col] = clean(fin.loc["Total Revenue", col])
                if "Diluted Average Shares" in fin.index: sh_by_fy[col] = clean(fin.loc["Diluted Average Shares", col])
            except Exception:
                continue

    shares = None
    if sh_by_fy:
        shares = sh_by_fy[max(sh_by_fy.keys())]
    if not shares:
        shares = clean(info.get("sharesOutstanding"))

    fwd_rev_ps = (fwd_rev_total / shares) if (fwd_rev_total and shares) else None

    # proxy historycznych mnoznikow forward:
    # cena na 12 mies. przed koncem roku obrotowego / wynik wypracowany w tym roku
    # lata, w ktorych EPS byl skrajnie niski (dolek cyklu), sa pomijane - inaczej
    # mnoznik eksploduje i psuje zarowno mediane, jak i wariant optymistyczny
    pe_series, ps_series = [], []
    fys = sorted([d for d in eps_by_fy if eps_by_fy.get(d) is not None])
    pos_eps = [float(eps_by_fy[d]) for d in fys if eps_by_fy.get(d) and float(eps_by_fy[d]) > 0]
    med_pos = st.median(pos_eps) if pos_eps else None
    for fy in fys:
        anchor = pd.Timestamp(fy) - pd.Timedelta(days=365)
        w = px.loc[:anchor]
        if w.empty:
            continue
        p = float(w.iloc[-1])
        e = eps_by_fy.get(fy); r = rev_by_fy.get(fy); s = sh_by_fy.get(fy)
        if e and float(e) > 0 and (med_pos is None or float(e) >= 0.25 * med_pos):
            v = p / float(e)
            if 0.5 <= v <= 100:
                pe_series.append(v)
        if r and s and float(s) > 0:
            v = p / (float(r) / float(s))
            if 0.2 <= v <= 50:
                ps_series.append(v)

    def band(vals):
        if not vals:
            return None, None, None
        s = sorted(float(v) for v in vals)
        if len(s) < 3:
            return st.median(s), max(s), min(s)

        def pct(q):
            k = (len(s) - 1) * q
            f, c = math.floor(k), math.ceil(k)
            return s[f] if f == c else s[f] * (c - k) + s[c] * (k - f)

        return pct(0.5), pct(0.95), pct(0.05)

    med_pe, max_pe, min_pe = band(pe_series)
    med_ps, max_ps, _ = band(ps_series)

    cons = {}
    try:
        cons = t.analyst_price_targets or {}
    except Exception:
        pass

    return dict(
        ticker=ticker.upper(), name=info.get("shortName") or info.get("longName") or ticker.upper(),
        exchange=info.get("fullExchangeName") or info.get("exchange") or "",
        currency=info.get("currency") or "", price=price,
        fwd_eps=fwd_eps, fwd_rev_ps=fwd_rev_ps,
        med_pe=med_pe, max_pe=max_pe, min_pe=min_pe, med_ps=med_ps, max_ps=max_ps,
        n_pe=len(pe_series), n_ps=len(ps_series),
        cons_median=clean(cons.get("median")), cons_mean=clean(cons.get("mean")),
        cons_low=clean(cons.get("low")), cons_high=clean(cons.get("high")),
    )


def fetch_meta(ticker):
    """Nazwa, gielda, waluta i konsensus - z Yahoo, tylko jako uzupelnienie."""
    try:
        info = yf.Ticker(ticker).info or {}
        cons = {}
        try:
            cons = yf.Ticker(ticker).analyst_price_targets or {}
        except Exception:
            pass
        return dict(name=info.get("shortName") or info.get("longName") or ticker.upper(),
                    exchange=info.get("fullExchangeName") or info.get("exchange") or "",
                    currency=info.get("currency") or "",
                    cons_median=clean(cons.get("median")))
    except Exception:
        return dict(name=ticker.upper(), exchange="", currency="", cons_median=None)


def fetch(ticker):
    """Scrab (FactSet) jako zrodlo glowne, Yahoo jako fallback."""
    meta = fetch_meta(ticker)
    try:
        d = scrab_client.fetch(ticker)
        d.update({k: v for k, v in meta.items() if v})
        d["source"] = "Scrab (FactSet)"
        d["window"] = f"{d['n_pe']} sesji P/E · {d['n_ps']} sesji P/S"
        d["scrab_err"] = None
        return d
    except Exception as e:
        d = fetch_yahoo(ticker)
        d.update({k: v for k, v in meta.items() if v})
        d["source"] = "Yahoo (proxy)"
        d["window"] = f"proxy z {d['n_pe']} lat obrotowych"
        d["scrab_err"] = str(e)[:120]
        return d


def calc(d):
    """Wyniki liczone w Pythonie - do dziennika i do kontroli."""
    pe_m, pe_x = d["med_pe"], d["max_pe"]
    ps_m, ps_x = d["med_ps"], d["max_ps"]
    eps, rps, p = d["fwd_eps"], d["fwd_rev_ps"], d["price"]
    out = {}
    out["pt_pe_c"] = pe_m * eps if (pe_m and eps) else None
    out["pt_pe_o"] = pe_x * eps if (pe_x and eps) else None
    out["pt_ps_c"] = ps_m * rps if (ps_m and rps) else None
    out["pt_ps_o"] = ps_x * rps if (ps_x and rps) else None
    cs = [v for v in (out["pt_pe_c"], out["pt_ps_c"]) if v]
    os_ = [v for v in (out["pt_pe_o"], out["pt_ps_o"]) if v]
    out["pt_c"] = sum(cs) / len(cs) if cs else None
    out["pt_o"] = sum(os_) / len(os_) if os_ else None
    out["pot_c"] = (out["pt_c"] / p - 1) if (out["pt_c"] and p) else None
    out["pot_o"] = (out["pt_o"] / p - 1) if (out["pt_o"] and p) else None
    return out


# ---------- arkusz ----------
CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_charts")
os.makedirs(CHART_DIR, exist_ok=True)


def insert_image(ws, path, cell="A2", width_cm=25.0):
    img = XLImage(path)
    w = width_cm * 37.795
    h = w * (img.height / img.width)
    img.width, img.height = w, h
    ws.add_image(img, cell)


def add_chart_sheet(wb, name, series, med, mx, mn, ticker, label, hexcolor):
    """Dane w kolumnach S:W (poza obszarem wydruku), wykres na A2."""
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = hexcolor

    C0 = 19  # kolumna S
    heads = ["Data", "Mnożnik", "Mediana", "Max", "Min"]
    for i, h in enumerate(heads):
        cl = ws.cell(row=1, column=C0 + i, value=h)
        cl.font = Font(bold=True, size=9, color=WHITE)
        cl.fill = PatternFill("solid", fgColor=DARK)
    for r, (ts, val) in enumerate(series, start=2):
        ws.cell(row=r, column=C0, value=datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"))
        ws.cell(row=r, column=C0 + 1, value=val).number_format = "0.00"
        ws.cell(row=r, column=C0 + 2, value=med).number_format = "0.00"
        ws.cell(row=r, column=C0 + 3, value=mx).number_format = "0.00"
        ws.cell(row=r, column=C0 + 4, value=mn).number_format = "0.00"

    png = os.path.join(CHART_DIR, f"{name.replace(' ', '_')}.png")
    try:
        made = charts.multiple_chart(series, med, mx, mn, ticker, label, png)
        if made:
            insert_image(ws, png, width_cm=26.5)
    except Exception as e:
        print("  (wykres", name, "pominięty:", e, ")")
    for col in ("S", "T"):
        ws.column_dimensions[col].width = 11
    ws.print_area = "A1:P34"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    for m in ("leftMargin", "rightMargin"):
        setattr(ws.page_setup, m, 0.25)
    for m in ("topMargin", "bottomMargin"):
        setattr(ws.page_setup, m, 0.3)
    return ws


def _dump_rows(ws, heads, rows, C0=19):
    """Zapisuje dane poza obszarem wydruku (od kolumny S)."""
    for i, h in enumerate(heads):
        cl = ws.cell(row=1, column=C0 + i, value=h)
        cl.font = Font(bold=True, size=9, color=WHITE)
        cl.fill = PatternFill("solid", fgColor=DARK)
    for r, row in enumerate(rows, start=2):
        for i, v in enumerate(row):
            cell = ws.cell(row=r, column=C0 + i, value=v)
            if isinstance(v, float):
                cell.number_format = "#,##0.00"
    for col in ("S", "T", "U", "V", "W", "X"):
        ws.column_dimensions[col].width = 11


def add_pt_history_sheet(wb, rows, ticker):
    """Historia ceny docelowej: wykres cena vs PT konserwatywna vs optymistyczna."""
    if not rows:
        return
    ws = wb.create_sheet("Historia PT")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "2F6DF6"
    data = [(datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
             price, pt_c, pt_o, med_pe, (pt_c / price - 1) if price else None)
            for ts, price, pt_c, pt_o, med_pe, _eps in rows]
    _dump_rows(ws, ["Data", "Cena", "PT kons.", "PT opt.", "Mediana P/E", "Potencjał"],
               data, C0=19)

    png = os.path.join(CHART_DIR, "historia_pt.png")
    try:
        charts.pt_history_chart(rows, ticker, png)
        insert_image(ws, png, width_cm=26.5)
    except Exception as e:
        print("  (wykres historii pominięty:", e, ")")

    ws.print_area = "A1:P34"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    for m in ("leftMargin", "rightMargin"):
        setattr(ws.page_setup, m, 0.25)
    for m in ("topMargin", "bottomMargin"):
        setattr(ws.page_setup, m, 0.3)


def add_potential_sheet(wb, rows, ticker):
    """Potencjał PT konserwatywnej do kursu w czasie."""
    if not rows:
        return
    ws = wb.create_sheet("Potencjał")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "1C8A5A"
    data = [(datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
             (pt_c / price - 1) if price else None)
            for ts, price, pt_c, pt_o, med_pe, _eps in rows]
    _dump_rows(ws, ["Data", "Potencjał"], data, C0=19)

    png = os.path.join(CHART_DIR, "potencjal.png")
    try:
        charts.potential_chart(rows, ticker, png)
        insert_image(ws, png, width_cm=26.5)
    except Exception as e:
        print("  (wykres potencjału pominięty:", e, ")")

    ws.print_area = "A1:P26"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    for m in ("leftMargin", "rightMargin"):
        setattr(ws.page_setup, m, 0.25)
    for m in ("topMargin", "bottomMargin"):
        setattr(ws.page_setup, m, 0.3)


def build(d, c, log_rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Cena docelowa"
    ws.sheet_view.showGridLines = False
    widths = {"A": 46, "B": 17, "C": 27, "D": 17, "E": 17, "F": 16}
    for k, v in widths.items():
        ws.column_dimensions[k].width = v

    def put(cell, value, bold=False, size=11, color=INK, fill=None, fmt=None,
            border=False, wrap=False, align=None):
        cl = ws[cell]
        cl.value = value
        cl.font = Font(name="Calibri", size=size, bold=bold, color=color)
        if fill:
            cl.fill = PatternFill("solid", fgColor=fill)
        if fmt:
            cl.number_format = fmt
        if border:
            cl.border = BOX
        if wrap:
            cl.alignment = Alignment(wrap_text=True, vertical="top")
        cl.alignment = Alignment(horizontal=(align or "left"), vertical="center",
                                 wrap_text=wrap)
        return cl

    put("A1", "Cena docelowa", bold=True, size=20, color=WHITE)
    ws["A1"].fill = PatternFill("solid", fgColor=DARK)
    for col in "BCDEF":
        ws[f"{col}1"].fill = PatternFill("solid", fgColor=DARK)
    ws.row_dimensions[1].height = 34
    ws.sheet_properties.tabColor = "1F2933"
    put("A2", "Trading For a Living · arkusz zautomatyzowany  |  wpisz ticker w B6 → uruchom Odswiez.bat → reszta liczy się sama", size=10, color=MUTE)
    warn = ""
    if d.get("scrab_err"):
        warn = f"  ⚠ Scrab niedostępny ({d['scrab_err']}) — użyto proxy Yahoo"
    put("A3", f"Ostatnia aktualizacja: {datetime.now():%Y-%m-%d %H:%M}  ·  źródło: {d['source']}"
              f"  ·  okno: {d.get('window_txt') or d['window']}{warn}", size=10, color=MUTE)

    for cell, txt in (("A5", "DANE WEJŚCIOWE"), ("B5", "Wartość"), ("C5", "Skąd")):
        put(cell, txt, bold=True, size=10, fill=DARK, color=WHITE)
    ws.row_dimensions[5].height = 20
    for r, lab, src in [
        (6, "Spółka (ticker)", "wpisz ticker"),
        (7, "Nazwa spółki", "auto"),
        (8, "Aktualny kurs", "ostatnie zamknięcie (Scrab)"),
        (9, "Prognoza EPS na 12 mies.", "konsensus analityków"),
        (10, "Prognoza sprzedaży na akcję, 12 mies.", "konsensus ÷ liczba akcji"),
        (11, "Mediana P/E (3 lata)", "mediana dziennych obserwacji"),
        (12, "Maksimum P/E (3 lata)", "najwyższy mnożnik z 3Y"),
        (13, "Minimum P/E (3 lata)", "najniższy mnożnik z 3Y"),
        (14, "Mediana P/S (3 lata)", "mediana dziennych obserwacji"),
        (15, "Maksimum P/S (3 lata)", "najwyższy mnożnik z 3Y"),
    ]:
        put(f"A{r}", lab, border=True)
        put(f"C{r}", src, size=9, color=MUTE, wrap=True)

    for r in range(6, 16):
        ws.row_dimensions[r].height = 18
    ws.row_dimensions[7].height = 32
    put("B6", d["ticker"], bold=True, fill=YELLOW, border=True)
    put("B7", f"{d['name']}\n{d['exchange']}", size=9, border=True, wrap=True)
    put("B8", fnum(d["price"]), fmt='#,##0.00', border=True, fill=GREY)
    put("B9", fnum(d["fwd_eps"]), fmt='#,##0.00', border=True, fill=GREY)
    put("B10", fnum(d["fwd_rev_ps"]), fmt='#,##0.00', border=True, fill=GREY)
    put("B11", fnum(d["med_pe"]), fmt='0.0', border=True, fill=GREY)
    put("B12", fnum(d["max_pe"]), fmt='0.0', border=True, fill=GREY)
    put("B13", fnum(d["min_pe"]), fmt='0.0', border=True, fill=GREY)
    put("B14", fnum(d["med_ps"]), fmt='0.0', border=True, fill=GREY)
    put("B15", fnum(d["max_ps"]), fmt='0.0', border=True, fill=GREY)

    dist = ""
    if d.get("pe_dist") and d.get("ps_dist"):
        pe, ps = d["pe_dist"], d["ps_dist"]
        dist = (f"Rozkład z próby — P/E: p25 {pe['p25']:.1f} · mediana {pe['med']:.1f} · p75 {pe['p75']:.1f} · p90 {pe['p90']:.1f}"
                f"   |   P/S: p25 {ps['p25']:.2f} · mediana {ps['med']:.2f} · p75 {ps['p75']:.2f} · p90 {ps['p90']:.2f}")
    put("A16", dist, size=9, color=MUTE)

    put("A17", "NADPISANIE (opcjonalnie — własne liczby ze Scraba)", bold=True, size=10, color=MUTE)
    put("A18", "Mediana P/E (Scrab) → nadpisuje B11", size=10)
    put("A19", "Max P/E (Scrab) → nadpisuje B12", size=10)
    put("A20", "Mediana P/S (Scrab) → nadpisuje B14", size=10)
    put("A21", "Max P/S (Scrab) → nadpisuje B15", size=10)
    for r in (18, 19, 20, 21):
        put(f"B{r}", None, fill=YELLOW, border=True, fmt='0.0')
    put("C18", "zostaw puste = użyj proxy", size=9, color=MUTE)

    PE_M = 'IF($B$18<>"",$B$18,$B$11)'
    PE_X = 'IF($B$19<>"",$B$19,$B$12)'
    PS_M = 'IF($B$20<>"",$B$20,$B$14)'
    PS_X = 'IF($B$21<>"",$B$21,$B$15)'

    put("A23", "WYNIK", bold=True, size=10, fill=DARK, color=WHITE)
    ws.row_dimensions[23].height = 20
    for col, lab in zip("BCDE", ["Konserwatywna", "Optymistyczna", "Potencjał (kons.)", "Potencjał (opt.)"]):
        put(f"{col}23", lab, bold=True, size=9, fill=DARK, color=WHITE, align="center", wrap=True)
    ws.row_dimensions[23].height = 30

    rows = [
        (24, "Cena docelowa z P/E  (mnożnik × prognoza EPS)",
         [f"={PE_M}*$B$9", f"={PE_X}*$B$9", "=IFERROR($B24/$B$8-1,\"\")", "=IFERROR($C24/$B$8-1,\"\")"]),
        (25, "Cena docelowa z P/S  (mnożnik × prognoza sprzedaży)",
         [f"={PS_M}*$B$10", f"={PS_X}*$B$10", "=IFERROR($B25/$B$8-1,\"\")", "=IFERROR($C25/$B$8-1,\"\")"]),
        (26, "CENA DOCELOWA 12M (średnia P/E i P/S)",
         ["=IFERROR(AVERAGE($B24:$B25),\"\")", "=IFERROR(AVERAGE($C24:$C25),\"\")",
          "=IFERROR($B26/$B$8-1,\"\")", "=IFERROR($C26/$B$8-1,\"\")"]),
    ]
    for r, lab, vals in rows:
        bold = (r == 26)
        put(f"A{r}", lab, bold=bold, border=True)
        for col, v in zip("BCDE", vals):
            fmt = '#,##0.00' if col in "BC" else '+0.0%;-0.0%'
            put(f"{col}{r}", v, bold=bold, fmt=fmt, border=True,
                fill=(GREY if r == 26 else None))
    ws.row_dimensions[26].height = 22

    ws.conditional_formatting.add("D24:E26", CellIsRule(
        operator="greaterThan", formula=["0.15"],
        fill=PatternFill("solid", bgColor=GREEN_F), font=Font(color=GREEN_T, bold=True)))
    ws.conditional_formatting.add("D24:E26", CellIsRule(
        operator="lessThan", formula=["0"],
        fill=PatternFill("solid", bgColor=RED_F), font=Font(color=RED_T, bold=True)))

    put("A28", "Konsensus analityków (mediana)", size=10, color=MUTE)
    put("B28", fnum(d["cons_median"]), fmt='#,##0.00', size=10, color=MUTE)
    put("C28", "=IFERROR(B28/$B$8-1,\"\")", fmt='+0.0%;-0.0%', size=10, color=MUTE)

    # --- siatki wrażliwości ---
    def grid(title_row, note, unit, mult_cell, base_label, steps):
        put(f"A{title_row}", f"Siatka wrażliwości: {base_label}", bold=True, size=11)
        put(f"A{title_row+1}", note, size=9, color=MUTE)
        head = title_row + 2
        put(f"A{head}", f"mnożnik ↓ / prognoza →", bold=True, size=9, fill=GREY, align="right", border=True)
        dels = [-0.10, -0.05, 0, 0.05, 0.10]
        for i, dv in enumerate(dels):
            put(f"{get_column_letter(2+i)}{head}", dv, bold=True, size=9, fill=GREY, fmt='+0%;-0%;0%', align="center")
        first = head + 1
        for j, mv in enumerate(steps):
            r = first + j
            put(f"A{r}", mv, bold=True, size=9, fill=GREY, fmt='+0.0;-0.0;0.0', align="right", border=True)
            for i, dv in enumerate(dels):
                col = get_column_letter(2 + i)
                put(f"{col}{r}", f"=({mult_cell}{mv:+g})*{unit}*(1+{dv})", fmt='#,##0', size=10, border=True, align="center")
        rng = f"B{first}:F{first+len(steps)-1}"
        ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThan", formula=["$B$8"],
                                                      fill=PatternFill("solid", bgColor=GREEN_F), font=Font(color=GREEN_T, size=10)))
        ws.conditional_formatting.add(rng, CellIsRule(operator="lessThanOrEqual", formula=["$B$8"],
                                                      fill=PatternFill("solid", bgColor=RED_F), font=Font(color=RED_T, size=10)))
        return first + len(steps) + 1

    nxt = grid(30, "Mnożnik P/E przesunięty o ±1 i ±2 pkt, prognoza EPS o ±5% i ±10%.", "$B$9", PE_M, "P/E", [-2, -1, 0, 1, 2])
    nxt = grid(nxt + 1, "Mnożnik P/S przesunięty o ±0,25 i ±0,50, prognoza sprzedaży o ±5% i ±10%.", "$B$10", PS_M, "P/S", [-0.5, -0.25, 0, 0.25, 0.5])

    ws.row_dimensions[nxt+1].height = 30
    ws.row_dimensions[nxt+2].height = 30
    put(f"A{nxt+1}", "To jest materiał edukacyjny, nie rekomendacja inwestycyjna w rozumieniu rozporządzenia MAR. "
                     "Cena docelowa podana przy założeniach, które sam ustawiasz — to nie gwarancja kursu akcji.",
        size=9, color=MUTE, wrap=True)
    ws.merge_cells(f"A{nxt+1}:F{nxt+2}")
    put(f"A{nxt+3}", "© Tomasz Trela · tradingforaliving.pl · automatyzacja arkusza", size=9, color=MUTE)

    # --- Rewizje ---
    ws2 = wb.create_sheet("Rewizje")
    ws2.sheet_view.showGridLines = False
    ws2.sheet_properties.tabColor = "6B7280"
    ws2["A1"] = "Dziennik rewizji"
    ws2["A1"].font = Font(size=16, bold=True, color=INK)
    ws2["A2"] = "Każde uruchomienie skryptu dopisuje wiersz. Pokazuje, czy cena docelowa zmieniła się z powodu rewizji prognozy, czy ze zmiany mnożnika."
    ws2["A2"].font = Font(size=9, color=MUTE)
    heads = ["Data", "Kurs", "EPS 12m", "Sprzedaż/akcję", "Med. P/E", "Max P/E", "Med. P/S", "Max P/S",
             "PT z P/E", "PT z P/S", "PT KONS.", "PT OPT.", "Zmiana PT m/m", "z tego: rewizja EPS",
             "z tego: mnożnik P/E", "Potencjał do PT"]
    for i, h in enumerate(heads, 1):
        cl = ws2.cell(row=4, column=i, value=h)
        cl.font = Font(bold=True, size=9, color=WHITE)
        cl.fill = PatternFill("solid", fgColor=DARK)
        cl.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws2.row_dimensions[4].height = 30
    widths2 = [11, 10, 10, 13, 9, 9, 9, 9, 10, 10, 11, 11, 12, 14, 14, 13]
    for i, w in enumerate(widths2, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    for j, row in enumerate(log_rows):
        r = 5 + j
        for i, v in enumerate(row, 1):
            cl = ws2.cell(row=r, column=i, value=v)
            cl.font = Font(size=9, color=INK)
            cl.border = BOX
            if i == 1:
                cl.number_format = "yyyy-mm-dd"
            elif i >= 5 and i <= 12:
                cl.number_format = "#,##0.00"
            elif i >= 13:
                cl.number_format = "+0.0%;-0.0%"

    ws2.print_area = "A1:P30"
    ws2.sheet_properties.pageSetUpPr.fitToPage = True
    ws2.page_setup.fitToWidth = 1
    ws2.page_setup.fitToHeight = 0

    # --- Założenia ---
    ws3 = wb.create_sheet("Założenia")
    ws3.sheet_view.showGridLines = False
    ws3.sheet_properties.tabColor = "9AA5B1"
    ws3.column_dimensions["A"].width = 26
    ws3.column_dimensions["B"].width = 98
    ws3["A1"] = "Założenia i metoda"
    ws3["A1"].font = Font(size=16, bold=True, color=INK)
    ws3.print_area = "A1:B22"
    ws3.sheet_properties.pageSetUpPr.fitToPage = True
    ws3.page_setup.fitToWidth = 1
    ws3.page_setup.fitToHeight = 1
    ws3["A2"] = "Jak powstają price targety: inżynieria wsteczna wskaźnika wyceny."
    ws3["A2"].font = Font(size=10, color=MUTE)
    lines = [
        ("Wzór podstawowy", "P/E = Cena ÷ Zysk na akcję. Odwrócony: Cena = P/E × Zysk na akcję. Podstawiamy historyczny mnożnik i prognozę zysku na kolejne 12 miesięcy."),
        ("Konserwatywna", "Mediana historycznego mnożnika × prognoza na 12 mies. (dla P/S: mediana P/S × prognoza sprzedaży na akcję)."),
        ("Optymistyczna", "Maksimum historycznego mnożnika (95. percentyl) × ta sama prognoza."),
        ("Cena docelowa", "Średnia z metody P/E i P/S. Jeśli spółka nie ma zysku, wpisz EPS = 0 i czytaj tylko wiersz P/S."),
        ("", ""),
        ("DANE — SKĄD", ""),
        ("Kurs", "Scrab (FactSet) → MasterChart → Price; ostatnie zamknięcie, dane odświeżane 1–7 UTC."),
        ("EPS / sprzedaż 12m", "Scrab → MasterChart → EPS Estimates for Next Fiscal Year oraz Revenue Per Share Estimates for Next Fiscal Year (konsensus FactSet)."),
        ("Mediana / max / min P/E", "Okno 3 lata (zakres wykresu 3Y), obserwacje dzienne: mediana = mediana dziennych mnożników (ta sama, którą Scrab rysuje jako linię Median), max i min = najwyższa i najniższa wartość w tym oknie."),
        ("", ""),
        ("OGRANICZENIA", ""),
        ("Okno 3 lata", "API Scraba zwraca do 5 lat — skrypt odcina ostatnie 3. Jeśli chcesz inny zakres, poproś o zmianę; blok B18–B21 pozwala wpisać własne mnożniki."),
        ("Spółki bez zysku", "Ujemny EPS → wiersze P/E są puste, czyta się tylko P/S."),
        ("Waluta", "Liczby w walucie notowania. Dla GPW to PLN, dla ADR-ów USD."),
        ("Aktualizacja", "Prognozy są rewidowane na bieżąco — uruchamiaj skrypt raz w miesiącu; dziennik w zakładce Rewizje pokaże kierunek zmian."),
        ("", ""),
        ("ZASTRZEŻENIA", ""),
        ("Skuteczność", "Nawet najlepsi analitycy z rankingu TipRanks mają skuteczność rzędu 70%. Cena docelowa to wycena przy określonych założeniach, a nie prognoza kursu."),
        ("MAR", "Materiał edukacyjny. Nie stanowi rekomendacji inwestycyjnej w rozumieniu rozporządzenia MAR."),
    ]
    r = 4
    for k, v in lines:
        if k:
            ws3.cell(row=r, column=1, value=k).font = Font(bold=True, size=10, color=INK)
        if v:
            cl = ws3.cell(row=r, column=2, value=v)
            cl.font = Font(size=10, color=INK)
            cl.alignment = Alignment(wrap_text=True, vertical="top")
            ws3.row_dimensions[r].height = max(15, 15 * (len(v) // 105 + 1))
        r += 1

    if d.get("pe_series"):
        add_chart_sheet(wb, "PE 3Y", d["pe_series"], d["med_pe"], d["max_pe"], d["min_pe"],
                        d["ticker"], "P/E forward (1Y)", "2F6DF6")
    if d.get("ps_series"):
        add_chart_sheet(wb, "PS 3Y", d["ps_series"], d["med_ps"], d["max_ps"], d.get("min_ps"),
                        d["ticker"], "P/S forward (1Y)", "1C8A5A")

    try:
        hist_rows = hist.build(d.get("raw") or {})
        add_pt_history_sheet(wb, hist_rows, d["ticker"])
        add_potential_sheet(wb, hist_rows, d["ticker"])
    except Exception as e:
        print("  (historia PT pominięta:", e, ")")

    ws.print_area = f"A1:F{nxt+3}"
    ws.page_setup.orientation = "portrait"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    for w in wb.worksheets:
        w.print_options.horizontalCentered = True
        w.oddFooter.left.text = "Trading For a Living · model ceny docelowej"
        w.oddFooter.left.size = 8
        w.oddFooter.left.color = "808080"
        w.oddFooter.center.text = "&P / &N"
        w.oddFooter.center.size = 8
        w.oddFooter.center.color = "808080"
        w.oddFooter.right.text = "&A · &D"
        w.oddFooter.right.size = 8
        w.oddFooter.right.color = "808080"

    wb.calculation.fullCalcOnLoad = True
    wb.save(OUT)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        guess = None
        if os.path.exists(OUT):
            try:
                guess = load_workbook(OUT)["Cena docelowa"]["B6"].value
            except Exception:
                guess = None
        try:
            ans = input(f"Ticker [{guess}]: " if guess else "Ticker: ").strip()
        except EOFError:
            ans = ""
        args = [ans or guess or ""]
        if not args[0]:
            raise SystemExit("Podaj ticker, np. MU albo ALE.WA")
    ticker = args[0].strip()
    print(f"Pobieram dane dla {ticker} ...")
    d = fetch(ticker)
    c = calc(d)

    log = {}
    if os.path.exists(LOG):
        try:
            log = json.load(open(LOG, encoding="utf-8"))
        except Exception:
            log = {}
    rows = log.get(d["ticker"], [])

    today = date.today()
    prev = rows[-1] if rows else None
    delta = pot = None
    dec_eps = dec_pe = None
    if prev:
        if c["pt_c"] and prev[10]:
            delta = c["pt_c"] / prev[10] - 1
            dec_eps = (d["fwd_eps"] - prev[2]) * prev[4] if (d["fwd_eps"] and prev[2] and prev[4]) else None
            dec_pe = (d["med_pe"] - prev[4]) * d["fwd_eps"] if (d["med_pe"] and prev[4] and d["fwd_eps"]) else None
    if c["pt_c"] and d["price"]:
        pot = c["pt_c"] / d["price"] - 1

    row = [today.isoformat(), fnum(d["price"]), fnum(d["fwd_eps"]), fnum(d["fwd_rev_ps"]),
           fnum(d["med_pe"], 1), fnum(d["max_pe"], 1), fnum(d["med_ps"], 1), fnum(d["max_ps"], 1),
           fnum(c["pt_pe_c"]), fnum(c["pt_ps_c"]), fnum(c["pt_c"]), fnum(c["pt_o"]),
           delta, dec_eps, dec_pe, pot]
    if not (rows and rows[-1][0] == today.isoformat()):
        rows.append(row)
    else:
        rows[-1] = row
    log[d["ticker"]] = rows
    json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    try:
        build(d, c, rows)
    except PermissionError:
        raise SystemExit(f"Plik {OUT} jest otwarty w Excelu. Zamknij go i uruchom ponownie.")

    print(f"\n{d['name']} ({d['ticker']})  kurs {d['price']:.2f} {d['currency']}")
    print(f"  źródło: {d['source']}  ({d['window']})")
    if d.get("scrab_err"):
        print(f"  UWAGA: Scrab odrzucił sesję ({d['scrab_err']}) — policzone z Yahoo")
    print(f"  EPS 12m: {d['fwd_eps']}   sprzedaż/akcję 12m: {d['fwd_rev_ps'] and round(d['fwd_rev_ps'],2)}")
    print(f"  P/E: med {c and round(d['med_pe'] or 0,1)} / max {round(d['max_pe'] or 0,1)} / min {round(d['min_pe'] or 0,1)}")
    print(f"  P/S: med {round(d['med_ps'] or 0,1)} / max {round(d['max_ps'] or 0,1)}")
    print(f"  PT konserwatywna: {c['pt_c'] and round(c['pt_c'],2)}  ({c['pot_c'] and round(c['pot_c']*100,1)}%)")
    print(f"  PT optymistyczna: {c['pt_o'] and round(c['pt_o'],2)}  ({c['pot_o'] and round(c['pot_o']*100,1)}%)")
    print(f"  -> {OUT}")

    if "--pdf" in sys.argv:
        try:
            import pdf_export
            pdf_export.export(OUT)
        except Exception as e:
            print(f"  PDF nie wyszedł: {e}")

    if "--open" in sys.argv:
        os.startfile(OUT)


if __name__ == "__main__":
    main()
