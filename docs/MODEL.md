# Model — metodyka i wzory

Wszystko, co aplikacja liczy, sprowadza się do pięciu rzeczy: ceny docelowej, oceny jakości
(twardej), ryzyka, wyniku portfela i wag docelowych. Ten dokument opisuje każdy wzór
i każdy próg — bez zaglądania w kod.

## 1. Cena docelowa

Wejście: kurs, prognoza EPS na 12 miesięcy (`eps_est_1y`), prognoza sprzedaży na akcję
(`revenue_per_share_ntm`) oraz historia mnożników.

```
mediana P/E  = mediana dziennych forward P/E z okna 3 lat
mediana P/S  = mediana dziennych forward P/S z okna 3 lat
max P/E      = mediana rocznych maksimów z tego samego okna 3 lat
max P/S      = mediana rocznych maksimów z tego samego okna 3 lat
```

**Dlaczego mediana rocznych maksimów, a nie maksimum z okna:** jedno załamanie EPS potrafi dać
mnożnik rzędu 300× i rozjechać skalę na lata. Mediana rocznych maksimów opisuje „górny, ale
powtarzalny” poziom wyceny. Okno jest to samo co dla mediany (3 lata), żeby scenariusz optymistyczny
nie odwoływał się do innego reżimu wyceny niż scenariusz bazowy; przy historii krótszej niż rok
model schodzi do surowego maksimum i mówi o tym w karcie.

```
PT konserwatywna = średnia( mediana P/E × EPS , mediana P/S × sprzedaż/akcję )
PT optymistyczna = średnia( max P/E × EPS , max P/S × sprzedaż/akcję )
potencjał        = PT konserwatywna / kurs − 1
```

Składniki, których nie da się policzyć, są pomijane (np. spółka bez zysku ma tylko P/S — wtedy
PT to samo P/S, a nie średnia z jednym składnikiem zerowym). Uśrednianie dwóch metod jest celowe:
P/E zawodzi przy spółkach inwestujących w wzrost, P/S przy niskich marżach.

### Historia PT (okno kroczące)

Dla każdej sesji z 5-letniej historii liczone są mnożniki z **kroczącego okna 3 lat** (mediana
i górna granica = **p95** okna, nie surowe maksimum), mnożone przez prognozy z tego dnia i
uśredniane po P/E i P/S. Powstaje krzywa „co model mówiłby w danym dniu”. Skoki linii odpowiadają
zmianie roku obrotowego w prognozie („next fiscal year”), nie błędom danych.

## 2. Ocena twarda (16 kryteriów, 0–100)

Każde kryterium dostaje 1–10 punktów według progów, a punkty między progami są **interpolowane
liniowo** (2,49 i 2,50 nie różnią się o cztery punkty). Suma ważona jest normalizowana do 0–100;
brak danych nie zeruje oceny — wagi renormują się do dostępnych kryteriów.

| # | kryterium | waga | progi (wartość → punkty) |
|---|---|---|---|
| 1 | wzrost przychodów 3Y | 12 | −∞→1, 0%→2, 3%→4, 7%→6, 12%→8, 20%→9,5 (max 10) |
| 2 | prognoza przychodów 1Y | 10 | 0%→2, 3%→4, 7%→6, 15%→8, 25%→9,5 |
| 3 | prognoza EPS 1Y | 10 | 0%→2, 5%→4, 10%→6, 20%→8, 35%→9,5 |
| 4 | wzrost EPS r/r | 8 | 0%→2, 5%→4, 15%→6, 30%→8, 60%→9,5 |
| 5 | marża operacyjna | 8 | 0%→2, 5%→4, 10%→6, 15%→7,5, 25%→9 |
| 6 | ROA | 5 | 0%→2, 2%→4, 5%→6, 8%→7,5, 15%→9 |
| 7 | ROIC | 8 | 0%→1, 5%→3, 10%→5, 15%→7, 25%→8,5 (dalej 10) |
| 8 | konwersja zysku w gotówkę (FCF / zysk netto) | 6 | 0%→1, 40%→3, 60%→5, 80%→7, 100%→8,5 (dalej 10) |
| 9 | dług netto / EBITDA | 12 | ≤0,1→10, 1→9, 2→7, 3→5, 4→3 (powyżej 1) |
| 10 | płynność bieżąca | 5 | 0,8→1, 1,0→4, 1,5→7, 2,5→10 |
| 11 | P/E vs mediana 3Y | 12 | 0,7→10, 0,9→8, 1,1→6, 1,3→4 (powyżej 2) |
| 12 | P/S vs mediana 3Y | 8 | jak wyżej |
| 13 | EV/EBITDA vs mediana 3Y | 8 | jak wyżej |
| 14 | potencjał do PT | 8 | 0%→1, 10%→4, 25%→6, 50%→8 (powyżej 10) |
| 15 | rewizje ceny docelowej 3M | 6 | −10%→2, 0%→5, +5%→7, +10%→8,5 (dalej 10) |
| 16 | rewizje prognoz EPS 3M | 8 | −10%→2, −2%→4, 0%→5, +3%→7, +8%→9 (dalej 10) |

Bloki wag: wzrost 40, jakość i bilans 44, wycena 28, rewizje 22 (suma 134 — ocena jest
normalizowana do 0–100, więc waga jest względna).

Trzy nowe kryteria (model 2.0) domykają to, czego brakowało w fundamentach:

- **ROIC** — czy biznes zarabia więcej niż koszt kapitału; ROA nie odróżnia spółki
  z dźwignią od takiej, która naprawdę dobrze alokuje kapitał. ROA zostaje z mniejszą wagą (5),
  bo jest prostsze i dostępne dla każdej spółki.
- **Konwersja FCF/zysk** — zysk, który nie zamienia się w gotówkę, jest zyskiem księgowym.
  Liczona z sum czterech kwartałów: `FCF TTM / zysk netto TTM`. Przy stracie wypada.
- **EV/EBITDA vs mediana 3Y** — trzecia noga wyceny obok P/E i P/S; EV uwzględnia dług,
  więc porównuje spółki o różnej strukturze kapitału. Seria FactSetu jest dzienna (jak P/E).

Dwa kryteria rewizji mierzą to samo z różnych stron i dlatego mają różne wagi:

- **PT (6 pkt)** — seria `price_target_upside` przeliczana na poziom PT (kurs × (1 + upside)).
  Cena docelowa zmienia się jednak także wtedy, gdy analityk tylko „docenia” rosnący kurs,
  więc sygnał jest częściowo pochodną notowań.
- **EPS (8 pkt)** — zmiana prognozy zysku na najbliższy rok w 3 miesiące (`eps_est_1y`).
  Analityk zmienia ją wtedy, gdy naprawdę zmienia pogląd na biznes, dlatego waży więcej,
  a progi są ciaśniejsze (±10% to już dużo). Przy stracie lub przejściu przez zero zmiana
  procentowa traci sens — kryterium wtedy wypada, a waga się renormuje.

```
ocena_twarda = Σ(punkty × waga) / Σ(waga) × 10
```

Rating liczony z **niezaokrąglonej** oceny (74,96 to B, nie A):

| rating | próg |
|---|---|
| A | ≥ 75 |
| B | ≥ 60 |
| C | ≥ 45 |
| D | < 45 |

Skala jest ograniczona do 100 — spółka przekraczająca wszystkie progi dostaje 100,0, co znaczy
„model nie widzi słabości”, a nie „spółka idealna”. Różnicowanie w górnej części wymagałoby
modelu percentylowego (świadomie nie wprowadzony, bo zmienia interpretację ratingu w czasie).

### Źródło ceny docelowej: model czy konsensus Scraba

Przełącznik w nagłówku (`PT model` / `PT Scrab`) decyduje, która cena docelowa jest pokazywana
w portfelu, na karcie spółki, w tabeli porównywalnych i na wykresie:

| tryb | PT | potencjał | wykres |
|---|---|---|---|
| model | PT konserwatywna (średnia P/E i P/S × prognozy) i optymistyczna | do PT konserwatywnej | dwie linie: PT kons. i PT opt. |
| Scrab | `kurs × (1 + price_target_upside)` — konsensus analityków | do konsensusu | jedna linia: poziom PT w czasie (schodki = rewizje) |

Wybór jest lokalny (localStorage `tfal.ptsrc`) i nie zmienia niczego w danych — serwer liczy
oba warianty równolegle i wysyła je w tej samej odpowiedzi (`pt_cons`, `pt_opt`, `pt_scrab`).
Dzięki temu przełączenie jest natychmiastowe i nie wymaga ponownego pobierania z API.

Uwaga: konsensus na wykresie jest odtwarzany z tej samej serii `price_target_upside`, więc między
rewizjami leży płasko (analitycy nie zmieniają poziomu co dzień) — to nie błąd, to właśnie rewizje.

## 3. Analiza miękka — wyłączona (model 2.0)

Ocena końcowa to **wyłącznie ocena twarda** z 16 kryteriów danych. Ręczna analiza jakościowa
(branża, pozycja, zarząd, koncentracja) została wyłączona z modelu decyzją użytkownika — wróci
jako osobne narzędzie, nie jako składnik scoringu.

- Jeśli plik `soft.json` jeszcze istnieje, nic go nie czyta — można go spokojnie usunąć.
- Stare przeglądy w `reviews.json` mogą zawierać pola `soft`, `teza`, `falsyfikator` — są ignorowane.
- Checklista przed decyzją (5 pytań) zostaje jako element procesu, nie punktów.

## 4. Kryteria TFL liczone z danych (`tfl.py`)

Dziewięć pytań z listy „10 pytań przed zakupem”, na które odpowiadają dane (reszta — branża i produkt —
jest oceniana poza modelem). Statusy: `ok` / `uwaga` / `zle`.

| # | kryterium | wartość | ok | uwaga |
|---|---|---|---|---|
| 3 | przychody 3Y i prognoza 1Y | zmiany % | oba > 0 | jedno > 0 |
| 4 | marża operacyjna i trend r/r | % i zmiana w pkt | marża > 0 i zmiana ≥ −1 pkt | ≥ −3 pkt |
| 5 | dług netto / EBITDA | x | < 2 | < 3,5 |
| 6 | wycena vs własna mediana | P/E i P/S rel. | gorszy z dwóch < 0,9 | < 1,15 |
| 7 | potencjał do PT | % | > 15% | > 0% |
| 8 | rewizje ceny docelowej 3 mies. | % + ile razy w górę/dół | > +3% | > −3% |
| 9 | trend kursu vs SMA200 | % | > 0 | od szczytu > −15% |
| 10 | kapitalizacja | mld/bln | ≥ 2 mld | ≥ 0,5 mld |
| 11 | rewizje prognoz EPS 3 mies. | % + ile razy w górę/dół | > +2% | > −2% |

**Rewizje PT** nie są liczone z samego `price_target_upside` (ten spada, gdy rośnie kurs).
Poziom PT odtwarzamy jako `kurs × (1 + upside/100)` i dopiero z tej serii liczymy zmianę za
3 miesiące oraz liczbę miesięcy z rewizją w górę/w dół.

Te sygnały **nie zmieniają oceny twardej** — inaczej wszystkie dotychczasowe ratingi przesunęłyby
się przy każdej zmianie modelu. Są kontekstem przy ocenie spółki i w checkliście.

## 5. Ryzyko portfela

Dane: dzienne stopy zwrotu z 3 lat, wspólne dni notowań, wagi liczone po wartości pozycji w PLN.

```
w        = value_pln / Σ value_pln
σ_roczna = std(dzienne) × √252
cov      = macierz kowariancji × 252
σ_port   = √(wᵀ · cov · w)
VaR 5% 1D (param.) = 1,645 × std(dzienne portfela)
VaR 5% 10D        = VaR 1D × √10
VaR historyczny   = 5. percentyl dziennych stóp portfela
wkład w ryzyko    = w_i × (cov·w)_i / σ_port
budżet ryzyka     = Σ w_i × σ_i          # proxy limitu 15%
```

Interpretacja limitu 15%: 15% portfela jako **budżet ryzyka** (suma ważonych zmienności), nie suma
wag. Przy 6 spółkach o zmienności ~50% budżet jest wielokrotnie przekroczony — to sygnał, nie błąd.
Koncentracja sektorowa to suma wag w sektorze; HHI = Σ w² (w arkuszu `Analiza.xlsx`).

## 6. Wyniki portfela (TWR i MWR)

Portfel w każdym dniu jest odtwarzany z dziennika: kupno dodaje akcje i odejmuje gotówkę, sprzedaż
odwrotnie, wpłata zasila gotówkę, dywidenda ją zwiększa. Kursy z historii Scraba, waluty po NBP
z danego dnia.

```
indeks_TWR_0 = 100
r_d         = value_d / (value_{d-1} + flow_d) − 1     # flow > 0 dla wpłat
indeks_TWR_d = indeks_TWR_{d-1} × (1 + r_d)
```

- **TWR** — wynik strategii; wpłata nie zmienia stopy, bo neutralizuje ją mianownik.
- **MWR** — wewnętrzna stopa zwrotu (XIRR) przepływów: wpłaty ujemne, wartość końcowa dodatnia.
  To wynik Twoich pieniędzy — zależy od momentu i wielkości wpłat.
- CAGR = `(1+TWR)^(365,25/dni) − 1`; zmienność = std dziennych × √252; Sharpe = CAGR / zmienność
  (bez stopy wolnej od ryzyka); max drawdown = najgłębsze obsunięcie indeksu.
- **Atrybucja**: zrealizowane + niezrealizowane + dywidendy per spółka; różnica między P&L po
  kursie z dnia transakcji a przeliczeniem dzisiejszym kursem to osobny wiersz „Efekt walutowy”.
- **Benchmark**: S&P 500, Nasdaq 100 albo MSCI World, przeliczone na PLN; beta, alfa roczna,
  tracking error, information ratio, capture up/down.

Gdy w dzienniku brakuje wpłat, gotówka schodzi poniżej zera — aplikacja dolicza brakującą kwotę
jako wpłatę (ostrzeżenie w odpowiedzi) i pokazuje to w `implicit_deposits`. Poprawna droga to
import z XTB.

## 7. Rebalans

Metody wag docelowych:

| metoda | waga surowa |
|---|---|
| `inv_vol` | 1 / σ (odwrotnie do zmienności — Markowitz/Trela) |
| `equal` | 1 dla każdej spółki |
| `score` | ocena 0–100 |
| `score_vol` | ocena / σ |
| `manual` | kolumna F w `Rebalans.xlsx` |

```
eff_max = max(max_pos, 1/n)          # limit pozycji nie może być mniejszy niż równa waga
w_tgt   = cap_weights(raw, eff_max, 0, 1)       # water-filling
w_tgt   = cap_sector(w_tgt, sektory, max_sec)   # 25% domyślnie
```

**Water-filling**: nadwyżka ponad limit pozycji rozdawana jest proporcjonalnie do pozostałych
spółek, aż wszystkie mieszczą się w limicie. Gdy limit sektora ścina grupę, nadwyżka **nie** jest
rozdawana na siłę — zostaje gotówka, bo inaczej limit przestaje działać.

```
delta_i = w_tgt_i × total − value_i
qty     = |delta| / (kurs × fx)      # floor lub 0,01 przy akcjach ułamkowych
```

Pozycje z różnicą mniejszą niż jedna akcja dostają akcję „—” (nie „KUP 0”). Wykonanie zleceń
dopisuje transakcje do dziennika z opłatą 0 i aktualizuje `state.json` pod jedną blokadą.

## 8. Backtest (arkusz `Analiza.xlsx`)

Co kwartał: ranking po ocenie, portfel z górnej tercji równo ważony vs równa waga całego
uniwersum. Liczone: CAGR, trafność (udział kwartałów, w których scoring pobił benchmark), IC
(korelacja rangi oceny z przyszłą stopą zwrotu), maksymalne obsunięcie, tabela kwartałów.

**Ograniczenia wpisane w arkusz:** używana jest dzisiejsza lista spółek (bias przeżywalności),
okresów jest mało (kilka–kilkanaście kwartałów), a ocena historyczna liczona z danych
dostępnych w danym momencie. To test sanity, nie dowód statystyczny.

## 9. Kalendarz

Zdarzenia z Yahoo: daty wyników kwartalnych, ex-dividend i wypłaty dywidend. Każde dostaje wagę
pozycji w portfelu i liczbę dni do zdarzenia; najbliższe 14 dni jest podświetlone. Pokazywane są
wyłącznie zdarzenia przyszłe.

## 10. Przegląd (co widziałem ostatnio)

Zapis przeglądu odpowiada na inne pytanie niż historia ceny docelowej: tamta mówi, co model
wyliczyłby w dawnych sesjach, a przegląd — **co dokładnie widziałem i zakładałem, podejmując
decyzję**. Dlatego snapshot zawiera nie tylko liczby, ale i tezę oraz falsyfikator.

Co trafia do `reviews.json` (20 ostatnich zapisów na spółkę):

| pole | po co |
|---|---|
| `price`, `eps`, `rev_ps` | kurs i prognozy w dniu przeglądu |
| `pt_cons`, `pt_opt`, `comp_pe`, `comp_ps` | cena docelowa i **osobno oba składniki** |
| `med_pe`, `med_ps`, `max_pe`, `max_ps` | mnożniki, żeby odróżnić zmianę wyceny od zmiany prognozy |
| `score_hard`, `score`, `rating` | ocena w dniu przeglądu |
| `methods` | które metody weszły do PT (`PE`, `PS`) |
| `model` | wersja modelu (`MODEL_VER`) |
| `checklist` | 5 pytań przed decyzją — które były zaznaczone w chwili przeglądu |

Checklista przed decyzją jest zapisywana na bieżąco w `checklist.json` (per spółka), a przy
przeglądzie jej stan trafia do migawki — dzięki temu po kwartale widzisz nie tylko liczby,
ale i to, co wtedy uznałeś za sprawdzone. Bez tego checkboxy były dekoracją, która znikała
po odświeżeniu strony.

Porównanie (`review_diff`) liczy zmianę procentową dla każdej pozycji i osobno:

- **utratę metody** — gdy np. spółka przestała mieć prognozę EPS, PT liczy się już tylko z P/S;
  karta pokazuje „z wyceny wypadła metoda P/E”. To nie jest drobiazg: cena docelowa zmienia wtedy
  charakter, a nie tylko wartość,
- **zmianę wersji modelu** — jeśli `MODEL_VER` się różni, różnica może wynikać z metodologii,
  nie z danych; interfejs mówi to wprost.

Cel: po 90 dniach (przypomnienie „przegląd zaległy”) masz obok siebie **dawne założenie i dzisiejsze
dane** — widać, czy zmieniła się teza, czy tylko kurs.

## 11. Wskaźnik PS2y/Rev2y (autorski)

Panel na wykresie spółki: **PS Ratio (2Y forward) ÷ średni roczny wzrost przychodów**. Odpowiednik
PEG, ale liczony na sprzedaży — „ile płacisz za punkt wzrostu przychodów”.

```
PS2y    = forward_ps_ratio_2y                        # FactSet, seria dzienna
wzrost% = PS1y / PS2y − 1 = (kurs/rev1) / (kurs/rev2) − 1 = rev2/rev1 − 1
PS2y/Rev2y = PS2y / wzrost%
```

Skąd wzrost bez osobnej prognozy: oba mnożniki mają w liczniku ten sam kurs, więc iloraz PS1y/PS2y
to wprost stosunek prognoz sprzedaży na akcję (rok +2 do roku +1). Przy założeniu, że dynamika
roku +1 jest zbliżona do roku +2, to jest **średni roczny wzrost w najbliższych dwóch latach**.
Założenie jest jawnie opisane, bo to jedyne miejsce, gdzie model „dopowiada” dane.

Interpretacja:

| wartość | znaczenie |
|---|---|
| < 0,10 | bardzo tanio względem wzrostu (np. CDR.WA ~0,04) |
| 0,10–0,20 | tanio (NVDA ~0,12, AVGO ~0,10) |
| 0,20–0,50 | neutralnie (ALE.WA ~0,30) |
| > 0,50 | drogo — wzrost nie uzasadnia mnożnika (MU ~1,14) |

Zasady, żeby wskaźnik nie kłamał:

- **Wzrost poniżej 1% jest pomijany.** Dzielenie przez ~0 daje wartości rzędu setek — takie punkty
  wypadają z serii (dziura na wykresie jest informacją, nie błędem).
- **Wartości ujemne wzrostu** (spadek przychodów) też są pomijane — wskaźnik nie ma wtedy sensu.
- Na panelu rysowana jest **mediana** serii jako punkt odniesienia: porównujesz dziś z własną historią,
  a nie z jedną uniwersalną liczbą.
- Seria jest przycinana do 600 punktów (ok. 2,5 roku sesji) na potrzeby wykresu.

Dane: `forward_ps_ratio_2y` (FactSet, ok. 1300 punktów dziennych) i `forward_ps_ratio`.
Endpoint: `GET /api/series/<ticker>` → `ps2y_rev2y` (seria) i `ps2y_info` (ostatnie wartości:
PS2y, wzrost, wynik). Panel włącza się przyciskiem `PS2y/Rev2y` w pasku wykresu.

## 12. Decyzje metodologiczne (skrót)

| decyzja | uzasadnienie |
|---|---|
| mediana dzienna mnożników, nie średnia | odporność na pojedyncze skoki notowań |
| max = mediana rocznych maksimów (3 lata) | pojedynczy szczyt mnożnika psuje skalę na lata |
| interpolacja progów | brak progów-skoków, ocena różnicuje w całym przedziale |
| wagi renormowane przy brakach | brak danych nie może karać ani nagradzać |
| TFL poza oceną | zmiana zestawu kryteriów przesunęłaby wszystkie ratingi historyczne |
| skala logarytmiczna **tylko kursu** | równe procenty w każdym rejonie osi; panele mnożników i PT upside są liniowe, bo czytasz z nich poziom, nie procent |
| PT panel liniowy | potencjał bywa ujemny, log nie istnieje dla ≤ 0 |
| budżet ryzyka Σw·σ zamiast sumy wag | 15% na pozycję to limit ryzyka, nie udziału |
