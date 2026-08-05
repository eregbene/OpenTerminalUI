# Supported Runtime

Primary supported backend runtime: Python 3.11.

Docker uses `python:3.11-slim`, CI uses `actions/setup-python` with `3.11`, and `.python-version` pins local development to `3.11`.

Python 3.13 is not claimed as supported. During Phase 6/7 local verification it exposed dependency incompatibilities, including legacy `empyrical` build behavior. `backend/main.py` now fails early with a clear message when the app starts on an unsupported Python minor version.

Windows setup:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend/requirements-dev.txt
```

Docker-first setup:

```powershell
docker compose build
docker compose up -d
```
