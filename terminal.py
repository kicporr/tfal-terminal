# -*- coding: utf-8 -*-
"""TFAL Terminal — okno desktopowe nad lokalnym serwerem Flask.

Okno to Edge/Chrome w trybie aplikacji (bez pasków adresu) — startuje w ~1 s,
nie wymaga Electrona ani dodatkowych runtime'ów. Serwer słucha tylko 127.0.0.1
na wolnym porcie, więc nie koliduje z `python app.py`.

Uruchomienie:  python terminal.py     (albo Terminal.bat)
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
if FROZEN:
    HERE = os.path.dirname(sys.executable)      # dane obok .exe, nie w paczce
    # okno bez konsoli: wszystkie błędy lądują w pliku, inaczej giną bez śladu
    try:
        _log = open(os.path.join(HERE, "TFAL-log.txt"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = _log
    except OSError:
        pass
sys.path.insert(0, HERE)
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "TFAL", "window")

CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _msgbox(text, title="TFAL Terminal"):
    """Okno komunikatu — w trybie exe nie ma konsoli, więc inaczej nic nie widać."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        print(text)


def run_script(argv):
    """Tryb --run: ten sam plik .exe wykonuje skrypt (eksport PDF) w swoim interpreterze."""
    import runpy
    if not argv:
        return 2
    script = argv[0]
    if not os.path.isabs(script):
        script = os.path.join(HERE, script)
    if not os.path.exists(script):
        print(f"Brak skryptu: {script}")
        return 2
    sys.argv = [script] + argv[1:]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")
    return 0


def free_port(start=8765):
    for p in range(start, start + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def serve(port):
    import traceback
    try:
        import app as backend
        backend.app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
    except Exception:
        traceback.print_exc()


def wait_up(port, timeout=25.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def main():
    # Najpierw własne okno bez ramki (WebView2). Gdy pywebview/WebView2 nie działa,
    # zostaje dotychczasowy tryb Edge --app (z ramką systemową).
    if not os.environ.get("TFAL_FORCE_BROWSER"):
        try:
            import webview  # noqa: F401  — sprawdzamy, czy środowisko to udźwignie
            import desktop
            return desktop.main()
        except Exception as e:      # noqa: BLE001
            print(f"Okno natywne niedostępne ({e.__class__.__name__}: {e}) — otwieram w Edge")
    port = free_port()
    threading.Thread(target=serve, args=(port,), daemon=True).start()
    if not wait_up(port):
        print("Serwer nie wstał.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    exe = next((p for p in CANDIDATES if os.path.exists(p)), None)
    proc = None
    if exe:
        os.makedirs(PROFILE, exist_ok=True)
        proc = subprocess.Popen([
            exe, f"--app={url}", "--window-size=1480,950",
            f"--user-data-dir={PROFILE}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
        ])
    else:
        webbrowser.open(url)

    print(f"TFAL Terminal: {url}")
    try:
        # serwer żyje tyle, ile okno — zamknięcie okna kończy proces (koniec wycieku portów).
        # Wyjątek: Edge oddaje okno już działającej instancji i sam się zamyka w sekundę —
        # wtedy nie ubijamy serwera, tylko mówimy, gdzie szukać okna.
        quick = None
        if proc is not None:
            try:
                quick = proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                quick = False
        if proc is not None and quick not in (False, None):
            _msgbox(f"TFAL Terminal już działa — okno jest na pasku zadań.\n\n"
                    f"Jeśli go nie widzisz, otwórz w przeglądarce:\n{url}")
            return 0
        if proc is not None:
            proc.wait()
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run":
        sys.exit(run_script(sys.argv[2:]))
    sys.exit(main())
