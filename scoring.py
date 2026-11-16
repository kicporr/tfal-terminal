# -*- coding: utf-8 -*-
"""Model scoringowy portfela (0-100) na danych Scraba.

16 kryteriow w czterech blokach: jakosc biznesu, bezpieczenstwo bilansu, wycena, rewizje.
Kazde kryterium oceniane 1-10 wg jawnych progów, potem wazona suma.
Brak danych nie zeruje oceny - wagi sa renormowane do dostepnych kryteriow.
"""
import statistics as st

import tfl

# (klucz, etykieta, waga)
CRITERIA = [
    ("rev3y", "Wzrost przychodów 3Y", 12),
    ("rev1y", "Prognoza przychodów 1Y", 10),
    ("eps1y", "Prognoza EPS 1Y", 10),
    ("epsreal", "Wzrost EPS (r/r)", 8),
    ("margin", "Marża operacyjna", 8),
    ("roa", "Zwrot z aktywów", 5),
    ("roic", "Zwrot z kapitału (ROIC)", 8),
    ("fcf_conv", "Konwersja zysku w gotówkę", 6),
    ("leverage", "Dług netto / EBITDA", 12),
    ("current", "Płynność bieżąca", 5),
    ("pe_rel", "P/E vs mediana 3Y", 12),
    ("ps_rel", "P/S vs mediana 3Y", 8),
    ("ev_rel", "EV/EBITDA vs mediana 3Y", 8),
    ("pt_up", "Potencjał do konsensusu", 8),
    # rewizje ceny docelowej i prognoz EPS to ten sam sygnał, ale PT zmienia się też
    # razem z kursem — dlatego EPS (fundamentalny) waży więcej niż PT (pochodny)
    ("pt_rev", "Rewizje ceny docelowej (3M)", 6),
    ("eps_rev", "Rewizje prognoz EPS (3M)", 8),
]


def _sc(v, table, top):
    """Punkty interpolowane liniowo między progami (a nie schodkami).

    Dzięki temu 2,49 i 2,50 nie różnią się o cztery punkty, a ocena różnicuje
    spółki w całym przedziale zamiast grupować je na kilku poziomach.
    Powyżej ostatniego progu obowiązuje `top`, poniżej pierwszego — pierwsza wartość.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    pts = sorted((float(th), float(p)) for th, p in table)
    if v <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if v < x1:
            if x1 == x0:
                return y1
            return round(y0 + (y1 - y0) * (v - x0) / (x1 - x0), 2)
    return top


def _pct(x):
    return None if x is None else x * 100.0


def score_parts(d):
    """d: slownik z surowymi wartosciami -> {klucz: punkty 1-10}."""
    p = {}
    p["rev3y"] = _sc(d.get("rev3y_pct"), [(-1e9, 1), (0, 2), (3, 4), (7, 6), (12, 8), (20, 9.5)], 10)
    p["rev1y"] = _sc(d.get("rev1y_pct"), [(-1e9, 1), (0, 2), (3, 4), (7, 6), (15, 8), (25, 9.5)], 10)
    p["eps1y"] = _sc(d.get("eps1y_pct"), [(-1e9, 1), (0, 2), (5, 4), (10, 6), (20, 8), (35, 9.5)], 10)
    p["epsreal"] = _sc(d.get("eps_growth"), [(-1e9, 1), (0, 2), (5, 4), (15, 6), (30, 8), (60, 9.5)], 10)
    p["margin"] = _sc(d.get("op_margin"), [(-1e9, 1), (0, 2), (5, 4), (10, 6), (15, 7.5), (25, 9)], 10)
    p["roa"] = _sc(d.get("roa"), [(-1e9, 1), (0, 2), (2, 4), (5, 6), (8, 7.5), (15, 9)], 10)
    # ROIC: czy biznes zarabia więcej niż koszt kapitału (15% to umowna granica „dobrego biznesu")
    p["roic"] = _sc(d.get("roic"), [(-1e9, 1), (0, 1), (5, 3), (10, 5), (15, 7), (25, 8.5)], 10)
    # konwersja FCF/zysk: 100% = zysk w całości zamienia się w gotówkę
    p["fcf_conv"] = _sc(d.get("fcf_conv"), [(-1e9, 1), (0, 1), (40, 3), (60, 5), (80, 7), (100, 8.5)], 10)
    p["leverage"] = _sc(d.get("net_debt_ebitda"), [(-1e9, 10), (0.1, 10), (1, 9), (2, 7), (3, 5), (4, 3)], 1)
    p["current"] = _sc(d.get("current_ratio"), [(-1e9, 1), (0.8, 1), (1.0, 4), (1.5, 7), (2.5, 10)], 10)
    pe = d.get("pe_rel")
    ps = d.get("ps_rel")
    p["pe_rel"] = _sc(pe if (isinstance(pe, (int, float)) and pe > 0) else None,
                      [(-1e9, 10), (0.7, 10), (0.9, 8), (1.1, 6), (1.3, 4)], 2)
    p["ps_rel"] = _sc(ps if (isinstance(ps, (int, float)) and ps > 0) else None,
                      [(-1e9, 10), (0.7, 10), (0.9, 8), (1.1, 6), (1.3, 4)], 2)
    ev = d.get("ev_rel")
    p["ev_rel"] = _sc(ev if (isinstance(ev, (int, float)) and ev > 0) else None,
                      [(-1e9, 10), (0.7, 10), (0.9, 8), (1.1, 6), (1.3, 4)], 2)
    p["pt_up"] = _sc(d.get("pt_upside"), [(-1e9, 1), (0, 1), (10, 4), (25, 6), (50, 8)], 10)
    # premia za podnoszenie konsensusu, kara za cięcie: -10% → 2 pkt, 0% → 5, +10% → 8,5
    p["pt_rev"] = _sc(d.get("pt_rev_3m"), [(-1e9, 1), (-10, 2), (0, 5), (5, 7), (10, 8.5)], 10)
    # prognoza EPS to mniej zaszumiony sygnał niż cena docelowa, więc progi są ciaśniejsze:
    # cięcie o 10% to już zła wiadomość, podniesienie o 10% — mocna
    p["eps_rev"] = _sc(d.get("eps_rev_pct"),
                       [(-1e9, 1), (-10, 2), (-2, 4), (0, 5), (3, 7), (8, 9)], 10)
    return p


def total_raw(parts):
    """Niezaokrąglona ocena — rating musi porównywać się z progami na surowej wartości."""
    num = den = 0.0
    for key, _, w in CRITERIA:
        v = parts.get(key)
        if v is None:
            continue
        num += v * w
        den += w
    return (num / den * 10) if den else None


def total_score(parts):
    raw = total_raw(parts)
    return round(raw, 1) if raw is not None else None


def rating(total):
    if total is None:
        return "—"
    if total >= 75:
        return "A"
    if total >= 60:
        return "B"
    if total >= 45:
        return "C"
    return "D"


LABELS = {k: lab for k, lab, _ in CRITERIA}
SHORT = {
    "rev3y": "Przychody 3Y", "rev1y": "Prognoza przych. 1Y", "eps1y": "Prognoza EPS 1Y",
    "epsreal": "Wzrost EPS r/r", "margin": "Marża oper.", "roa": "ROA",
    "leverage": "Dług/EBITDA", "current": "Płynność", "pe_rel": "P/E vs med.",
    "ps_rel": "P/S vs med.", "ev_rel": "EV/EBITDA vs med.", "pt_up": "Potencjał kons.",
    "pt_rev": "Rewizje PT 3M", "roic": "ROIC", "fcf_conv": "FCF/zysk",
    "eps_rev": "Rewizje EPS 3M",
}
WEIGHTS = {k: w for k, _, w in CRITERIA}


def highlights(parts):
    """Dwie najmocniejsze (>=7) i dwie najslabsze (<7) kategorie."""
    known = [(k, v) for k, v in parts.items() if v is not None]
    if not known:
        return "", ""
    strong = [x for x in sorted(known, key=lambda x: -x[1]) if x[1] >= 7][:2]
    weak = [x for x in sorted(known, key=lambda x: x[1]) if x[1] < 7][:2]
    fmt = lambda xs: ", ".join(f"{SHORT[k]} {v:.1f}" for k, v in xs) if xs else "—"
    return fmt(strong), fmt(weak)


def series_change(dp, years):
    """Zmiana wartości w ciągu 'years' lat — na posortowanej serii i tylko gdy historia sięga okna."""
    pts = sorted((float(p[0]), float(p[1])) for p in (dp or [])
                 if isinstance(p, (list, tuple)) and len(p) >= 2
                 and isinstance(p[1], (int, float)) and not isinstance(p[1], bool))
    if len(pts) < 30:
        return None
    end_ts, end_val = pts[-1]
    span_days = (end_ts - pts[0][0]) / 86400
    if span_days < years * 365.25 * 0.9:
        return None                      # za krótka historia — nie udajemy "3Y"
    target = end_ts - years * 365.25 * 86400
    start = next((val for ts, val in pts if ts >= target), None)
    if not start or start <= 0 or end_val is None:
        return None
    return (end_val / start - 1)


def build_metrics(series):
    """series: {metric: datapoints} jednego tickera -> slownik wartosci."""
    def last(m):
        dp = series.get(m) or []
        return dp[-1][1] if dp else None

    rev_ps = series.get("revenue_per_share_ntm") or []
    eps_est = series.get("eps_est_1y") or []
    ebitda = last("ebitda")
    net_debt = last("net_debt")

    def ttm(metric):
        """Suma ostatnich czterech kwartałów (sprawozdania są kwartalne)."""
        vals = [p[1] for p in (series.get(metric) or [])
                if isinstance(p, (list, tuple)) and len(p) >= 2
                and isinstance(p[1], (int, float)) and not isinstance(p[1], bool)]
        return sum(vals[-4:]) if len(vals) >= 4 else None

    fcf_ttm, ni_ttm = ttm("free_cash_flow"), ttm("net_income")
    fcf_conv = (fcf_ttm / ni_ttm * 100.0) if (fcf_ttm is not None and ni_ttm and ni_ttm > 0) else None

    # EV/EBITDA względem własnej mediany 3Y — trzecia noga wyceny, odporna na strukturę kapitału
    ev_dp = [(p[0], p[1]) for p in (series.get("ev_ebitda") or [])
             if isinstance(p, (list, tuple)) and len(p) >= 2
             and isinstance(p[1], (int, float)) and p[1] > 0]
    ev_last = ev_dp[-1][1] if ev_dp else None
    ev_med = None
    if ev_dp:
        cut = ev_dp[-1][0] - 3 * 365.25 * 86400
        vals = [v for ts, v in ev_dp if ts >= cut]
        ev_med = st.median(vals) if vals else None
    ev_rel = (ev_last / ev_med) if (ev_last and ev_med) else None

    d = dict(
        rev3y_pct=_pct(series_change(rev_ps, 3)),
        rev1y_pct=_pct(series_change(rev_ps, 1)),
        eps1y_pct=_pct(series_change(eps_est, 1)),
        eps_growth=last("eps_growth"),
        op_margin=last("operating_margin"),
        roa=last("return_on_assets"),
        current_ratio=last("current_ratio"),
        pt_upside=last("price_target_upside"),
        pt_rev_3m=(tfl.pt_revisions(series) or {}).get("chg_3m"),
        eps_rev_pct=_pct((tfl.eps_revisions(series) or {}).get("chg_3m")),
        roic=last("return_on_invested_capital"),
        fcf_conv=fcf_conv,
        ev_rel=ev_rel,
    )
    if ebitda and ebitda > 0 and net_debt is not None:
        d["net_debt_ebitda"] = net_debt / (ebitda * 4)  # EBITDA kwartalny -> roczny
    else:
        d["net_debt_ebitda"] = None
    return d
