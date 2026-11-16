# -*- coding: utf-8 -*-
"""Eksport arkusza do PDF przez Excel (COM).

Uzycie:
    python pdf_export.py                       # Cena-docelowa-auto.xlsx
    python pdf_export.py Portfel.xlsx          # inny plik
    python pdf_export.py plik.xlsx wynik.pdf   # wlasna nazwa
"""
import os
import sys


def export(xlsx, pdf=None, quiet=False):
    xlsx = os.path.abspath(xlsx)
    if not os.path.exists(xlsx):
        raise SystemExit(f"Nie ma pliku {xlsx}")
    pdf = os.path.abspath(pdf or os.path.splitext(xlsx)[0] + ".pdf")

    try:
        import win32com.client as win32
    except ImportError:
        raise SystemExit("Brak pywin32. Uruchom: venv\\Scripts\\python.exe -m pip install pywin32")

    excel = None
    wb = None
    try:
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.Interactive = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(xlsx, ReadOnly=True, UpdateLinks=0)
        excel.CalculateFull()
        # 0 = PDF, jakosc standardowa
        wb.ExportAsFixedFormat(0, pdf)
    except Exception as e:
        raise RuntimeError(f"Excel nie wyeksportowal PDF: {e}") from e
    finally:
        if wb is not None:
            try:
                wb.Close(False)
            except Exception:
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        wb = None
        excel = None
        import gc
        gc.collect()

    if not quiet:
        pages = ""
        try:
            import pypdfium2 as pdfium
            with pdfium.PdfDocument(pdf) as doc:
                pages = f", {len(doc)} stron"
        except Exception:
            pass
        print(f"  PDF: {pdf}{pages}")
    return pdf


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = args[0] if args else "Cena-docelowa-auto.xlsx"
    dst = args[1] if len(args) > 1 else None
    print("Eksport do PDF ...")
    export(src, dst)
