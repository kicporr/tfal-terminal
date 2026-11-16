# Dane

## Skąd co pochodzi

| dana | źródło | odświeżanie | co się dzieje przy awarii |
|---|---|---|---|
| kursy, prognozy EPS i sprzedaży, mnożniki, marże, dług | Scrab → FactSet (`app.scrab.com`) | na żądanie, cache 4 h | błąd w `errors[]`, stara wartość z cache do 4 h |
| OHLC 5 lat, daty wyników, nazwa, sektor, kapitalizacja | Yahoo (`yfinance`) | cache 12 h | wykres bez świec, sektor „—” |
| kursy walut bieżące | NBP (`api.nbp.pl`, tabela A) | cache 12 h | ostatni zapisany + flaga `stale` |
| kursy walut historyczne (opłaty XTB) | NBP, archiwum po dacie | cache `fx_hist.json` | opłata 0 + ostrzeżenie |
| transakcje | XTB (xlsx/csv) albo ręcznie | przy imporcie | — |
| konfiguracja portfela | `state.json` (lokalnie) | przy każdej zmianie | — |

Aplikacja nie scrapuje stron: dane idą przez API albo pliki. Nie ma szyfrowania — pliki są
czytelne, więc katalog traktuj jak dane prywatne.

## Scrab (FactSet)

```
POST https://app.scrab.com/data/metric/
Cookie: <z scrab_session.json>
X-CSRFToken: <z sesji>
Content-Type: application/json

{"tickers": ["NVDA","MU"], "metrics": ["price","eps_est_1y", ...]}
```

Odpowiedź to lista `{ticker, metric, datapoints:[[ts, wartość], …]}`; `ts` = UNIX (UTC),
część serii dzienna, część kwartalna.

### Metryki

| grupa | klucz | co to |
|---|---|---|
| podstawowe | `price` | zamknięcie sesji (EOD, nie live) |
| | `eps_est_1y` | prognoza EPS na najbliższy rok obrotowy |
| | `revenue_per_share_ntm` | prognoza sprzedaży na akcję, 12 mies. |
| | `forward_pe_ratio` | P/E forward (seria dzienna) |
| | `forward_ps_ratio` | P/S forward (seria dzienna) |
| dodatkowe | `eps_growth` | wzrost EPS r/r |
| | `operating_margin` | marża operacyjna (kwartalnie) |
| | `return_on_assets` | ROA |
| | `current_ratio` | płynność bieżąca |
| | `ebitda` | EBITDA (kwartalnie) |
| | `net_debt` | dług netto (ujemny = gotówka netto) |
| | `price_target_upside` | potencjał do konsensusu, **w procentach** |
| | `forward_ps_ratio_2y` | P/S forward na rok +2 (seria dzienna, ~5 lat) |
| | `eps_est_2y` | prognoza EPS na rok +2 |

Zakres: **maks. 5 lat** dziennych notowań. Model wyceny używa 3 lat, historia PT — kroczącego
okna 3 lat po całej serii 5-letniej.

### Sesja

`scrab_session.json` zawiera cookies zalogowanej przeglądarki. Ważność ~2 tygodnie; `/api/status`
zwraca `valid`, `expires` i źródło daty. Po wygaśnięciu:

1. poproś o odświeżenie sesji (nowy plik `scrab_session.json` z zalogowanej przeglądarki),
2. bez sesji działa tylko to, co jest w cache (bundle do 4 h) — reszta zwróci błąd.

Plik traktuj jak hasło: nie wysyłaj go, nie wrzucaj do repo, nie loguj zawartości.

## Yahoo

Używane do rzeczy, których FactSet w tym API nie daje: OHLC (świece), nazwy i sektory,
kapitalizacja, daty wyników kwartalnych, dywidendy.

- `_cache/ohlc_<TICKER>.json` — 5 lat OHLC + 24 ostatnie daty wyników, TTL 12 h.
- Profile trafiają do `_cache/sectors2.json` (bez TTL; wpis „—” nie jest zapisywany, żeby
  porażka nie zatruła mapy na zawsze).
- Przy braku danych Yahoo wykres działa bez świec i znaczników — kurs i PT są ze Scraba.

## NBP

- Kursy bieżące: tabela A, cache 12 h (`_cache/fx.json`) z datą tabeli.
- Kursy historyczne: `nbp_rate_hist(ccy, data)` — szuka kursu z dnia, cofa się maks. 7 dni
  (weekendy, święta), cache w `_cache/fx_hist.json`.
- **Brak kursu nigdy nie jest liczony jako 1,0.** `fx_one()` zwraca `None`, a wiersze dostają
  `fx_missing: true` / `missing_fx: ["USD"]`; ryzyko i rebalans w takim stanie zwracają błąd
  zamiast liczyć bzdury.

## Cache — co można kasować

| plik | TTL | kasowanie |
|---|---|---|
| `_cache/bundle_*.json` | 4 h | bezpieczne, odtworzy się z API |
| `_cache/ohlc_*.json` | 12 h | bezpieczne |
| `_cache/fx.json`, `fx_hist.json` | 12 h / bez | bezpieczne (historyczne dociągną się ponownie) |
| `_cache/sectors2.json` | 7 dni | bezpieczne, ale pierwsze wejście w spółkę będzie wolniejsze |
| `_cache/calendar.json` | 6 h | bezpieczne |
| `_cache/import/*` | do commitu | bezpieczne po zakończeniu importu |
| `_charts/` | 4 h | bezpieczne, odtwarzane |
| `state.json`, `journal.json`, `reviews.json`, `checklist.json` | — | **nie** — to dane użytkownika |
| `scrab_session.json` | ~2 tyg. | nie — bez niego nie ma danych |

Przycinanie: bundle trzymane jest do 60 plików, reszta usuwana (`prune_cache`).

## Import z XTB

Plik: xStation 5 → **Historia → Operacje gotówkowe → Eksport** (`.xlsx`) albo CSV.
Pełny raport ma nagłówek tabeli w 5. wierszu, nad nim metadane — parser to obsługuje.

### Jak parser czyta plik

1. **Kolumny po nazwach, nie po pozycji.** Synonimy PL/EN (`TYPE_SYN`): `type/typ/rodzaj`,
   `amount/kwota/wartość/change`, `volume/ilość/quantity/sztuki`, `time/data/czas`.
   `Ticker` ma pierwszeństwo przed `Instrument` (tam XTB podaje nazwę spółki).
2. **Ticker i waluta** z przyrostka: `.US` → bez zmian, `.PL` → `.WA`, `.UK` → `.L`, `.DE` → `.DE`,
   `.FR` → `.PA`, `.NL` → `.AS`, `.ES` → `.MC`, `.IT` → `.MI`, `.CH` → `.SW`, `.JP` → `.T`.
   Waluta z tego samego przyrostka (`SUFFIX_TO_CCY`).
3. **Typ operacji**: `TRADE` (kupno/sprzedaż), `DIV` (dywidenda), `TAX` (podatek — ujemna
   dywidenda), `DEPOSIT`, `WITHDRAWAL`, `COMMISSION`.
   `CLOSE BUY` w komentarzu = **sprzedaż**, `CLOSE SELL` = kupno (zamykanie pozycji).
4. **Prowizja** z osobnego wiersza jest doklejana do transakcji, jeśli pasuje instrumentem i czasem.
5. **Opłaty są w PLN** — przeliczane na walutę instrumentu kursem NBP z dnia transakcji.
6. **Duplikaty**: klucz `(data, ticker, strona, sztuki, cena)` dla transakcji i
   `(data, CASH, strona, kwota)` dla kasowych — takie wiersze są odznaczone (`dup: true`).
7. **Wiersze niepewne** dostają `ok: false` i `reason` (nie wchodzą do importu).
8. **Open Positions** z tego samego pliku porównuje się z `state.json`; commit może nadpisać
   sztuki (`update_shares: true`). `ASML.AS` mapowane jest na istniejący `ASML`.

Po commicie: transakcje lądują w `journal.json`, sztuki i gotówka w `state.json`. Sprzedaż bez
pokrycia w dzienniku jest odrzucana z powodem.

## Kopia zapasowa

Trzy pliki = cała historia i konfiguracja:

```
state.json        portfel, sztuki, gotówka, limity
journal.json      wszystkie transakcje
checklist.json    checklista przed decyzją (5 pytań)
```

Do tego warto zachować `scrab_session.json` (żeby nie logować się ponownie) — ale to sekret,
więc nie trzymaj go w chmurze bez zabezpieczenia.

Odtworzenie: wrzuć pliki do katalogu aplikacji i uruchom `Terminal.bat`. Cache i `_charts/`
odtworzą się same; brak `checklist.json` oznacza po prostu puste checkboxy.

## Znane pułapki danych

| objaw | przyczyna | co zrobić |
|---|---|---|
| spółka ma `—` w P/E | brak prognozy EPS (strata) | czyta się P/S i potencjał |
| kurs nie zmienia się w ciągu dnia | Scrab to EOD (aktualizacja 1–7 UTC) | odśwież po sesji |
| `missing_fx` na USD | NBP nie odpowiedział | odśwież dane, sprawdź sieć |
| świece puste | Yahoo nie zna tickera (np. `.WA` bez notowań) | zostaje linia kursu ze Scraba |
| skoki linii PT | zmiana roku obrotowego w prognozie | to nie błąd danych |
| duplikaty po imporcie | inny format daty/ceny w pliku | sprawdź `reason` w podglądzie |
