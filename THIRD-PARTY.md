# Third-party components

## Fonts (bundled in `web/fonts/`)

| Font | License | Use |
|---|---|---|
| Spectral (Production Type) | SIL Open Font License 1.1 | headings, verdicts |
| Inter (Rasmus Andersson) | SIL Open Font License 1.1 | interface text |
| IBM Plex Mono (IBM) | SIL Open Font License 1.1 | numbers, labels |

The fonts are redistributed as WOFF2 subsets (latin, latin-ext) under the OFL, which permits
bundling with software. The licenses are available at https://scripts.sil.org/OFL.

## Libraries

| Component | License | Use |
|---|---|---|
| [Lightweight Charts](https://github.com/tradingview/lightweight-charts) 4.2.3 (TradingView) | Apache 2.0 | price and multiple charts, vendored in `web/vendor/` |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause | local HTTP API |
| [pywebview](https://pywebview.flowrl.com/) | BSD-3-Clause | frameless WebView2 window |
| [matplotlib](https://matplotlib.org/) | PSF-based (Matplotlib license) | company card PDF |
| [yfinance](https://github.com/ranaroussi/yfinance) | Apache 2.0 | quotes, corporate events |
| [openpyxl](https://openpyxl.readthedocs.io/) | MIT | XTB import, Excel export |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | Apache 2.0 / BSD-3-Clause | PDF rendering and export |
| [reportlab](https://www.reportlab.com/) | BSD-3-Clause | documentation PDF |

## Data

Market data shown in screenshots and the demo video comes from FactSet (through the Scrab API),
Yahoo Finance and the National Bank of Poland (NBP) FX rates. **No market data, credentials or
portfolio records are redistributed in this repository.** FactSet and Scrab data are subject to
their own terms of service.
