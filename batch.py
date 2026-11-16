# -*- coding: utf-8 -*-
"""Tryb wsadowy: lista tickerow -> tabela wycen + model scoringowy (Scrab/FactSet).

Uzycie:
    python batch.py             # czyta Portfel.xlsx (kolumna A)
    python batch.py MU AAPL     # nadpisuje liste
"""
import os, re, sys, statistics as st
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule

import scrab_client as sc
import scoring

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Portfel.xlsx")

INK, MUTE, DARK, WHITE = "1F2933", "6B7280", "1F2933", "FFFFFF"
GREY, YELLOW = "F2F4F7", "FFF2CC"
GREEN_F, GREEN_T = "C6EFCE", "0B6B3A"
RED_F, RED_T = "FFC7CE", "9C1C1C"
AMBER_F, AMBER_T = "FFEB9C", "7A5B00"
THIN = Side(style="thin", color="D0D5DD")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

METRICS = ["price", "eps_est_1y", "revenue_per_share_ntm", "forward_pe_ratio", "forward_ps_ratio",
           "eps_growth", "operating_margin", "return_on_assets", "current_ratio",
           "ebitda", "net_debt", "price_target_upside"]

HEAD = ["Ticker", "Ocena", "Rating", "Kurs", "P/E teraz", "P/E med. 3Y", "P/E vs med.",
        "P/S teraz", "P/S med. 3Y", "P/S vs med.", "PT kons.", "PT opt.",
        "Potencjał kons.", "Potencjał opt.", "Sygnał", "Mocne strony", "Słabe strony",
        "Wal.", "EPS 12M", "Kryt."]
WIDTHS = [11, 8, 8, 10, 10, 11, 11, 10, 11, 11, 11, 11, 13, 13, 18, 38, 38, 6, 10, 7]
EXAMPLES = ["MU", "AAPL", "MSFT", "NVDA", "ALE.WA", "PKN.WA", "DNP.WA"]


def currency_of(ticker):
    t = ticker.upper()
    if t.endswith(".WA"):
        return "PLN"
    if t.endswith(".L"):
        return "GBP"
    if t.endswith((".DE", ".F", ".PA", ".AS", ".MI", ".MC", ".BR", ".LS")):
        return "EUR"
    if t.endswith(".TO"):
        return "CAD"
    if t.endswith(".HK"):
        return "HKD"
    if t.endswith(".T"):
        return "JPY"
    return "USD"


def read_tickers():
    if not os.path.exists(OUT):
        return []
    try:
        ws = load_workbook(OUT)["Portfel"]
    except Exception:
        return []
    ok = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
    out = []
    for r in range(5, ws.max_row + 1):
        v = ws.cell(row=r, column=1).value
        if not v:
            continue
        s = str(v).strip().upper()
        if ok.match(s):
            out.append(s)
    return out


def signal(up):
    if up is None:
        return "brak danych"
    if up >= 0.15:
        return "OK — potencjał"
    if up >= 0:
        return "blisko wyceny"
    return "powyżej wyceny"


def compute_row(t, d, series):
    price, eps, rps = d["price"], d["fwd_eps"], d["fwd_rev_ps"]
    pe_now = (price / eps) if (price and eps and eps > 0) else None
    ps_now = (price / rps) if (price and rps and rps > 0) else None
    pe_rel = (pe_now / d["med_pe"]) if (pe_now and d["med_pe"]) else None
    ps_rel = (ps_now / d["med_ps"]) if (ps_now and d["med_ps"]) else None

    pt_pe_c = d["med_pe"] * eps if (d["med_pe"] and eps) else None
    pt_pe_o = d["max_pe"] * eps if (d["max_pe"] and eps) else None
    pt_ps_c = d["med_ps"] * rps if (d["med_ps"] and rps) else None
    pt_ps_o = d["max_ps"] * rps if (d["max_ps"] and rps) else None
    cs = [v for v in (pt_pe_c, pt_ps_c) if v]
    op = [v for v in (pt_pe_o, pt_ps_o) if v]
    pt_c = sum(cs) / len(cs) if cs else None
    pt_o = sum(op) / len(op) if op else None
    up_c = (pt_c / price - 1) if (pt_c and price) else None
    up_o = (pt_o / price - 1) if (pt_o and price) else None

    m = scoring.build_metrics(series)
    m["pe_rel"] = pe_rel
    m["ps_rel"] = ps_rel
    parts = scoring.score_parts(m)
    total = scoring.total_score(parts)
    strong, weak = scoring.highlights(parts)

    coverage = f"{sum(1 for v in parts.values() if v is not None)}/{len(parts)}"
    return [t, total, scoring.rating(total), price, pe_now, d["med_pe"], pe_rel,
            ps_now, d["med_ps"], ps_rel, pt_c, pt_o, up_c, up_o, signal(up_c),
            strong, weak, currency_of(t), eps, coverage]


def model_sheet(wb):
    ws = wb.create_sheet("Model oceny")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "9AA5B1"
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 74
    ws["A1"] = "Model scoringowy — 0 do 100"
    ws["A1"].font = Font(size=16, bold=True, color=INK)
    ws["A2"] = ("Każde kryterium oceniane w skali 1–10 według progów poniżej, potem suma ważona. "
                "Brak danych nie zeruje oceny — wagi renormują się do dostępnych kryteriów.")
    ws["A2"].font = Font(size=9, color=MUTE)

    rows = [
        ("Kryterium", "Waga", "Progi (wartość → punkty)"),
        ("Wzrost przychodów 3Y", 12, "<0% →1 · 0–3% →2 · 3–7% →4 · 7–12% →6 · 12–20% →8 · >20% →10"),
        ("Prognoza przychodów 1Y", 10, "<0% →1 · 0–3% →2 · 3–7% →4 · 7–15% →6 · 15–25% →8 · >25% →10"),
        ("Prognoza EPS 1Y", 10, "<0% →1 · 0–5% →2 · 5–10% →4 · 10–20% →6 · 20–35% →8 · >35% →10"),
        ("Wzrost EPS (r/r, kwartalny)", 8, "<0% →1 · 0–5% →2 · 5–15% →4 · 15–30% →6 · 30–60% →8 · >60% →10"),
        ("Marża operacyjna", 8, "<0% →1 · 0–5% →2 · 5–10% →4 · 10–15% →6 · 15–25% →7,5 · >25% →10"),
        ("Zwrot z aktywów (ROA)", 7, "<0% →1 · 0–2% →2 · 2–5% →4 · 5–8% →6 · 8–15% →7,5 · >15% →10"),
        ("Dług netto / EBITDA", 12, "gotówka netto →10 · <1x →9 · 1–2x →7 · 2–3x →5 · 3–4x →3 · >4x →1"),
        ("Płynność bieżąca", 5, "<0,8 →1 · 0,8–1,0 →4 · 1,0–1,5 →7 · 1,5–2,5 →10 · >2,5 →6"),
        ("P/E vs mediana 3Y", 12, "<0,7x →10 · 0,7–0,9x →8 · 0,9–1,1x →6 · 1,1–1,3x →4 · >1,3x →2"),
        ("P/S vs mediana 3Y", 8, "jak wyżej"),
        ("Potencjał do konsensusu", 8, "<0% →1 · 0–10% →4 · 10–25% →6 · 25–50% →8 · >50% →10"),
    ]
    r = 4
    for i, row in enumerate(rows):
        for j, v in enumerate(row, 1):
            cl = ws.cell(row=r, column=j, value=v)
            cl.border = BOX
            if i == 0:
                cl.font = Font(bold=True, size=10, color=WHITE)
                cl.fill = PatternFill("solid", fgColor=DARK)
                cl.alignment = Alignment(horizontal="center")
            else:
                cl.font = Font(size=10)
                if j == 2:
                    cl.alignment = Alignment(horizontal="center")
                if j == 3:
                    cl.alignment = Alignment(wrap_text=True, vertical="center")
        ws.row_dimensions[r].height = 24 if i else 20
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Rating").font = Font(bold=True, size=11)
    r += 1
    for lab, txt in [("A", "75–100 — mocna: jakość i wycena po naszej stronie"),
                     ("B", "60–74 — dobra: solidna, ale bez wyraźnej przewagi"),
                     ("C", "45–59 — przeciętna: wymaga wyraźnej tezy, nie tylko wyceny"),
                     ("D", "0–44 — słaba: model nie widzi przewagi")]:
        ws.cell(row=r, column=1, value=lab).font = Font(bold=True, size=10)
        ws.cell(row=r, column=3, value=txt).font = Font(size=10)
        r += 1

    ws.print_area = f"A1:C{r}"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1


def build(rows, missing):
    wb = Workbook()
    ws = wb.active
    ws.title = "Portfel"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = DARK

    for i, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[chr(64 + i)].width = w

    lastcol = chr(64 + len(HEAD))
    ws.merge_cells(f"A1:{lastcol}1")
    c = ws["A1"]
    c.value = "Portfel — wycena i ocena jakości"
    c.font = Font(size=16, bold=True, color=WHITE)
    c.fill = PatternFill("solid", fgColor=DARK)
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 30

    ups = [r[12] for r in rows if r[12] is not None]
    scores = [r[1] for r in rows if r[1] is not None]
    med = st.median(ups) if ups else None
    med_s = st.median(scores) if scores else None
    ws["A2"] = (f"Spółek: {len(rows)}  ·  mediana oceny: {med_s if med_s else '—'}  ·  "
                f"mediana potencjału: {med*100:.1f}%  ·  A/B: {sum(1 for x in scores if x >= 60)}  ·  "
                f"źródło: Scrab (FactSet)  ·  {datetime.now():%Y-%m-%d %H:%M}")
    ws["A2"].font = Font(size=10, color=MUTE)

    ws["A3"] = ("Wpisz tickery w kolumnie A (jeden na wiersz), zapisz, uruchom Portfel.bat. "
                "Reszta kolumn nadpisuje się sama; tabela sortuje się po ocenie.")
    ws["A3"].font = Font(size=9, color=MUTE, italic=True)

    hr = 4
    for i, h in enumerate(HEAD, 1):
        cl = ws.cell(row=hr, column=i, value=h)
        cl.font = Font(bold=True, size=9, color=WHITE)
        cl.fill = PatternFill("solid", fgColor=DARK)
        cl.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cl.border = BOX
    ws.row_dimensions[hr].height = 32

    for j, row in enumerate(rows):
        r = hr + 1 + j
        for i, v in enumerate(row, 1):
            cl = ws.cell(row=r, column=i, value=v)
            cl.font = Font(size=10, color=INK, bold=(i == 1))
            cl.border = BOX
            if i == 1:
                cl.fill = PatternFill("solid", fgColor=YELLOW)
            if i in (4, 5, 6, 8, 9, 11, 12, 19):
                cl.number_format = "#,##0.00"
            if i in (7, 10):
                cl.number_format = '0.00"x"'
            if i == 2:
                cl.number_format = "0.0"
                cl.alignment = Alignment(horizontal="center")
            if i == 3:
                cl.alignment = Alignment(horizontal="center")
                cl.font = Font(size=11, bold=True, color=INK)
            if i in (13, 14):
                cl.number_format = '+0.0%;-0.0%'
                cl.alignment = Alignment(horizontal="center")
            if i in (16, 17):
                cl.font = Font(size=9, color=MUTE)
                cl.alignment = Alignment(wrap_text=True, vertical="center")
            if i == 20:
                cl.font = Font(size=8, color=MUTE)
                cl.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[r].height = 40

    last = hr + len(rows)
    ws.conditional_formatting.add(f"B{hr+1}:B{last}", ColorScaleRule(
        start_type="num", start_value=30, start_color="F8CBAD",
        mid_type="num", mid_value=60, mid_color="FFF2CC",
        end_type="num", end_value=85, end_color="C6EFCE"))
    ws.conditional_formatting.add(f"C{hr+1}:C{last}", CellIsRule(
        operator="equal", formula=['"A"'], fill=PatternFill("solid", bgColor=GREEN_F), font=Font(color=GREEN_T, bold=True)))
    ws.conditional_formatting.add(f"C{hr+1}:C{last}", CellIsRule(
        operator="equal", formula=['"B"'], fill=PatternFill("solid", bgColor="E2EFDA"), font=Font(color=GREEN_T, bold=True)))
    ws.conditional_formatting.add(f"C{hr+1}:C{last}", CellIsRule(
        operator="equal", formula=['"C"'], fill=PatternFill("solid", bgColor=AMBER_F), font=Font(color=AMBER_T, bold=True)))
    ws.conditional_formatting.add(f"C{hr+1}:C{last}", CellIsRule(
        operator="equal", formula=['"D"'], fill=PatternFill("solid", bgColor=RED_F), font=Font(color=RED_T, bold=True)))

    rng = f"M{hr+1}:N{last}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThanOrEqual", formula=["0.15"],
                                                  fill=PatternFill("solid", bgColor=GREEN_F),
                                                  font=Font(color=GREEN_T, bold=True)))
    ws.conditional_formatting.add(rng, CellIsRule(operator="between", formula=["0", "0.15"],
                                                  fill=PatternFill("solid", bgColor=AMBER_F),
                                                  font=Font(color=AMBER_T)))
    ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["0"],
                                                  fill=PatternFill("solid", bgColor=RED_F),
                                                  font=Font(color=RED_T)))
    ws.conditional_formatting.add(f"G{hr+1}:G{last}", CellIsRule(operator="lessThan", formula=["0.8"],
                                                 fill=PatternFill("solid", bgColor=GREEN_F)))
    ws.conditional_formatting.add(f"J{hr+1}:J{last}", CellIsRule(operator="lessThan", formula=["0.8"],
                                                 fill=PatternFill("solid", bgColor=GREEN_F)))

    if rows:
        ws.auto_filter.ref = f"A{hr}:{lastcol}{last}"
        ws.freeze_panes = f"B{hr+1}"

    if missing:
        r = last + 2
        cl = ws.cell(row=r, column=1, value="Nie znaleziono: " + ", ".join(missing))
        cl.font = Font(size=9, color=RED_T)

    ws.print_area = f"A1:{lastcol}{last}"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0

    model_sheet(wb)

    ws2 = wb.create_sheet("Jak czytać")
    ws2.sheet_view.showGridLines = False
    ws2.column_dimensions["A"].width = 24
    ws2.column_dimensions["B"].width = 100
    ws2["A1"] = "Jak czytać tabelę"
    ws2["A1"].font = Font(size=16, bold=True)
    lines = [
        ("Ocena / Rating", "Model 0–100 (16 kryteriów: wzrost, jakość, wycena, rewizje). A ≥75, B ≥60, C ≥45, D poniżej. Szczegóły w zakładce Model oceny."),
        ("P/E vs med.", "Mnożnik dzisiejszy w relacji do własnej mediany z 3 lat. Poniżej 0,8x = dolna część historycznego przedziału (zielone)."),
        ("P/S vs med.", "To samo na sprzedaży — dla cyklików wiarygodniejsze niż P/E."),
        ("PT kons.", "Mediana historycznego mnożnika × prognoza 12M, uśredniona po P/E i P/S."),
        ("PT opt.", "Maksimum mnożnika z 3 lat × ta sama prognoza, uśrednione po P/E i P/S."),
        ("Potencjał", "PT ÷ kurs − 1. Zielone ≥ +15%, żółte 0…+15%, czerwone poniżej zera."),
        ("Sygnał", "Próg na potencjale konserwatywnym — sito do dalszej analizy, nie rekomendacja."),
        ("Mocne / słabe", "Dwie najlepsze i dwie najsłabsze kategorie scoringu, z punktacją."),
        ("", ""),
        ("Sortowanie", "Tabela sortuje się po ocenie (jakość najpierw). Autofiltr pozwala przestawić na potencjał."),
        ("Aktualizacja", "Zmień tickery w kolumnie A, zapisz i uruchom Portfel.bat. Dane Scrab/FactSet, mnożniki z okna 3 lat."),
        ("Brak danych", "Ticker nieznany Scrabowi albo spółka bez prognozy EPS — wtedy zostaje P/S, a ocena liczy się z dostępnych kryteriów."),
    ]
    r = 3
    for k, v in lines:
        ws2.cell(row=r, column=1, value=k).font = Font(bold=True, size=10)
        cl = ws2.cell(row=r, column=2, value=v)
        cl.font = Font(size=10)
        cl.alignment = Alignment(wrap_text=True, vertical="top")
        ws2.row_dimensions[r].height = 30 if len(v) > 80 else 18
        r += 1
    ws2.print_area = f"A1:B{r}"
    ws2.sheet_properties.pageSetUpPr.fitToPage = True
    ws2.page_setup.fitToWidth = 1
    ws2.page_setup.fitToHeight = 1

    for w in wb.worksheets:
        w.print_options.horizontalCentered = True
        w.oddFooter.left.text = "Trading For a Living · przegląd portfela"
        w.oddFooter.left.size = 8
        w.oddFooter.left.color = "808080"
        w.oddFooter.center.text = "&P / &N"
        w.oddFooter.center.size = 8
        w.oddFooter.center.color = "808080"
        w.oddFooter.right.text = "&A · &D"
        w.oddFooter.right.size = 8
        w.oddFooter.right.color = "808080"

    wb.calculation.fullCalcOnLoad = True
    try:
        wb.save(OUT)
    except PermissionError:
        alt = OUT.replace(".xlsx", "_nowy.xlsx")
        wb.save(alt)
        raise SystemExit(f"Plik {os.path.basename(OUT)} jest otwarty w Excelu — zapisałem {os.path.basename(alt)}. "
                         f"Zamknij Excel i uruchom ponownie, żeby nadpisać oryginał.")


def main():
    global OUT
    argv = sys.argv[1:]
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            OUT = os.path.abspath(argv[i + 1])
            del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    tickers = [a.upper() for a in args] if args else [t for t in read_tickers() if t.strip()]
    created = False
    if not tickers:
        tickers = EXAMPLES[:]
        created = True
        print("Nie znalazłem listy tickerów — tworzę Portfel.xlsx z przykładami.")

    tickers = list(dict.fromkeys([t for t in tickers if t]))
    print(f"Pobieram {len(tickers)} tickerów ze Scraba ...")
    data = sc.post(tickers, METRICS)

    grouped = {}
    for s in data:
        tk = (s.get("ticker") or "").upper()
        if tk and s.get("datapoints"):
            grouped.setdefault(tk, {})[s.get("metric")] = s.get("datapoints")

    rows, missing = [], []
    for t in tickers:
        series = grouped.get(t)
        if not series or not series.get("price"):
            missing.append(t)
            continue
        try:
            base = sc._compute(t, series)
        except Exception:
            missing.append(t)
            continue
        rows.append(compute_row(t, base, series))

    rows.sort(key=lambda x: (x[1] is None, -(x[1] or 0), -(x[12] or 0)))
    build(rows, missing)

    print(f"  gotowe: {len(rows)} spółek" + (f", brak danych: {', '.join(missing)}" if missing else ""))
    print(f"  {'ticker':9} {'ocena':>6} {'rat':>4} {'PT':>9} {'potencjał':>10}")
    for r in rows:
        up = f"{r[12]*100:+.1f}%" if r[12] is not None else "—"
        print(f"  {r[0]:9} {r[1] if r[1] is not None else '—':>6} {r[2]:>4} {r[10]:9.2f} {up:>10}")
    print(f"  -> {OUT}")

    if "--pdf" in sys.argv:
        try:
            import pdf_export
            pdf_export.export(OUT)
        except Exception as e:
            print(f"  PDF nie wyszedł: {e}")

    if created:
        print("  (podmień przykładowe tickery w kolumnie A i uruchom ponownie)")


if __name__ == "__main__":
    main()
