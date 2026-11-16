# -*- coding: utf-8 -*-
"""Karta spółki — trzystronicowa ocena (A4) generowana z danych Scraba.

Strona 1: werdykt, wycena, scoring. Strona 2: historia kursu vs PT, checklist.
Strona 3: ocena, checklista przed decyzją i kryteria TFL policzone z danych.

Uzycie:
    python karta.py MU
    python karta.py ALE.WA --open
"""
import json
import os
import sys
import textwrap
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.ticker import FuncFormatter, FixedLocator, NullLocator

import scrab_client as sc
import scoring
import history as hist
import tfl

INK, MUTE, GRID = "#1F2933", "#6B7280", "#E4E7EB"
BLUE, GREEN, RED, AMBER = "#2F6DF6", "#1C8A5A", "#D2453C", "#B87A00"
SOFT = "#F5F7FA"

plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": INK,
                     "figure.facecolor": "white", "savefig.facecolor": "white"})

A4 = (8.27, 11.69)


def _num(v, nd=0):
    if v is None:
        return "—"
    s = f"{v:,.{nd}f}"
    return s.replace(",", " ").replace(".", ",")


def _pct(v, nd=1, sign=True):
    if v is None:
        return "—"
    s = f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"
    return s.replace(".", ",")


def _plain_pct(v, nd=1):
    if v is None:
        return "—"
    return f"{v:.{nd}f}%".replace(".", ",")


def _score_color(s):
    if s is None:
        return MUTE
    if s >= 7:
        return GREEN
    if s >= 4:
        return AMBER
    return RED


def _verdict(total, upside):
    if total is None or upside is None:
        return "brak danych do werdyktu", MUTE
    if total >= 60 and upside >= 0.15:
        return "tania jakość — model widzi i jakość, i zapas do wyceny", GREEN
    if total >= 60 and upside >= 0:
        return "dobra spółka, wycena blisko historycznej — bez wyraźnego zapasu", BLUE
    if total >= 60:
        return "dobra spółka, ale kurs powyżej modelu — jakość już w cenie", AMBER
    if total >= 45 and upside >= 0.15:
        return "przeciętna jakość, ale wycena daje zapas — do dalszej analizy", AMBER
    if total >= 45:
        return "przeciętna spółka bez przewagi wycenowej — brak powodu do pozycji", AMBER
    return "słaba jakość i brak zapasu — model nie widzi tu przewagi", RED


def _checklist(ticker):
    """Checklista przed decyzją zapisana w aplikacji (checklist.json obok skryptów)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checklist.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        e = data.get((ticker or "").upper()) or {}
        return [bool(x) for x in (e.get("items") or [])]
    except Exception:
        return []


def _wrap(txt, n=100, lines=4):
    return textwrap.wrap(str(txt or "").strip() or "—", n)[:lines]


def _header(fig, d, page, pages):
    fig.text(0.06, 0.962, f"{d['ticker']}  ·  {d['name']}", fontsize=17, fontweight="bold", color=INK)
    fig.text(0.06, 0.941, f"{d['exchange']}  ·  {d['currency']}  ·  dane: Scrab (FactSet), okno 3 lata  ·  "
                          f"{datetime.now():%Y-%m-%d}", fontsize=8, color=MUTE)
    fig.lines.append(plt.Line2D([0.06, 0.94], [0.933, 0.933], color=GRID, lw=1, transform=fig.transFigure))
    fig.text(0.94, 0.02, f"{page}/{pages}", fontsize=8, color=MUTE, ha="right")


def _footer(fig, text="Materiał edukacyjny — nie rekomendacja inwestycyjna w rozumieniu rozporządzenia MAR."):
    fig.text(0.06, 0.02, text, fontsize=7.5, color=MUTE)


def page_one(pdf, d, parts, total, rating, hist_rows):
    fig = plt.figure(figsize=A4)
    _header(fig, d, 1, 3)

    # ---- pasek werdyktu
    upside = None
    if hist_rows:
        upside = hist_rows[-1][2] / hist_rows[-1][1] - 1
    verdict, vcolor = _verdict(total, upside)

    import textwrap
    vlines = textwrap.wrap(verdict, 62)[:2]
    fig.patches.append(FancyBboxPatch((0.06, 0.838), 0.88, 0.080,
                                      boxstyle="round,pad=0.004,rounding_size=0.01",
                                      transform=fig.transFigure, fc=SOFT, ec=GRID, lw=1))
    fig.text(0.075, 0.902, "WERDYKT", fontsize=7.5, color=MUTE, fontweight="bold")
    for i, ln in enumerate(vlines):
        fig.text(0.075, 0.880 - i * 0.019, ln, fontsize=10, color=vcolor, fontweight="bold")

    # KPI po prawej stronie paska
    kpis = [(f"{_num(total,1)}", "ocena /100"), (rating, "rating"),
            (_pct(upside, 0), "potencjał")]
    x = 0.672
    for val, lab in kpis:
        fig.text(x, 0.893, val, fontsize=15, fontweight="bold", color=INK, ha="left")
        fig.text(x, 0.874, lab, fontsize=7.5, color=MUTE, ha="left")
        x += 0.088

    # ---- dwie kolumny: wycena / jakość
    y0 = 0.795
    fig.text(0.06, y0, "WYCENA", fontsize=9, fontweight="bold", color=INK)
    fig.text(0.52, y0, "JAKOŚĆ BIZNESU", fontsize=9, fontweight="bold", color=INK)

    val_rows = [
        ("Cena docelowa — konserwatywna", _num(d.get("pt_cons")), ""),
        ("Cena docelowa — optymistyczna", _num(d.get("pt_opt")), ""),
        ("Potencjał do PT konserwatywnej", _pct(upside), ""),
        ("P/E teraz", _num(d["price"] / d["fwd_eps"], 1) if d.get("fwd_eps") and d["fwd_eps"] > 0 else "—", ""),
        ("P/E mediana 3Y", _num(d.get("med_pe"), 1), ""),
        ("P/E vs mediana", f"{d['price'] / d['fwd_eps'] / d['med_pe']:.2f}x".replace(".", ",") if d.get("fwd_eps") and d.get("med_pe") and d["fwd_eps"] > 0 else "—", ""),
        ("P/S teraz", _num(d["price"] / d["fwd_rev_ps"], 2) if d.get("fwd_rev_ps") else "—", ""),
        ("P/S mediana 3Y", _num(d.get("med_ps"), 2), ""),
        ("P/S vs mediana", f"{d['price'] / d['fwd_rev_ps'] / d['med_ps']:.2f}x".replace(".", ",") if d.get("fwd_rev_ps") and d.get("med_ps") else "—", ""),
        ("Konsensus analityków (mediana PT)", _num(d.get("cons_median")), ""),
    ]
    m = scoring.build_metrics(d.get("raw") or {})
    q_rows = [
        ("Wzrost przychodów 3Y", _plain_pct(m.get("rev3y_pct"))),
        ("Prognoza przychodów 1Y", _plain_pct(m.get("rev1y_pct"))),
        ("Prognoza EPS 1Y", _plain_pct(m.get("eps1y_pct"))),
        ("Wzrost EPS r/r", _plain_pct(m.get("eps_growth"))),
        ("Marża operacyjna", _plain_pct(m.get("op_margin"))),
        ("Zwrot z aktywów (ROA)", _plain_pct(m.get("roa"))),
        ("Dług netto / EBITDA", f"{m['net_debt_ebitda']:.2f}x".replace(".", ",") if m.get("net_debt_ebitda") is not None else "—"),
        ("Płynność bieżąca", _num(m.get("current_ratio"), 2)),
    ]

    def table(x, y, rows, w, key_col=None):
        for i, row in enumerate(rows):
            yy = y - i * 0.0235
            fig.text(x, yy, row[0], fontsize=8.5, color=MUTE)
            val = row[1] if len(row) > 1 else ""
            col = INK
            if key_col and row[0].startswith(key_col):
                col = INK
            fig.text(x + w, yy, str(val), fontsize=8.5, color=col, ha="right", fontweight="bold")

    table(0.06, 0.772, val_rows, 0.36)
    table(0.52, 0.772, q_rows, 0.40)

    # ---- scoring w rozbiciu
    fig.text(0.06, 0.505, f"SCORING — {len(scoring.CRITERIA)} KRYTERIÓW", fontsize=9, fontweight="bold", color=INK)
    miss = sum(1 for v in parts.values() if v is None)
    fig.text(0.94, 0.505, f"pokrycie {len(parts) - miss}/{len(parts)}  ·  skala 1–10",
             fontsize=7.5, color=MUTE, ha="right")

    ax = fig.add_axes([0.235, 0.075, 0.70, 0.40])
    ax.set_xlim(0, 11.6)
    names = [scoring.SHORT[k] for k, _, _ in scoring.CRITERIA]
    vals = [parts.get(k) or 0 for k, _, _ in scoring.CRITERIA]
    weights = [w for _, _, w in scoring.CRITERIA]
    ypos = list(range(len(names)))[::-1]
    ax.barh(ypos, [10] * len(names), color="#EEF1F5", height=0.62, zorder=1)
    ax.barh(ypos, vals, color=[_score_color(v if v else None) for v in vals], height=0.62, zorder=2)
    for yv, v, w in zip(ypos, vals, weights):
        ax.text(10.2, yv, f"{v:.1f}".replace(".", ",") if v else "—", va="center", fontsize=8.5,
                color=INK, fontweight="bold")
        ax.text(11.0, yv, f"waga {w}", va="center", fontsize=7.2, color=MUTE)
    ax.set_yticks(ypos)
    ax.set_yticklabels(names, fontsize=8.5, color=INK)
    ax.set_xticks([])
    for s in ("top", "right", "bottom", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)

    _footer(fig)
    pdf.savefig(fig)
    plt.close(fig)


def page_two(pdf, d, hist_rows):
    fig = plt.figure(figsize=A4)
    _header(fig, d, 2, 3)

    if hist_rows:
        dates = [datetime.fromtimestamp(r[0], timezone.utc).replace(tzinfo=None) for r in hist_rows]
        price = [r[1] for r in hist_rows]
        cons = [r[2] for r in hist_rows]
        opt = [r[3] for r in hist_rows]

        ax = fig.add_axes([0.09, 0.60, 0.85, 0.315])
        ax.fill_between(dates, cons, opt, color="#E8F4EE", zorder=1)
        ax.plot(dates, opt, color=GREEN, lw=1.2, ls=(0, (4, 2)), zorder=2)
        ax.plot(dates, cons, color=BLUE, lw=2.0, zorder=3)
        ax.plot(dates, price, color=INK, lw=1.8, zorder=4)
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: _num(v)))
        ax.yaxis.set_minor_locator(NullLocator())
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.tick_params(length=0, labelsize=7.5, colors=MUTE)
        ax.set_title("Kurs vs cena docelowa (model, okno kroczące 3Y)", fontsize=9,
                     loc="left", color=INK, pad=8)
        ax.tick_params(axis="x", labelbottom=False)

        ax2 = fig.add_axes([0.09, 0.445, 0.85, 0.095])
        pot = [(c / p - 1) * 100 for c, p in zip(cons, price)]
        ax2.fill_between(dates, 0, [max(p, 0) for p in pot], color="#CFEBD9", zorder=2)
        ax2.fill_between(dates, 0, [min(p, 0) for p in pot], color="#F7D2D0", zorder=2)
        ax2.plot(dates, pot, color=BLUE, lw=1.2, zorder=3)
        ax2.axhline(0, color=INK, lw=0.8)
        ax2.axhline(15, color=GREEN, lw=0.8, ls=(0, (3, 2)))
        ax2.text(dates[1], 15, " próg +15%", fontsize=7, color=GREEN, va="bottom")
        ax2.set_ylim(min(pot + [-10]) - 6, max(pot + [20]) + 6)
        ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:.0f}%"))
        ax2.grid(axis="y", color=GRID, lw=0.7)
        ax2.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax2.spines[s].set_visible(False)
        ax2.tick_params(length=0, labelsize=7, colors=MUTE)
        ax2.set_title("Potencjał do PT konserwatywnej", fontsize=8.5, loc="left", color=INK, pad=4)

        for a in (ax, ax2):
            a.set_xlim(dates[0], dates[-1])

    # ---- checklist
    fig.text(0.06, 0.375, "CHECKLIST PRZED DECYZJĄ", fontsize=9, fontweight="bold", color=INK)
    qs = [
        "Czy umiem jednym zdaniem powiedzieć, na czym ta spółka zarabia?",
        "Jaka jest moja teza inwestycyjna i co konkretnie musiałoby się stać, żeby ją odrzucić?",
        "Czy zgadzam się z konsensusem prognoz, czy zakładam rewizję — i w którą stronę?",
        "Ile mogę stracić, jeśli się mylę, i czy ta strata mieści się w limicie ryzyka portfela?",
        "Czy mam dziś lepszą alternatywę na ten kapitał?",
    ]
    y = 0.345
    for i, q in enumerate(qs, 1):
        fig.patches.append(Rectangle((0.062, y - 0.004), 0.012, 0.012,
                                     transform=fig.transFigure, fc="white", ec=MUTE, lw=0.8))
        fig.text(0.085, y - 0.004, f"{i}. {q}", fontsize=8.5, color=INK)
        y -= 0.032

    # ---- metryki pomocnicze
    fig.text(0.06, 0.165, "ZAŁOŻENIA I UWAGI", fontsize=9, fontweight="bold", color=INK)
    lines = [
        f"Cena docelowa liczona z mediany i maksimum mnożnika z 3 lat × prognoza na 12 miesięcy "
        f"(P/E i P/S, uśrednione). Kurs {_num(d['price'])} {d['currency']}.",
        "Ocena 0–100 z 16 kryteriów (wzrost, jakość, wycena, rewizje). Brak danych nie zeruje oceny — wagi się renormują.",
        "Mnożniki są forward (1Y), okno 3 lata, źródło FactSet. Dane EOD — kurs to zamknięcie poprzedniej sesji.",
        "To nie jest rekomendacja: to arkusz założeń, które sam ustawiasz i sam musisz obronić.",
    ]
    y = 0.140
    for ln in lines:
        fig.text(0.06, y, ln, fontsize=7.8, color=MUTE, wrap=True)
        y -= 0.021

    _footer(fig)
    pdf.savefig(fig)
    plt.close(fig)


def page_three(pdf, d, tl, total_hard, final):
    """Strona 3: ocena, checklista przed decyzją i kryteria TFL policzone z danych."""
    fig = plt.figure(figsize=A4)
    _header(fig, d, 3, 3)

    y = 0.900
    fig.text(0.06, y, "OCENA, CHECKLISTA I KRYTERIA TFL", fontsize=9.5, fontweight="bold", color=INK)
    y -= 0.030
    fig.text(0.06, y, f"ocena końcowa {_num(final, 1)} / 100 — {len(scoring.CRITERIA)} kryteriów "
                      f"twardych (bez analizy miękkiej)", fontsize=8.5, color=INK)
    y -= 0.036

    items = _checklist(d.get("ticker") or "")
    qs = [
        "Czy umiem jednym zdaniem powiedzieć, na czym ta spółka zarabia?",
        "Jaka jest moja teza inwestycyjna i co konkretnie musiałoby się stać, żeby ją odrzucić?",
        "Czy zgadzam się z konsensusem prognoz, czy zakładam rewizję — i w którą stronę?",
        "Ile mogę stracić, jeśli się mylę, i czy ta strata mieści się w limicie ryzyka portfela?",
        "Czy mam dziś lepszą alternatywę na ten kapitał?",
    ]
    fig.text(0.06, y, "CHECKLISTA PRZED DECYZJĄ", fontsize=7.2, color=MUTE)
    y -= 0.022
    for i, q in enumerate(qs):
        on = bool(items[i]) if i < len(items) else False
        fig.patches.append(Rectangle((0.062, y - 0.003), 0.011, 0.011, transform=fig.transFigure,
                                     facecolor=(GREEN if on else "white"),
                                     edgecolor=(GREEN if on else GRID), lw=0.8))
        if on:
            fig.text(0.0655, y - 0.0065, "✓", fontsize=6.4, color="white")
        fig.text(0.082, y - 0.003, f"{i + 1}. {q}", fontsize=7.8, color=INK)
        y -= 0.027
    y -= 0.016

    rows = tl.get("rows") or []
    fig.lines.append(plt.Line2D([0.06, 0.94], [y + 0.012, y + 0.012], color=GRID, lw=1,
                                transform=fig.transFigure))
    fig.text(0.06, y, "KRYTERIA TFL — CO MÓWIĄ DANE", fontsize=9, fontweight="bold", color=INK)
    ok = sum(1 for r in rows if r["status"] == "ok")
    fig.text(0.94, y, f"{ok} z {len(rows)} na tak", fontsize=7.5, color=MUTE, ha="right")
    y -= 0.026

    marks = {"ok": ("✓", GREEN), "uwaga": ("~", AMBER), "zle": ("✗", RED)}
    for r in rows:
        mark, col = marks.get(r["status"], ("—", MUTE))
        fig.text(0.06, y, f"{r['q']}.", fontsize=7, color=MUTE)
        fig.text(0.084, y, r["label"], fontsize=8.3, color=INK)
        fig.text(0.895, y, r["val"], fontsize=7.8, color=MUTE, ha="right")
        fig.text(0.925, y, mark, fontsize=8.8, color=col)
        y -= 0.031

    _footer(fig)
    pdf.savefig(fig)
    plt.close(fig)


def _meta(ticker):
    """Nazwa, giełda, waluta i konsensus — z Yahoo, jako uzupełnienie."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        cons = {}
        try:
            cons = t.analyst_price_targets or {}
        except Exception:
            pass
        return (info.get("shortName") or info.get("longName") or ticker,
                info.get("fullExchangeName") or info.get("exchange") or "",
                info.get("currency") or "", cons.get("median"),
                info.get("marketCap"))
    except Exception:
        return ticker, "", "", None, None


def build(ticker, out=None):
    d = sc.fetch(ticker, sc.METRICS + sc.EXTRA_METRICS)
    name, exchange, currency, cons_median, mcap = _meta(ticker)
    d.update({"name": name, "exchange": exchange,
              "currency": currency or d.get("currency") or "", "cons_median": cons_median})
    m = scoring.build_metrics(d.get("raw") or {})
    pe_rel = (d["price"] / d["fwd_eps"] / d["med_pe"]) if (d.get("fwd_eps") and d.get("med_pe") and d["fwd_eps"] > 0) else None
    ps_rel = (d["price"] / d["fwd_rev_ps"] / d["med_ps"]) if (d.get("fwd_rev_ps") and d.get("med_ps")) else None
    parts = scoring.score_parts({**m, "pe_rel": pe_rel, "ps_rel": ps_rel})
    total_hard = scoring.total_score(parts)
    rows = hist.build(d.get("raw") or {})

    # PT policzone jak w arkuszu (dla tabeli)
    eps, rps = d.get("fwd_eps"), d.get("fwd_rev_ps")
    cons = []
    opt = []
    if d.get("med_pe") and eps and eps > 0:
        cons.append(d["med_pe"] * eps)
    if d.get("med_ps") and rps:
        cons.append(d["med_ps"] * rps)
    if d.get("max_pe") and eps and eps > 0:
        opt.append(d["max_pe"] * eps)
    if d.get("max_ps") and rps:
        opt.append(d["max_ps"] * rps)
    d["pt_cons"] = sum(cons) / len(cons) if cons else None
    d["pt_opt"] = sum(opt) / len(opt) if opt else None

    up = (d["pt_cons"] / d["price"] - 1) if (d.get("pt_cons") and d.get("price")) else None
    final = total_hard
    rating = scoring.rating(final)
    tl = tfl.build(d.get("raw") or {}, m, {"pe_rel": pe_rel, "ps_rel": ps_rel, "upside": up}, mcap)

    out = out or os.path.join(os.path.dirname(os.path.abspath(__file__)), f"Karta_{d['ticker'].replace('.', '_')}.pdf")
    with PdfPages(out) as pdf:
        page_one(pdf, d, parts, final, rating, rows)
        page_two(pdf, d, rows)
        page_three(pdf, d, tl, total_hard, final)
        info = pdf.infodict()
        info["Title"] = f"Karta spółki — {d['ticker']}"
        info["Subject"] = "Ocena spółki: wycena, jakość, scoring, historia"
        info["Author"] = "Trading For a Living — arkusz własny"
    print(f"  {d['ticker']}: ocena {_num(final, 1)}, {rating}, potencjał {(rows[-1][2]/rows[-1][1]-1)*100:+.0f}%" if rows else "")
    print(f"  -> {out}")
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tk = args[0] if args else input("Ticker: ").strip()
    path = build(tk)
    if "--open" in sys.argv:
        os.startfile(path)
