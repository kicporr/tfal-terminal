# -*- coding: utf-8 -*-
"""TFAL Terminal — własne okno bez ramki (WebView2 przez pywebview).

W odróżnieniu od `terminal.py` (Edge w trybie --app, z systemową ramką okna)
tutaj rysujemy własny pasek tytułu w HTML i wyłączamy ramkę: aplikacja wygląda
jak program desktopowy, nie jak strona w przeglądarce.
"""
import os
import re
import socket
import sys
import threading
import time

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))

PORT_START = 8765

T0 = time.time()
LOG_PATH = os.path.join(HERE, "TFAL-log.txt")


def _log(msg):
    """Ślad startu do TFAL-log.txt — .bat uruchamia pythonw, więc nie ma konsoli."""
    line = f"[start {time.time() - T0:5.2f}s] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 200_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass


def free_port(start=PORT_START):
    for p in range(start, start + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def wait_up(port, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def serve(port):
    """Flask w wątku — serwer żyje tyle, ile okno."""
    from werkzeug.serving import make_server
    import app as backend
    srv = make_server("127.0.0.1", port, backend.app, threaded=True)
    srv.serve_forever()


class Api:
    """Mostek JS → Python dla przycisków okna i otwierania raportów."""

    def __init__(self):
        # UWAGA: atrybuty mostu JS widzi pywebview i próbuje je serializować.
        # Trzymamy okno pod _win — podkreślnik wyłącza je z tej inspekcji
        # (w przeciwnym razie pywebview chodzi po window.native i zawiesza UI).
        self._win = None
        self._maxed = False

    def close(self):
        try:
            self._win.destroy()
        except Exception:
            pass
        return True

    def minimize(self):
        try:
            self._win.minimize()
        except Exception:
            pass
        return True

    def toggle_max(self):
        try:
            if self._maxed:
                self._win.restore()
            else:
                self._win.maximize()
            self._maxed = not self._maxed
            return self._maxed
        except Exception:
            return False

    def open_file(self, name):
        """Otwiera wygenerowany raport systemowo (PDF w czytniku, xlsx w Excelu)."""
        if not re.match(r"^[A-Za-z0-9_.\-]+\.(pdf|xlsx)$", str(name or "")):
            return {"ok": False, "error": "niedozwolona nazwa"}
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            return {"ok": False, "error": "brak pliku"}
        try:
            os.startfile(path)          # noqa: S606 — lokalny plik, zaufane źródło
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def already_running():
    """Jeden proces = jeden portfolio.json. Drugie uruchomienie tylko pokazuje okno."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.SetLastError(0)
    k32.CreateMutexW(None, False, "Local\\TFAL_Terminal_single")
    return k32.GetLastError() == 183          # ERROR_ALREADY_EXISTS


SPLASH = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#FAFAF6;color:#14140F;
  font-family:"Segoe UI",system-ui,sans-serif;display:flex;align-items:center;justify-content:center}
.b{display:flex;flex-direction:column;align-items:center;gap:16px}
.s{width:26px;height:26px;border:2px solid #E5E2D7;border-top-color:#1E4A3A;border-radius:50%;
  animation:r .8s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
.t{font-size:10px;letter-spacing:.3em;text-transform:uppercase;color:#6A6A63}
</style></head><body><div class="b"><div class="s"></div><div class="t">TFAL Terminal</div></div></body></html>"""


def show_existing():
    """Wyjście z drugiego uruchomienia: podnieś okno, które już działa."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w > 400 and h > 300 and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append((w * h, hwnd))
        return True

    user32.EnumWindows(cb, 0)
    import subprocess
    me = os.getpid()
    for _, hwnd in sorted(found, reverse=True):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me:
            continue
        # tylko nasze własne okno: tytuł zgodny z aplikacją
        n = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(n)
        user32.GetWindowTextW(hwnd, buf, n)
        if buf.value == "TFAL Terminal":
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    return False


def main():
    if already_running():
        if not show_existing():
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, "TFAL Terminal już działa — okno jest na pasku zadań.",
                "TFAL Terminal", 0x40)
        return 0
    import logging
    # pywebview potrafi zasypać log ostrzeżeniami z .NET/COM przy każdej inspekcji okna
    logging.getLogger("pywebview").setLevel(logging.CRITICAL)
    import webview

    port = free_port()
    _log(f"port {port}, uruchamiam serwer")
    threading.Thread(target=serve, args=(port,), daemon=True).start()

    # przeciąganie okna tylko za pasek tytułu — wykresy i tabele mają zostać klikalne
    webview.settings["DRAG_REGION_SELECTOR"] = "#appbar"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

    api = Api()
    win = webview.create_window(
        "TFAL Terminal",
        html=SPLASH,
        frameless=True,
        easy_drag=True,
        width=1480,
        height=950,
        min_size=(1120, 720),
        background_color="#FAFAF6",
        js_api=api,
        text_select=True,
    )
    api._win = win
    win.events.shown += lambda: _log("okno pokazane")

    def boot():
        """Ekran startowy widoczny od razu; aplikacja podmienia go, gdy serwer wstanie."""
        if not wait_up(port, timeout=40):
            _log("serwer nie wstał")
            return
        _log("serwer gotowy — ładuję aplikację")
        try:
            win.load_url(f"http://127.0.0.1:{port}/?shell=app")
        except Exception as e:
            _log(f"load_url: {e}")

    threading.Thread(target=boot, daemon=True).start()
    _log("start WebView2")
    webview.start()
    _log("okno zamknięte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
