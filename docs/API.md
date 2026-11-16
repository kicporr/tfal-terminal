# API

Serwer: `http://127.0.0.1:<port>` (domyślnie 8765, `desktop.py` bierze pierwszy wolny od 8765).
Wszystko w JSON (poza plikami i PNG). Błędy mają kształt `{"error": "opis"}` i kod 4xx/5xx.
Nie ma autoryzacji — serwer słucha tylko na localhost.

## Przegląd

| # | metoda | ścieżka | do czego |
|---|---|---|---|
| 1 | GET | `/` | `web/index.html` |
| 2 | GET | `/static/<path>` | fonty, motyw, biblioteka wykresów |
| 3 | GET/POST | `/api/state` | portfel: tickery, sztuki, gotówka, limity |
| 4 | GET | `/api/journal` | dziennik + pozycje + podsumowanie FIFO |
| 5 | POST | `/api/journal` | dodanie transakcji (trade lub typ kasowy) |
| 6 | DELETE | `/api/journal/<id>` | usunięcie transakcji |
| 7 | POST | `/api/journal/seed` | pozycje otwarcia z aktualnego portfela |
| 8 | POST | `/api/import/xtb` | wgranie i sparsowanie pliku XTB (podgląd) |
| 9 | POST | `/api/import/xtb/commit` | zapis wybranych wierszy importu |
| 10 | GET | `/api/performance` | TWR/MWR, drawdown, benchmark (sp500/ndx/world/wig20), miesięczne |
| 11 | GET | `/chart/perf/<bench>.png` | statyczny wykres wyników (legacy) |
| 12 | GET | `/api/series/<ticker>` | serie do wykresu interaktywnego |
| 13 | GET | `/api/status` | wiek danych, cache, ważność sesji Scraba |
| 14 | POST | `/api/refresh` | czyszczenie cache bundle i wykresów |
| 15 | POST | `/api/ui-ready` | ślad startu UI (diagnostyka) |
| 16 | GET/POST | `/api/review/<ticker>` | przegląd: zapis i porównanie z dziś |
| 17 | GET | `/api/portfolio` | tabela portfela z ocenami |
| 18 | GET | `/api/company/<ticker>` | pełna analiza jednej spółki |
| 19 | GET | `/api/screener` | filtrowanie uniwersum (portfel + watchlist) |
| 20 | GET | `/api/peers/<ticker>` | spółki podobne + mediana grupy |
| 21 | GET | `/chart/<kind>/<ticker>.png` | statyczne wykresy (legacy) |
| 22 | GET | `/api/risk` | zmienność, VaR, korelacje, sektory |
| 23 | GET | `/api/calendar` | najbliższe zdarzenia |
| 24 | POST | `/api/rebalance` | wagi docelowe i lista transakcji |
| 25 | POST | `/api/rebalance/execute` | wykonanie zleceń → dziennik + stan |
| 26 | POST | `/api/export/<what>` | PDF/xlsx: karta, portfel, analiza, rebalans |
| 27 | GET | `/file/<name>` | pobranie wygenerowanego raportu |
| 28 | GET/POST | `/api/checklist/<ticker>` | checklista przed decyzją: 5 pytań, zapis per spółka |

## Stan portfela

### `GET /api/state`

```json
{"tickers": ["NVDA","MU"], "shares": {"NVDA": 45, "MU": 6}, "cash": 0.0,
 "method": "inv_vol", "max_pos": 0.5, "max_sec": 1.0, "fractional": true,
 "watchlist": [], "_migrated_from": "Rebalans.xlsx"}
```

### `POST /api/state`

Częściowa aktualizacja — wystarczy jedno pole.

```json
{"tickers": ["NVDA","MU","AVGO"], "shares": {"NVDA": 45}, "watchlist": ["AMD"]}
```

Zwraca pełny stan po zapisie. `shares` jest docinane do listy `tickers` (nie zostają sieroty).

## Dziennik

### `GET /api/journal`

```json
{
  "transactions": [{"id":"a1b2c3d4","seq":1,"date":"2026-02-11","ticker":"NVDA","side":"KUP",
                    "qty":10,"price":100.0,"fee":5.0,"amount":null,"note":"","xtb":true}],
  "positions": [{"ticker":"NVDA","qty":45,"avg_cost":173.73,"price":173.73,
                 "value_pln":0.0,"unreal_pln":0.0}],
  "summary": {"deposits_pln":0,"withdrawals_pln":0,"dividends_pln":0,"net_invested_pln":0,
              "realized_pln":0,"unreal_pln":0,"fees_pln":0,
              "closed":12,"wins":7,"losses":5,"win_rate":0.583,"unmatched":0,"trades":113},
  "data_ts": "2026-11-12T18:40:00"
}
```

`summary.by_ticker` to skuteczność per spółka: `trades`, `win_rate`, `realized_pln`, `hold_days`
(średni czas trzymania ważony sztukami), `after_sell` (zmiana kursu 60 dni po sprzedaży) oraz
`best_pln` / `worst_pln`. Obok są `avg_hold_days` i `after_sell_avg` dla całego dziennika.

`realized_pln` i `unreal_pln` liczone są po kursie waluty **z dnia transakcji** (koszt po kursie
z dnia kupna, przychód po kursie z dnia sprzedaży) — dzięki temu dziennik zgadza się z zakładką
Wyniki, a wynik nie zmienia się przy ruchu kursu po zamknięciu pozycji. Każdy wiersz transakcji
ma `fee` (waluta instrumentu) i `fee_pln` (tak jak wpisane w formularzu).

### `POST /api/journal`

Transakcja:

```json
{"side":"KUP","ticker":"MU","qty":6,"price":811.30,"fee":4.5,
 "date":"2026-11-12","note":"HBM — wejście na cykl"}
```

`fee` podajesz **w złotych** — serwer przelicza na walutę instrumentu po kursie NBP z dnia
transakcji i zapisuje obie wartości (`fee` w walucie spółki, `fee_pln` tak jak wpisane).

Typy kasowe (`WPLATA`/`WPŁATA`, `WYPLATA`/`WYPŁATA`, `DYWIDENDA`) używają pola `amount`
(akceptowany jest też `price`), a `ticker` może zostać pusty. `amount` dla wpłat i wypłat jest
w złotych, a dla **dywidendy w walucie spółki** — przy podsumowaniu przeliczamy ją na PLN po
kursie NBP z dnia wypłaty (tak samo robi import z XTB).

| błąd | kod |
|---|---|
| brak tickera/strony/sztuk/ceny | 400 |
| qty ≤ 0 lub cena ≤ 0 | 400 |
| data nie w formacie `RRRR-MM-DD` | 400 |
| sprzedaż większa niż stan FIFO | 400 |

Odpowiedź: `{"ok": true, "count": 154}`.

### `DELETE /api/journal/<id>`

`{"ok": true, "count": 153}` — usuwa wpis i przelicza wszystko od nowa (bez kaskad).

### `POST /api/journal/seed`

Tworzy kupna po dzisiejszych cenach dla spółek z `state.json` (jeśli dziennik jest pusty dla
danego tickera). Odpowiedź `{"ok": true, "added": 5}`; 400, gdy portfel nie ma pozycji.

## Import XTB

### `POST /api/import/xtb`

`multipart/form-data`, pole `file` — `.xlsx`, `.xlsm` albo `.csv` z xStation 5.

```json
{"token":"9f3ac1b2c4d5e6f7",
 "rows":[{"key":"0","kind":"TRADE","ok":true,"date":"2026-02-11","ticker":"NVDA",
          "side":"KUP","qty":10,"price":100.0,"fee":0,"dup":false,"reason":null}],
 "summary":{"trades":112,"cash":1,"duplicates":3,"rejected":0},
 "open_positions":{"NVDA":45,"MU":6}, "state_shares":{"NVDA":45,"MU":6}}
```

`ok:false` + `reason` = wiersz nierozpoznany (nie wchodzi do importu). `dup:true` = już jest
w dzienniku (data + ticker + strona + sztuki + cena).

### `POST /api/import/xtb/commit`

```json
{"token":"9f3ac1b2c4d5e6f7", "keys":["0","3","4"], "update_shares":true}
```

Odpowiedź: `{"added": 3, "skipped": ["sprzedaż bez pokrycia"], "cash": 0.0, "shares": {...}}`.

## Spółka i portfel

### `GET /api/portfolio`

```json
{
  "rows":[{"ticker":"NVDA","name":"NVIDIA Corporation","sector":"Technology","currency":"USD",
           "price":173.73,"pe_rel":0.42,"ps_rel":0.41,"pt_cons":420.2,"pt_opt":520.0,
           "upside":0.763,"pt_scrab":292.97,"upside_scrab":0.763,
           "eps_rev":0.2298,"eps_rev_dir":"up",
           "score":96.3,"score_hard":96.3,"rating":"A",
           "mcap":4.0e12,"shares":45,"value_native":7817.85,"value_pln":null,"fx":null,
           "fx_missing":true,"growth":141.2,"margin":66.24,"tfl":{...}}],
  "cash":0.0,"method":"inv_vol","data_ts":"2026-11-12T18:40:00",
  "total_pln":0.0,"invested_pln":0.0,
  "by_currency":{"USD":{"native":7817.85,"pln":0.0,"n":1}},
  "fx":{"USD":null},"fx_status":"stale","fx_date":"2026-11-12",
  "missing_fx":["USD"],"errors":[]
}
```

`score` = `score_hard` (analiza miękka usunięta w modelu 2.0).
`pt_cons`/`pt_opt` = model TFAL, `pt_scrab` = konsensus Scraba (kurs × (1 + `price_target_upside`));
frontend wybiera parę na podstawie przełącznika, więc wysyłane są zawsze oba warianty.

### `GET /api/company/<ticker>`

Zwraca bundle z `bundle_for()` plus:

- `parts` — 16 kryteriów (punkty 1–10), `metrics` — surowe metryki,
- `score` / `score_hard` / `rating`,
- `tfl` — `{rows:[{q,label,val,status}], ok, n, trend, pt_rev, margin}`,
- `tfl_summary` — jedno zdanie do podpowiedzi,
- `window`, `n_pe`, `updated`.

404: `{"error":"Nie znam tickera XYZ"}` (Scrab nie ma danych).

### `GET /api/screener?sector=Technology&rating=A`

Filtry opcjonalne. Odpowiedź: `{rows, universe, errors, sectors, watchlist}`.
Uniwersum = `tickers` + `watchlist` ze `state.json`.

### `GET /api/peers/<ticker>`

```json
{"peers":[{...wiersz jak w portfelu...}], "sector":"Technology",
 "target":{...}, "median":{"score":88.1,"pe_rel":0.62,"ps_rel":0.71,"upside":0.31}}
```

## Checklista

### `GET/POST /api/checklist/<ticker>`

```json
{"items":[true,false,true,true,false],"updated":"2026-11-16T10:38:41"}
```

`POST` przyjmuje `{"items": [true/false × 5]}`; stan jest zapisywany w `checklist.json`
i wchodzi do następnej migawki przeglądu (`snapshot.checklist`).

## Przegląd

### `GET /api/review/<ticker>`

```json
{"last": {"ts": "2026-11-16T10:09:46", "model": "2.0", "price": 798.44,
          "checklist": [true, false, true, true, false],
          "pt_cons": 1396.71, "pt_opt": 2764.89, "comp_pe": 1916.62, "comp_ps": 876.8,
          "eps": 211.13, "rev_ps": 283.94, "med_pe": 9.08, "med_ps": 3.09,
          "max_pe": 13.09, "max_ps": 9.74, "score_hard": 98.3, "score": 92.8,
          "rating": "A", "methods": ["PE", "PS"],
          "teza": "…", "falsyfikator": "…"},
 "count": 2, "model_now": "1.0", "model_desc": "PT: mediana 3Y + mediana rocznych maksimów (5Y) …",
 "diff": {"since": "2026-11-13T10:09:46", "days": 0,
          "rows": [{"key": "price", "label": "Kurs", "was": 798.44, "now": 798.44, "chg": 0.0}, …],
          "score": {"hard": [98.3, 98.3], "final": [92.8, 92.8], "rating": ["A", "A"]},
          "methods": {"was": ["PE", "PS"], "now": ["PE", "PS"], "lost": [], "gained": []},
          "model": {"was": "1.0", "now": "1.0", "changed": false},
          "checklist": {"was": [], "now": [true, false, true, true, false]}}
```

`last` = ostatni zapis (bez niego `null`), `diff` = porównanie ostatniego zapisu z **dzisiejszymi**
danymi (`chg` = zmiana procentowa, `null` gdy którejś wartości brakuje).

### `POST /api/review/<ticker>`

Bez body — zapisuje bieżący snapshot (kurs, oba składniki PT, prognozy, mnożniki, oceny, tezę
i falsyfikator z analizy miękkiej). Zwraca `{"ok": true, "saved": {…}, "count": 3, "diff": {…}}`.
Na spółkę trzymanych jest 20 ostatnich zapisów (`reviews.json`).

## Wyniki

### `GET /api/performance?bench=sp500|ndx|world|wig20&range=1M|3M|6M|1R|3L|MAX`

```json
{"start":"2026-02-11","end":"2026-11-11","days":196,
 "value_end":15104.0,"cash_end":0.0,"net_invested":15536.0,"profit_pln":-432.0,
 "deposits":15536.0,"withdrawals":0.0,"dividends":0.0,
 "twr":{"total":0.065,"cagr":0.124,"vol":0.361,"sharpe":0.34,"maxdd":-0.305},
 "mwr":{"annual":-0.082},
 "series":[["2026-02-11",10000.0],…], "wealth":[…], "drawdown":[…],
 "monthly":[["2026-02",0.012],…],
 "attribution":[…], "relative":{"beta":0.9,"alpha":0.05,…},
 "bench":{"key":"sp500","label":"S&P 500","series":[…],"stats":{…}},
 "missing_prices":[], "implicit_deposits":0.0, "warnings":[]}
```

400, gdy brak transakcji albo danych do policzenia.

### `GET /api/series/<ticker>`

```json
{"ticker": "NVDA", "currency": "USD",
 "price": [["2021-11-15", 30.03], …], "ohlc": [["2021-11-15", 30.1, 30.4, 29.9, 30.2], …],
 "pe": [[date, 64.14], …], "ps": [[date, 26.11], …],
 "pt": [["2021-11-15", 45.2, 61.7], …], "pt_scrab": [["2026-09-01", 285.4], …],
 "ps2y_rev2y": [["2026-01-02", 0.1988], …],
 "ps2y_info": {"ts": 1794787200, "ratio": 0.1249, "ps2y": 4.25, "growth": 34.0},
 "events": [{"date": "2026-11-17", "title": "Wyniki"}], "avg_cost": 212.13,
 "med_pe": 26.35, "med_ps": 8.9, "min_pe": 12.1, "n_pe": 740, "pe_band": [18.2, 33.1]}
```

`pt` = model (konserwatywna, optymistyczna), `pt_scrab` = konsensus Scraba w czasie
(kurs × (1 + `price_target_upside`), maks. 400 punktów). Panel „PT upside” i linia PT na wykresie
korzystają z tego wariantu, który jest wybrany przełącznikiem `PT model / PT Scrab`.

`ps2y_rev2y` = autorski wskaźnik PS 2Y forward ÷ średni roczny wzrost przychodów (maks. 600 punktów);
`ps2y_info` podaje ostatnie wartości: mnożnik, wzrost w % i wynik.

## Ryzyko i kalendarz

### `GET /api/risk`

```json
{"tickers":["NVDA","MU"],"skipped":[],
 "corr":[[1.0,0.55],[0.55,1.0]],
 "vol":{"NVDA":0.52},"weights":{"NVDA":0.6},"risk_contrib":{"NVDA":0.63},
 "vol_pos":{"NVDA":0.31},"portfolio_vol":0.514,
 "var_param":0.084,"var_hist":-0.075,"var_10d":0.266,"risk_budget":0.63,
 "total":15092.0,"total_pln":15092.0,"var_pln":803.0,"var_hist_pln":-710.0,
 "invested_pln":15092.0,"currency":{"NVDA":"USD"},"fx":{"NVDA":3.65},
 "sectors":{"Technology":0.57},"data_ts":"…"}
```

400: brak wspólnych dni notowań (mniej niż 60) albo brak notowań dla żadnej spółki.

### `GET /api/calendar`

```json
{"events":[{"date":"2026-11-17","ticker":"NVDA","event":"Wyniki","days":5},
           {"date":"2026-11-19","ticker":"MSFT","event":"Ex-div","days":7}]}
```

`event`: `Wyniki` | `Ex-div` | `Dywidenda`. Tylko przyszłe zdarzenia, posortowane po dacie.
Odpowiedź cache’owana 6 h (`_cache/calendar.json`).

## Rebalans

### `POST /api/rebalance`

```json
{"method":"inv_vol","max_pos":0.5,"max_sec":1.0,"cash":0,"shares":{"NVDA":45}}
```

Wszystkie pola opcjonalne (domyślnie ze `state.json`). Metody: `inv_vol`, `equal`, `score`,
`score_vol`, `manual`.

```json
{"trades":[{"ticker":"NVDA","price":173.73,"value":7817.85,"w_now":0.61,"w_tgt":0.45,
            "delta":-0.16,"action":"SPRZEDAJ","qty":15,"amount":2605.95,"amount_pln":9511.7,
            "currency":"USD","fx":3.65,"sector":"Technology"}],
 "buy":0.0,"sell":9511.7,"buy_pln":0.0,"sell_pln":9511.7,
 "total":15092.0,"cash_after":1200.0,"turnover":0.31,"eff_max":0.5,"method":"inv_vol"}
```

400: brak kursu NBP dla waluty albo portfel o zerowej wartości.

### `POST /api/rebalance/execute`

```json
{"orders":[{"ticker":"NVDA","action":"SPRZEDAJ","qty":15,"price":173.73}]}
```

Pod jedną blokadą: dopisuje transakcje do dziennika (data dzisiejsza, notatka z wagą docelową,
opłata 0), zmienia sztuki i gotówkę. Odpowiedź: `{"added":2,"skipped":["…"],"cash":1200.0,"shares":{…}}`.

## Eksport i pliki

### `POST /api/export/<karta|portfel|analiza|rebalans>`

```json
{"ticker":"NVDA"}      // tylko dla karta
```

Uruchamia skrypt w `venv` (subprocess, timeout 300 s), usuwa stary plik przed startem, zwraca:

```json
{"ok":true,"file":"Karta_NVDA.pdf","url":"/file/Karta_NVDA.pdf","seconds":4.5,"size":78}
```

500: skrypt nie utworzył pliku (w treści ostatnia linia błędu). 504: przekroczony czas.

### `GET /file/<name>`

Pobranie/otwarcie raportu z katalogu aplikacji (`send_from_directory`, brak przechodzenia w górę).

## Diagnostyka

### `GET /api/status`

```json
{"data_ts":"2026-11-12T18:40:00","bundles":3,"cache_mb":12.4,
 "scrab":{"valid":true,"days_left":11,"expires":"2026-11-24","saved":"2026-11-10T09:12:00"},
 "fx":{"status":"ok","date":"2026-11-12","usd":3.65}}
```

### `POST /api/refresh`

```json
{"cleared": 4, "ts": "21:15"}
```

Czyści `bundle_*.json` i wygenerowane PNG. Nie rusza dziennika, stanu, sesji Scraba ani sektorów.

### `POST /api/ui-ready`

`{"ms": 407}` — frontend raportuje czas pierwszego renderu; wpis ląduje w `TFAL-log.txt`.
Zwraca `{"ok": true}`.

## Legacy (wciąż działające)

- `GET /chart/<pe|ps|pt|pot>/<ticker>.png?theme=light|dark` — statyczne wykresy matplotlib.
- `GET /chart/perf/<bench>.png?theme=&range=` — wykres wyników dla PDF.

Frontend ich nie używa (zastąpione Lightweight Charts), ale zostały dla starych linków i PDF.
