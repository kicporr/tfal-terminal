# Architektura

## Warstwy

```
┌─ shell ─────────────────────────────────────────────────────────────┐
│ desktop.py (pywebview/WebView2)  ·  terminal.py (fallback Edge)     │
│  okno bez ramki, ekran startowy, mostek JS→Python, jedna instancja  │
└───────────────┬─────────────────────────────────────────────────────┘
                │ HTTP 127.0.0.1:<port>
┌───────────────▼─────────────┐   ┌───────────────────────────────────┐
│ app.py — Flask (28 routes)  │   │ web/ — UI (vanilla JS, brak buildu)│
│  stan, cache, dziennik,     │   │  index.html: router, widoki, wykres│
│  import, eksporty           │   │  theme.css: motyw jasny/ciemny     │
└───────────────┬─────────────┘   └───────────────────────────────────┘
                │ import
┌───────────────▼─────────────────────────────────────────────────────┐
│ model: scrab_client · scoring · tfl · history · wyniki · xtb_import   │
│        rebalans · analiza · charts · karta · pdf_export               │
└───────────────┬─────────────────────────────────────────────────────┘
                │ pliki
        state.json · journal.json · reviews.json · checklist.json · _cache/ · *.xlsx/*.pdf
```

Zasada: **aplikacja nie ma własnej kopii modelu**. CLI (`pt.py`, `batch.py`, `analiza.py`,
`rebalans.py`, `karta.py`) i backend importują te same moduły, więc arkusz i widok liczą to samo.
`app.py` jest cienki: składa dane i serializuje do JSON, logika siedzi w modułach.

## Start aplikacji

`Terminal.bat` → `venv\Scripts\pythonw.exe terminal.py` → `desktop.main()`:

1. **Muteks** `Local\TFAL_Terminal_single` — drugie uruchomienie nie tworzy serwera (dwa procesy
   pisałyby do `state.json`); tylko podnosi istniejące okno przez `EnumWindows`.
2. **Wątek serwera** — `werkzeug.make_server(..., threaded=True)` na pierwszym wolnym porcie
   od 8765 (`free_port`).
3. **Okno od razu** — `create_window(html=SPLASH, frameless=True)`: spinner widoczny po ~1,4 s,
   zanim Python dokończy importy.
4. **Podmiana** — gdy socket odpowiada, wątek `boot()` woła `win.load_url("…/?shell=app")`.

Zmierzony start: serwer ~1,5 s, okno ~1,5 s, pierwszy render ~2,0 s (`TFAL-log.txt`).

## Wątki i spójność danych

- Flask: `threaded=True` — żądania obsługiwane równolegle; jeden wolny request (Scrab, PDF)
  nie blokuje UI.
- `_lock` (threading.Lock) chroni `POST /api/state` i wykonanie rebalansu (`/api/rebalance/execute`) —
  odczyt i zapis stanu nie przeplatają się. Pojedynczy `POST /api/journal` nie jest objęty blokadą:
  przy dwóch równoległych zapisach z tej samej chwili możliwa jest zguba jednego wpisu (w praktyce
  interfejs wysyła jeden request naraz).
- Zapis stanu: `_atomic_write_json` — plik tymczasowy `.tmp` → `os.replace` → kopia `.bak`.
  Przerwany zapis nie zostawia pół pliku.
- PDF-y i arkusze: subprocess z `timeout=300`; brak pliku po zakończeniu = błąd, a nie „stary plik”.

## Cache

| co | gdzie | TTL | unieważnianie |
|---|---|---|---|
| bundle metryk (jeden POST na listę tickerów) | `_cache/bundle_<sha1>.json` | 4 h | `POST /api/refresh`, przycinanie do 60 plików |
| wykresy PNG (legacy endpointy) | `_cache/` | 4 h | razem z bundle |
| kursy walut bieżące | `_cache/fx.json` | 12 h | po TTL |
| kursy historyczne NBP (do opłat XTB) | `_cache/fx_hist.json` | bez TTL | rośnie, mały plik |
| profile spółek (sektor, nazwa, kapitalizacja) | `_cache/sectors2.json` | bez TTL | wpis „—” nie jest zapisywany |
| uploady importu | `_cache/import/<token>.<ext>` | do commit | kasowane po imporcie |

Klucz bundle to SHA-1 posortowanej listy tickerów — portfel 5 spółek i portfel 6 spółek to dwa
różne pliki, więc dodanie spółki nie psuje cache’u pozostałych.

## Pliki stanu

**`state.json`** — portfel:

```json
{
  "tickers": ["NVDA", "MU"], "shares": {"NVDA": 45, "MU": 6},
  "cash": 0.0, "method": "inv_vol", "max_pos": 0.5, "max_sec": 1.0,
  "fractional": true, "watchlist": [], "_migrated_from": "Rebalans.xlsx"
}
```

**`journal.json`** — jedna lista transakcji (typy: `KUP`, `SPRZEDAJ`, `WPŁATA`, `WYPŁATA`,
`DYWIDENDA`):

```json
{"transactions": [
  {"id": "a1b2c3d4", "seq": 1, "date": "2026-02-11", "ticker": "NVDA", "side": "KUP",
   "qty": 10, "price": 100.0, "fee": 5.0, "amount": null, "note": "…", "xtb": true}
]}
```

`seq` rozstrzyga kolejność transakcji z tego samego dnia (data + seq + id), `amount` wypełniane
tylko dla typów kasowych.

**`checklist.json`** — checklista przed decyzją (klucz = ticker, 5 wartości true/false).

**`reviews.json`** — przeglądy: 20 ostatnich zapisów na spółkę (liczby z dnia przeglądu, checklista,
wersja modelu, lista metod wchodzących do PT). Porównanie liczy się na bieżąco
w `review_diff` — plik nie przechowuje różnic, tylko punkty odniesienia.

Migracje: brak wersjonowania schematu — nowe pola są dokładane z wartościami domyślnymi przy
odczycie (`load_state`, `load_journal`), stare dane działają bez konwersji.

## Frontend

`web/index.html` — jeden plik, zero build-stepu:

- **Router** na `location.hash`: `#portfolio` (domyślnie), `#company/<ticker>`, `#risk`,
  `#calendar`, `#rebalance`, `#journal`, `#performance`.
- **`state`** — jeden obiekt: `rows`, `tickers`, `current`, `sort`, `chart` (instancja wykresu).
- **Rendering** — szablony literowe + `innerHTML`; wszystkie dane z API przechodzą przez `esc()`
  (XSS), liczby przez `fmt()/pct()/qty()`, kolory przez `scoreColor()`.
- **Wyścigi** — `beginView()` zwraca token; spóźniona odpowiedź nie nadpisuje nowszego widoku
  (`isCurrent(seq)`), a `fetch` ma `AbortSignal.timeout`.
- **Wykres** — Lightweight Charts v4.2.3 z `web/vendor/` (lokalnie): kurs + panele P/E, P/S,
  PT upside; `mkChart(el, log=true)`; instancja niszczona przy zmianie widoku.
- **Motyw** — `data-theme` na `<html>`, wybór w `localStorage["tfal.theme"]`, `?theme=dark`
  wymusza go z linku; kolory wykresów idą z tokenów CSS (`--acc`, `--good`, `--bad`, `--orange`
  dla panelu PT upside), więc oba motywy mają własne, czytelne odcienie
  wymusza z linku; skrypt w `<head>` ustawia go przed pierwszym rysowaniem.
- **Pasek okna** — `#appbar` widoczny tylko przy `?shell=app`; przyciski wołają `pywebview.api`
  (`minimize`, `toggle_max`, `close`), eksport PDF otwiera plik przez `os.startfile`.

## Bezpieczeństwo

- Serwer słucha wyłącznie `127.0.0.1` — brak dostępu z sieci.
- `GET /file/<name>` — `send_from_directory` (bez przechodzenia po katalogach).
- `pywebview.api.open_file` — nazwę pliku przepuszcza regex `[A-Za-z0-9_.-]+\.(pdf|xlsx)`
  i sprawdza, że plik leży w katalogu aplikacji.
- `/api/journal`, `/api/state`, `/api/checklist` — walidacja typów i zakresów; brak SQL, brak eval.
- `scrab_session.json` to sekret: nie loguje się go, nie wysyła, nie commitować do repo.
- Sesję ustawia `Sesja-Scrab.bat` → `sesja.py` (wklejenie nagłówka Cookie z DevTools); procedura w `docs/OPERACJE.md`.

## Wydajność — gdzie uważać

- Najdroższe są **importy** (pandas/matplotlib/yfinance) i **pierwszy request do Scraba**.
  Dlatego: `charts` importowany leniwie, okno pokazywane przed serwerem, bundle cache’owany.
- `/api/company` liczy pełny model jednej spółki (~0,2–1 s po cache).
- `/api/portfolio` liczy wiersze dla wszystkich tickerów naraz (jeden bundle).
- Eksport PDF: karta ~4–5 s, portfel/analiza/rebalans 5–18 s (subprocess + Excel COM).

## Czego tu nie ma (świadomie)

- Brak ORM-a i bazy — pliki JSON wystarczają do portfela tej wielkości i są czytelne „gołym okiem”.
- Brak frameworka JS — jeden plik HTML jest szybszy w utrzymaniu niż build-step dla tej skali.
- Brak testów automatycznych — weryfikacja idzie przez `probe_*`/`curl` i porównanie z arkuszem;
  przy większych zmianach warto dodać `pytest` na `scoring`, `tfl` i `wyniki` (funkcje czyste).
