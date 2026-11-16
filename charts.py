# -*- coding: utf-8 -*-
"""Wykresy do arkusza (matplotlib -> PNG -> obraz w xlsx).

Zasady: brak ozdobnikow, grube linie, opisy tylko tam, gdzie cos znacza,
skala logarytmiczna tam, gdzie dane roznia sie rzedem wielkosci.
"""
from datetime import datetime, timezone, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, FuncFormatter, NullLocator, FixedLocator
from matplotlib.dates import MonthLocator, DateFormatter
from matplotlib.lines import Line2D

PALETTES = {
    "light": dict(INK="#14140F", MUTE="#5F5F58", GRID="#E5E2D7", BLUE="#1E4A3A",
                  GREEN="#2F6B4F", RED="#8A3A32", BAND="#F1EFE6", BAND2="#E4EFE8",
                  BG="#FFFFFF"),
    "dark":  dict(INK="#EFEAE0", MUTE="#A9A398", GRID="#33332B", BLUE="#8FBF9F",
                  GREEN="#7FB08F", RED="#C4695C", BAND="#232320", BAND2="#22302A",
                  BG="#171714"),
}

INK = MUTE = GRID = BLUE = GREEN = RED = BAND = BAND2 = BG = None


def use_theme(name="light"):
    """Przełącza paletę wykresów (jasna/ciemna). Wywoływać przed rysowaniem."""
    p = PALETTES.get(name, PALETTES["light"])
    g = globals()
    for k, v in p.items():
        g[k] = v
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10,
        "axes.edgecolor": GRID, "axes.labelcolor": MUTE, "axes.titlecolor": INK,
        "text.color": INK, "xtick.color": MUTE, "ytick.color": MUTE,
        "figure.facecolor": BG, "figure.edgecolor": BG, "axes.facecolor": BG,
        "savefig.facecolor": BG,
        "legend.facecolor": BG, "legend.edgecolor": GRID, "legend.labelcolor": INK,
    })


use_theme("light")


def _ts(x):
    return datetime.fromtimestamp(x, timezone.utc).replace(tzinfo=None)


def _num(v):
    return f"{v:,.0f}".replace(",", " ")


def _month_ticks(dates):
    """Równe etykiety na osi czasu: styczeń/lipiec, a dla krótkich serii co kwartał."""
    start, end = dates[0], dates[-1]
    months = max(1, (end.year - start.year) * 12 + end.month - start.month)
    step = 6 if months >= 18 else 3
    ticks = []
    y, m = start.year, start.month
    if m < 1:
        m = 1
    # ustaw na pierwszy pełny miesiąc z krokiem
    while m % step != 0:
        m += 1
        if m > 12:
            m = 1
            y += 1
    cur = datetime(y, m, 1)
    while cur <= end:
        ticks.append(cur)
        m += step
        y += (m - 1) // 12
        m = (m - 1) % 12 + 1
        cur = datetime(y, m, 1)
    return ticks


def _ticks_for(lo, hi):
    cand = [1, 2, 5, 10, 20, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000,
            1500, 2000, 3000, 5000, 7500, 10000, 15000, 20000, 50000]
    out = [c for c in cand if lo * 0.85 <= c <= hi * 1.15]
    if len(out) > 7:                      # za dużo etykiet = nieczytelna oś
        step = -(-len(out) // 7)
        out = out[::step]
    return out if len(out) >= 3 else []


def _finish(fig, ax, title, subtitle, source, out):
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.yaxis.grid(True, color=GRID, lw=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=9)
    fig.text(0.012, 0.962, title, fontsize=15, fontweight="bold", color=INK, ha="left")
    if subtitle:
        fig.text(0.012, 0.905, subtitle, fontsize=9.5, color=MUTE, ha="left")
    fig.text(0.012, 0.018, source, fontsize=8, color=MUTE, ha="left")
    fig.tight_layout(rect=(0.012, 0.05, 0.985, 0.875))
    fig.savefig(out, dpi=170)
    plt.close(fig)
    return out


def multiple_chart(series, med, p95, mn, ticker, label, out):
    """Mnożnik na tle własnego zakresu: pasmo min–p95, mediana, wartość dziś."""
    if not series:
        return None
    dates = [_ts(p[0]) for p in series]
    vals = [p[1] for p in series]

    fig, ax = plt.subplots(figsize=(11.4, 6.2))
    if mn is not None and p95 is not None:
        ax.fill_between(dates, mn, p95, color=BAND, zorder=0)
    if med is not None:
        ax.axhline(med, color=MUTE, lw=1.1, ls=(0, (5, 3)), zorder=2)

    ax.plot(dates, vals, color=BLUE, lw=2.3, zorder=3, solid_capstyle="round")

    lo, hi = min(vals + ([mn] if mn else [])), max(vals + ([p95] if p95 else []))
    pad = (hi - lo) * 0.10
    ax.set_ylim(max(0, lo - pad), hi + pad)

    # opisy po prawej, z białym tłem, żeby nie nachodziły na linię
    if p95 is not None:
        ax.text(dates[-1], p95, f"górna granica  {p95:.1f}", fontsize=8.5, color=MUTE,
                va="bottom", ha="right", bbox=dict(fc=BG, ec="none", alpha=.9, pad=1.2))
    if mn is not None:
        ax.text(dates[-1], mn, f"minimum 3Y  {mn:.1f}", fontsize=8.5, color=MUTE,
                va="top", ha="right", bbox=dict(fc=BG, ec="none", alpha=.9, pad=1.2))
    if med is not None:
        ax.text(dates[-1], med, f"mediana  {med:.1f}", fontsize=9, color=MUTE,
                va="center", ha="right", bbox=dict(fc=BG, ec="none", alpha=.9, pad=1.2))

    last = vals[-1]
    ax.scatter([dates[-1]], [last], s=46, color=BLUE, zorder=4, edgecolor=BG, linewidth=1.5)
    ax.annotate(f"{last:.1f}", (dates[-1], last), xytext=(9, 8), textcoords="offset points",
                fontsize=12, fontweight="bold", color=BLUE)

    ticks = _month_ticks(dates)
    if ticks:
        ax.set_xticks(ticks)
        ax.set_xticklabels([d.strftime("%m-%y") for d in ticks])
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.set_xlim(dates[0], dates[-1])
    ax.margins(x=0.005)

    return _finish(fig, ax, f"{ticker} · {label}",
                   "Dzienny mnożnik forward 1Y na tle własnego zakresu z 3 lat",
                   "Źródło: Scrab (FactSet)", out)


def pt_history_chart(rows, ticker, out):
    """Kurs vs cena docelowa: pasmo konserwatywna–optymistyczna."""
    if not rows:
        return None
    dates = [_ts(r[0]) for r in rows]
    price = [r[1] for r in rows]
    cons = [r[2] for r in rows]
    opt = [r[3] for r in rows]

    fig, ax = plt.subplots(figsize=(11.4, 6.8))
    ax.fill_between(dates, cons, opt, color=BAND2, zorder=1)
    ax.plot(dates, opt, color=GREEN, lw=1.5, ls=(0, (4, 2)), zorder=2)
    ax.plot(dates, cons, color=BLUE, lw=2.3, zorder=3)
    ax.plot(dates, price, color=INK, lw=2.0, zorder=4)

    ax.set_yscale("log")
    ticks = _ticks_for(min(price + cons), max(opt))
    # wąski zakres potrafi nie dać ani trzech ładnych progów — wtedy zostawiamy automat
    ax.yaxis.set_major_locator(FixedLocator(ticks) if ticks else MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: _num(v)))

    span = (dates[-1] - dates[0]).days
    ax.set_xlim(dates[0], dates[-1] + timedelta(days=int(span * 0.16)))
    ax.margins(x=0.005)

    for y, col, lab in ((price[-1], INK, "kurs"), (cons[-1], BLUE, "PT kons."), (opt[-1], GREEN, "PT opt.")):
        ax.scatter([dates[-1]], [y], s=42, color=col, zorder=5, edgecolor=BG, linewidth=1.3)
        ax.annotate(f"{_num(y)}", (dates[-1], y), xytext=(8, 0), textcoords="offset points",
                    fontsize=10.5, color=col, fontweight="bold", va="center")

    pot = (cons[-1] / price[-1] - 1) if price[-1] else None
    if pot is not None:
        ax.text(0.988, 0.06, f"potencjał dziś  {pot*100:+.0f}%", transform=ax.transAxes,
                fontsize=11, fontweight="bold", color=GREEN if pot > 0 else RED,
                va="bottom", ha="right",
                bbox=dict(boxstyle="round,pad=0.4", fc=BG, ec=GRID))

    handles = [Line2D([], [], color=INK, lw=2, label="kurs"),
               Line2D([], [], color=BLUE, lw=2.3, label="PT konserwatywna"),
               Line2D([], [], color=GREEN, lw=1.5, ls=(0, (4, 2)), label="PT optymistyczna")]
    ax.legend(handles=handles, loc="upper left", frameon=False, fontsize=9.5, ncol=3,
              bbox_to_anchor=(0, 1.06), handlelength=2.2, columnspacing=1.6)

    ticks = _month_ticks(dates)
    if ticks:
        ax.set_xticks(ticks)
        ax.set_xticklabels([d.strftime("%m-%y") for d in ticks])
    ax.xaxis.set_major_formatter(DateFormatter("%m-%y"))

    return _finish(fig, ax, f"{ticker} · cena vs cena docelowa",
                   "Model z kroczącym oknem 3 lat; zielone pasmo = przedział PT konserwatywna–optymistyczna",
                   "Skala logarytmiczna · źródło: Scrab (FactSet) · pionowe schodki = zmiana roku obrotowego w prognozie EPS",
                   out)


def potential_chart(rows, ticker, out):
    """Potencjał PT konserwatywnej do kursu w czasie."""
    if not rows:
        return None
    pts = [(r[0], (r[2] / r[1] - 1) * 100) for r in rows if r[1]]
    dates = [_ts(p[0]) for p in pts]
    pot = [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(11.4, 4.3))
    ax.fill_between(dates, 0, [max(p, 0) for p in pot], color="#CFEBD9", zorder=2)
    ax.fill_between(dates, 0, [min(p, 0) for p in pot], color="#F7D2D0", zorder=2)
    ax.plot(dates, pot, color=BLUE, lw=1.8, zorder=3)
    ax.axhline(0, color=INK, lw=1.0, zorder=4)
    ax.axhline(15, color=GREEN, lw=1.0, ls=(0, (4, 2)), zorder=3)
    ax.text(dates[1], 15, " próg +15%", fontsize=8.5, color=GREEN, va="bottom", ha="left",
            bbox=dict(fc=BG, ec="none", alpha=.75, pad=1.2))

    ax.annotate(f"{pot[-1]:+.0f}%", (dates[-1], pot[-1]), xytext=(9, 0), textcoords="offset points",
                fontsize=11.5, fontweight="bold", color=BLUE if pot[-1] >= 0 else RED, va="center")

    lo = min(pot + [-10])
    hi = max(pot + [20])
    ax.set_ylim(lo - 8, hi + 8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v:.0f}%"))
    ax.yaxis.set_minor_locator(NullLocator())
    ticks = _month_ticks(dates)
    if ticks:
        ax.set_xticks(ticks)
        ax.set_xticklabels([d.strftime("%m-%y") for d in ticks])
    ax.set_xlim(dates[0], dates[-1])
    ax.margins(x=0.005)

    return _finish(fig, ax, f"{ticker} · potencjał do ceny docelowej",
                   "PT konserwatywna ÷ kurs − 1; zielone pola = model widzi przewagę, czerwone = kurs powyżej wyceny",
                   "Źródło: Scrab (FactSet)", out)


def equity_chart(days, port, bench, bench_label, out, title="Wartość portfela"):
    """Krzywa wartości (start = 100) na tle benchmarku."""
    import matplotlib.dates as mdates
    dts = [datetime.strptime(d, "%Y-%m-%d") for d in days]
    fig, ax = plt.subplots(figsize=(11.4, 5.4))
    ax.plot(dts, port, color=BLUE, lw=2.4, solid_capstyle="round", label="Portfel", zorder=3)
    if bench:
        ax.plot(dts, bench, color=MUTE, lw=1.6, ls=(0, (5, 3)), label=bench_label, zorder=2)
    ax.axhline(100, color=GRID, lw=1.0, zorder=1)
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m.%y"))
    _finish(fig, ax, title, "start = 100 · dzisiejszy skład portfela, PLN", "źródło: Scrab (kursy), NBP (waluty)", out)
