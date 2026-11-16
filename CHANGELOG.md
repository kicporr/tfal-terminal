# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

## [1.0.2] — 2026-11-16

### Fixed
- Company card PDF duplicated the decision checklist on pages 2 and 3; it now appears once, and page 3
  uses the space for "what decided the score" (strongest and weakest criteria) instead.
- Logarithmic price chart in the PDF had only two gridlines; it now uses round ticks fitted to the data.
- Chart price lines for cost and target price no longer print labels on the axis, where they collided
  with the axis ticks.
- Rebalance view states the currency explicitly: separate "amount (currency)" and "amount (PLN)"
  columns, PLN totals in the KPI cards and the NBP rate used for conversion.

### Changed
- Dropdown lists follow the colour scheme of the app (light and dark), not the operating system.

## [1.0.1] — 2026-11-16

### Added
- `sesja.py` and `Sesja-Scrab.bat`: paste the `Cookie` header copied from the browser's DevTools,
  and the script writes `scrab_session.json` and validates it with a test request.
- "How to renew the Scrab session" instructions in `docs/OPERACJE.md`, in the README quick start
  and in the reviewer guide (PDF).

### Fixed
- The journal view no longer crashes on an empty journal (`UnboundLocalError: sec`); it shows the
  "Pusty dziennik" card with the option to seed opening positions from the portfolio.

### Changed
- Documentation PDF rebuilt with the new section (32 pages).

## [1.0.0] — 2026-11-16

First public release.

### Added
- Valuation from 3-year P/E, P/S and EV/EBITDA multiples applied to 12-month forward estimates,
  with conservative (median) and optimistic (median of yearly highs) target prices, sensitivity
  grids and an inverted "what the price assumes" calculator.
- 16-criterion quality score (growth, balance sheet, capital returns, cash conversion, relative
  valuation, estimate revisions) with interpolated thresholds and weight renormalisation on
  missing data.
- Portfolio accounting: FIFO cost basis in the instrument currency, P&L converted at the
  transaction-date FX rate, TWR and MWR (XIRR), drawdown, monthly returns, attribution.
- Journal with XTB import, duplicate detection and per-ticker statistics (hit rate, holding
  period, price 60 days after a sale).
- Risk view: annualised volatility, parametric and historical VaR, correlation matrix, risk
  contribution, HHI concentration, simple stress scenarios.
- Benchmark comparison (S&P 500, Nasdaq 100, MSCI World, WIG20TR) with beta, alpha, tracking
  error and up/down capture.
- Rebalancing: target weights (equal, inverse volatility, score), position and sector caps,
  order list.
- Frameless WebView2 desktop shell, single-instance guard, light and dark themes.
- Three-page company card PDF, portfolio and rebalance exports, 31-page documentation PDF
  generated from Markdown.
- 7-page reviewer guide (`docs/PRZEWODNIK-po-projekcie.pdf`) and a 90-second demo video.

### Fixed
- Missing Scrab session now returns a readable message instead of a server traceback.
- Chart panels are linear; only the price chart uses a logarithmic scale.
- Optimistic target price uses the same 3-year window as the median (no one-off multiple spikes).
- Attribution includes cash and FX effects so the components add up to the portfolio result.
