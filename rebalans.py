# -*- coding: utf-8 -*-
"""Rebalans portfela: stan -> wagi docelowe -> lista transakcji.

Uzycie:
    python rebalans.py            # czyta Rebalans.xlsx
    python rebalans.py --pdf
"""
import math
import os
import sys
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import CellIsRule
from openpyxl.worksheet.datavalidation import DataValidation

import scrab_client as sc
import scoring

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Rebalans.xlsx")
CH = os.path.join(HERE, "_charts")
os.makedirs(CH, exist_ok=True)

INK, MUTE, DARK, WHITE = "1F2933", "6B7280", "1F2933", "FFFFFF"
GREY, YELLOW = "F2F4F7", "FFF2CC"
GREEN_F, GREEN_T = "C6EFCE", "0B6B3A"
RED_F, RED_T = "FFC7CE", "9C1C1C"
THIN = Side(style="thin", color="D0D5DD")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

METHODS = {
    "inv_vol": "odwrotnie do zmienności (Markowitz / Trela)",
    "equal": "równe wagi",
    "score": "według oceny scoringowej",
    "score_vol": "ocena ÷ zmienność",
    "manual": "ręcznie (kolumna F)",
}


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


def cap_weights(w, max_w=0.05, min_w=0.0, budget=1.0):
    """Water-filling: zamraża pozycje na limicie i rozdaje resztę pozostałym.

    Nie oscyluje — jeśli budżet się skończy, reszta zostaje niezainwestowana
    (świadomie pokazuje się jako gotówka, a nie rozdmuchuje pozostałych wag).
    """
    w = [max(float(x), 0.0) for x in w]
    s = sum(w)
    if s <= 0:
        return [0.0] * len(w)
    w = [x / s for x in w]
    fixed, idx, remaining = {}, set(range(len(w))), float(budget)
    for _ in range(60):
        if not idx or remaining <= 1e-9:
            break
        s_rest = sum(w[i] for i in idx)
        if s_rest <= 0:
            break
        prop = {i: remaining * w[i] / s_rest for i in idx}
        over = [i for i in idx if prop[i] > max_w + 1e-12]
        if not over:
            for i in idx:
                fixed[i] = prop[i]
            idx = set()
            break
        for i in over:
            fixed[i] = max_w
            remaining -= max_w
            idx.discard(i)
    out = [max(fixed.get(i, 0.0), 0.0) for i in range(len(w))]
    if min_w > 0:
        out = [x if x >= min_w or x == 0 else 0.0 for x in out]
    return out


def cap_sector(w, sectors, max_sec=0.25):
    """Każdy sektor przycięty do limitu; nadwyżka zostaje w gotówce.

    Nie przelewamy pieniędzy ze ściętej branży do innej — limit sektora jest
    limitem ryzyka, a nie celem inwestycyjnym. Wcześniejsza wersja rozdzielała
    budżet między sektory, przez co ścięcie jednej branży PODNOSIŁO inną
    (np. 20% → 25%), czyli zwiększało ryzyko wbrew decyzji użytkownika.
    """
    w = [max(float(x), 0.0) for x in w]
    tot = sum(w)
    if tot <= 0:
        return w
    groups = {}
    for i, s in enumerate(sectors):
        groups.setdefault(s, []).append(i)
    keys = list(groups)
    sec_w = [sum(w[i] for i in groups[k]) for k in keys]
    sec_capped = [min(x, max_sec) for x in sec_w]
    out = [0.0] * len(w)
    for k, target in zip(keys, sec_capped):
        base = sum(w[i] for i in groups[k])
        if base <= 0 or target <= 0:
            continue
        for i in groups[k]:
            out[i] = w[i] * (target / base)
    return out


def volatilities(series, tickers):
    """Zmienność liczona per spółka, na jej własnej serii.

    Krótka seria jednej spółki nie może przestawić zmienności całego portfela na 0.30
    (wtedy inv_vol po cichu staje się equal-weight) — brakującą wartość dostaje tylko ta spółka.
    """
    out = {}
    for t in tickers:
        pm = {int(ts): float(v) for ts, v in (series.get(t) or []) if isinstance(v, (int, float))}
        ts = sorted(pm)
        if len(ts) < 60:
            out[t] = None
            continue
        r = [(pm[ts[i]] / pm[ts[i - 1]] - 1) if pm[ts[i - 1]] else 0.0 for i in range(1, len(ts))][-750:]
        v = float(np.std(np.array(r), ddof=1) * math.sqrt(252))
        out[t] = v if math.isfinite(v) and v > 0 else None
    known = [v for v in out.values() if v]
    fallback = float(np.median(known)) if known else 0.30
    return {t: (out[t] if out[t] else fallback) for t in tickers}


def read_state():
    """Wczytuje tickery, sztuki i konfigurację z Rebalans.xlsx."""
    wb = load_workbook(OUT)
    ws = wb["Rebalans"]
    method = str(ws["D4"].value or "inv_vol").strip().lower()
    cfg = {
        "method": method if method in METHODS else "inv_vol",
        "cash": float(ws["D5"].value or 0),
        "max_pos": float(ws["D6"].value or 0.05),
        "max_sec": float(ws["D7"].value or 0.25),
        "fractional": str(ws["D8"].value or "nie").strip().lower().startswith("t"),
    }
    rows = []
    r = 11
    while r <= ws.max_row:
        t = ws.cell(row=r, column=1).value
        if not t:
            break
        rows.append({"ticker": str(t).strip().upper(),
                     "shares": float(ws.cell(row=r, column=2).value or 0),
                     "manual": ws.cell(row=r, column=6).value})
        r += 1
    return cfg, rows


def make_template(tickers):
    wb = Workbook()
    ws = wb.active
    ws.title = "Rebalans"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = DARK

    ws.merge_cells("A1:K1")
    c = ws["A1"]
    c.value = "Rebalans portfela"
    c.font = Font(size=16, bold=True, color=WHITE)
    c.fill = PatternFill("solid", fgColor=DARK)
    ws.row_dimensions[1].height = 30

    put(ws, "A2", "Wpisz liczbę akcji w kolumnie B (żółte). Reszta liczy się sama przy uruchomieniu Rebalans.bat.",
        size=9, color=MUTE)

    cfg = [("Metoda wag docelowych", "inv_vol", "inv_vol / equal / score / score_vol / manual"),
           ("Gotówka (w walucie portfela)", 100000, "pominięta przy liczeniu wag, wchodzi do sumy"),
           ("Limit na pozycję", 0.05, "5% — powyżej wagi są przycinane"),
           ("Limit na sektor", 0.25, "25%"),
           ("Akcje ułamkowe (tak/nie)", "nie", "dotyczy rynku US w IBKR")]
    r = 4
    for lab, val, note in cfg:
        put(ws, f"A{r}", lab, size=10)
        ws.merge_cells(f"A{r}:C{r}")
        put(ws, f"D{r}", val, size=10, fill=YELLOW, border=True,
            fmt=("0%" if isinstance(val, float) else None), align="right")
        put(ws, f"E{r}", "  " + note, size=8, color=MUTE)
        ws.merge_cells(f"E{r}:K{r}")
        r += 1

    heads = ["Ticker", "Sztuk", "Kurs", "Wartość", "Waga teraz", "Waga docelowa",
             "Różnica", "Transakcja", "Sztuk do obrotu", "Kwota", "Sektor"]
    for i, h in enumerate(heads, 1):
        cl = ws.cell(row=10, column=i, value=h)
        cl.font = Font(bold=True, size=9, color=WHITE)
        cl.fill = PatternFill("solid", fgColor=DARK)
        cl.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cl.border = BOX
    ws.row_dimensions[10].height = 30
    for i, w in enumerate([11, 9, 11, 13, 11, 12, 10, 13, 13, 12, 20]):
        ws.column_dimensions[chr(65 + i)].width = w

    r = 11
    for t in tickers:
        put(ws, f"A{r}", t, bold=True, border=True, fill=YELLOW)
        put(ws, f"B{r}", 0, border=True, fill=YELLOW, align="right")
        r += 1

    dv = DataValidation(type="list", formula1='"inv_vol,equal,score,score_vol,manual"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("D4")

    ws2 = wb.create_sheet("Zasady")
    ws2.sheet_view.showGridLines = False
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 95
    ws2["A1"] = "Jak to liczy"
    ws2["A1"].font = Font(size=15, bold=True)
    rows = [
        ("Metody wag", "inv_vol — im większa zmienność, tym mniejsza waga (metoda Markowitza, której używa Trela). "
                       "equal — po równo. score — proporcjonalnie do oceny 0–100. score_vol — ocena podzielona przez zmienność. "
                       "manual — bierzesz kolumnę F tak, jak ją wpiszesz."),
        ("Limity", "Wagi są przycinane do limitu pozycji i sektora, nadwyżka rozdzielana na resztę (iteracyjnie). "
                   "To odpowiada jego zasadzie 3–5% na pozycję i 20–25% na grupę."),
        ("Gotówka", "Wchodzi do sumy portfela, ale nie dostaje wagi docelowej — chyba że ustawisz metodę manual i wpiszesz jej udział."),
        ("Transakcje", "Sztuki = różnica wartości ÷ kurs, zaokrąglona w dół do pełnych akcji (albo do 0,01 przy ułamkowych). "
                       "Kwota to ta sama różnica w walucie. Sprzedaże finansują zakupy."),
        ("Kiedy nie rebalansować", "Przy różnicy poniżej ~2 pkt proc. transakcja nie ma sensu — koszty zjedzą efekt. "
                                   "Arkusz pokazuje wielkość obrotu, żebyś to widział."),
        ("Rytm", "U Treli przegląd kwartalny. Rebalans wymusza dyscyplinę, ale nie jest sygnałem kupna — "
                 "spółkę wyrzuca się wtedy, gdy przestaje spełniać kryteria, nie gdy zmieniła wagę."),
    ]
    r = 3
    for k, v in rows:
        ws2.cell(row=r, column=1, value=k).font = Font(bold=True, size=10)
        cl = ws2.cell(row=r, column=2, value=v)
        cl.font = Font(size=10)
        cl.alignment = Alignment(wrap_text=True, vertical="top")
        ws2.row_dimensions[r].height = 15 * (len(v) // 95 + 2)
        r += 1

    wb.save(OUT)
    print(f"  Utworzyłem {OUT} z {len(tickers)} tickerami — uzupełnij liczbę akcji i uruchom ponownie.")


def build(cfg, rows):
    tickers = [r["ticker"] for r in rows]
    data = sc.post(tickers, sc.METRICS + sc.EXTRA_METRICS)
    g = {}
    for s in data:
        tk = (s.get("ticker") or "").upper()
        if tk and s.get("datapoints"):
            g.setdefault(tk, {})[s.get("metric")] = s.get("datapoints")

    price, vols, scores, sectors = {}, {}, {}, {}
    for t in tickers:
        ser = g.get(t, {})
        dp = ser.get("price") or []
        price[t] = float(dp[-1][1]) if dp else None
    vols = volatilities({t: g.get(t, {}).get("price") or [] for t in tickers}, tickers)

    try:
        import yfinance as yf
        for t in tickers:
            info = {}
            try:
                info = yf.Ticker(t).info or {}
            except Exception:
                pass
            sectors[t] = info.get("sector") or "—"
    except Exception:
        sectors = {t: "—" for t in tickers}

    for t in tickers:
        ser = g.get(t, {})
        d = sc._compute(t, ser) if ser.get("price") else None
        if not d:
            scores[t] = None
            continue
        parts = scoring.score_parts({**scoring.build_metrics(ser),
                                     "pe_rel": (d["price"] / d["fwd_eps"] / d["med_pe"]) if (d.get("fwd_eps") and d.get("med_pe") and d["fwd_eps"] > 0) else None,
                                     "ps_rel": (d["price"] / d["fwd_rev_ps"] / d["med_ps"]) if (d.get("fwd_rev_ps") and d.get("med_ps")) else None})
        scores[t] = scoring.total_score(parts)

    total_cash = cfg["cash"]
    value = {r["ticker"]: (r["shares"] or 0) * (price.get(r["ticker"]) or 0) for r in rows}
    invested = sum(value.values())
    total = invested + total_cash
    if total <= 0:
        raise SystemExit("Portfel ma zerową wartość — wpisz liczbę akcji albo gotówkę.")

    cur_w = {t: value[t] / total for t in tickers}

    n = len(tickers)
    method = cfg["method"]
    if method == "manual":
        raw = []
        for r in rows:
            m = r.get("manual")
            raw.append(float(m) if isinstance(m, (int, float)) else 0.0)
    elif method == "equal":
        raw = [1.0] * n
    elif method == "score":
        raw = [(scores.get(t) or 0) for t in tickers]
        if sum(raw) <= 0:
            raw = [1.0] * n
    elif method == "score_vol":
        raw = [((scores.get(t) or 0) / max(vols.get(t, 0.3), 0.05)) for t in tickers]
        if sum(raw) <= 0:
            raw = [1.0] * n
    else:  # inv_vol
        raw = [1.0 / max(vols.get(t, 0.3), 0.05) for t in tickers]

    # przy malej liczbie spolek limit 5% nie pozwala zainwestowac calosci -
    # podnosimy go do 1/n, zeby model nie trzymal 65% w gotowce przez artefakt limitu
    eff_max = max(cfg["max_pos"], 1.0 / n)
    tgt = cap_weights(raw, eff_max, 0.0, 1.0)
    tgt = cap_sector(tgt, [sectors.get(t, "—") for t in tickers], cfg["max_sec"])
    # po limicie sektorów tylko przycinamy do limitu pozycji — bez redystrybucji,
    # bo ona rozdmuchuje z powrotem sektor, który właśnie został ścięty
    tgt = [min(x, eff_max) for x in tgt]
    tgt = {t: tgt[i] for i, t in enumerate(tickers)}

    # zapis do arkusza
    wb = load_workbook(OUT)
    ws = wb["Rebalans"]
    ws["D5"] = cfg["cash"]

    buy_total = sell_total = 0.0
    trades = []
    for i, r in enumerate(rows):
        t = r["ticker"]
        row = 11 + i
        p = price.get(t) or 0
        v = value[t]
        tw = tgt.get(t, 0.0)
        delta = tw * total - v
        step = 0.01 if cfg["fractional"] else 1
        shares = (abs(delta) / p) if p else 0
        shares = math.floor(shares / step) * step
        if cfg["fractional"]:
            shares = round(shares, 2)
        amt = shares * p
        if delta > 0:
            buy_total += amt
            action = "KUP"
        elif delta < 0:
            sell_total += amt
            action = "SPRZEDAJ"
        else:
            action = "—"
        trades.append([t, r["shares"], p, v, cur_w[t], tw, tw - cur_w[t], action, shares, amt, sectors.get(t, "—")])

        put(ws, f"C{row}", round(p, 2), fmt="#,##0.00", size=10, border=True, align="right")
        put(ws, f"D{row}", round(v, 2), fmt="#,##0", size=10, border=True, align="right")
        put(ws, f"E{row}", cur_w[t], fmt="0.0%", size=10, border=True, align="right")
        put(ws, f"F{row}", tw, fmt="0.0%", size=10, border=True, align="right", bold=True)
        put(ws, f"G{row}", tw - cur_w[t], fmt="+0.0%;-0.0%", size=10, border=True, align="right")
        put(ws, f"H{row}", action, size=10, border=True, align="center", bold=True,
            color=GREEN_T if action == "KUP" else RED_T if action == "SPRZEDAJ" else MUTE)
        put(ws, f"I{row}", shares, fmt=("#,##0" if float(shares).is_integer() else "#,##0.00"),
            size=10, border=True, align="right")
        put(ws, f"J{row}", round(amt, 2), fmt="#,##0", size=10, border=True, align="right")
        put(ws, f"K{row}", sectors.get(t, "—"), size=9, border=True)

    last = 11 + len(rows) - 1
    ws.print_area = f"A1:K{last + 6}"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0

    # arkusz wyniku
    if "Wynik" in wb.sheetnames:
        del wb["Wynik"]
    ws2 = wb.create_sheet("Wynik", 1)
    ws2.sheet_view.showGridLines = False
    ws2.sheet_properties.tabColor = "2F6DF6"
    put(ws2, "A1", "Co zrobić", bold=True, size=15)
    put(ws2, "A2", f"Metoda: {METHODS.get(method, method)} · portfel {round(total,0)} · "
                   f"gotówka {round(total_cash,0)} · {datetime.now():%Y-%m-%d %H:%M}", size=9, color=MUTE)
    if eff_max > cfg["max_pos"]:
        put(ws2, "A3", f"Limit pozycji podniesiony z {cfg['max_pos']*100:.0f}% do {eff_max*100:.0f}% — "
                       f"przy {n} spółkach mniejszy limit oznaczałby trzymanie reszty w gotówce.",
            size=9, color=RED_T)

    turnover = (buy_total + sell_total) / 2 / total if total else 0
    invested_after = sum(tgt.values()) * total
    kpis = [("Kupić za", f"{round(buy_total,0)}"),
            ("Sprzedać za", f"{round(sell_total,0)}"),
            ("Gotówka po (w tym niezainwestowana)", f"{round(total - invested_after,0)}"),
            ("Obrót portfela", f"{turnover*100:.1f}%"),
            ("Transakcji", str(sum(1 for x in trades if x[7] != "—")))]
    r = 4
    for lab, val in kpis:
        put(ws2, f"A{r}", lab, size=10)
        put(ws2, f"B{r}", val, bold=True, size=11, align="right", border=True)
        r += 1

    # wykres wag przed/po
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    y = np.arange(len(tickers))
    ax.barh(y - 0.2, [cur_w[t] * 100 for t in tickers], height=0.38, color="#C7D2E4", label="teraz")
    ax.barh(y + 0.2, [tgt[t] * 100 for t in tickers], height=0.38, color="#2F6DF6", label="po rebalansie")
    ax.set_yticks(y)
    ax.set_yticklabels(tickers, fontsize=8)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, p: f"{v:.0f}%"))
    ax.grid(axis="x", color="#E4E7EB")
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0, labelsize=8, colors="#6B7280")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.set_title("Wagi: teraz vs po rebalansie", fontsize=9, loc="left")
    fig.tight_layout()
    p1 = os.path.join(CH, "rebalans_wagi.png")
    fig.savefig(p1, dpi=170)
    plt.close(fig)
    img = XLImage(p1)
    img.width, img.height = 700, 340
    ws2.add_image(img, "A11")

    # wykres sektorów
    sb, sa = {}, {}
    for t in tickers:
        sb[sectors.get(t, "—")] = sb.get(sectors.get(t, "—"), 0) + cur_w[t]
        sa[sectors.get(t, "—")] = sa.get(sectors.get(t, "—"), 0) + tgt[t]
    keys = sorted(set(sb) | set(sa), key=lambda k: -sa.get(k, 0))
    fig, ax = plt.subplots(figsize=(7.4, 2.6))
    y = np.arange(len(keys))
    ax.barh(y - 0.2, [sb.get(k, 0) * 100 for k in keys], height=0.38, color="#C7D2E4", label="teraz")
    ax.barh(y + 0.2, [sa.get(k, 0) * 100 for k in keys], height=0.38, color="#1C8A5A", label="po rebalansie")
    ax.set_yticks(y)
    ax.set_yticklabels(keys, fontsize=8)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, p: f"{v:.0f}%"))
    ax.grid(axis="x", color="#E4E7EB")
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0, labelsize=8, colors="#6B7280")
    ax.legend(frameon=False, fontsize=8, loc="upper right", bbox_to_anchor=(1.0, 1.02))
    ax.set_title("Ekspozycja sektorowa", fontsize=9, loc="left")
    fig.tight_layout()
    p2 = os.path.join(CH, "rebalans_sektory.png")
    fig.savefig(p2, dpi=170)
    plt.close(fig)
    img2 = XLImage(p2)
    img2.width, img2.height = 700, 250
    ws2.add_image(img2, "A32")

    ws2.print_area = "A1:N48"
    ws2.page_setup.orientation = "landscape"
    ws2.sheet_properties.pageSetUpPr.fitToPage = True
    ws2.page_setup.fitToWidth = 1
    ws2.page_setup.fitToHeight = 1

    wb.calculation.fullCalcOnLoad = True
    wb.save(OUT)
    return dict(total=total, buy=buy_total, sell=sell_total, turnover=turnover, n=len(trades))


def main():
    if not os.path.exists(OUT):
        tickers = []
        if os.path.exists(os.path.join(HERE, "Portfel.xlsx")):
            try:
                ws = load_workbook(os.path.join(HERE, "Portfel.xlsx"))["Portfel"]
                for r in range(5, ws.max_row + 1):
                    v = ws.cell(row=r, column=1).value
                    if v:
                        tickers.append(str(v).strip().upper())
            except Exception:
                pass
        tickers = tickers or ["MU", "AAPL", "MSFT", "NVDA", "ALE.WA", "PKN.WA", "DNP.WA"]
        make_template(tickers)
        return

    cfg, rows = read_state()
    if not rows:
        raise SystemExit("W Rebalans.xlsx nie ma tickerów.")
    if sum(r["shares"] for r in rows) == 0:
        print("  Uwaga: wszystkie pozycje mają 0 akcji — liczę zakupy od zera (start z gotówki).")
    print(f"Rebalans: {len(rows)} pozycji, metoda {cfg['method']} ...")
    res = build(cfg, rows)
    print(f"  kupić za {res['buy']:,.0f} · sprzedać za {res['sell']:,.0f} · obrót {res['turnover']*100:.1f}% "
          f"· transakcji {res['n']}")
    print(f"  -> {OUT}")

    if "--pdf" in sys.argv:
        try:
            import pdf_export
            pdf_export.export(OUT)
        except Exception as e:
            print(f"  PDF nie wyszedł: {e}")


if __name__ == "__main__":
    main()
