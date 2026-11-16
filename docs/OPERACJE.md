# Operacje

## Uruchamianie

```
Terminal.bat            # aplikacja (pythonw, bez konsoli)
Terminal-debug.bat      # to samo z konsolą — pierwsze podejście przy problemach
```

`Terminal.bat` robi `start "" venv\Scripts\pythonw.exe terminal.py`. Okno jest właścicielem
procesu: **zamknięcie okna kończy serwer**. Drugie uruchomienie nie tworzy drugiego serwera —
podnosi istniejące okno (muteks `Local\TFAL_Terminal_single`).

Sam serwer, bez okna (np. do testów w przeglądarce):

```
venv\Scripts\python.exe app.py        # http://127.0.0.1:8765
```

Zatrzymanie „na twardo”, gdy coś zostało w tle:

```
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess -Force
```

## Pliki wsadowe

| plik | składnia | wynik |
|---|---|---|
| `Odswiez.bat` | `[TICKER] [--pdf]` | `Cena-docelowa-auto.xlsx` (+ `.pdf`) |
| `Portfel.bat` | `[TICKERY] [--pdf]` | `Portfel.xlsx` / `Portfel.pdf` |
| `Analiza.bat` | `[TICKERY] [--pdf]` | `Analiza.xlsx` / `Analiza.pdf` |
| `Rebalans.bat` | `[--pdf]` | `Rebalans.xlsx` / `Rebalans.pdf` |
| `Karta.bat` | `TICKER [--noopen]` | `Karta_<TICKER>.pdf` |
| `PDF.bat` | — | PDF z gotowego arkusza |

Wszystkie używają `venv\Scripts\python.exe`. Gdy arkusz jest otwarty w Excelu, powstaje wersja
`*_nowy.xlsx` zamiast nadpisania (i komunikat) — to celowe, żeby nie stracić pracy.

## Logi i diagnostyka

`TFAL-log.txt` w katalogu aplikacji:

```
2026-11-12 20:09:54 [start  0.01s] port 8765, uruchamiam serwer
2026-11-12 20:09:54 [start  0.01s] start WebView2
2026-11-12 20:09:55 [start  1.37s] okno pokazane
2026-11-12 20:09:55 [start  1.54s] serwer gotowy — ładuję aplikację
2026-11-12 20:09:56 [ui] pierwszy render po 407 ms
```

- `[start …]` — znaczniki z `desktop.py` (muteks → serwer → okno → url).
- `[ui]` — raport z przeglądarki (`POST /api/ui-ready`) o pierwszym renderze.
- Plik rotuje się przy 200 kB do `TFAL-log.txt.old`.
- Wyjątki z wątku serwera lecą na stderr; przy `pythonw` nie ma konsoli, więc uruchamiaj
  `Terminal-debug.bat`, żeby je zobaczyć.

## Typowe problemy

| objaw | przyczyna | rozwiązanie |
|---|---|---|
| okno się nie pojawia, proces żyje | WebView2/pywebview nie wystartował | uruchom `Terminal-debug.bat`, sprawdź komunikat; `set TFAL_FORCE_BROWSER=1` wymusza okno Edge |
| „TFAL Terminal już działa” | druga instancja | użyj otwartego okna; jeśli okno zniknęło — zamknij proces TFAL-Terminal/pythonw |
| pusty ekran po starcie | serwer nie wstał (błąd importu) | `Terminal-debug.bat` — zobaczysz traceback |
| `Nie znam tickera XYZ` | zły symbol lub brak w Scrabie | ticker Yahoo: `AAPL`, `ALE.WA`, `ASML.AS` |
| `missing_fx`, wartości `—` | NBP nie odpowiedział | odśwież dane; sprawdź sieć/VPN |
| sesja Scraba wygasła | po ~2 tygodniach | poproś o nowy `scrab_session.json` (logowanie w przeglądarce) |
| eksport PDF: „Nie powstał plik” | brak Excela / Excel w dialogu | zamknij Excel, sprawdź, czy zainstalowany (COM) |
| `PermissionError` przy xlsx | plik otwarty w Excelu | zamknij plik; skrypt sam tworzy `*_nowy.xlsx` |
| wykres bez świec | Yahoo nie zna tickera | działa linia kursu i panele ze Scraba |
| wolny start (>6 s) | antywirus skanuje `venv` | dodaj katalog do wyjątków; sprawdź `[start]` w logu |
| „za mało wspólnych dni notowań” | spółki z różnych rynków/świeżo dodane | ryzyko liczy się od 60 wspólnych sesji |
| rebalans: brak kursu NBP | waluta bez notowania w NBP | sprawdź walutę spółki (np. HKD bywa problemem) |

## Ograniczenia (świadome)

- **Dane EOD.** Kurs to zamknięcie poprzedniej sesji (Scrab aktualizuje 1–7 UTC). To nie terminal
  intraday.
- **5 lat historii** z API; model używa 3 lat, więc zapas jest, ale dłuższej historii nie ma.
- **Tylko long.** Brak krótkiej sprzedaży, dźwigni, instrumentów pochodnych.
- **Brak podatków w wynikach.** TWR/MWR są brutto; PIT-38/Belka nie są liczone.
- **Dywidendy netto.** Podatek z XTB wchodzi jako ujemna dywidenda (nie ma osobnej kategorii).
- **Waluty w formularzu.** Opłata, wpłaty i wypłaty są w złotych; dywidenda — w walucie spółki.
  Na PLN przeliczamy po kursie NBP z dnia transakcji, więc wynik nie zależy od dzisiejszego kursu.
- **WIG20 to ETF, nie indeks.** Yahoo nie ma historii WIG20, więc benchmarkiem jest Beta ETF
  WIG20TR (dywidendy reinwestowane). Różnica względem indeksu to opłata ETF i tracking error.
- **„Po sprzedaży” wymaga 60 dni historii** po dacie sprzedaży — świeże transakcje pokazują „—”.
- **Rewizje EPS** liczone z serii `eps_est_1y`; przy stracie lub przejściu przez zero zmiana
  procentowa jest nieistotna, więc pokazujemy tylko kierunki (ile razy w górę / w dół).
- **Backtest to test sanity**, nie dowód: dzisiejsza lista spółek (bias przeżywalności),
  kilka–kilkanaście kwartałów.
- **Scoring nasyca się przy 100** — spółki przekraczające wszystkie progi dostają 100,0.
- **Jedna instancja.** Dwa procesy pisałyby do tego samego `state.json`.
- **Brak testów automatycznych.** Weryfikacja przez `curl`, `probe_*` i porównanie z arkuszem.

## Czyszczenie i odświeżanie

| cel | jak |
|---|---|
| świeże dane z API | przycisk „Odśwież dane” → `POST /api/refresh` (czyści bundle i wykresy) |
| wszystko od zera (bez danych użytkownika) | usuń `_cache/` i `_charts/` |
| reset portfela | `state.json` → `{"tickers": [], "shares": {}, "cash": 0, ...}` |
| nowa sesja Scraba | `Sesja-Scrab.bat` (albo `python sesja.py`), potem restart aplikacji |

## Jak odnowić sesję Scraba

Sesja to plik `scrab_session.json` z cookies z zalogowanej przeglądarki. Wygasa po około dwóch
tygodniach — wtedy `api/company` zwraca 503 z komunikatem „Brak sesji Scrab”.

1. Zaloguj się na https://app.scrab.com w Chrome lub Edge.
2. Otwórz DevTools (F12) → zakładka **Network** → odśwież stronę → kliknij dowolne zapytanie do
   `app.scrab.com/data/metric/`.
3. W sekcji **Request Headers** znajdź `Cookie:`, skopiuj całą wartość (długi ciąg `nazwa=wartość; …`).
4. Uruchom `Sesja-Scrab.bat` w katalogu projektu i wklej skopiowany ciąg, zatwierdź Enterem.
   Skrypt zapisze plik i od razu sprawdzi sesję zapytaniem do API („Test API: OK”).
5. Zrestartuj aplikację albo kliknij „Odśwież dane”.

Bez skryptu: zapisz ręcznie JSON w formacie
`{"saved": "2026-11-16 13:00", "cookies": [{"name": "sessionid", "value": "…"}, {"name": "csrftoken", "value": "…"}]}`.
Plik jest w `.gitignore` — nigdy nie trafia do repozytorium i nie wolno go nikomu wysyłać.

## Dokumentacja PDF

```
Dokumentacja.bat                 # buduje i otwiera Dokumentacja-TFAL.pdf
venv\Scripts\python.exe docs\build_pdf.py        # to samo bez otwierania
```

`docs/build_pdf.py` składa `README.md` + `docs/*.md` w jeden HTML (markdown → HTML), drukuje go
w Edge headless (`--print-to-pdf`, bez nagłówka z URL), a potem dokłada stopkę „TFAL Terminal —
dokumentacja” i numer „3 / 27” przez `reportlab` + `pypdf`. Wymaga: `markdown`, `pypdf`,
`reportlab` (są w `venv`).

Po każdej zmianie w `docs/` uruchom `Dokumentacja.bat` — inaczej PDF zostaje z poprzednią treścią.
Spis treści i wewnętrzne linki działają w PDF-ie (klikalne).

## Jak dodawać funkcje

### Nowy endpoint

1. Funkcja w `app.py` z `@app.route("/api/…")`, walidacja wejścia, `jsonify({...})`.
2. Błędy: `return jsonify({"error": "opis"}), 400` — frontend pokazuje `e.message`.
3. Ciężkie obliczenia trzymaj w module (`scoring`, `tfl`, `wyniki`), nie w routingu.
4. Dopisz endpoint do `docs/API.md`.

### Nowe kryterium scoringu

1. `CRITERIA` w `scoring.py` — klucz, etykieta, waga (pamiętaj o sumie 100).
2. `score_parts()` — tabela progów dla nowej metryki (`_sc`).
3. `build_metrics()` — skąd wziąć wartość (seria Scraba).
4. UI: `names` i `weights` w `loadCompany` (`web/index.html`), żeby pasek się rysował.
5. `docs/MODEL.md` — tabela progów; sprawdź, jak zmieniają się ratingi na 5–6 spółkach.

### Nowy widok

1. `views` w `index.html` + funkcja `viewX()` + wpis w `#nav`.
2. Router: `#x` w `route()`.
3. Jeśli widok ma wykres — `initChart`/`mkChart`, pamietaj o `destroy()` przy zmianie widoku.

### Checklist przed commitem

- [ ] `node --check` na wyciągniętym skrypcie z `index.html` (składnia JS).
- [ ] `venv\Scripts\python.exe -c "import app"` (składnia i importy Pythona).
- [ ] Przejście: Portfel → Spółka → Ryzyko → Kalendarz → Rebalans → Dziennik → Wyniki.
- [ ] Motyw jasny i ciemny (tabele, checkboxy, wykresy).
- [ ] Eksport PDF dla widoku, którego dotyczyła zmiana.
- [ ] `docs/` zaktualizowane (API / MODEL / DANE zależnie od zmiany).

## Wydajność — punkty odniesienia

| operacja | czas |
|---|---|
| start okna (splash) | ~1,4 s |
| pierwszy render z danymi (cache) | ~2,0 s |
| `/api/portfolio` (cache) | 0,04–0,1 s |
| `/api/portfolio` (zimny, Scrab) | 1,5–6 s zależnie od liczby tickerów |
| `/api/company` (cache) | 0,2–1 s |
| eksport karty PDF | ~4,5 s |
| eksport portfela / analizy / rebalansu | 5–18 s |

Gdy coś jest wyraźnie wolniejsze: sprawdź `[start]` w logu (czy to import, czy sieć), potem
`/api/status` (czy sesja Scraba ważna, czy cache nie urósł).
