@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Wersja z widoczną konsolą — do diagnozy błędów startu.
"%~dp0venv\Scripts\python.exe" "%~dp0terminal.py"
pause
