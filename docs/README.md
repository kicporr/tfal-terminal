# Documentation

| Document | Contents |
|---|---|
| [MODEL.md](MODEL.md) | every formula: target price, the 16-criterion score, PS 2Y ratio, risk, performance |
| [ARCHITEKTURA.md](ARCHITEKTURA.md) | modules, data flow, files on disk, request lifecycle |
| [API.md](API.md) | all 28 endpoints with payload examples |
| [DANE.md](DANE.md) | data sources, metric keys, caching, FX and corporate actions |
| [OPERACJE.md](OPERACJE.md) | running, troubleshooting, limitations, backup |
| [PRZEWODNIK-po-projekcie.pdf](PRZEWODNIK-po-projekcie.pdf) | 7-page guide for reviewers (Polish): what it does, screen by screen |
| [Dokumentacja-TFAL.pdf](Dokumentacja-TFAL.pdf) | the same documents as a printable 31-page PDF |

The PDFs are generated from the Markdown sources by `build_pdf.py` (Markdown → HTML → Edge
headless print-to-PDF).
