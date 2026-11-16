# -*- coding: utf-8 -*-
"""Historia ceny docelowej: co model mówiłby w każdym dniu z ostatnich lat.

Dla każdego dnia liczymy mediane i maksimum mnoznika z ostatnich 3 lat (okno kroczace),
mnozymy przez prognozy z tego dnia i usredniamy po P/E i P/S. Dzieki temu widac, jak
cena docelowa przesuwala sie razem z rewizjami prognoz i zmianami wyceny.
"""
import bisect
import math
from datetime import datetime, timezone

DAY = 86400
WINDOW = 3 * 365.25 * DAY


def _ffill_series(dp):
    """Zwraca (daty, wartosci) posortowane; do uzupelniania ostatnia znana wartoscia."""
    pts = [(int(t), float(v)) for t, v in dp if isinstance(v, (int, float))]
    pts.sort()
    return pts


def _value_at(pts, ts):
    """Ostatnia znana wartosc na moment ts (forward fill)."""
    i = bisect.bisect_right([p[0] for p in pts], ts) if len(pts) < 400 else None
    if i is None:
        # szybsza sciezka dla dlugich serii
        lo, hi = 0, len(pts) - 1
        if ts < pts[0][0]:
            return None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if pts[mid][0] <= ts:
                lo = mid
            else:
                hi = mid - 1
        return pts[lo][1]
    return pts[i - 1][1] if i > 0 else None


def _rolling_stats(pairs, window=WINDOW, min_obs=60):
    """Dla kazdego punktu: mediana i maksimum z okna kroczacego."""
    out = []
    vals = []          # posortowana lista wartosci w oknie
    queue = []         # (ts, val) do usuwania
    qi = 0
    for ts, v in pairs:
        bisect.insort(vals, v)
        queue.append((ts, v))
        while queue and queue[0][0] < ts - window:
            old_ts, old_v = queue.pop(0)
            idx = bisect.bisect_left(vals, old_v)
            if idx < len(vals) and vals[idx] == old_v:
                vals.pop(idx)
        if len(vals) >= min_obs:
            n = len(vals)
            med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
            # gorna granica jako p95, nie maksimum - pojedynczy skok przy zalamaniu
            # EPS psulby skale na lata
            out.append((ts, med, vals[min(n - 1, int(0.95 * (n - 1)))]))
    return out


def build(series, every=1):
    """Historia PT: każda sesja = kurs + PT z mediany P/E i/lub P/S.

    Wystarczy JEDNA noga (P/E albo P/S) — wcześniej wymagane były obie, więc spółki
    bez historii przychodów na akcję nie miały w ogóle wykresu. Statystyki mnożnika
    dopasowuję po najbliższej wcześniejszej dacie, bo kalendarze sesji bywają różne.
    """
    def clean(metric):
        dp = [p for p in (series.get(metric) or [])
              if isinstance(p, (list, tuple)) and len(p) >= 2
              and isinstance(p[1], (int, float)) and not isinstance(p[1], bool)]
        return _ffill_series(sorted(dp, key=lambda p: p[0]))

    pe, ps = clean("forward_pe_ratio"), clean("forward_ps_ratio")
    eps, rps, px = clean("eps_est_1y"), clean("revenue_per_share_ntm"), clean("price")
    if not px or (not pe and not ps):
        return []

    pe_s = sorted(_rolling_stats(pe)) if pe else []
    ps_s = sorted(_rolling_stats(ps)) if ps else []
    pe_k, ps_k = [x[0] for x in pe_s], [x[0] for x in ps_s]

    def at(keys, stats, ts):
        i = bisect.bisect_right(keys, ts) - 1
        return stats[i][1:] if i >= 0 else None

    rows = []
    for ts, price in px:
        if not price or price <= 0:
            continue
        e, r = _value_at(eps, ts), _value_at(rps, ts)
        cons, opt, med_pe = [], [], None
        sp = at(pe_k, pe_s, ts)
        if sp and e and e > 0:
            med_pe, max_pe = sp
            cons.append(med_pe * e)
            opt.append(max_pe * e)
        ss = at(ps_k, ps_s, ts)
        if ss and r and r > 0:
            med_ps, max_ps = ss
            cons.append(med_ps * r)
            opt.append(max_ps * r)
        if not cons:
            continue
        rows.append((ts, price, sum(cons) / len(cons), sum(opt) / len(opt), med_pe, e))

    every = max(1, int(every or 1))
    if every > 1:
        rows = rows[::every]
    if len(rows) > 260:
        step = max(1, math.ceil(len(rows) / 240))
        thinned = rows[::step]
        if thinned and thinned[-1] != rows[-1]:
            thinned.append(rows[-1])
        rows = thinned
    return rows
