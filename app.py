# -*- coding: utf-8 -*-
"""Aplikacja lokalna: jedno miejsce do oceny spółki, portfela i ryzyka.

Uruchomienie:  python app.py   (potem http://127.0.0.1:8765)
"""
import glob
import json
import math
import re
import os
import statistics as st
import sys
import threading
import time
from datetime import datetime, timezone, date, timedelta

import numpy as np
from flask import Flask, jsonify, request, send_from_directory, abort

import scrab_client as sc
import scoring
import xtb_import
import wyniki
import history as hist
import tfl
# matplotlib (~0,6-1,5 s importu) ładujemy dopiero przy rysowaniu wykresów —
# dzięki temu okno aplikacji wstaje szybciej.

# W exe (PyInstaller) zasoby są w środku paczki, a dane muszą leżeć obok .exe —
# inaczej każdy zapis ginie w katalogu tymczasowym przy zamknięciu.
FROZEN = bool(getattr(sys, "frozen", False))
BASE = sys._MEIPASS if FROZEN else os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(sys.executable) if FROZEN else BASE
WEB = os.path.join(BASE, "web")
CACHE = os.path.join(HERE, "_cache")
CHARTS = os.path.join(HERE, "_charts")
STATE = os.path.join(HERE, "state.json")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(CHARTS, exist_ok=True)

ALL = sc.METRICS + sc.EXTRA_METRICS
TTL = 4 * 3600
app = Flask(__name__, static_folder=None)


@app.errorhandler(sc.ScrabError)
def _scrab_error(e):
    """Brak sesji Scrab nie może kończyć się surowym tracebackiem w UI."""
    return jsonify({"error": "Brak sesji Scrab — dodaj plik scrab_session.json i odśwież dane."}), 503
_lock = threading.Lock()


# ---------- cache danych ----------
def _cache_path(key):
    return os.path.join(CACHE, f"{key.replace('.', '_')}.json")


def _load_json_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump_json_file(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _seed_from_rebalans():
    """Pierwsze uruchomienie: przenieś stan z Rebalans.xlsx, żeby nic nie zginęło."""
    p = os.path.join(HERE, "Rebalans.xlsx")
    if not os.path.exists(p):
        return None
    try:
        from openpyxl import load_workbook
        ws = load_workbook(p, data_only=True)["Rebalans"]
        method = str(ws["D4"].value or "inv_vol").strip().lower()
        cash = float(ws["D5"].value or 100000)
        max_pos = float(ws["D6"].value or 0.05)
        max_sec = float(ws["D7"].value or 0.25)
        frac = str(ws["D8"].value or "nie").strip().lower().startswith("t")
        tickers, shares = [], {}
        for r in range(11, ws.max_row + 1):
            t = ws.cell(row=r, column=1).value
            if not t:
                continue
            t = str(t).strip().upper()
            if not t or len(t) > 12:
                continue
            tickers.append(t)
            try:
                shares[t] = int(ws.cell(row=r, column=2).value or 0)
            except Exception:
                shares[t] = 0
        if not tickers:
            return None
        return {"tickers": tickers, "shares": shares, "cash": cash, "method": method,
                "max_pos": max_pos, "max_sec": max_sec, "fractional": frac,
                "_migrated_from": "Rebalans.xlsx"}
    except Exception:
        return None


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                s = json.load(f)
            s.setdefault("tickers", [])
            s.setdefault("shares", {})
            s.setdefault("watchlist", [])
            return s
        except Exception as e:
            # NIGDY nie nadpisujemy uszkodzonego stanu danymi z arkusza
            bad = STATE + f".bad-{int(time.time())}"
            try:
                os.replace(STATE, bad)
            except OSError:
                bad = STATE
            bak = STATE + ".bak"
            if os.path.exists(bak):
                try:
                    with open(bak, encoding="utf-8") as f:
                        s = json.load(f)
                    s.setdefault("tickers", [])
                    s.setdefault("shares", {})
                    s.setdefault("watchlist", [])
                    return s
                except Exception:
                    pass
            raise RuntimeError(
                f"state.json jest nieczytelny ({e.__class__.__name__}). "
                f"Kopia przeniesiona do {os.path.basename(bad)} — popraw plik albo go usuń, żeby zacząć od nowa.")
    seeded = _seed_from_rebalans()
    if seeded:
        save_state(seeded)
        return seeded
    # awaryjnie: tickery z Portfel.xlsx
    tickers = []
    p = os.path.join(HERE, "Portfel.xlsx")
    if os.path.exists(p):
        try:
            from openpyxl import load_workbook
            ws = load_workbook(p)["Portfel"]
            for r in range(5, ws.max_row + 1):
                v = ws.cell(row=r, column=1).value
                if v:
                    tickers.append(str(v).strip().upper())
        except Exception:
            pass
    tickers = tickers or ["MU", "AAPL", "MSFT", "NVDA", "ALE.WA", "PKN.WA", "DNP.WA"]
    stt = {"tickers": tickers, "shares": {t: 0 for t in tickers}, "cash": 100000,
           "method": "inv_vol", "max_pos": 0.05, "max_sec": 0.25, "fractional": False}
    save_state(stt)
    return stt


def _atomic_write_json(path, obj):
    """Unikalny plik tymczasowy + replace; wcześniejsza wersja ląduje w .bak."""
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        if os.path.exists(path):
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def save_state(s):
    _atomic_write_json(STATE, s)


def fx_rates(extra=None):
    """Kursy NBP (PLN za 1 jednostkę). Dociąga brakujace waluty, cache 12 h.

    Brak kursu NIE jest podstawiany jako 1.0 — taka waluta jest zwracana jako None
    i wykluczana z sum, zeby nie pokazywac cichych bzdur.
    """
    path = os.path.join(CACHE, "fx.json")
    cur = {"PLN": 1.0, "_status": "ok", "_date": date.today().isoformat()}
    if os.path.exists(path):
        try:
            cur = _load_json_file(path)
        except Exception:
            pass
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < 12 * 3600
    need = {c.upper() for c in (extra or []) if c and c.upper() != "PLN"}
    missing = [c for c in need if cur.get(c) is None]
    if fresh and not missing:
        return cur
    if missing or not fresh:
        import urllib.request
        todo = missing or [c for c in ("USD", "EUR", "GBP", "CHF") if c not in cur]
        try:
            for c in todo:
                url = f"https://api.nbp.pl/api/exchangerates/rates/A/{c}/?format=json"
                with urllib.request.urlopen(url, timeout=7) as resp:
                    j = json.loads(resp.read().decode("utf-8"))
                cur[c] = float(j["rates"][0]["mid"])
                cur["_date"] = j["rates"][0]["effectiveDate"]
            cur["_status"] = "ok"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cur, f)
            return cur
        except Exception:
            cur["_status"] = "stale" if any(k in cur for k in ("USD", "EUR")) else "missing"
    return cur


FX_HIST = os.path.join(CACHE, "fx_hist.json")


def nbp_rate_hist(ccy, iso_date=None):
    """Ile PLN za 1 jednostkę waluty — kurs NBP z dnia (albo ostatniego sprzed).

    Potrzebne do przeliczania opłat XTB (kwoty w PLN) na walutę instrumentu.
    Brak kursu → kurs bieżący, a jak i tego nie ma → None (bez cichego 1.0).
    """
    c = (ccy or "PLN").upper()
    if c == "PLN":
        return 1.0
    if not iso_date:
        return fx_one(c)
    hist = {}
    if os.path.exists(FX_HIST):
        try:
            hist = _load_json_file(FX_HIST)
        except Exception:
            hist = {}
    key = f"{c}:{iso_date}"
    if key in hist:
        return hist[key]
    val = None
    try:
        import urllib.request
        d0 = date.fromisoformat(iso_date)
        for delta in range(0, 7):
            d = (d0 - timedelta(days=delta)).isoformat()
            url = f"https://api.nbp.pl/api/exchangerates/rates/A/{c}/{d}/?format=json"
            try:
                with urllib.request.urlopen(url, timeout=6) as resp:
                    j = json.loads(resp.read().decode("utf-8"))
                val = float(j["rates"][0]["mid"])
                break
            except Exception:
                continue
    except Exception:
        val = None
    if val is None:
        val = fx_one(c)
    if val:
        hist[key] = val
        try:
            _atomic_write_json(FX_HIST, hist)
        except Exception:
            pass
    return val


def fx_one(cur_name, rates=None):
    """Kurs waluty albo None, gdy NBP go nie zna — nigdy cichego 1.0."""
    c = (cur_name or "PLN").upper()
    if c == "PLN":
        return 1.0
    rates = rates if rates is not None else fx_rates([c])
    v = rates.get(c)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def currency_of(sec_map, t):
    return ((sec_map.get(t) or {}).get("currency") or ("PLN" if t.endswith(".WA") else "USD")).upper()


def cached_post(tickers):
    """Jedno zapytanie na listę tickerów, z cache na dysku (TTL 4h)."""
    import hashlib
    # wersja w kluczu: zmiana listy metryk musi unieważnić stare bundle
    raw = "|".join(sorted(set(t.upper() for t in tickers))) + "|v3"
    key = hashlib.sha1(raw.encode()).hexdigest()[:24]
    path = _cache_path("bundle_" + key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < TTL:
        try:
            return _load_json_file(path)
        except Exception:
            pass
    data = sc.post(tickers, ALL)
    if not isinstance(data, list):
        raise RuntimeError("Odpowiedź API nie jest listą — nie zapisuję tego do cache.")
    grouped = {}
    for s in data:
        if not isinstance(s, dict):
            continue
        tk = (s.get("ticker") or "").upper()
        if tk and s.get("datapoints"):
            grouped.setdefault(tk, {})[s.get("metric")] = s.get("datapoints")
    if not grouped:
        # pusta odpowiedź nie może zatruć cache na 4 h
        return {}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(grouped, f)
    prune_cache()
    return grouped


def _bundle_files():
    return sorted(glob.glob(os.path.join(CACHE, "bundle_*.json")),
                  key=os.path.getmtime, reverse=True)


def prune_cache(keep=60):
    """Trzyma najnowsze bundle, reszta out — inaczej cache rośnie bez końca."""
    files = _bundle_files()
    for p in files[keep:]:
        try:
            os.remove(p)
        except OSError:
            pass
    return len(files[keep:])


def newest_bundle_ts():
    files = _bundle_files()
    for f in files:
        try:
            return datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            continue
    return None


def scrab_session_info():
    p = os.path.join(HERE, "scrab_session.json")
    if not os.path.exists(p):
        return {"valid": False, "reason": "brak pliku sesji", "days_left": None}
    try:
        d = _load_json_file(p)
    except Exception:
        return {"valid": False, "reason": "nieczytelna sesja", "days_left": None}
    saved = d.get("saved")
    exp_ts = None
    for c in d.get("cookies") or []:
        for k in ("expires", "expirationDate", "expiry"):
            v = c.get(k)
            if isinstance(v, (int, float)) and v > 0:
                exp_ts = min(exp_ts, v) if exp_ts else v
    if exp_ts:
        exp = datetime.fromtimestamp(exp_ts)
    elif saved:
        try:
            exp = datetime.fromisoformat(saved) + timedelta(days=14)
        except Exception:
            exp = None
    else:
        exp = None
    if exp is None:
        return {"valid": False, "reason": "brak daty sesji", "days_left": None}
    left = (exp - datetime.now()).days
    return {"valid": left >= 0, "expires": exp.strftime("%Y-%m-%d"), "days_left": left,
            "saved": saved}


def sector_cache():
    path = os.path.join(CACHE, "sectors2.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        try:
            return _load_json_file(path)
        except Exception:
            pass
    return {}


def fill_sectors(tickers):
    sec = sector_cache()
    missing = [t for t in tickers if t not in sec]
    if missing:
        try:
            import yfinance as yf
            for t in missing:
                try:
                    info = yf.Ticker(t).info or {}
                    nm = info.get("longName") or info.get("shortName") or t
                    if nm.endswith(" PLN") or nm.endswith(" USD"):
                        nm = nm[:-4]
                    sec[t] = {"sector": info.get("sector") or "—",
                              "industry": info.get("industry") or "—",
                              "name": nm,
                              "currency": (info.get("currency") or ("PLN" if t.endswith(".WA") else "USD")).upper(),
                              "mcap": info.get("marketCap")}
                except Exception:
                    sec[t] = {"sector": "—", "industry": "—", "name": t,
                              "currency": "PLN" if t.endswith(".WA") else "USD", "mcap": None}
            # nie zapisujemy porażek: wpis "—" przez 7 dni psułby peers i filtr sektorów
            good = {k: v for k, v in sec.items() if (v or {}).get("sector") not in ("—", None)}
            if good:
                with open(os.path.join(CACHE, "sectors2.json"), "w", encoding="utf-8") as f:
                    json.dump(good, f)
        except Exception:
            pass
    return sec


# ---------- przeglądy: co widziałem ostatnim razem ----------
REVIEWS = os.path.join(HERE, "reviews.json")
MODEL_VER = "2.0"
MODEL_DESC = ("PT: mediana i mediana rocznych maksimów z okna 3Y · scoring 16 kryteriów · "
              "bez analizy miękkiej")
REVIEW_KEEP = 20

CHECKLIST = os.path.join(HERE, "checklist.json")
CHECKLIST_N = 5


def load_checklist(t):
    """Stan checklisty przed decyzją (5 pytań) — zapisany, nie dekoracyjny."""
    try:
        d = _load_json_file(CHECKLIST) if os.path.exists(CHECKLIST) else {}
    except Exception:
        d = {}
    v = d.get(t) or {}
    items = [bool(x) for x in (v.get("items") or [])][:CHECKLIST_N]
    return {"items": items + [False] * (CHECKLIST_N - len(items)),
            "updated": v.get("updated")}


def save_checklist(t, items):
    try:
        d = _load_json_file(CHECKLIST) if os.path.exists(CHECKLIST) else {}
    except Exception:
        d = {}
    d[t] = {"items": [bool(x) for x in items][:CHECKLIST_N],
            "updated": datetime.now().isoformat(timespec="seconds")}
    _atomic_write_json(CHECKLIST, d)


REVIEW_ROWS = [
    ("price", "Kurs"),
    ("eps", "Prognoza EPS 12M"),
    ("rev_ps", "Prognoza sprzedaży/akcję"),
    ("pt_cons", "Cena docelowa (konserwatywna)"),
    ("pt_opt", "Cena docelowa (optymistyczna)"),
    ("comp_pe", "Składnik P/E"),
    ("comp_ps", "Składnik P/S"),
    ("med_pe", "Mediana P/E 3Y"),
    ("med_ps", "Mediana P/S 3Y"),
]


def load_reviews():
    if os.path.exists(REVIEWS):
        try:
            d = _load_json_file(REVIEWS)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def save_reviews(d):
    _atomic_write_json(REVIEWS, d)


def review_snapshot(t, d, comp_pe, comp_ps, pt_c, pt_o, total, final, rating):
    """Zapis tego, co użytkownik widział: liczby, mnożniki i checklista."""
    methods = [name for name, v in (("PE", comp_pe), ("PS", comp_ps)) if v]
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL_VER,
        "price": (round(d["price"], 4) if d.get("price") else None),
        "pt_cons": (round(pt_c, 2) if pt_c else None),
        "pt_opt": (round(pt_o, 2) if pt_o else None),
        "comp_pe": (round(comp_pe, 2) if comp_pe else None),
        "comp_ps": (round(comp_ps, 2) if comp_ps else None),
        "eps": d.get("fwd_eps"), "rev_ps": d.get("fwd_rev_ps"),
        "med_pe": d.get("med_pe"), "med_ps": d.get("med_ps"),
        "max_pe": d.get("max_pe"), "max_ps": d.get("max_ps"),
        "score_hard": total, "score": final, "rating": rating,
        "methods": methods,
        "checklist": load_checklist(t)["items"],
    }


def review_diff(prev, cur):
    """Różnica: ostatni zapis → dzisiejsze dane."""
    rows = []
    for key, label in REVIEW_ROWS:
        a, b = prev.get(key), (cur or {}).get(key)
        chg = (b / a - 1) if (a and b) else None
        rows.append({"key": key, "label": label, "was": a, "now": b,
                     "chg": (round(chg, 4) if chg is not None else None)})
    pm = set(prev.get("methods") or [])
    cm = set((cur or {}).get("methods") or [])
    days = None
    if prev.get("ts"):
        try:
            days = (datetime.now() - datetime.fromisoformat(str(prev["ts"])[:19])).days
        except ValueError:
            days = None
    return {
        "since": prev.get("ts"), "days": days, "rows": rows,
        "score": {"hard": [prev.get("score_hard"), (cur or {}).get("score_hard")],
                  "final": [prev.get("score"), (cur or {}).get("score")],
                  "rating": [prev.get("rating"), (cur or {}).get("rating")]},
        "methods": {"was": sorted(pm), "now": sorted(cm),
                    "lost": sorted(pm - cm), "gained": sorted(cm - pm)},
        "model": {"was": prev.get("model"), "now": (cur or {}).get("model"),
                  "changed": prev.get("model") != (cur or {}).get("model")},
        "checklist": {"was": prev.get("checklist") or [], "now": (cur or {}).get("checklist") or []},
    }


def review_payload(t, snap):
    hist = ((load_reviews().get(t) or {}).get("history") or [])
    hist = [x for x in hist if isinstance(x, dict)]
    last = hist[-1] if hist else None
    return {"last": last, "count": len(hist), "model_now": MODEL_VER,
            "model_desc": MODEL_DESC,
            "diff": (review_diff(last, snap) if last else None), "snapshot": snap}


def bundle_for(t):
    """Wszystko, co wiemy o jednej spółce, w jednym słowniku."""
    grouped = cached_post([t])
    ser = grouped.get(t) or {}
    mcap = (fill_sectors([t]).get(t) or {}).get("mcap")
    if not ser.get("price"):
        raise KeyError(t)
    d = sc._compute(t, ser)
    m = scoring.build_metrics(ser)
    pe_rel = (d["price"] / d["fwd_eps"] / d["med_pe"]) if (d.get("fwd_eps") and d.get("med_pe") and d["fwd_eps"] > 0) else None
    ps_rel = (d["price"] / d["fwd_rev_ps"] / d["med_ps"]) if (d.get("fwd_rev_ps") and d.get("med_ps")) else None
    parts = scoring.score_parts({**m, "pe_rel": pe_rel, "ps_rel": ps_rel})
    raw = scoring.total_raw(parts)
    total = round(raw, 1) if raw is not None else None
    final = total            # ocena końcowa = twarda (analiza miękka usunięta)
    rating = scoring.rating(final)

    eps, rps = d.get("fwd_eps"), d.get("fwd_rev_ps")
    # składniki trzymamy osobno — przegląd pokazuje, która metoda wypadła
    c_pe = (d["med_pe"] * eps) if (d.get("med_pe") and eps and eps > 0) else None
    c_ps = (d["med_ps"] * rps) if (d.get("med_ps") and rps) else None
    o_pe = (d["max_pe"] * eps) if (d.get("max_pe") and eps and eps > 0) else None
    o_ps = (d["max_ps"] * rps) if (d.get("max_ps") and rps) else None
    cs = [x for x in (c_pe, c_ps) if x]
    op = [x for x in (o_pe, o_ps) if x]
    pt_c = sum(cs) / len(cs) if cs else None
    pt_o = sum(op) / len(op) if op else None
    up = (pt_c / d["price"] - 1) if (pt_c and d["price"]) else None
    scrab_up = (m.get("pt_upside") / 100.0) if m.get("pt_upside") is not None else None
    pt_s = (d["price"] * (1 + scrab_up)) if (scrab_up is not None and d["price"]) else None

    tl = tfl.build(ser, m, {**d, "pe_rel": pe_rel, "ps_rel": ps_rel, "upside": up}, mcap)
    snap = review_snapshot(t, d, c_pe, c_ps, pt_c, pt_o, total, final, rating)
    rev = review_payload(t, snap)
    rows = hist.build(ser)
    pot_hist = [[str(datetime.fromtimestamp(r[0], timezone.utc).date()), round(r[2] / r[1] - 1, 4)]
                for r in rows[::5] if r[1]]

    return {
        "ticker": t, "price": d["price"], "currency": d.get("currency") or "",
        "eps": d.get("fwd_eps"), "rev_ps": d.get("fwd_rev_ps"),
        "pe": (d["price"] / eps) if (eps and eps > 0) else None,
        "med_pe": d.get("med_pe"), "max_pe": d.get("max_pe"), "min_pe": d.get("min_pe"),
        "ps": (d["price"] / rps) if rps else None,
        "med_ps": d.get("med_ps"), "pe_rel": pe_rel, "ps_rel": ps_rel,
        "pt_cons": pt_c, "pt_opt": pt_o, "upside": up,
        "comp_pe": c_pe, "comp_ps": c_ps, "pt_scrab": pt_s, "upside_scrab": scrab_up,
        "review": rev, "snapshot": snap, "checklist": load_checklist(t),
        "score": final, "score_hard": total, "rating": rating,
        "parts": parts, "metrics": m, "tfl": tl, "tfl_summary": tfl.summary(tl),
        "window": d.get("window_txt"), "n_pe": d.get("n_pe"),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ---------- API ----------
@app.route("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory(WEB, p)


@app.route("/api/state", methods=["GET", "POST"])
def api_state():
    s = load_state()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        with _lock:
            s = load_state()
            tick = data.get("tickers")
            if isinstance(tick, list):
                ok = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
                s["tickers"] = [str(t).strip().upper() for t in tick
                                if ok.match(str(t).strip().upper())]
            if isinstance(data.get("watchlist"), list):
                s["watchlist"] = [str(t).strip().upper() for t in data["watchlist"]
                                  if isinstance(t, str) and t.strip()]
            if isinstance(data.get("shares"), dict):
                base = dict(s.get("shares") or {})
                for t, v in data["shares"].items():
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if fv == fv and abs(fv) != float("inf"):
                        base[str(t).upper()] = fv
                s["shares"] = base
            for k in ("cash", "max_pos", "max_sec"):
                if k in data:
                    try:
                        s[k] = float(data[k])
                    except (TypeError, ValueError):
                        pass
            for k in ("method", "fractional"):
                if k in data:
                    s[k] = data[k]
            s["shares"] = {t: s.get("shares", {}).get(t, 0) for t in s.get("tickers", [])}
            save_state(s)
    return jsonify(s)


def _analyse_row(t, ser, sec, shares, rates):
    d = sc._compute(t, ser)
    m = scoring.build_metrics(ser)
    pe_rel = (d["price"] / d["fwd_eps"] / d["med_pe"]) if (d.get("fwd_eps") and d.get("med_pe") and d["fwd_eps"] > 0) else None
    ps_rel = (d["price"] / d["fwd_rev_ps"] / d["med_ps"]) if (d.get("fwd_rev_ps") and d.get("med_ps")) else None
    parts = scoring.score_parts({**m, "pe_rel": pe_rel, "ps_rel": ps_rel})
    raw = scoring.total_raw(parts)
    total = round(raw, 1) if raw is not None else None
    final = total            # ocena końcowa = twarda
    eps, rps = d.get("fwd_eps"), d.get("fwd_rev_ps")
    cs, op = [], []
    if d.get("med_pe") and eps and eps > 0:
        cs.append(d["med_pe"] * eps)
    if d.get("med_ps") and rps:
        cs.append(d["med_ps"] * rps)
    if d.get("max_pe") and eps and eps > 0:
        op.append(d["max_pe"] * eps)
    if d.get("max_ps") and rps:
        op.append(d["max_ps"] * rps)
    pt_c = sum(cs) / len(cs) if cs else None
    pt_o = sum(op) / len(op) if op else None
    cur = currency_of(sec, t)
    f = fx_one(cur, rates)
    price = d["price"]
    value_native = float(shares or 0) * price
    up = (pt_c / price - 1) if (pt_c and price) else None
    scrab_up = (m.get("pt_upside") / 100.0) if m.get("pt_upside") is not None else None
    pt_s = (price * (1 + scrab_up)) if (scrab_up is not None and price) else None
    tl = tfl.build(ser, m, {**d, "pe_rel": pe_rel, "ps_rel": ps_rel, "upside": up},
                   (sec.get(t) or {}).get("mcap"))
    return {
        "ticker": t, "price": price, "pe_rel": pe_rel, "ps_rel": ps_rel,
        "pt_cons": pt_c, "pt_opt": pt_o,
        "upside": up, "pt_scrab": pt_s, "upside_scrab": scrab_up, "tfl": tl,
        "eps_rev": (tl.get("eps_rev") or {}).get("chg_3m"),
        "eps_rev_dir": (tl.get("eps_rev") or {}).get("dir"),
        "score": final, "rating": scoring.rating(final),
        "score_hard": total,
        "sector": (sec.get(t) or {}).get("sector", "—"),
        "industry": (sec.get(t) or {}).get("industry", "—"),
        "name": (sec.get(t) or {}).get("name", t),
        "mcap": (sec.get(t) or {}).get("mcap"),
        "currency": cur, "fx": (round(f, 4) if f else None),
        "fx_missing": f is None,
        "shares": float(shares or 0),
        "value_native": round(value_native, 2),
        "value_pln": (round(value_native * f, 2) if f else None),
        "growth": m.get("rev1y_pct"), "margin": m.get("op_margin"),
    }


def _rows_for(tickers, shares_map, with_meta=True):
    grouped = cached_post(tickers)
    sec = fill_sectors(tickers)
    rates = fx_rates([currency_of(sec, t) for t in tickers])
    out, errors = [], []
    for t in tickers:
        ser = grouped.get(t) or {}
        if not ser.get("price"):
            errors.append({"ticker": t, "error": "brak danych"})
            continue
        try:
            out.append(_analyse_row(t, ser, sec, (shares_map or {}).get(t, 0), rates))
        except Exception as e:
            errors.append({"ticker": t, "error": f"{e.__class__.__name__}: {str(e)[:70]}"})
    return out, rates, errors


# ---------- dziennik transakcji (FIFO) ----------
JOURNAL = os.path.join(HERE, "journal.json")


def load_journal():
    if os.path.exists(JOURNAL):
        try:
            d = _load_json_file(JOURNAL)
            d.setdefault("transactions", [])
            if any("seq" not in t for t in d["transactions"]):
                for i, t in enumerate(d["transactions"], 1):
                    t.setdefault("seq", i)
                save_journal(d)
            return d
        except Exception:
            pass
    return {"transactions": []}


def save_journal(d):
    _atomic_write_json(JOURNAL, d)


def _fifo(txs, fx=None):
    """FIFO: loty, wynik w walucie instrumentu i wynik w PLN po kursie z dnia.

    `fx(ticker, iso_date)` zwraca kurs waluty instrumentu do PLN (None = brak).
    Wynik PLN liczymy per transakcja — koszt po kursie z dnia kupna, przychód po
    kursie z dnia sprzedaży — więc zmiana kursu waluty jest częścią wyniku, a nie
    skutkiem przeliczenia całości po dzisiejszym kursie (tak robił stary kod i
    przez to dziennik nie zgadzał się z zakładką Wyniki).

    Bez `fx` wynik PLN = wynik w walucie instrumentu (używane tam, gdzie liczy się
    tylko stan posiadania).
    """
    lots, realized, fees, closed = {}, {}, {}, []
    realized_pln, fees_pln = {}, {}
    memo = {}

    def rate(t, iso):
        if fx is None:
            return 1.0
        key = (t, iso)
        if key not in memo:
            r = fx(t, iso) if iso else None
            memo[key] = 1.0 if r is None else float(r)
        return memo[key]

    for tx in sorted(txs, key=lambda t: (t.get("date", ""), t.get("seq", 0), t.get("id", ""))):
        if tx.get("side") not in ("KUP", "SPRZEDAJ"):
            continue          # wpłaty, wypłaty i dywidendy nie tworzą lotów
        t = tx["ticker"]
        day = tx.get("date")
        qty = float(tx.get("qty") or 0)
        price = float(tx.get("price") or 0)
        fee = float(tx.get("fee") or 0)
        f_tx = rate(t, day)
        fees[t] = fees.get(t, 0.0) + fee
        fees_pln[t] = fees_pln.get(t, 0.0) + fee * f_tx
        book = lots.setdefault(t, [])
        if tx["side"] == "KUP":
            book.append([qty, price, fee, qty, day])
            continue
        left, gain, cost_gone = qty, 0.0, 0.0
        cost_pln, proceeds_pln = 0.0, 0.0
        hold_w, hold_q = 0.0, 0.0
        while left > 1e-9 and book:
            lot = book[0]
            take = min(left, lot[0])
            fee_part = lot[2] * (take / lot[3]) if lot[3] else 0.0
            lot_day = lot[4] if len(lot) > 4 else None
            f_lot = rate(t, lot_day)
            if lot_day and day:
                try:
                    hd = (date.fromisoformat(day) - date.fromisoformat(lot_day)).days
                    hold_w += hd * take
                    hold_q += take
                except ValueError:
                    pass
            gain += (price - lot[1]) * take - fee_part
            cost_gone += lot[1] * take + fee_part
            cost_pln += (lot[1] * take + fee_part) * f_lot
            proceeds_pln += price * take * f_tx
            lot[0] -= take
            lot[2] -= fee_part
            left -= take
            if lot[0] <= 1e-9:
                book.pop(0)
        gain -= fee
        proceeds_pln -= fee * f_tx
        realized[t] = realized.get(t, 0.0) + gain
        pnl_pln = proceeds_pln - cost_pln
        realized_pln[t] = realized_pln.get(t, 0.0) + pnl_pln
        closed.append({"id": tx.get("id"), "ticker": t, "date": day, "qty": qty,
                       "proceeds": price * qty, "cost": cost_gone, "pnl": gain,
                       "proceeds_pln": proceeds_pln, "cost_pln": cost_pln,
                       "pnl_pln": pnl_pln, "fx": f_tx,
                       "hold_days": round(hold_w / hold_q, 1) if hold_q else None,
                       "unmatched": left})
    return {"lots": lots, "realized": realized, "fees": fees, "closed": closed,
            "realized_pln": realized_pln, "fees_pln": fees_pln}


_FX_MEMO = {}


def _fx_for(sec):
    """fx(ticker, iso_date) -> ile PLN za jednostkę waluty instrumentu.

    Kurs z dnia transakcji; gdy NBP nie ma notowania — ostatni znany. Brak
    kursu zwraca None (żadnego cichego 1.0), a PLN to zawsze 1.0.
    """
    def f(t, iso):
        if not t:
            return 1.0
        ccy = currency_of(sec, t)
        if not ccy or ccy == "PLN":
            return 1.0
        key = (ccy, iso or "")
        if key in _FX_MEMO:
            return _FX_MEMO[key]
        rate = nbp_rate_hist(ccy, iso) if iso else nbp_rate_hist(ccy)
        if rate is None and iso:
            rate = nbp_rate_hist(ccy)
        if len(_FX_MEMO) > 4000:
            _FX_MEMO.clear()
        _FX_MEMO[key] = rate
        return rate
    return f


@app.route("/api/journal", methods=["GET"])
def api_journal():
    d = load_journal()
    txs = d["transactions"]
    tickers = sorted({t["ticker"] for t in txs if t.get("ticker")})
    prices, fxmap, meta, pser = {}, {}, {}, {}
    if tickers:
        grouped = cached_post(tickers)
        sec = fill_sectors(tickers)
        rates = fx_rates([currency_of(sec, t) for t in tickers])
        for t in tickers:
            dp = (grouped.get(t) or {}).get("price") or []
            prices[t] = float(dp[-1][1]) if dp else None
            hist_px = {}
            for ts, v in dp:
                if isinstance(v, (int, float)):
                    hist_px[datetime.fromtimestamp(ts, timezone.utc).date().isoformat()] = float(v)
            pser[t] = hist_px
            cur = currency_of(sec, t)
            meta[t] = {"currency": cur, "name": (sec.get(t) or {}).get("name", t)}
            fxmap[t] = fx_one(cur, rates)
    fx_at = _fx_for(sec)
    fifo = _fifo(txs, fx_at)
    lots, realized, fees, closed = fifo["lots"], fifo["realized"], fifo["fees"], fifo["closed"]
    pos, unreal_total, real_total = [], 0.0, 0.0
    for t, book in lots.items():
        qty = sum(l[0] for l in book)
        if qty <= 1e-9:
            continue
        cost = sum(l[0] * l[1] for l in book) + sum(l[2] for l in book)
        # koszt w PLN po kursie z dnia kupna — tak jak liczy zakładka Wyniki
        cost_pln = sum((l[0] * l[1] + l[2]) * (fx_at(t, l[4] if len(l) > 4 else None) or 0.0)
                       for l in book)
        avg = cost / qty if qty else 0
        p = prices.get(t)
        f = fxmap.get(t)
        if p is None or f is None:
            pos.append({"ticker": t, "name": meta.get(t, {}).get("name", t),
                        "currency": meta.get(t, {}).get("currency", "PLN"),
                        "qty": round(qty, 4), "avg_cost": round(avg, 2), "price": None,
                        "value_pln": None, "unreal_pln": None, "unreal_pct": None,
                        "realized_pln": round(fifo["realized_pln"].get(t, 0.0), 2),
                        "cost_pln": round(cost_pln, 2), "fx": f, "no_quote": True})
            continue
        value_pln = p * qty * f
        unreal = value_pln - cost_pln
        unreal_total += unreal
        pos.append({"ticker": t, "name": meta.get(t, {}).get("name", t),
                    "currency": meta.get(t, {}).get("currency", "PLN"),
                    "qty": round(qty, 4), "avg_cost": round(avg, 2), "price": round(p, 2),
                    "value_pln": round(value_pln, 2),
                    "unreal_pln": round(unreal, 2),
                    "unreal_pct": round(value_pln / cost_pln - 1, 4) if cost_pln else None,
                    "realized_pln": round(fifo["realized_pln"].get(t, 0.0), 2),
                    "cost_pln": round(cost_pln, 2),
                    "fx": round(f, 4)})
    for t, v in fifo["realized_pln"].items():
        real_total += v
    pos.sort(key=lambda x: -(x["value_pln"] or 0))
    by_id = {c["id"]: c for c in closed}
    rows = []
    for tx in sorted(txs, key=lambda t: (t.get("date", ""), t.get("seq", 0), t.get("id", "")), reverse=True):
        c = by_id.get(tx.get("id"))
        f = fxmap.get(tx["ticker"], 1.0)
        fee_pln = round(float(tx.get("fee") or 0) * (fx_at(tx.get("ticker"), tx.get("date")) or 1.0), 2)
        rows.append({**tx, "currency": meta.get(tx["ticker"], {}).get("currency", "PLN"),
                     "realized_pln": round(c["pnl_pln"], 2) if (c and tx["side"] == "SPRZEDAJ") else None,
                     "fee_pln": fee_pln,
                     "value_pln": round(float(tx.get("qty") or 0) * float(tx.get("price") or 0) * f, 2)})
    matched = [c for c in closed if c.get("unmatched", 0) <= 1e-9]
    unmatched = [c for c in closed if c.get("unmatched", 0) > 1e-9]
    wins = sum(1 for c in matched if c["pnl"] > 0)
    losses = sum(1 for c in matched if c["pnl"] <= 0)

    # --- skuteczność per spółka: trafność, czas trzymania, co działo się po sprzedaży ---
    def price_after(t, day, horizon=60):
        m = pser.get(t) or {}
        if not m or not day:
            return None
        try:
            cut = (date.fromisoformat(day) + timedelta(days=horizon)).isoformat()
        except ValueError:
            return None
        later = [d for d in m if d >= cut]
        return m[min(later)] if later else None

    by_t = {}
    for c in matched:
        b = by_t.setdefault(c["ticker"], {"ticker": c["ticker"], "trades": 0, "wins": 0,
                                          "realized_pln": 0.0, "hold": 0.0, "hold_q": 0.0,
                                          "best": None, "worst": None, "after": []})
        b["trades"] += 1
        if c["pnl_pln"] > 0:
            b["wins"] += 1
        b["realized_pln"] += c["pnl_pln"]
        if c.get("hold_days") is not None:
            b["hold"] += c["hold_days"] * c["qty"]
            b["hold_q"] += c["qty"]
        b["best"] = c["pnl_pln"] if b["best"] is None else max(b["best"], c["pnl_pln"])
        b["worst"] = c["pnl_pln"] if b["worst"] is None else min(b["worst"], c["pnl_pln"])
        px0 = (c["proceeds"] / c["qty"]) if c["qty"] else None
        px1 = price_after(c["ticker"], c.get("date"))
        if px0 and px1:
            b["after"].append(px1 / px0 - 1)

    stats = []
    for b in by_t.values():
        stats.append({
            "ticker": b["ticker"], "trades": b["trades"],
            "win_rate": round(b["wins"] / b["trades"], 3) if b["trades"] else None,
            "realized_pln": round(b["realized_pln"], 2),
            "hold_days": round(b["hold"] / b["hold_q"], 1) if b["hold_q"] else None,
            "after_sell": round(sum(b["after"]) / len(b["after"]), 4) if b["after"] else None,
            "best_pln": round(b["best"], 2) if b["best"] is not None else None,
            "worst_pln": round(b["worst"], 2) if b["worst"] is not None else None,
        })
    stats.sort(key=lambda x: -x["realized_pln"])
    holds = [c["hold_days"] for c in matched if c.get("hold_days") is not None]
    afters = [x for b in by_t.values() for x in b["after"]]

    deposits = sum(float(t.get("amount") or 0) for t in txs if t.get("side") == "WPŁATA")
    withdrawals = sum(float(t.get("amount") or 0) for t in txs if t.get("side") == "WYPŁATA")
    def _div_pln(t):
        return float(t.get("amount") or 0) * (fx_at(t.get("ticker"), t.get("date")) or 1.0)

    dividends = sum(_div_pln(t) for t in txs if t.get("side") == "DYWIDENDA")
    return jsonify({
        "transactions": rows,
        "positions": pos,
        "summary": {
            "deposits_pln": round(deposits, 2),
            "withdrawals_pln": round(withdrawals, 2),
            "dividends_pln": round(dividends, 2),
            "net_invested_pln": round(deposits - withdrawals, 2),
            "realized_pln": round(real_total, 2),
            "unreal_pln": round(unreal_total, 2),
            "fees_pln": round(sum(fifo["fees_pln"].values()), 2),
            "closed": len(matched), "wins": wins, "losses": losses,
            "win_rate": round(wins / len(matched), 3) if matched else None,
            "avg_hold_days": round(sum(holds) / len(holds), 1) if holds else None,
            "after_sell_avg": round(sum(afters) / len(afters), 4) if afters else None,
            "by_ticker": stats,
            "unmatched": len(unmatched),
            "trades": len([t for t in txs if t.get("side") in ("KUP", "SPRZEDAJ")]),
        },
        "data_ts": newest_bundle_ts(),
    })


@app.route("/api/journal", methods=["POST"])
def api_journal_add():
    body = request.get_json(force=True) or {}
    raw_side = str(body.get("side", "KUP")).upper().strip()
    cash_kind = None
    for k, label in (("WPLATA", "WPŁATA"), ("WPŁATA", "WPŁATA"),
                     ("WYPLATA", "WYPŁATA"), ("WYPŁATA", "WYPŁATA"),
                     ("DYWIDENDA", "DYWIDENDA")):
        if raw_side == k:
            cash_kind = label
            break
    if cash_kind:
        try:
            amount = float(body.get("amount") or body.get("price") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            return jsonify({"error": "Kwota musi być dodatnia."}), 400
        try:
            dstr = date.fromisoformat(str(body.get("date") or date.today().isoformat())).isoformat()
        except ValueError:
            return jsonify({"error": "Data musi być w formacie RRRR-MM-DD."}), 400
        d = load_journal()
        nxt = max([t.get("seq", 0) for t in d["transactions"]] or [0]) + 1
        d["transactions"].append({
            "id": os.urandom(4).hex(), "seq": nxt, "date": dstr,
            "ticker": str(body.get("ticker") or "").upper().strip(),
            "side": cash_kind, "qty": 0.0, "price": 0.0, "fee": 0.0,
            "amount": round(amount, 2),
            "note": str(body.get("note") or "")[:400],
        })
        save_journal(d)
        return jsonify({"ok": True, "count": len(d["transactions"])})
    try:
        ticker = str(body["ticker"]).upper().strip()
        side = "SPRZEDAJ" if raw_side.startswith("S") else "KUP"
        qty = float(body["qty"])
        price = float(body["price"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Podaj ticker, stronę, liczbę sztuk i cenę."}), 400
    if qty <= 0 or price <= 0:
        return jsonify({"error": "Liczba sztuk i cena muszą być dodatnie."}), 400
    try:
        dstr = date.fromisoformat(str(body.get("date") or date.today().isoformat())).isoformat()
    except ValueError:
        return jsonify({"error": "Data musi być w formacie RRRR-MM-DD."}), 400
    d = load_journal()
    if side == "SPRZEDAJ":
        lots = _fifo(d["transactions"])["lots"]
        held = sum(l[0] for l in lots.get(ticker, []))
        if qty > held + 1e-9:
            return jsonify({"error": f"Masz {held:g} szt. {ticker} — nie sprzedasz {qty:g}."}), 400
    fee_pln = abs(float(body.get("fee") or 0))
    cur = currency_of(fill_sectors([ticker]), ticker)
    rate = 1.0 if cur in (None, "PLN") else (nbp_rate_hist(cur, dstr) or nbp_rate_hist(cur))
    if rate is None:
        return jsonify({"error": f"Brak kursu NBP dla {cur} — nie przeliczę opłaty."}), 400
    nxt = max([t.get("seq", 0) for t in d["transactions"]] or [0]) + 1
    d["transactions"].append({
        "id": os.urandom(4).hex(),        "seq": nxt, "date": dstr,
        "ticker": ticker, "side": side, "qty": qty, "price": price,
        "fee": round(fee_pln / rate, 6),          # w walucie instrumentu
        "fee_pln": round(fee_pln, 2),             # tak jak wpisał użytkownik
        "note": str(body.get("note") or "")[:400],
    })
    save_journal(d)
    return jsonify({"ok": True, "count": len(d["transactions"])})


@app.route("/api/journal/<tid>", methods=["DELETE"])
def api_journal_del(tid):
    d = load_journal()
    d["transactions"] = [t for t in d["transactions"] if t.get("id") != tid]
    save_journal(d)
    return jsonify({"ok": True, "count": len(d["transactions"])})


@app.route("/api/journal/seed", methods=["POST"])
def api_journal_seed():
    """Zakłada pozycje otwarcia dla aktualnego stanu, w cenach rynkowych."""
    s = load_state()
    tickers = [t for t in s.get("tickers", []) if (s.get("shares") or {}).get(t)]
    if not tickers:
        return jsonify({"error": "Portfel nie ma pozycji do przeniesienia."}), 400
    grouped = cached_post(tickers)
    d = load_journal()
    have = {(t["ticker"]) for t in d["transactions"]}
    added = 0
    for t in tickers:
        if t in have:
            continue
        dp = (grouped.get(t) or {}).get("price") or []
        price = float(dp[-1][1]) if dp else 0.0
        if not price:
            continue
        nxt = max([x.get("seq", 0) for x in d["transactions"]] or [0]) + 1
        d["transactions"].append({
            "id": os.urandom(4).hex(), "seq": nxt,
            "date": date.today().isoformat(), "ticker": t,
            "side": "KUP", "qty": float(s["shares"][t]), "price": price, "fee": 0.0,
            "note": "pozycja otwarcia (cena rynkowa z dnia założenia dziennika)",
        })
        added += 1
    save_journal(d)
    return jsonify({"ok": True, "added": added})


# ---------- import z XTB ----------
IMPORT_DIR = os.path.join(CACHE, "import")


@app.route("/api/import/xtb", methods=["POST"])
def api_import_xtb():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Nie wybrano pliku"}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".csv"):
        return jsonify({"error": "Wgraj plik .xlsx albo .csv z XTB (Historia → Eksport)"}), 400
    os.makedirs(IMPORT_DIR, exist_ok=True)
    token = os.urandom(8).hex()
    path = os.path.join(IMPORT_DIR, token + ext)
    f.save(path)
    try:
        res = xtb_import.parse_file(path, rate_fn=nbp_rate_hist)
    except Exception as e:
        return jsonify({"error": f"Nie umiem odczytać pliku: {e}"}), 400

    j = load_journal()
    have = set()
    for t in j["transactions"]:
        if t.get("side") in ("WPŁATA", "WYPŁATA", "DYWIDENDA"):
            have.add((str(t.get("date")), "CASH", t.get("side"), round(float(t.get("amount") or 0), 2)))
        else:
            have.add((str(t.get("date")), t.get("ticker"), t.get("side"),
                      round(float(t.get("qty") or 0), 6), round(float(t.get("price") or 0), 6)))
    for r in res["rows"]:
        k = (str(r.get("date")), r.get("ticker"), r.get("side"),
             round(float(r.get("qty") or 0), 6), round(float(r.get("price") or 0), 6))
        kr = (str(r.get("date")), "CASH", r.get("side"), round(float(r.get("amount") or 0), 2))
        r["dup"] = bool(r.get("ok") and (
            (r.get("kind") == "TRADE" and k in have) or
            (r.get("kind") == "CASH" and kr in have)))
    # stan otwartych pozycji z tego samego pliku — do porównania z portfelem w aplikacji
    try:
        file_pos = xtb_import.parse_open_positions(path)
    except Exception:
        file_pos = {}
    st = load_state()
    cur_shares = {t: float((st.get("shares") or {}).get(t) or 0) for t in st.get("tickers", [])}
    # XTB trzyma ASML.AS, a w portfelu bywa ASML (ADR) — porównuj pod tym samym tickerem
    norm_pos = {}
    for t, q in file_pos.items():
        target = t if (t in cur_shares or t.split(".")[0] not in cur_shares) else t.split(".")[0]
        norm_pos[target] = round(norm_pos.get(target, 0.0) + float(q), 6)
    res["open_positions"] = norm_pos
    res["state_shares"] = cur_shares
    _atomic_write_json(os.path.join(IMPORT_DIR, token + ".json"), res)
    return jsonify({"token": token, "summary": res["summary"], "rows": res["rows"],
                    "open_positions": norm_pos, "state_shares": cur_shares,
                    "sheet": res.get("sheet")})


@app.route("/api/import/xtb/commit", methods=["POST"])
def api_import_xtb_commit():
    body = request.get_json(force=True) or {}
    token = re.sub(r"[^a-f0-9]", "", str(body.get("token") or ""))
    keys = {str(k) for k in (body.get("keys") or [])}
    path = os.path.join(IMPORT_DIR, token + ".json")
    if not token or not os.path.exists(path):
        return jsonify({"error": "Sesja importu wygasła — wgraj plik jeszcze raz"}), 400
    res = _load_json_file(path)
    rows = [r for r in res.get("rows", []) if r.get("key") in keys
            and r.get("kind") in ("TRADE", "CASH") and r.get("ok")]
    if not rows:
        return jsonify({"error": "Nie zaznaczono żadnej transakcji"}), 400

    d = load_journal()
    txs = d["transactions"]
    nxt = max([t.get("seq", 0) for t in txs] or [0]) + 1
    added, skipped = 0, []
    for r in sorted(rows, key=lambda x: (str(x.get("date")), str(x.get("time") or ""))):
        if r.get("kind") == "CASH":
            txs.append({
                "id": os.urandom(4).hex(), "seq": nxt,
                "date": r["date"], "ticker": r.get("ticker") or "",
                "side": r["side"], "qty": 0.0, "price": 0.0, "fee": 0.0,
                "amount": float(r.get("amount") or 0),
                "note": str(r.get("note") or "")[:400],
                "xtb": r.get("key"),
            })
            nxt += 1
            added += 1
            continue
        if r["side"] == "SPRZEDAJ":
            lots = _fifo(txs)["lots"]
            held = sum(l[0] for l in lots.get(r["ticker"], []))
            if r["qty"] > held + 1e-9:
                skipped.append({"key": r["key"], "ticker": r["ticker"], "date": r["date"],
                                "reason": f"brak pokrycia: masz {held:g} szt."})
                continue
        txs.append({
            "id": os.urandom(4).hex(), "seq": nxt,
            "date": r["date"], "ticker": r["ticker"], "side": r["side"],
            "qty": float(r["qty"]), "price": float(r["price"]),
            "fee": float(r.get("fee") or 0),
            "fee_pln": r.get("fee_pln"),
            "note": str(r.get("note") or "")[:400],
            "xtb": r.get("key"),
        })
        nxt += 1
        added += 1
    save_journal(d)

    changed = {}
    if body.get("update_shares") and res.get("open_positions"):
        st = load_state()
        shares = st.setdefault("shares", {})
        tickers = list(st.get("tickers", []))
        for t, qty in (res.get("open_positions") or {}).items():
            if qty <= 0:
                continue
            # XTB trzyma ASML.AS, a w portfelu bywa ASML (ADR) — nie duplikuj pozycji
            target = t
            if target not in shares and t.split(".")[0] in shares:
                target = t.split(".")[0]
            if target not in shares or abs(float(shares.get(target) or 0) - float(qty)) > 1e-6:
                changed[target] = {"from": float(shares.get(target) or 0), "to": float(qty)}
            shares[target] = float(qty)
            if target not in tickers:
                tickers.append(target)
        st["tickers"] = tickers
        save_state(st)

    return jsonify({"added": added, "skipped": skipped, "shares_changed": changed})


# ---------- wyniki portfela vs benchmark ----------
import perf as perf_mod


def _journal_prices(tickers):
    """Dzienne zamknięcia ze Scraba dla tickerów z dziennika."""
    if not tickers:
        return {}
    import scrab_client as sc
    grouped = sc.post(tickers, ["price"])
    out = {}
    for s in grouped:
        t = (s.get("ticker") or "").upper()
        m = {}
        for ts, v in (s.get("datapoints") or []):
            if isinstance(v, (int, float)):
                m[datetime.fromtimestamp(ts, None).date().isoformat()] = float(v)
        if t and m:
            out[t] = m
    return out


def _journal_attribution(txs, prices, ccy_of, fx_of, end):
    """Kto zrobił wynik: zrealizowane + niezrealizowane + dywidendy, per spółka (PLN).

    Kurs waluty bierzemy z dnia transakcji (jak w zakładce Wyniki), a nie
    dzisiejszy — inaczej atrybucja nie zgadzałaby się z wynikiem portfela.
    """
    def fx_t(t, iso):
        return fx_of(ccy_of(t))(iso) if iso else None

    fifo = _fifo(txs, fx_t)
    lots, realized_pln, fees_pln = fifo["lots"], fifo["realized_pln"], fifo["fees_pln"]
    divs = {}
    for t in txs:
        if t.get("side") == "DYWIDENDA" and t.get("ticker"):
            # dywidenda w walucie spółki → PLN po kursie z dnia wypłaty
            pln = float(t.get("amount") or 0) * (fx_t(t["ticker"], t.get("date")) or 1.0)
            divs[t["ticker"]] = divs.get(t["ticker"], 0.0) + pln
    tickers = set(lots) | set(realized_pln) | set(divs)
    rows = []
    for t in sorted(tickers):
        cur = ccy_of(t)
        book = lots.get(t) or []
        qty = sum(l[0] for l in book)
        cost_pln = sum((l[0] * l[1] + l[2]) * (fx_t(t, l[4] if len(l) > 4 else None) or 1.0)
                       for l in book)
        px = None
        m = prices.get(t) or {}
        if m:
            px = m[max(m)]
        f_end = fx_t(t, end) or 1.0
        u_pln = (px * qty * f_end - cost_pln) if (px is not None and qty > 1e-9) else 0.0
        r_pln = realized_pln.get(t, 0.0)
        d_pln = divs.get(t, 0.0)
        rows.append({
            "ticker": t, "qty": round(qty, 4), "currency": cur, "px": px,
            "realized_pln": round(r_pln, 2), "unreal_pln": round(u_pln, 2),
            "dividends_pln": round(d_pln, 2), "fees_pln": round(fees_pln.get(t, 0.0), 2),
            "total_pln": round(r_pln + u_pln + d_pln, 2),
        })
    s = sum(abs(r["total_pln"]) for r in rows) or 1.0
    for r in rows:
        r["share"] = round(r["total_pln"] / s, 4)
    rows.sort(key=lambda r: -r["total_pln"])
    return rows


def _rel_metrics(series, bench_series):
    """Beta, alfa, tracking error, information ratio i capture względem benchmarku."""
    bmap = {d: v for d, v in bench_series}
    pts = [(d, i) for d, i, _v in series if d in bmap]
    if len(pts) < 30:
        return {}
    rp, rb = [], []
    for k in range(1, len(pts)):
        d0, i0 = pts[k - 1]
        d1, i1 = pts[k]
        b0, b1 = bmap[d0], bmap[d1]
        if i0 > 0 and b0 > 0:
            rp.append(i1 / i0 - 1)
            rb.append(b1 / b0 - 1)
    n = len(rp)
    if n < 20:
        return {}
    mp, mb = sum(rp) / n, sum(rb) / n
    var_b = sum((x - mb) ** 2 for x in rb) / (n - 1)
    cov = sum((x - mp) * (y - mb) for x, y in zip(rp, rb)) / (n - 1)
    beta = cov / var_b if var_b else None
    var_p = sum((x - mp) ** 2 for x in rp) / (n - 1)
    corr = cov / ((var_p ** 0.5) * (var_b ** 0.5)) if var_b and var_p else None
    diff = [x - y for x, y in zip(rp, rb)]
    md = sum(diff) / n
    te = (sum((x - md) ** 2 for x in diff) / (n - 1)) ** 0.5 * (252 ** 0.5) if n > 2 else None
    alpha = (mp - (beta or 1.0) * mb) * 252
    ir = alpha / te if te else None
    up_p = sum(x for x, y in zip(rp, rb) if y > 0)
    up_b = sum(y for y in rb if y > 0)
    dn_p = sum(x for x, y in zip(rp, rb) if y < 0)
    dn_b = sum(y for y in rb if y < 0)
    return {
        "beta": round(beta, 3) if beta is not None else None,
        "corr": round(corr, 3) if corr is not None else None,
        "alpha": round(alpha, 4),
        "te": round(te, 4) if te else None,
        "ir": round(ir, 3) if ir is not None else None,
        "up_capture": round(up_p / up_b, 3) if up_b else None,
        "down_capture": round(dn_p / dn_b, 3) if dn_b else None,
    }


def _perf_data(bench_key="sp500", rng="MAX"):
    """Wyniki liczone z dziennika: portfel zmienia się wraz z transakcjami (TWR + MWR)."""
    j = load_journal()
    txs = j.get("transactions") or []
    if not txs:
        return None, {"error": "Dziennik jest pusty — dodaj transakcje albo zaimportuj plik z XTB."}
    tickers = sorted({t["ticker"] for t in txs
                      if t.get("side") in ("KUP", "SPRZEDAJ") and t.get("ticker")})
    sec = fill_sectors(tickers)
    cur_of = lambda t: currency_of(sec, t)
    prices = _journal_prices(tickers)
    start = min(t["date"] for t in txs)
    end = date.today().isoformat()

    fx_cache = {}

    def fx_of(c):
        c = (c or "PLN").upper()
        if c == "PLN":
            return lambda _d: 1.0
        if c not in fx_cache:
            fx_cache[c] = perf_mod._ffill_map(perf_mod.fx_history(c, start, end))
        return fx_cache[c]

    res = wyniki.compute(txs, prices, cur_of, fx_of, end=end)
    if res.get("error"):
        return None, res

    bench_raw, meta = perf_mod.bench_series(bench_key, start, end)
    bserie, btotal, bcagr, bmaxdd = [], None, None, None
    if bench_raw:
        bl = perf_mod._ffill_map(bench_raw)
        base = None
        for day, _idx, _v in res["series"]:
            v = bl(day)
            if v is None:
                continue
            if base is None:
                base = v
            bserie.append([day, round(v / base * 100, 4)])
    if len(bserie) > 5:
        btotal = bserie[-1][1] / 100 - 1
        yrs = len(bserie) / 365.25
        bcagr = (1 + btotal) ** (1 / yrs) - 1 if yrs > 0 and btotal > -1 else None
        peak, mdd = bserie[0][1], 0.0
        for _d, v in bserie:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        bmaxdd = mdd
    res["bench"] = {
        "key": bench_key,
        "name": (meta or {}).get("name") if meta else None,
        "series": bserie,
        "total": round(btotal, 4) if btotal is not None else None,
        "cagr": round(bcagr, 4) if bcagr is not None else None,
        "maxdd": round(bmaxdd, 4) if bmaxdd is not None else None,
    }
    try:
        res["attribution"] = _journal_attribution(txs, prices, cur_of, fx_of, end)
        # Atrybucja to wynik per spółka — gotówka nie tworzy wyniku, więc nie jest
        # osobnym wierszem (wcześniej wchodziła do sumy i trzeba ją było „odjąć"
        # wierszem „efekt walutowy", który mieszał obraz). Zostaje tylko drobna
        # różnica z przeliczeń kursów NBP, pokazana jawnie.
        s_total = sum(a["total_pln"] for a in res["attribution"])
        diff = round(float(res["profit_pln"]) - s_total, 2)
        if abs(diff) >= 1:
            res["attribution"].append({
                "ticker": "Różnica kursów", "qty": 0, "currency": "PLN", "px": None,
                "realized_pln": 0.0, "unreal_pln": 0.0, "dividends_pln": 0.0,
                "fees_pln": 0.0, "total_pln": diff, "share": 0.0,
            })
        s_abs = sum(abs(a["total_pln"]) for a in res["attribution"]) or 1.0
        for a in res["attribution"]:
            a["share"] = round(a["total_pln"] / s_abs, 4)
        res["attribution"].sort(key=lambda a: -a["total_pln"])
    except Exception as e:
        res["attribution"] = []
        res.setdefault("warnings", []).append(f"atrybucja: {e.__class__.__name__}")
    try:
        res["relative"] = _rel_metrics(res["series"], res["bench"]["series"])
    except Exception:
        res["relative"] = {}
    res["range"] = rng
    return res, None


@app.route("/api/performance")
def api_performance():
    data, err = _perf_data(request.args.get("bench", "sp500"), request.args.get("range", "MAX"))
    if err:
        return jsonify(err), 400
    return jsonify(data)


@app.route("/chart/perf/<bench>.png")
def api_chart_perf(bench):
    theme = request.args.get("theme", "light")
    if theme not in ("light", "dark"):
        theme = "light"
    rng = request.args.get("range", "MAX")
    path = os.path.join(CHARTS, f"perf_{bench}_{rng}_{theme}.png")
    if not (os.path.exists(path) and time.time() - os.path.getmtime(path) < 1800):
        data, err = _perf_data(bench, rng)
        if err:
            abort(404)
        import charts
        charts.use_theme(theme)
        days = [p[0] for p in data["series"]]
        port = [p[1] for p in data["series"]]
        bench_vals = [b[1] for b in data["bench"]["series"]]
        charts.equity_chart(days, port, bench_vals,
                            data["bench"].get("name") or "benchmark", path)
    return send_from_directory(CHARTS, os.path.basename(path), mimetype="image/png")


# ---------- serie do interaktywnych wykresów ----------
def _yahoo_ohlc_events(t):
    """OHLC 5Y i daty wyników z Yahoo, cache 12 h (świece + znaczniki zdarzeń)."""
    p = os.path.join(CACHE, f"ohlc_{t.replace('.', '_')}.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 12 * 3600:
        try:
            j = json.load(open(p, encoding="utf-8"))
            return j.get("ohlc") or [], j.get("events") or []
        except Exception:
            pass
    import yfinance as yf
    tk = yf.Ticker(t)
    h = tk.history(period="5y", auto_adjust=True)
    ohlc = []
    for ts, row in h.iterrows():
        try:
            ohlc.append([str(ts.date()), round(float(row["Open"]), 4), round(float(row["High"]), 4),
                         round(float(row["Low"]), 4), round(float(row["Close"]), 4)])
        except Exception:
            continue
    events = []
    try:
        ed = tk.get_earnings_dates(limit=24)
        for ts in ed.index:
            events.append({"date": str(ts.date()), "label": "wyniki"})
    except Exception:
        pass
    events.sort(key=lambda x: x["date"])
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"ohlc": ohlc, "events": events}, f)
    return ohlc, events


@app.route("/api/series/<ticker>")
def api_series(ticker):
    t = ticker.upper()
    if not re.match(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$", t):
        abort(404)
    grouped = cached_post([t])
    ser = grouped.get(t) or {}
    if not ser.get("price"):
        return jsonify({"error": f"Brak danych dla {t}"}), 404

    def pts(dp):
        out = []
        for p in dp or []:
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                continue
            v = p[1]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            out.append([datetime.fromtimestamp(p[0], timezone.utc).strftime("%Y-%m-%d"),
                        round(float(v), 4)])
        return out

    d = sc._compute(t, ser)
    rows = hist.build(ser)
    pt = [[datetime.fromtimestamp(r[0], timezone.utc).strftime("%Y-%m-%d"),
           round(r[2], 2), round(r[3], 2)] for r in rows if r[1]]
    sec = fill_sectors([t])

    # --- świece i zdarzenia z Yahoo (Scrab ma tylko zamknięcie) ---
    ohlc, events = [], []
    try:
        ohlc, events = _yahoo_ohlc_events(t)
    except Exception:
        pass

    # --- średni koszt z dziennika (jeśli są otwarte loty) ---
    avg_cost = None
    try:
        jd = load_journal()
        lots = _fifo(jd["transactions"])["lots"]
        book = lots.get(t) or []
        qty = sum(l[0] for l in book)
        if qty > 1e-9:
            cost = sum(l[0] * l[1] for l in book) + sum(l[2] for l in book)
            avg_cost = round(cost / qty, 2)
    except Exception:
        pass

    cons_hist = [[datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), round(v, 2)]
                 for ts, v in tfl.consensus_series(ser)][-400:]
    p2g = tfl.ps2y_growth(ser)
    ps2y_hist = [[datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), round(v, 4)]
                 for ts, v in p2g["points"]][-600:]
    dist = d.get("pe_dist") or {}
    return jsonify({
        "ticker": t, "pt_scrab": cons_hist,
        "ps2y_rev2y": ps2y_hist, "ps2y_info": p2g["last"],
        "currency": currency_of(sec, t),
        "price": pts(ser.get("price")),
        "pe": pts(ser.get("forward_pe_ratio")),
        "ps": pts(ser.get("forward_ps_ratio")),
        "pt": pt,
        "ohlc": ohlc, "events": events, "avg_cost": avg_cost,
        "med_pe": d.get("med_pe"), "med_ps": d.get("med_ps"),
        "min_pe": d.get("min_pe"), "n_pe": d.get("n_pe"),
        "pe_band": [dist.get("p25"), dist.get("p75")] if dist else [None, None],
    })


@app.route("/api/status")
def api_status():
    files = _bundle_files()
    size = 0
    for f in files:
        try:
            size += os.path.getsize(f)
        except OSError:
            continue
    chart_files = []
    try:
        chart_files = os.listdir(CHARTS)
    except OSError:
        os.makedirs(CHARTS, exist_ok=True)      # katalog mógł zostać skasowany w trakcie działania
    for f in chart_files:
        if f.endswith(".png"):
            try:
                size += os.path.getsize(os.path.join(CHARTS, f))
            except OSError:
                continue
    rates = fx_rates()
    return jsonify({
        "data_ts": newest_bundle_ts(),
        "bundles": len(files),
        "cache_mb": round(size / 1024 / 1024, 1),
        "scrab": scrab_session_info(),
        "fx": {"status": rates.get("_status"), "date": rates.get("_date"),
               "usd": rates.get("USD")},
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    removed = 0
    for p in glob.glob(os.path.join(CACHE, "bundle_*.json")):
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    for p in glob.glob(os.path.join(CHARTS, "*.png")):
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    for extra in ("calendar.json", "fx.json", "sectors2.json"):
        p = os.path.join(CACHE, extra)
        if os.path.exists(p):
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass
    return jsonify({"cleared": removed, "ts": datetime.now().strftime("%H:%M")})


@app.errorhandler(Exception)
def _on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Nieobsłużony błąd", exc_info=e)
    return jsonify({"error": str(e)[:220] or e.__class__.__name__}), 500


@app.route("/api/ui-ready", methods=["POST"])
def api_ui_ready():
    """Ślad z przeglądarki: kiedy interfejs jest naprawdę gotowy (diagnostyka startu)."""
    try:
        ms = int((request.get_json(silent=True) or {}).get("ms") or 0)
    except Exception:
        ms = 0
    try:
        with open(os.path.join(HERE, "TFAL-log.txt"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [ui] pierwszy render po {ms} ms\n")
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/checklist/<ticker>", methods=["GET", "POST"])
def api_checklist(ticker):
    """Checklista przed decyzją: 5 pytań, zapis per spółka (trafia też do przeglądu)."""
    t = re.sub(r"[^A-Z0-9.\-]", "", ticker.upper())[:12]
    if not t:
        return jsonify({"error": "Nieprawidłowy ticker"}), 400
    if request.method == "GET":
        return jsonify(load_checklist(t))
    body = request.get_json(force=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or len(items) != CHECKLIST_N:
        return jsonify({"error": f"Oczekuję listy {CHECKLIST_N} wartości true/false."}), 400
    save_checklist(t, items)
    return jsonify({"ok": True, **load_checklist(t)})


@app.route("/api/review/<ticker>", methods=["GET", "POST"])
def api_review(ticker):
    """Przegląd spółki: POST zapisuje to, co widzę; GET porównuje z dzisiejszymi danymi."""
    t = re.sub(r"[^A-Z0-9.\-]", "", ticker.upper())[:12]
    if not t:
        return jsonify({"error": "Nieprawidłowy ticker"}), 400
    try:
        b = bundle_for(t)
    except KeyError:
        return jsonify({"error": f"Brak danych dla {t}"}), 404
    if request.method == "GET":
        return jsonify(b["review"])

    snap = b.get("snapshot") or {}
    if not snap.get("price"):
        return jsonify({"error": "Brak kursu — nie ma czego zapisać"}), 400
    d = load_reviews()
    entry = d.get(t) if isinstance(d.get(t), dict) else {}
    hist = [x for x in (entry.get("history") or []) if isinstance(x, dict)]
    prev = hist[-1] if hist else None
    hist.append(snap)
    d[t] = {"history": hist[-REVIEW_KEEP:]}
    save_reviews(d)
    return jsonify({"ok": True, "saved": snap, "count": len(d[t]["history"]),
                    "diff": (review_diff(prev, snap) if prev else None)})


@app.route("/api/portfolio")
def api_portfolio():
    s = load_state()
    tickers = s["tickers"]
    out, rates, errors = _rows_for(tickers, s.get("shares") or {})
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    cash = float(s.get("cash") or 0)
    priced = [r for r in out if r["value_pln"] is not None]
    missing_fx = sorted({r["currency"] for r in out if r["value_pln"] is None})
    invested_pln = sum(r["value_pln"] for r in priced)
    by_cur = {}
    for r in priced:
        b = by_cur.setdefault(r["currency"], {"native": 0.0, "pln": 0.0, "n": 0})
        b["native"] += r["value_native"]
        b["pln"] += r["value_pln"]
        b["n"] += 1
    return jsonify({"rows": out, "cash": cash, "method": s["method"],
                    "data_ts": newest_bundle_ts(),
                    "total_pln": round(invested_pln + cash, 2),
                    "invested_pln": round(invested_pln, 2),
                    "by_currency": {k: {"native": round(v["native"], 2), "pln": round(v["pln"], 2), "n": v["n"]}
                                    for k, v in by_cur.items()},
                    "fx": {k: v for k, v in rates.items() if not k.startswith("_")},
                    "fx_status": rates.get("_status"), "fx_date": rates.get("_date"),
                    "missing_fx": missing_fx,
                    "errors": errors})


@app.route("/api/company/<ticker>")
def api_company(ticker):
    t = ticker.upper()
    try:
        b = bundle_for(t)
    except KeyError:
        return jsonify({"error": f"Nie znam tickera {t}"}), 404
    sec = fill_sectors([t])
    b["sector"] = (sec.get(t) or {}).get("sector", "—")
    b["name"] = (sec.get(t) or {}).get("name", t)
    b["currency"] = currency_of(sec, t)
    return jsonify(b)


# ---------- screener i porównanie ze spółkami podobnymi ----------
PEERS = {
    "NVDA": ["AMD", "AVGO", "MU", "TSM", "INTC", "QCOM"],
    "AMD": ["NVDA", "AVGO", "INTC", "QCOM", "MU"],
    "MU": ["NVDA", "AMD", "AVGO", "WDC", "INTC"],
    "AVGO": ["NVDA", "AMD", "QCOM", "MU", "TSM"],
    "MSFT": ["GOOGL", "AMZN", "AAPL", "META", "ORCL", "CRM"],
    "AAPL": ["MSFT", "GOOGL", "AMZN", "META", "SONY"],
    "GOOGL": ["MSFT", "META", "AMZN", "AAPL"],
    "AMZN": ["MSFT", "GOOGL", "WMT", "BABA"],
    "META": ["GOOGL", "SNAP", "PINS", "MSFT"],
    "TSLA": ["GM", "F", "RIVN", "BYDDY"],
    "CRDO": ["AVGO", "MRVL", "AMD", "NVDA"],
    "JPM": ["BAC", "GS", "MS", "C", "WFC"],
    "XOM": ["CVX", "COP", "SHEL", "BP"],
    "PKN.WA": ["DNP.WA", "ALE.WA", "KGH.WA", "PGE.WA"],
    "DNP.WA": ["ALE.WA", "PKN.WA", "LPP.WA", "KGH.WA"],
    "ALE.WA": ["DNP.WA", "PKN.WA", "CDR.WA", "LPP.WA"],
    "CDR.WA": ["ALE.WA", "DNP.WA", "PKN.WA", "11B.WA"],
}


@app.route("/api/screener")
def api_screener():
    s = load_state()
    uni = list(dict.fromkeys(s.get("tickers", []) + s.get("watchlist", [])))
    rows, _, errs = _rows_for(uni, s.get("shares") or {})

    def num(k):
        v = request.args.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    mins, maxpe, minup = num("min_score"), num("max_pe"), num("min_upside")
    sector, rating = request.args.get("sector") or "", request.args.get("rating") or ""
    out = []
    for r in rows:
        if mins is not None and (r["score"] or 0) < mins:
            continue
        if maxpe is not None and (r["pe_rel"] is None or r["pe_rel"] > maxpe):
            continue
        if minup is not None and (r["upside"] is None or r["upside"] < minup):
            continue
        if sector and r["sector"] != sector:
            continue
        if rating and r["rating"] != rating:
            continue
        r["in_portfolio"] = r["ticker"] in s.get("tickers", [])
        out.append(r)
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    return jsonify({"rows": out, "universe": len(uni), "errors": errs,
                    "sectors": sorted({r["sector"] for r in rows if r["sector"] != "—"}),
                    "watchlist": s.get("watchlist", [])})


@app.route("/api/peers/<ticker>")
def api_peers(ticker):
    t = ticker.upper()
    s = load_state()
    mine = (fill_sectors([t]).get(t) or {})
    uni = list(dict.fromkeys(s.get("tickers", []) + s.get("watchlist", [])))
    secu = fill_sectors(uni) if uni else {}
    cands = set(PEERS.get(t, []))
    cands |= {x for x in uni if (secu.get(x) or {}).get("sector") == mine.get("sector")}
    cands.discard(t)
    cands = list(cands)[:14]
    if not cands:
        return jsonify({"peers": [], "target": None, "sector": mine.get("sector")})
    rows, _, _ = _rows_for(cands, {})
    tm = mine.get("mcap") or 0
    for r in rows:
        m = r.get("mcap") or 0
        same = r["sector"] == mine.get("sector")
        dist = abs(math.log(m / tm)) if (m and tm) else 9.0
        r["same_sector"] = same
        r["_rank"] = (0 if same else 1, round(dist, 3))
    rows.sort(key=lambda r: r["_rank"])
    top = rows[:5]

    def med(key):
        vals = [r[key] for r in top if r.get(key) is not None]
        return round(float(np.median(vals)), 3) if vals else None

    tgt, _, _ = _rows_for([t], {})
    return jsonify({"peers": top, "sector": mine.get("sector"),
                    "target": tgt[0] if tgt else None,
                    "median": {"score": med("score"), "pe_rel": med("pe_rel"),
                               "ps_rel": med("ps_rel"), "upside": med("upside")}})


@app.route("/chart/<kind>/<ticker>.png")
def api_chart(kind, ticker):
    if kind not in ("pe", "ps", "pt", "pot"):
        abort(404)
    t = ticker.upper()
    if not re.match(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$", t):
        abort(404)
    theme = request.args.get("theme", "light")
    if theme not in ("light", "dark"):
        theme = "light"
    path = os.path.join(CHARTS, f"{kind}_{t.replace('.', '_')}_{theme}.png")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < TTL:
        return send_from_directory(CHARTS, os.path.basename(path), mimetype="image/png")
    grouped = cached_post([t])
    ser = grouped.get(t) or {}
    if not ser.get("price"):
        abort(404)
    d = sc._compute(t, ser)
    import charts
    charts.use_theme(theme)
    if kind == "pe":
        if not d.get("pe_dist") or d.get("med_pe") is None:
            abort(404)
        charts.multiple_chart(d["pe_series"], d["med_pe"], d["pe_dist"]["p90"], d.get("min_pe"),
                              t, "P/E forward (1Y)", path)
    elif kind == "ps":
        if not d.get("ps_dist") or d.get("med_ps") is None:
            abort(404)
        charts.multiple_chart(d["ps_series"], d["med_ps"], d["ps_dist"]["p90"], d.get("min_ps"),
                              t, "P/S forward (1Y)", path)
    elif kind == "pt":
        charts.pt_history_chart(hist.build(ser), t, path)
    elif kind == "pot":
        charts.potential_chart(hist.build(ser), t, path)
    else:
        abort(404)
    return send_from_directory(CHARTS, os.path.basename(path), mimetype="image/png")


@app.route("/api/risk")
def api_risk():
    s = load_state()
    tickers_all = s["tickers"]
    grouped = cached_post(tickers_all)
    sec = fill_sectors(tickers_all)
    prices, skipped = {}, []
    for t in tickers_all:
        dp = (grouped.get(t) or {}).get("price") or []
        prices[t] = {int(x[0]): float(x[1]) for x in dp if isinstance(x[1], (int, float))}
        if len(prices[t]) < 60:
            skipped.append(t)
    # liczymy dla tego, co ma notowania; reszta ląduje w "skipped" zamiast wywalać cały widok
    tickers = [t for t in tickers_all if t not in skipped]
    if not tickers:
        return jsonify({"error": "brak notowań dla żadnej spółki z portfela",
                        "skipped": skipped}), 400
    common = sorted(set.intersection(*[set(prices[t]) for t in tickers]))
    if len(common) < 60:
        return jsonify({"error": f"za mało wspólnych dni notowań ({len(common)})",
                        "skipped": skipped}), 400
    rets = {t: [] for t in tickers}
    for i in range(1, len(common)):
        for t in tickers:
            p0, p1 = prices[t][common[i - 1]], prices[t][common[i]]
            rets[t].append(p1 / p0 - 1 if p0 else 0.0)
    win = 750
    R = np.array([rets[t][-win:] for t in tickers])
    if len(tickers) == 1:
        sd = float(np.std(R[0], ddof=1))
        C = np.array([[1.0]])
        cov = np.array([[sd * sd * 252]])
        vol = np.array([sd * math.sqrt(252)])
    else:
        C = np.nan_to_num(np.corrcoef(R), nan=0.0)
        cov = np.nan_to_num(np.cov(R) * 252, nan=0.0)
        vol = np.sqrt(np.clip(np.diag(cov), 0, None))
    n = len(tickers)
    rates = fx_rates([currency_of(sec, t) for t in tickers])
    fxmap = {t: fx_one(currency_of(sec, t), rates) for t in tickers}
    no_fx = sorted({currency_of(sec, t) for t, f in fxmap.items() if f is None})
    if no_fx:
        return jsonify({"error": f"Brak kursu NBP dla walut: {', '.join(no_fx)} — uzupełnij fx.json"}), 400
    w = np.array([s["shares"].get(t, 0) * (prices[t][max(prices[t])] if prices[t] else 0) * fxmap[t]
                  for t in tickers])
    invested = float(w.sum())
    total = invested + float(s.get("cash") or 0)
    w = (w / invested) if invested > 0 else np.array([1 / n] * n)
    port_var = float(w @ cov @ w)
    port_vol = math.sqrt(max(port_var, 0))
    mcr = (cov @ w) / port_vol if port_vol else np.zeros(n)
    rc = (w * mcr) / port_vol if port_vol else np.zeros(n)
    daily = (R.T @ w)
    var_hist = float(np.percentile(daily, 5))
    var_param = 1.645 * float(np.std(daily, ddof=1))

    sectors = {}
    for i, t in enumerate(tickers):
        sec_name = (sec.get(t) or {}).get("sector", "—")
        sectors[sec_name] = sectors.get(sec_name, 0) + float(w[i])

    return jsonify({
        "tickers": tickers,
        "skipped": skipped,
        "corr": [[round(float(C[i][j]), 3) for j in range(n)] for i in range(n)],
        "vol": {t: round(float(vol[i]), 4) for i, t in enumerate(tickers)},
        "weights": {t: round(float(w[i]), 4) for i, t in enumerate(tickers)},
        "risk_contrib": {t: round(float(rc[i]), 4) for i, t in enumerate(tickers)},
        "vol_pos": {t: round(float(w[i] * vol[i]), 4) for i, t in enumerate(tickers)},
        "portfolio_vol": round(port_vol, 4),
        "var_param": round(var_param, 4),
        "var_hist": round(var_hist, 4),
        "var_10d": round(var_param * math.sqrt(10), 4),
        "risk_budget": round(float(sum(w[i] * vol[i] for i in range(n))), 4),
        "total": round(total, 2),
        "total_pln": round(total, 2),
        "data_ts": newest_bundle_ts(),
        "var_pln": round(var_param * invested, 2),
        "var_hist_pln": round(var_hist * invested, 2),
        "invested_pln": round(invested, 2),
        "currency": {t: currency_of(sec, t) for t in tickers},
        "fx": {t: round(fxmap[t], 4) for t in tickers},
        "sectors": {k: round(v, 4) for k, v in sorted(sectors.items(), key=lambda x: -x[1])},
    })


@app.route("/api/calendar")
def api_calendar():
    s = load_state()
    tickers = s["tickers"]
    path = os.path.join(CACHE, "calendar.json")
    data = None
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
        try:
            data = _load_json_file(path)
        except Exception:
            data = None
    if data is None:
        data = []
        try:
            import yfinance as yf
            today = date.today()
            for t in tickers:
                try:
                    cal = yf.Ticker(t).calendar or {}
                except Exception:
                    cal = {}
                for key, label in (("Earnings Date", "Wyniki"), ("Ex-Dividend Date", "Ex-div"),
                                   ("Dividend Date", "Dywidenda")):
                    v = cal.get(key)
                    vals = v if isinstance(v, (list, tuple)) else [v] if v else []
                    for it in vals:
                        d = it.date() if isinstance(it, datetime) else it
                        if isinstance(d, date) and d >= today:
                            data.append({"date": d.isoformat(), "ticker": t, "event": label,
                                         "days": (d - today).days})
            data.sort(key=lambda x: (x["date"], x["ticker"]))
            _dump_json_file(path, data)
        except Exception:
            pass
    # przeglądy spółek: kiedy ostatni zapis i czy wypada zrobić nowy
    today = date.today()
    rev = load_reviews()
    reviews = []
    for t in tickers:
        hist = [x for x in ((rev.get(t) or {}).get("history") or []) if isinstance(x, dict)]
        if not hist:
            reviews.append({"ticker": t, "date": None, "days": None, "stale": True})
            continue
        try:
            d0 = datetime.fromisoformat(str(hist[-1].get("ts"))[:19]).date()
            days = (today - d0).days
        except Exception:
            d0, days = None, None
        reviews.append({"ticker": t, "date": (d0.isoformat() if d0 else None),
                        "days": days, "stale": (days is None or days > 90)})
    reviews.sort(key=lambda r: (r["days"] is None, -(r["days"] or 0)))
    return jsonify({"events": data, "reviews": reviews, "review_after": 90})


@app.route("/api/rebalance", methods=["POST"])
def api_rebalance():
    s = load_state()
    payload = request.get_json(force=True) or {}
    method = payload.get("method") or s.get("method") or "inv_vol"
    max_pos = float(payload.get("max_pos") or s.get("max_pos") or 0.05)
    max_sec = float(payload.get("max_sec") or s.get("max_sec") or 0.25)
    cash = float(payload.get("cash") if payload.get("cash") is not None else s.get("cash") or 0)
    shares_in = payload.get("shares") or s.get("shares") or {}

    import rebalans as rb
    tickers = s["tickers"]
    grouped = cached_post(tickers)
    sec = fill_sectors(tickers)
    price = {}
    for t in tickers:
        dp = (grouped.get(t) or {}).get("price") or []
        price[t] = float(dp[-1][1]) if dp else 0.0
    vols = rb.volatilities({t: (grouped.get(t) or {}).get("price") or [] for t in tickers}, tickers)

    rates = fx_rates([currency_of(sec, t) for t in tickers])
    fxmap = {t: fx_one(currency_of(sec, t), rates) for t in tickers}
    no_fx = sorted({currency_of(sec, t) for t, f in fxmap.items() if f is None})
    if no_fx:
        return jsonify({"error": f"Brak kursu NBP dla walut: {', '.join(no_fx)}"}), 400
    val = {t: float(shares_in.get(t, 0)) * price.get(t, 0) * fxmap[t] for t in tickers}
    invested = sum(val.values())
    total = invested + cash
    if total <= 0:
        return jsonify({"error": "portfel ma zerową wartość"}), 400
    cur_w = {t: val[t] / total for t in tickers}
    n = len(tickers)

    def _score_of(t):
        try:
            return float(bundle_for(t).get("score") or 0)
        except Exception:
            return 0.0

    if method == "equal":
        raw = [1.0] * n
    elif method == "score_vol":
        raw = [_score_of(t) / max(vols[t], 0.05) for t in tickers]
    elif method == "score":
        raw = [_score_of(t) for t in tickers]
    else:
        raw = [1.0 / max(vols[t], 0.05) for t in tickers]
    if sum(raw) <= 0:
        raw = [1.0] * n

    eff_max = max(max_pos, 1.0 / n)
    tgt = rb.cap_weights(raw, eff_max, 0.0, 1.0)
    tgt = rb.cap_sector(tgt, [sec.get(t, {}).get("sector", "—") for t in tickers], max_sec)
    tgt = [min(x, eff_max) for x in tgt]
    tgt = {t: tgt[i] for i, t in enumerate(tickers)}

    trades, buy, sell = [], 0.0, 0.0
    for t in tickers:
        delta = tgt[t] * total - val[t]
        p = price.get(t) or 0
        qty = abs(delta) / (p * fxmap[t]) if (p and fxmap[t]) else 0
        qty = math.floor(qty + 1e-9) if not s.get("fractional") else round(qty, 2)
        amt = qty * p
        amt_pln = amt * fxmap[t]
        if delta > 0:
            buy += amt_pln
        elif delta < 0:
            sell += amt_pln
        trades.append({"ticker": t, "price": round(p, 2), "value": round(val[t], 2),
                       "w_now": round(cur_w[t], 4), "w_tgt": round(tgt[t], 4),
                       "delta": round(tgt[t] - cur_w[t], 4),
                       "action": ("KUP" if delta > 0 else "SPRZEDAJ") if qty > 1e-9 else "—",
                       "qty": qty, "amount": round(amt, 2), "amount_pln": round(amt_pln, 2),
                       "currency": currency_of(sec, t), "fx": round(fxmap[t], 4),
                       "sector": sec.get(t, {}).get("sector", "—")})
    invested_after = sum(tgt.values()) * total
    return jsonify({"trades": trades, "buy": round(buy, 2), "sell": round(sell, 2),
                    "buy_pln": round(buy, 2), "sell_pln": round(sell, 2),
                    "total": round(total, 2), "cash_after": round(total - invested_after, 2),
                    "turnover": round((buy + sell) / 2 / total, 4) if total else 0,
                    "eff_max": eff_max, "method": method})


@app.route("/api/rebalance/execute", methods=["POST"])
def api_rebalance_execute():
    """Wykonuje zamówienia z rebalansu: wpis do dziennika + aktualizacja sztuk i gotówki."""
    body = request.get_json(force=True) or {}
    orders = body.get("orders") or []
    if not orders:
        return jsonify({"error": "Nie zaznaczono żadnego zamówienia"}), 400

    with _lock:
        s = load_state()
        sec = fill_sectors(s.get("tickers", []))
        rates = fx_rates([currency_of(sec, o.get("ticker")) for o in orders if o.get("ticker")])
        j = load_journal()
        txs = j["transactions"]
        nxt = max([t.get("seq", 0) for t in txs] or [0]) + 1
        today = date.today().isoformat()
        added, skipped, cash_delta = 0, [], 0.0
        shares = s.setdefault("shares", {})
        tickers = list(s.get("tickers", []))

        for o in orders:
            tk = str(o.get("ticker") or "").upper().strip()
            side = "SPRZEDAJ" if str(o.get("side", "")).upper().startswith("S") else "KUP"
            try:
                qty = float(o.get("qty") or 0)
                px = float(o.get("price") or 0)
            except (TypeError, ValueError):
                qty = px = 0.0
            if not tk or qty <= 0 or px <= 0:
                skipped.append({"ticker": tk, "reason": "brak tickera, sztuk lub ceny"})
                continue
            cur = currency_of(sec, tk)
            f = fx_one(cur, rates)
            if f is None:
                skipped.append({"ticker": tk, "reason": f"brak kursu NBP dla {cur}"})
                continue
            if side == "SPRZEDAJ":
                lots = _fifo(txs)["lots"]
                held = sum(l[0] for l in lots.get(tk, []))
                if qty > held + 1e-9:
                    skipped.append({"ticker": tk, "reason": f"masz {held:g} szt. w dzienniku"})
                    continue

            txs.append({
                "id": os.urandom(4).hex(), "seq": nxt, "date": today,
                "ticker": tk, "side": side, "qty": round(qty, 6), "price": round(px, 6),
                "fee": 0.0,
                "note": f"rebalans → waga docelowa {round(float(o.get('w_tgt') or 0) * 100, 1)}%",
            })
            nxt += 1
            added += 1
            if side == "KUP":
                shares[tk] = round(float(shares.get(tk) or 0) + qty, 6)
                cash_delta -= qty * px * f
            else:
                shares[tk] = round(max(float(shares.get(tk) or 0) - qty, 0.0), 6)
                cash_delta += qty * px * f
            if tk not in tickers:
                tickers.append(tk)

        if added:
            save_journal(j)
            s["tickers"] = tickers
            s["cash"] = round(max(float(s.get("cash") or 0) + cash_delta, 0.0), 2)
            save_state(s)

    return jsonify({"added": added, "skipped": skipped,
                    "cash": s.get("cash"), "shares": shares})


def _write_rebalans_xlsx(s):
    """Wpisuje stan z aplikacji do Rebalans.xlsx, żeby PDF powstawał z tych samych liczb."""
    from openpyxl import load_workbook
    p = os.path.join(HERE, "Rebalans.xlsx")
    wb = load_workbook(p)
    ws = wb["Rebalans"]
    ws["D4"] = s.get("method", "inv_vol")
    ws["D5"] = float(s.get("cash", 0))
    ws["D6"] = float(s.get("max_pos", 0.05))
    ws["D7"] = float(s.get("max_sec", 0.25))
    ws["D8"] = "tak" if s.get("fractional") else "nie"
    row = 11
    frac = bool(s.get("fractional"))
    for t in s.get("tickers", []):
        ws.cell(row=row, column=1, value=t)
        qty = float(s.get("shares", {}).get(t, 0) or 0)
        ws.cell(row=row, column=2, value=qty if frac else int(qty))
        row += 1
    wb.save(p)


@app.route("/api/export/<what>", methods=["POST"])
def api_export(what):
    """Generuje PDF/xlsx synchronicznie i zwraca gotowy plik do otwarcia."""
    import subprocess
    # w exe nie ma venv — ten sam plik wykonywalny uruchamia skrypt w trybie --run
    if FROZEN:
        py, prefix = sys.executable, ["--run"]
    else:
        py, prefix = os.path.join(BASE, "venv", "Scripts", "python.exe"), []
    body = request.get_json(silent=True) or {}
    state = load_state()

    if what == "karta":
        t = (body.get("ticker") or "MU").upper()
        args = ["karta.py", t]
        out = f"Karta_{t.replace('.', '_')}.pdf"
    elif what == "portfel":
        args = ["batch.py", *state.get("tickers", []), "--pdf"]
        out = "Portfel.pdf"
    elif what == "analiza":
        args = ["analiza.py", "--pdf"]
        out = "Analiza.pdf"
    elif what == "rebalans":
        _write_rebalans_xlsx(state)
        args = ["rebalans.py", "--pdf"]
        out = "Rebalans.pdf"
    else:
        abort(404)

    path = os.path.join(HERE, out)
    if os.path.exists(path):
        try:
            os.remove(path)          # stary plik nie może udawać świeżego wyniku
        except OSError:
            pass
    t0 = time.time()
    try:
        r = subprocess.run([py] + prefix + args, cwd=HERE, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Eksport przekroczył 5 minut."}), 504
    path = os.path.join(HERE, out)
    if not os.path.exists(path):
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return jsonify({"error": "Nie powstał plik " + out + (" (" + err[-1][:160] + ")" if err else "")}), 500
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return jsonify({"error": "Skrypt zgłosił błąd: " + (err[-1][:160] if err else r.returncode)}), 500
    return jsonify({"ok": True, "file": out, "url": "/file/" + out,
                    "seconds": round(time.time() - t0, 1),
                    "size": round(os.path.getsize(path) / 1024)})


REPORT_RE = re.compile(r"^(Karta_[A-Za-z0-9_.\-]+|Portfel(_scoring)?|Analiza|Rebalans|Cena-docelowa-auto)\.(pdf|xlsx)$",
                       re.IGNORECASE)


@app.route("/file/<path:name>")
def api_file(name):
    if not REPORT_RE.match(name):
        abort(404)
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        abort(404)
    return send_from_directory(HERE, name, as_attachment=False)


if __name__ == "__main__":
    print("Aplikacja: http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
