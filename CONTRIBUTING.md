# Contributing

This is a personal project built for my own investment workflow, published as portfolio evidence.
Bug reports and suggestions are welcome — please open an issue rather than a pull request first, so
we can agree on the scope.

## Running it locally

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Then double-click `Terminal.bat`. Market data needs your own Scrab session
(`scrab_session.json`); the app starts without it and shows a message instead of data.

## Before opening a pull request

- Keep the model deterministic: no new data sources without a cache and a fallback.
- If you change a formula, update `docs/MODEL.md` and bump `MODEL_VER` in `app.py`.
- Never commit `scrab_session.json`, `state.json`, `journal.json` or anything under `_cache/`.
  They are ignored by `.gitignore`; keep it that way.
- `python -m compileall -q .` must pass (this is what CI runs).
