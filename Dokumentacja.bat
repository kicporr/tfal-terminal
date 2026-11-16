@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Buduje Dokumentacja-TFAL.pdf z README.md i docs/*.md i otwiera go.
"%~dp0venv\Scripts\python.exe" "%~dp0docs\build_pdf.py" --open
if errorlevel 1 pause
