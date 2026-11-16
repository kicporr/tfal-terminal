# -*- coding: utf-8 -*-
"""Buduje PDF z dokumentacji markdown (README + docs/*.md).

Jak to działa: markdown → jeden HTML → Edge (headless) drukuje do PDF → strony
dostają numerację przez nakładkę reportlab. Bez LibreOffice i bez pandoc.

Użycie:
    venv\\Scripts\\python.exe docs\\build_pdf.py
    venv\\Scripts\\python.exe docs\\build_pdf.py --open
"""
import io
import os
import re
import subprocess
import sys
import tempfile
from datetime import date

import markdown
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "Dokumentacja-TFAL.pdf")
EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"

# kolejność i tytuły rozdziałów
DOCS = [
    ("README.md", "Wprowadzenie", "wprowadzenie"),
    ("docs/ARCHITEKTURA.md", "Architektura", "architektura"),
    ("docs/API.md", "API", "api"),
    ("docs/MODEL.md", "Model", "model"),
    ("docs/DANE.md", "Dane", "dane"),
    ("docs/OPERACJE.md", "Operacje", "operacje"),
]

MD = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"])


def convert(rel_path, prefix):
    """Markdown → HTML z unikalnymi id (prefiks), bez linków do plików .md."""
    with open(os.path.join(ROOT, rel_path), encoding="utf-8") as f:
        text = f.read()
    MD.reset()
    MD.md = text
    html = MD.convert(text)
    # linki do innych plików .md → odnośniki do rozdziałów PDF
    html = re.sub(r'href="(?:\.\./)?docs/([A-Z]+)\.md"', lambda m: f'href="#{m.group(1).lower()}"', html)
    html = re.sub(r'href="(?:\.\./)?README\.md"', 'href="#wprowadzenie"', html)
    # unikalne id nagłówków
    html = re.sub(r'id="([^"]+)"', lambda m: f'id="{prefix}-{m.group(1)}"', html)
    html = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{prefix}-{m.group(1)}"', html)
    return html


def headings(html, prefix):
    """(poziom, tekst, id) do spisu treści — tylko h2/h3."""
    out = []
    for m in re.finditer(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', html, re.S):
        hid, txt = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        out.append((2, txt.strip(), hid))
    return out


CSS = """
@font-face{font-family:'Spectral';src:url('../web/fonts/Spectral-500-latin.woff2') format('woff2');font-weight:500}
@font-face{font-family:'Spectral';src:url('../web/fonts/Spectral-400i-latin.woff2') format('woff2');font-style:italic;font-weight:400}
@font-face{font-family:'Spectral';src:url('../web/fonts/Spectral-600-latin.woff2') format('woff2');font-weight:600}
@font-face{font-family:'Inter';src:url('../web/fonts/Inter-400-latin.woff2') format('woff2');font-weight:400}
@font-face{font-family:'Inter';src:url('../web/fonts/Inter-500-latin.woff2') format('woff2');font-weight:500}
@font-face{font-family:'Inter';src:url('../web/fonts/Inter-600-latin.woff2') format('woff2');font-weight:600}
@font-face{font-family:'Plex Mono';src:url('../web/fonts/IBMPlexMono-400-latin.woff2') format('woff2');font-weight:400}

@page{size:A4;margin:19mm 16mm 20mm}
*{box-sizing:border-box}
body{margin:0;font:10.1pt/1.58 'Inter','Segoe UI',sans-serif;color:#14140F;
     -webkit-print-color-adjust:exact;print-color-adjust:exact}
h1,h2,h3,h4{font-family:'Spectral',Georgia,serif;font-weight:600;color:#14140F}
h1{font-size:23pt;line-height:1.15;margin:0 0 6mm;page-break-before:always;break-before:page;
   border-bottom:1.5pt solid #14140F;padding-bottom:3mm}
h2{font-size:13.5pt;margin:6mm 0 2.2mm;break-after:avoid}
h3{font-size:11pt;margin:5mm 0 2mm;break-after:avoid}
p{margin:0 0 2.6mm;text-align:left}
a{color:#1E4A3A;text-decoration:none;border-bottom:.4pt solid #1E4A3A}
strong{font-weight:600}
code,kbd{font-family:'Plex Mono',Consolas,monospace;font-size:8.6pt;background:#F4F2EA;padding:.3mm 1.2mm}
pre{font-family:'Plex Mono',Consolas,monospace;font-size:8.2pt;line-height:1.45;background:#F7F5EE;
    border-left:2pt solid #1E4A3A;padding:2.5mm 3mm;margin:0 0 3mm;white-space:pre-wrap;
    break-inside:avoid}
pre code{background:none;padding:0;font-size:inherit}
table{width:100%;border-collapse:collapse;font-size:8.6pt;margin:0 0 3.5mm}
th,td{border-bottom:.4pt solid #D9D5C7;padding:1.4mm 2mm;text-align:left;vertical-align:top}
th{background:#F4F2EA;font-weight:600;font-size:8.2pt;letter-spacing:.02em}
tr{break-inside:avoid}
ul,ol{margin:0 0 2.8mm;padding-left:5mm}
li{margin:0 0 1.2mm;break-inside:avoid}
hr{border:0;border-top:.4pt solid #D9D5C7;margin:4mm 0}
blockquote{margin:0 0 3mm;padding-left:3mm;border-left:2pt solid #D9D5C7;color:#4A4A42}
blockquote p{margin:0}

/* okładka */
.cover{height:245mm;display:flex;flex-direction:column;justify-content:space-between;page-break-after:always}
.cover .top{font:500 8.5pt 'Inter';letter-spacing:.32em;text-transform:uppercase;color:#6A6A63}
.cover h1{page-break-before:avoid;break-before:avoid;border:0;font-size:40pt;line-height:1.05;margin:0}
.cover .sub{font-family:'Spectral',Georgia,serif;font-size:14pt;color:#4A4A42;margin-top:4mm}
.cover .meta{font:400 8.5pt 'Plex Mono';color:#6A6A63;line-height:1.8}
.cover .rule{height:1.5pt;background:#14140F;margin:8mm 0}
.spis ol{list-style:none;padding:0;margin:0;font-size:8.6pt;line-height:1.3;column-count:2;column-gap:10mm}
.spis li{margin:0 0 .35mm}
.spis .lvl2{padding-left:0}
.spis .doc{font-family:'Spectral',Georgia,serif;font-size:10.4pt;margin-top:2.2mm}
.spis h1{margin-bottom:3mm}
.spis a{border:0;color:#14140F}
.spis .doc a{border:0;color:#14140F}
"""


def build_html():
    parts, toc = [], []
    for rel, title, prefix in DOCS:
        html = convert(rel, prefix)
        parts.append(f'<section class="doc" id="{prefix}">{html}</section>')
        toc.append((title, prefix, headings(html, prefix)))

    spis = ['<section class="spis"><h1 style="page-break-before:always">Spis treści</h1><ol>']
    for title, prefix, heads in toc:
        spis.append(f'<li class="doc"><a href="#{prefix}">{title}</a></li>')
        for level, txt, hid in heads:
            cls = "lvl3" if level == 3 else "lvl2"
            spis.append(f'<li class="{cls}"><a href="#{hid}">{txt}</a></li>')
    spis.append("</ol></section>")

    cover = f"""<div class="cover">
      <div class="top">TFAL Terminal · dokumentacja techniczna</div>
      <div>
        <h1>TFAL Terminal</h1>
        <div class="rule"></div>
        <div class="sub">Wycena, ocena jakości, ryzyko i wyniki portfela —<br>jeden lokalny terminal.</div>
      </div>
      <div class="meta">
        stan na {date.today():%Y-%m-%d}<br>
        README · architektura · API · model · dane · operacje<br>
        materiał własny, nie rekomendacja inwestycyjna
      </div>
    </div>"""

    return f"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<title>TFAL Terminal — dokumentacja</title><style>{CSS}</style></head>
<body>{cover}{''.join(spis)}{''.join(parts)}</body></html>"""


def to_pdf(html_path, pdf_path):
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--no-pdf-header-footer", "--virtual-time-budget=15000",
           f"--print-to-pdf={pdf_path}", "file:///" + html_path.replace("\\", "/")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not os.path.exists(pdf_path):
        raise RuntimeError((r.stderr or r.stdout or "Edge nie utworzył PDF")[-400:])


def stamp_numbers(src, dst):
    """Stopka: tytuł po lewej, numer strony po prawej (bez okładki)."""
    writer = PdfWriter(clone_from=src)
    total = len(writer.pages)
    for i, page in enumerate(writer.pages, start=1):
        if i > 1:
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=A4)
            c.setFillColorRGB(0.42, 0.42, 0.39)
            c.setFont("Helvetica", 7.2)
            c.drawString(45, 24, "TFAL Terminal — dokumentacja")
            c.drawRightString(A4[0] - 45, 24, f"{i} / {total}")
            c.save()
            buf.seek(0)
            page.merge_page(PdfReader(buf).pages[0])
    with open(dst, "wb") as f:
        writer.write(f)
    return total


def main():
    html = build_html()
    tmp_html = os.path.join(tempfile.gettempdir(), "tfal_docs.html")
    tmp_pdf = os.path.join(tempfile.gettempdir(), "tfal_docs_raw.pdf")
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)
    to_pdf(tmp_html, tmp_pdf)
    pages = stamp_numbers(tmp_pdf, OUT)
    print(f"  {os.path.basename(OUT)}: {pages} stron, {os.path.getsize(OUT)/1024:.0f} kB")
    print(f"  -> {OUT}")
    if "--open" in sys.argv:
        os.startfile(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
