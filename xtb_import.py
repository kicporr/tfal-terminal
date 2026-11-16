"""Parser eksportu XTB (xStation 5) → transakcje do dziennika.

Formaty (XTB zmieniał je kilka razy):
  * pełny raport XLSX: arkusze "Cash Operations", "Closed Positions", "Open Positions",
  * CSV z zakładki Historia (te same kolumny co "Cash Operations").

Kolumny rozpoznajemy po nazwach (PL/EN), nie po pozycji. Wiersza, którego nie
umiemy zaklasyfikować, nie zgadujemy — wraca z powodem i jest odznaczony.
Kwoty w arkuszu są w walucie rachunku (PLN), a ceny w walucie instrumentu —
opłaty przeliczamy kursem NBP przekazanym przez `rate_fn`.
"""
import csv
import hashlib
import io
import os
import re
import unicodedata
from datetime import datetime

SUFFIX_TO_YAHOO = {
    ".US": "", ".PL": ".WA", ".UK": ".L", ".DE": ".DE", ".FR": ".PA",
    ".NL": ".AS", ".ES": ".MC", ".IT": ".MI", ".CH": ".SW", ".JP": ".T",
    ".PT": ".LS", ".BE": ".BR", ".AT": ".VI", ".SE": ".ST", ".NO": ".OL",
}
SUFFIX_TO_CCY = {
    ".US": "USD", ".PL": "PLN", ".UK": "GBP", ".DE": "EUR", ".FR": "EUR",
    ".NL": "EUR", ".ES": "EUR", ".IT": "EUR", ".CH": "CHF", ".JP": "JPY",
    ".PT": "EUR", ".BE": "EUR", ".AT": "EUR", ".SE": "SEK", ".NO": "NOK",
}

TYPE_SYN = {
    "type":       ["type", "typ", "operation", "operacja", "rodzaj", "transaction type"],
    "time":       ["time", "time (utc)", "data", "date", "czas", "data operacji", "operation time"],
    "ticker":     ["ticker", "symbol"],
    "instrument": ["instrument", "walor", "nazwa instrumentu", "market"],
    "amount":     ["amount", "kwota", "value", "wartosc", "wartość", "change", "zmiana",
                   "net amount", "cash", "saldo"],
    "volume":     ["volume", "ilosc", "ilość", "quantity", "sztuki"],
    "comment":    ["comment", "komentarz", "description", "opis", "szczegoly", "szczegóły"],
    "id":         ["id", "numer", "reference", "ref", "transaction id"],
}


def _norm(s):
    s = str(s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s)


def _num(val):
    """'1 234,56' / '1,234.56' / '-34.5' → float."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("\u00a0", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _ticker(raw):
    """'AAPL.US' → ('AAPL', 'USD'); 'CDR.PL' → ('CDR.WA', 'PLN')."""
    s = str(raw or "").strip().upper()
    if not s or s in ("-", "—"):
        return None, None
    if " " in s:
        s = s.split(" ")[0]
    base, dot, suf = s.rpartition(".")
    if dot and ("." + suf) in SUFFIX_TO_YAHOO:
        key = "." + suf
        return (base + SUFFIX_TO_YAHOO[key]).strip(), SUFFIX_TO_CCY.get(key)
    if dot and len(suf) <= 3:
        return s, None
    return s, None


# "OPEN BUY SNDK.US 0.3819 @ 1336.98" / "CLOSE BUY PLTR.US 1.8368/2.531 @ 202.5"
# / "OPEN BUY 34 @ 11.748" (starszy format, bez tickera w komentarzu)
TRADE_RE = re.compile(
    r"(OPEN|CLOSE|OTWORZ|OTWÓRZ|ZAMKNIJ)\s+(BUY|SELL|KUP|SPRZEDAJ)\s+"
    r"(?:([A-Z0-9][A-Z0-9.\-]{0,15})\s+)?"
    r"([\d.,]+(?:/[\d.,]+)?)\s*@\s*([\d.,]+)", re.IGNORECASE)

SELL_WORDS = ("sell", "sprzedaj", "sprzedaz", "sprzedaż", "sale")
BUY_WORDS = ("buy", "kup", "zakup", "purchase", "kupno")


def _side_from(act, side_word):
    """XTB: OPEN/CLOSE dotyczy POZYCJI, BUY/SELL kierunku jej otwarcia.

    OPEN BUY   → kupno
    OPEN SELL  → sprzedaż (krótka)
    CLOSE BUY  → sprzedaż (zamykam długą)
    CLOSE SELL → kupno (zamykam krótką)
    """
    a = _norm(act)
    s = _norm(side_word)
    is_open = a.startswith("open") or a.startswith("otworz") or a.startswith("otwórz")
    is_buy = s in ("buy", "kup")
    if is_open:
        return "KUP" if is_buy else "SPRZEDAJ"
    return "SPRZEDAJ" if is_buy else "KUP"


def classify(type_v, comment_v):
    t = _norm(type_v)
    c = _norm(comment_v)
    both = f"{t} {c}"
    if ("podatek" in both and "dywidend" in both) or "dividend tax" in both or "withholding" in both:
        return "TAX"
    if "dywidend" in both or "dividend" in both:
        return "DIV"
    if "prowizj" in both or "commission" in t or "fee" in t:
        return "COMMISSION"
    if "przewalutow" in both or "currency conversion" in both or t.startswith("exchange"):
        return "FX"
    if "wyplat" in both or "wypłat" in both or "withdraw" in both:
        return "WITHDRAWAL"
    if "wplat" in both or "wpłat" in both or "deposit" in both:
        return "DEPOSIT"
    if "odsetk" in both or "interest" in t:
        return "INTEREST"
    if TRADE_RE.search(str(comment_v or "")):
        return "TRADE"
    if any(w in t for w in BUY_WORDS + SELL_WORDS):
        return "TRADE"
    return "OTHER"


def parse_trade(row):
    comment = str(row.get("comment") or "")
    m = TRADE_RE.search(comment)
    side = qty = price = None
    if m:
        act, side_w, _tk, qty_s, price_s = m.groups()
        price = _num(price_s)
        if "/" in qty_s:
            qty_s = qty_s.split("/")[0]
        qty = _num(qty_s)
        side = _side_from(act, side_w)
    amount = _num(row.get("amount"))
    ticker, ccy = _ticker(row.get("instrument"))
    if price is None and amount and qty:
        price = abs(amount) / qty if qty else None
    if qty is None and amount and price:
        qty = round(abs(amount) / price, 6)
    if side is None:
        t = _norm(row.get("type"))
        if any(w in t for w in SELL_WORDS):
            side = "SPRZEDAJ"
        elif any(w in t for w in BUY_WORDS):
            side = "KUP"
    return side, qty, price, ticker, ccy


def _header_map(cells):
    norm = [_norm(c) for c in cells]
    out = {}
    for key, syns in TYPE_SYN.items():
        for i, c in enumerate(norm):
            if c in syns:
                out[key] = i
                break
    return out


def _rows_from_csv(path):
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Nie umiem odczytać kodowania pliku")
    sample = text[:4000]
    delim = max([";", ",", "\t"], key=lambda d: sample.count(d))
    return [list(r) for r in csv.reader(io.StringIO(text), delimiter=delim)]


def _sheets(path):
    """[(nazwa, wiersze)] — bez read_only, bo XTB nie zapisuje wymiarów arkusza."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    out = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)])
           for ws in wb.worksheets]
    wb.close()
    return out


def _pick_cash_sheet(sheets):
    """Arkusz z nagłówkiem Typ + Czas + Kwota (preferując nazwę 'Cash Operations')."""
    best = None
    for name, rows in sheets:
        for i, row in enumerate(rows[:40]):
            m = _header_map(row)
            if "type" in m and "amount" in m and "time" in m:
                score = 2 + ("cash" in _norm(name))
                if best is None or score > best[0]:
                    best = (score, name, rows, i, m)
                break
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def parse_file(path, rate_fn=None):
    """rate_fn(ccy, iso_date) → ile PLN za 1 jednostkę (do przeliczania opłat)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        picked = _pick_cash_sheet(_sheets(path))
        if picked is None:
            raise ValueError("Nie znalazłem arkusza z kolumnami Typ / Czas / Kwota")
        sheet, grid, head_idx, cols = picked
    else:
        grid = _rows_from_csv(path)
        head_idx, cols = None, None
        for i, row in enumerate(grid[:40]):
            m = _header_map(row)
            if "type" in m and "amount" in m:
                head_idx, cols = i, m
                break
        if head_idx is None:
            raise ValueError("Nie znalazłem nagłówka tabeli (szukam kolumn Typ / Data / Kwota)")
        sheet = os.path.basename(path)

    def cell(row, key):
        i = cols.get(key)
        if i is None or i >= len(row):
            return None
        return row[i]

    rows = []
    for raw in grid[head_idx + 1:]:
        if not any(c not in (None, "") for c in raw):
            continue
        tkr = str(cell(raw, "ticker") or "").strip()
        inst = str(cell(raw, "instrument") or "").strip()
        item = {
            "raw_type": str(cell(raw, "type") or "").strip(),
            "instrument": tkr or inst,      # Ticker ma pierwszeństwo nad nazwą spółki
            "name": inst,
            "amount": _num(cell(raw, "amount")),
            "comment": str(cell(raw, "comment") or "").strip(),
            "id": str(cell(raw, "id") or "").strip(),
        }
        dt = _date(cell(raw, "time"))
        item["dt"] = dt
        item["date"] = dt.date().isoformat() if dt else None
        item["time"] = dt.strftime("%H:%M") if dt else ""
        kind = classify(item["raw_type"], item["comment"])
        item["kind"] = kind

        if kind == "TRADE":
            side, qty, price, ticker, ccy = parse_trade(item)
            item.update({"side": side, "qty": qty, "price": price,
                         "ticker": ticker, "currency": ccy, "fee": 0.0})
            item["ok"] = bool(side and qty and price and ticker and item["date"])
            item["reason"] = "" if item["ok"] else "brak tickera / ceny / liczby sztuk"
            if item["ok"]:
                item["note"] = f"XTB: {item['comment'] or item['raw_type']}"
        elif kind == "COMMISSION":
            t, ccy = _ticker(item["instrument"])
            item.update({"ticker": t, "currency": ccy, "fee": abs(item["amount"] or 0)})
            item["ok"] = False
            item["reason"] = "opłata — przypiszę do transakcji"
        elif kind in ("DIV", "TAX", "DEPOSIT", "WITHDRAWAL"):
            t, ccy = _ticker(item["instrument"])
            amt = item["amount"]
            if kind == "TAX":
                side, amt_abs = "DYWIDENDA", -abs(amt or 0)   # podatek ujemny w wyniku
                note = "podatek od dywidendy (WHT)"
            elif kind == "DIV":
                side, amt_abs = "DYWIDENDA", abs(amt or 0)
                note = "dywidenda"
            elif kind == "DEPOSIT":
                side, amt_abs = "WPŁATA", abs(amt or 0)
                note = "wpłata z XTB"
            else:
                side, amt_abs = "WYPŁATA", abs(amt or 0)
                note = "wypłata z XTB"
            item.update({"kind": "CASH", "cash_kind": kind, "side": side,
                         "ticker": t or "", "currency": ccy or "PLN",
                         "amount": round(amt_abs, 2)})
            item["ok"] = bool(item["date"] and amt_abs)
            item["reason"] = "" if item["ok"] else "brak daty lub kwoty"
            if item["ok"]:
                item["note"] = note
        else:
            item["ok"] = False
            item["reason"] = f"pominięte ({item['raw_type'] or kind})"
        if item["dt"] is None:
            item["ok"] = False
            item["reason"] = item["reason"] or "brak daty"
        rows.append(item)

    # opłaty → transakcja tego samego instrumentu: do 15 min,
    # a opłaty rozliczane po fakcie (SEC fee itp.) — do 7 dni
    for r in rows:
        if r["kind"] != "COMMISSION" or r["amount"] is None:
            continue
        wide = "fee" in _norm(r["raw_type"])
        best, best_gap = None, None
        for t in rows:
            if t["kind"] != "TRADE" or not t["dt"] or not r["dt"]:
                continue
            if _ticker(t["instrument"])[0] != _ticker(r["instrument"])[0]:
                continue
            gap = abs((r["dt"] - t["dt"]).total_seconds())
            if gap <= (7 * 86400 if wide else 900) and (best_gap is None or gap < best_gap):
                best, best_gap = t, gap
        if best is not None:
            fee = abs(r["amount"])                       # kwota w walucie rachunku (PLN)
            note_add = " + opłata"
            if rate_fn and best.get("currency") not in (None, "PLN"):
                rate = None
                try:
                    rate = rate_fn(best["currency"], best.get("date"))
                except Exception:
                    rate = None
                if rate:
                    fee = fee / float(rate)              # PLN → waluta instrumentu
                else:
                    note_add = " + opłata (PLN, brak kursu NBP)"
            best["fee"] = round((best.get("fee") or 0) + fee, 4)
            best["fee_pln"] = round((best.get("fee_pln") or 0) + abs(r["amount"]), 2)
            best["note"] = (best.get("note") or "") + note_add
            r["absorbed"] = True
        else:
            r["reason"] = "opłata bez pary — pominięta"

    for r in rows:
        r.pop("dt", None)
        if r.get("amount") is not None:
            r["amount"] = round(float(r["amount"]), 4)
        for k in ("qty", "price", "fee"):
            if r.get(k) is not None:
                r[k] = round(float(r[k]), 6)
        stamp = (f"{r.get('date')}|{r.get('ticker')}|{r.get('side')}|{r.get('qty')}|{r.get('price')}"
                 if r.get("kind") != "CASH"
                 else f"{r.get('date')}|CASH|{r.get('side')}|{r.get('amount')}|{r.get('ticker')}")
        r["key"] = hashlib.sha1(stamp.encode()).hexdigest()[:12]

    trades = [r for r in rows if r["kind"] == "TRADE"]
    cash = [r for r in rows if r["kind"] == "CASH"]
    divs = [r for r in cash if r["side"] == "DYWIDENDA"]
    return {
        "sheet": sheet,
        "rows": rows,
        "summary": {
            "all": len(rows),
            "trades": len(trades),
            "ok": len([r for r in trades if r["ok"]]),
            "divs": len(divs),
            "skipped": len([r for r in rows if not r["ok"] and not r.get("absorbed")
                            and r["kind"] != "COMMISSION"]),
            "cash": len(cash),
            "deposits": len([r for r in cash if r["side"] == "WPŁATA"]),
            "withdrawals": len([r for r in cash if r["side"] == "WYPŁATA"]),
            "tickers": sorted({r["ticker"] for r in trades if r.get("ticker")}),
            "from": min([r["date"] for r in rows if r.get("date")] or [""]),
            "to": max([r["date"] for r in rows if r.get("date")] or [""]),
        },
    }


def parse_open_positions(path):
    """Stan otwartych pozycji z raportu XTB: {ticker_yahoo: qty}.

    Arkusz ma wiersze podsumowania (nazwa spółki) i wiersze pozycji (ticker + ilość),
    dlatego sumujemy po tickerze tylko wiersze z niepustą ilością.
    """
    if os.path.splitext(path)[1].lower() not in (".xlsx", ".xlsm"):
        return {}
    out = {}
    for name, grid in _sheets(path):
        if "open" not in _norm(name):
            continue
        head_idx, cols = None, None
        for i, row in enumerate(grid[:40]):
            m = _header_map(row)
            if "ticker" in m and ("volume" in m or "amount" in m):
                head_idx, cols = i, m
                break
        if head_idx is None:
            continue
        qty_col = cols.get("volume", cols.get("amount"))
        type_col = cols.get("type")
        for raw in grid[head_idx + 1:]:
            if cols["ticker"] >= len(raw) or qty_col is None or qty_col >= len(raw):
                continue
            # wiersze podsumowania (nazwa spółki) nie mają typu — pomijamy je
            if type_col is not None and type_col < len(raw):
                if _norm(raw[type_col]) not in ("buy", "sell", "kup", "sprzedaj"):
                    continue
            t = _ticker(raw[cols["ticker"]])[0]
            if not t or not re.match(r"^[A-Z0-9.\-]+$", t):
                continue
            qty = _num(raw[qty_col])
            if qty:
                out[t] = round(out.get(t, 0.0) + float(qty), 6)
        break
    return out
