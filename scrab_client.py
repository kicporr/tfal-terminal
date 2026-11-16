# -*- coding: utf-8 -*-
"""Klient API Scraba (dane FactSet) na bazie zapisanej sesji.

Sesja to plik scrab_session.json z cookies pobranymi z zalogowanej przegladarki.
Gdy wygasnie, wystarczy poprosic Hermesa o odswiezenie sesji.
"""
import json, os, statistics as st, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = os.path.join(HERE, "scrab_session.json")
API = "https://app.scrab.com/data/metric/"
METRICS = ["price", "eps_est_1y", "revenue_per_share_ntm", "forward_pe_ratio", "forward_ps_ratio"]


class ScrabError(RuntimeError):
    pass


def load_session():
    if not os.path.exists(SESSION):
        raise ScrabError("brak pliku scrab_session.json")
    d = json.load(open(SESSION, encoding="utf-8"))
    ck = d.get("cookies") or []
    if not ck:
        raise ScrabError("sesja bez cookies")
    jar = "; ".join(f"{c['name']}={c['value']}" for c in ck)
    csrf = next((c["value"] for c in ck if c["name"] == "csrftoken"), "")
    return jar, csrf, d.get("saved", "?")


def post(tickers, metrics=None, index=1):
    jar, csrf, _ = load_session()
    body = json.dumps({"tickers": tickers, "metrics": metrics or METRICS,
                       "economic": [], "index": index}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-CSRFToken": csrf, "Cookie": jar,
        "Referer": "https://app.scrab.com/charts/master/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122 Safari/537.36"})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ScrabError("sesja wygasła (401/403) — zaloguj się ponownie do Scraba")
        raise ScrabError(f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        raise ScrabError(f"HTTP: {e}")
    if raw.lstrip().startswith("<"):
        raise ScrabError("sesja wygasla (serwer zwrocil HTML zamiast JSON)")
    try:
        return json.loads(raw)
    except Exception as e:
        raise ScrabError(f"zly JSON: {e}")


def yearly_extremes(dp):
    """Mediana rocznych maksimów i minimów - odporna na jednorazowe skoki."""
    import datetime as _dt
    buckets = {}
    for point in dp or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        ts, val = point[0], point[1]
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        y = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).year
        buckets.setdefault(y, []).append(float(val))
    yrs = [v for v in buckets.values() if len(v) >= 20]
    if not yrs:
        return None, None
    return st.median([max(v) for v in yrs]), st.median([min(v) for v in yrs])


def thin(dp, maxn=240):
    """Przerzedza serie do wykresu, zeby plik nie puchl."""
    if len(dp) <= maxn:
        return dp
    step = len(dp) / maxn
    return [dp[min(int(i * step), len(dp) - 1)] for i in range(maxn)]


def stat(vals):
    v = sorted(float(x) for x in vals if isinstance(x, (int, float)))
    if not v:
        return None
    n = len(v)

    def pct(q):
        k = (n - 1) * q
        f, c = int(k), min(int(k) + 1, n - 1)
        return v[f] if f == c else v[f] * (c - k) + v[c] * (k - f)

    return dict(n=n, med=st.median(v), p10=pct(.10), p25=pct(.25),
                p75=pct(.75), p90=pct(.90), mx=v[-1], mn=v[0])


def _compute(t, series):
    """Liczy wskazniki z zestawu serii jednego tickera."""
    if not series.get("price"):
        raise ScrabError(f"Scrab nie zna tickera {t}")

    def last(metric):
        dp = series.get(metric) or []
        for point in reversed(dp):
            if isinstance(point, (list, tuple)) and len(point) >= 2 \
                    and isinstance(point[1], (int, float)) and not isinstance(point[1], bool):
                return point[1]
        return None

    # okno 3 lat - tak jak wykres 3Y w Scrabie
    cut = 3 * 365.25 * 86400

    def last_years(dp):
        dp = [x for x in (dp or []) if isinstance(x, (list, tuple)) and len(x) >= 2]
        return [x for x in dp if x[0] >= dp[-1][0] - cut] if dp else []

    pe_all = series.get("forward_pe_ratio") or []
    ps_all = series.get("forward_ps_ratio") or []
    pe_dp = last_years(pe_all)
    ps_dp = last_years(ps_all)
    pe = stat([p[1] for p in pe_dp])
    ps = stat([p[1] for p in ps_dp])
    # granice z tego samego okna 3 lat co mediana: mediana rocznych maksimów/minimów,
    # a nie surowy szczyt (jeden skok mnożnika nie może ciągnąć wyceny przez lata)
    pe_up, pe_dn = yearly_extremes(pe_dp)
    ps_up, ps_dn = yearly_extremes(ps_dp)
    win = len(series.get("price", []))
    import datetime as _dt
    p0 = (pe_dp or series["price"])[0][0]
    p1 = (pe_dp or series["price"])[-1][0]
    d0 = _dt.datetime.fromtimestamp(p0, _dt.timezone.utc).strftime("%Y-%m-%d")
    d1 = _dt.datetime.fromtimestamp(p1, _dt.timezone.utc).strftime("%Y-%m-%d")

    return dict(
        ticker=t, source="Scrab (FactSet)", window_days=win,
        price=last("price"), fwd_eps=last("eps_est_1y"), fwd_rev_ps=last("revenue_per_share_ntm"),
        med_pe=pe["med"] if pe else None,
        max_pe=pe_up if pe_up is not None else (pe["mx"] if pe else None),
        min_pe=pe_dn if pe_dn is not None else (pe["mn"] if pe else None),
        med_ps=ps["med"] if ps else None,
        max_ps=ps_up if ps_up is not None else (ps["mx"] if ps else None),
        min_ps=ps_dn if ps_dn is not None else (ps["mn"] if ps else None),
        n_pe=pe["n"] if pe else 0, n_ps=ps["n"] if ps else 0,
        window_txt=f"{d0} → {d1} ({len(pe_dp)} sesji P/E)",
        pe_series=thin(pe_dp), ps_series=thin(ps_dp), raw=series,
        pe_dist=pe, ps_dist=ps,
        raw_pe=pe, raw_ps=ps,
    )


EXTRA_METRICS = ["eps_growth", "operating_margin", "return_on_assets", "current_ratio",
                 "ebitda", "net_debt", "price_target_upside",
                 "forward_ps_ratio_2y", "eps_est_2y",   # prognozy 2 lata do przodu
                 "return_on_invested_capital", "free_cash_flow", "net_income", "ev_ebitda"]


def fetch(ticker, metrics=None):
    """Zwraca dict z danymi dla jednego tickera albo rzuca ScrabError."""
    t = ticker.upper().strip()
    data = post([t], metrics or METRICS)
    series = {s.get("metric"): s.get("datapoints") or [] for s in data if s.get("datapoints")}
    return _compute(t, series)


def fetch_many(tickers):
    """Jedno zapytanie na wiele tickerow -> {ticker: dane}. Pomija nieznane."""
    ts = [x.upper().strip() for x in tickers if x and str(x).strip()]
    if not ts:
        return {}
    data = post(ts)
    grouped = {}
    for s in data:
        tk = (s.get("ticker") or "").upper()
        if not tk or not s.get("datapoints"):
            continue
        grouped.setdefault(tk, {})[s.get("metric")] = s.get("datapoints")
    out = {}
    for tk in ts:
        if tk in grouped:
            try:
                out[tk] = _compute(tk, grouped[tk])
            except ScrabError:
                continue
    return out


if __name__ == "__main__":
    import sys
    for tk in (sys.argv[1:] or ["MU"]):
        d = fetch(tk)
        print(f"{d['ticker']:10} kurs {d['price']:.2f}  EPS {d['fwd_eps']:.2f}  rev/akcję {d['fwd_rev_ps']:.2f}")
        print(f"  P/E med {d['med_pe']:.2f} | p25 {d['min_pe']:.2f} | p75 {d['max_pe']:.2f} | n={d['n_pe']}")
        print(f"  P/S med {d['med_ps']:.2f} | p75 {d['max_ps']:.2f} | n={d['n_ps']}")
