# -*- coding: utf-8 -*-
"""Zapisuje sesję Scraba z nagłówka Cookie skopiowanego z przeglądarki.

Użycie:
    python sesja.py "sessionid=...; csrftoken=...; ..."
albo bez argumentu — wtedy wklejasz nagłówek i naciskasz Enter.
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = os.path.join(HERE, "scrab_session.json")


def parse(header):
    cookies = []
    for part in header.replace("\n", " ").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append({"name": name.strip(), "value": value.strip()})
    return cookies


def main():
    header = " ".join(sys.argv[1:]).strip()
    if not header:
        print("Wklej cały nagłówek Cookie z DevTools (app.scrab.com) i naciśnij Enter:")
        header = sys.stdin.readline()

    cookies = parse(header)
    names = {c["name"] for c in cookies}
    if not cookies:
        print("Nie znalazłem żadnych cookies w tym, co wkleiłeś.")
        return 1
    if "csrftoken" not in names:
        print("UWAGA: brak csrftoken — Scrab odrzuci zapytania.")
    if not ({"sessionid", "sessionid2"} & names):
        print("UWAGA: brak sessionid — sesja raczej nie zadziała.")

    json.dump({"saved": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "cookies": cookies},
              open(SESSION, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"Zapisano {len(cookies)} cookies do {SESSION}")

    try:
        import scrab_client
        scrab_client.post(["NVDA"], ["price"])
        print("Test API: OK — sesja działa.")
    except Exception as e:
        print(f"Test API nieudany: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
