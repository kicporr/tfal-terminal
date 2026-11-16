# -*- coding: utf-8 -*-
"""Kryteria z listy Treli, które da się policzyć z danych, które już mamy.

Tylko odczyt — nic nie pobiera. Wejście to seria metryk Scraba (datapoints)
plus gotowe metryki scoringu, wyjście to lista wierszy z wartością i statusem:

    {"q": 9, "label": "Trend kursu", "val": "...", "note": "...", "status": "ok"}

status: "ok" / "uwaga" / "zle" / "brak".
"""


def _pts(series, key):
    out = []
    for p in (series.get(key) or []):
        try:
            ts, v = int(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if v == v:                    # NaN odpada
            out.append((ts, v))
    out.sort(key=lambda x: x[0])
    return out


def _at(pts, ts):
    """Ostatni punkt o czasie <= ts."""
    lo, hi, best = 0, len(pts) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if pts[mid][0] <= ts:
            best = pts[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def trend(series, window=200):
    px = [v for _, v in _pts(series, "price")]
    if len(px) < 30:
        return None
    last = px[-1]
    sma = sum(px[-window:]) / min(len(px), window)
    high = max(px[-252:]) if len(px) >= 50 else max(px)
    return {
        "last": round(last, 2),
        "sma": round(sma, 2),
        "vs_sma": round(last / sma - 1, 4) if sma else None,
        "off_high": round(last / high - 1, 4) if high else None,
    }


def consensus_series(series):
    """Konsensus ceny docelowej w czasie: kurs × (1 + price_target_upside/100)."""
    ups = _pts(series, "price_target_upside")
    px = _pts(series, "price")
    out = []
    for ts, u in ups:
        p = _at(px, ts)
        if p and p[1] > 0:
            out.append((ts, p[1] * (1 + u / 100.0)))
    return out


def pt_revisions(series, days=90, months=6):
    """Rewizje konsensusu ceny docelowej.

    Scrab daje `price_target_upside` w procentach, więc sam poziom PT
    odtwarzamy jako cena × (1 + upside). Dzięki temu wzrost kursu nie jest
    mylony z podniesieniem ceny docelowej przez analityków.
    """
    pts = consensus_series(series)
    if len(pts) < 30:
        return None
    last_ts, last_pt = pts[-1]
    old = _at(pts, last_ts - days * 86400)
    chg = (last_pt / old[1] - 1) if (old and old[1]) else None
    up = down = 0
    for m in range(1, months + 1):
        a = _at(pts, last_ts - m * 30 * 86400)
        b = _at(pts, last_ts - (m - 1) * 30 * 86400)
        if a and b and abs(b[1] - a[1]) > 1e-9:
            if b[1] > a[1]:
                up += 1
            else:
                down += 1
    return {"pt": round(last_pt, 2), "chg_3m": (round(chg, 4) if chg is not None else None),
            "up": up, "down": down,
            "dir": "up" if up > down else ("down" if down > up else "flat")}


def eps_revisions(series, days=90, months=6):
    """Rewizje prognoz EPS 1Y: poziom vs 3 miesiące temu i liczba ruchów w górę/w dół.

    Procent zmiany liczymy tylko, gdy oba końce są dodatnie — przy stracie lub
    przejściu przez zero zmiana procentowa kłamie, więc zostają same kierunki.
    """
    pts = _pts(series, "eps_est_1y")
    if len(pts) < 30:
        return None
    last_ts, last_v = pts[-1]
    old = _at(pts, last_ts - days * 86400)
    chg = None
    if old and old[1] and last_v and old[1] > 0 and last_v > 0:
        chg = last_v / old[1] - 1
    up = down = 0
    for m in range(1, months + 1):
        a = _at(pts, last_ts - m * 30 * 86400)
        b = _at(pts, last_ts - (m - 1) * 30 * 86400)
        if a and b and abs(b[1] - a[1]) > 1e-9:
            if b[1] > a[1]:
                up += 1
            elif b[1] < a[1]:
                down += 1
    return {"eps": round(last_v, 3), "old": round(old[1], 3) if old else None,
            "chg_3m": round(chg, 4) if chg is not None else None,
            "up": up, "down": down,
            "dir": "up" if up > down else ("down" if down > up else "flat")}


def ps2y_growth(series, min_growth=1.0):
    """Autorski wskaźnik: PS (2Y forward) / średni roczny wzrost przychodów.

    PS2y bierzemy wprost z FactSetu (`forward_ps_ratio_2y`), a wzrost z relacji
    prognoz: PS1y/PS2y − 1 = (kurs/rev1) / (kurs/rev2) − 1 = rev2/rev1 − 1,
    czyli roczna dynamika między rokiem +1 i +2 (w procentach).

    Wskaźnik = PS2y / wzrost% — „ile płacisz za punkt wzrostu”, odpowiednik PEG
    dla sprzedaży. Wartości przy wzroście poniżej `min_growth` pomijamy: dzielenie
    przez ~0 daje śmieci.
    """
    ps1 = {int(ts): v for ts, v in _pts(series, "forward_ps_ratio")}
    ps2 = {int(ts): v for ts, v in _pts(series, "forward_ps_ratio_2y")}
    if not ps1 or not ps2:
        return {"points": [], "last": None, "n": 0}
    out = []
    for ts in sorted(set(ps1) & set(ps2)):
        a, b = ps1[ts], ps2[ts]
        if not a or not b or b <= 0 or a <= 0:
            continue
        growth = (a / b - 1) * 100.0
        if growth < min_growth:
            continue
        out.append((ts, b / growth))
    if not out:
        return {"points": [], "last": None, "n": 0}
    ts, ratio = out[-1]
    return {"points": out, "n": len(out),
            "last": {"ts": ts, "ratio": round(ratio, 4),
                     "ps2y": round(ps2[ts], 2), "growth": round((ps1[ts] / ps2[ts] - 1) * 100, 1)}}


def margin_trend(series, quarters=4):
    om = _pts(series, "operating_margin")
    if len(om) < 2:
        return None
    last = om[-1][1]
    back = om[max(0, len(om) - 1 - quarters)][1]
    return {"last": round(last, 2), "back": round(back, 2), "chg": round(last - back, 2)}


def _status(ok, warn):
    return "ok" if ok else ("uwaga" if warn else "zle")


def build(series, m, d, mcap=None):
    """Lista kryteriów TFL 3-11 (1 i 2 są jakościowe — poza modelem)."""
    rows = []

    # 3. Przychody: historia i prognoza
    r3, r1 = m.get("rev3y_pct"), m.get("rev1y_pct")
    if r3 is not None or r1 is not None:
        both = (r3 or 0) > 0 and (r1 or 0) > 0
        any_pos = (r3 or 0) > 0 or (r1 or 0) > 0
        rows.append({"q": 3, "label": "Przychody rosną (3Y i prognoza 1Y)",
                     "val": f"{r3:+.0f}% / {r1:+.0f}%" if (r3 is not None and r1 is not None) else "—",
                     "status": _status(both, any_pos)})

    # 4. Model biznesowy: marża i jej kierunek
    mt = margin_trend(series)
    if mt:
        good = mt["last"] > 0 and mt["chg"] >= -1
        ok = mt["last"] > 0 and mt["chg"] >= -3
        rows.append({"q": 4, "label": "Marża operacyjna i jej trend",
                     "val": f"{mt['last']:.1f}% ({mt['chg']:+.1f} pkt r/r)",
                     "status": _status(good, ok)})

    # 5. Zadłużenie
    lev = m.get("net_debt_ebitda")
    if lev is not None:
        rows.append({"q": 5, "label": "Zadłużenie (dług netto / EBITDA)",
                     "val": f"{lev:.2f}x",
                     "status": _status(lev < 2, lev < 3.5)})

    # 6. Wycena względem własnej historii
    pe_rel, ps_rel = d.get("pe_rel"), d.get("ps_rel")
    if pe_rel is not None or ps_rel is not None:
        worst = max([x for x in (pe_rel, ps_rel) if x is not None], default=None)
        if worst is not None:
            rows.append({"q": 6, "label": "Wycena vs własna mediana",
                         "val": f"P/E {pe_rel:.2f}x · P/S {ps_rel:.2f}x" if (pe_rel is not None and ps_rel is not None)
                                else (f"P/E {pe_rel:.2f}x" if pe_rel is not None else f"P/S {ps_rel:.2f}x"),
                         "status": _status(worst < 0.9, worst < 1.15)})

    # 7. Upside do konsensusu
    up = d.get("upside")
    if up is not None:
        rows.append({"q": 7, "label": "Potencjał do ceny docelowej",
                     "val": f"{up*100:+.1f}%",
                     "status": _status(up > 0.15, up > 0)})

    # 8. Rewizje ceny docelowej
    pr = pt_revisions(series)
    if pr:
        chg = pr["chg_3m"]
        if chg is not None:
            note = f"{pr['up']} w górę / {pr['down']} w dół (6 mies.)"
            rows.append({"q": 8, "label": "Rewizje ceny docelowej (3 mies.)",
                         "val": f"{chg*100:+.1f}% · {note}",
                         "status": _status(chg > 0.03, chg > -0.03)})

    # 9. Trend kursu
    tr = trend(series)
    if tr:
        rows.append({"q": 9, "label": "Trend kursu vs SMA200",
                     "val": f"{tr['vs_sma']*100:+.1f}% · {tr['last']:.2f} vs {tr['sma']:.2f}",
                     "status": _status(tr["vs_sma"] is not None and tr["vs_sma"] > 0,
                                       tr["off_high"] is not None and tr["off_high"] > -0.15)})

    # 10. Kapitalizacja / płynność
    if mcap:
        big = mcap >= 2e9
        cap = (f"{mcap/1e12:.1f} bln" if mcap >= 1e12
               else f"{mcap/1e9:.0f} mld" if mcap >= 1e9 else f"{mcap/1e6:.0f} mln")
        rows.append({"q": 10, "label": "Kapitalizacja (manipulacja, płynność)", "val": cap,
                     "status": _status(big, mcap >= 5e8)})

    # 11. Rewizje prognoz EPS (nie tylko ceny docelowej)
    er = eps_revisions(series)
    if er and (er["chg_3m"] is not None or er["up"] + er["down"]):
        if er["chg_3m"] is not None:
            val = f"{er['chg_3m']*100:+.1f}% · {er['up']}↑ / {er['down']}↓"
            st = _status(er["chg_3m"] > 0.02, er["chg_3m"] > -0.02)
        else:
            val = f"{er['up']}↑ / {er['down']}↓ (6 mies.)"
            st = _status(er["up"] > er["down"], er["up"] >= er["down"])
        rows.append({"q": 11, "label": "Rewizje prognoz EPS (3 mies.)", "val": val, "status": st})

    return {"rows": rows, "trend": tr, "pt_rev": pr, "margin": mt, "eps_rev": er,
            "ok": sum(1 for r in rows if r["status"] == "ok"), "n": len(rows)}


def summary(t):
    """Jedno zdanie podsumowania kryteriów TFL."""
    if not t:
        return ""
    parts = []
    pr = t.get("pt_rev") or {}
    if pr.get("chg_3m") is not None:
        d = "w górę" if pr["chg_3m"] > 0.03 else ("w dół" if pr["chg_3m"] < -0.03 else "stabilnie")
        parts.append(f"PT {pr['chg_3m']*100:+.0f}% w 3 mies. ({d})")
    tr = t.get("trend") or {}
    if tr.get("vs_sma") is not None:
        parts.append(f"kurs {tr['vs_sma']*100:+.0f}% vs SMA200")
    ok = t.get("ok")
    if ok is not None and t.get("n"):
        parts.append(f"{ok}/{t['n']} kryteriów TFL na tak")
    return " · ".join(parts)
