# Dependency Management

Dependency groups:

- `backend/requirements-core.txt`: FastAPI app, auth, SQLAlchemy, Redis client, multipart/websocket basics.
- `backend/requirements-market-data.txt`: providers, market-data clients, NSE/Kite/options helpers.
- `backend/requirements-research.txt`: scientific, reporting, PDF, optimization, ML/statistics dependencies.
- `backend/requirements-dev.txt`: research plus pytest, async database drivers, coverage.
- `backend/requirements.txt`: compatibility wrapper that installs dev.

Known Phase 7 decisions:

- `empyrical` is isolated with `python_version < "3.13"` because the supported runtime is Python 3.11 and the package build path fails on Python 3.13.
- `pypdf`, `mibian`, `reportlab`, `statsmodels`, `scikit-learn`, `openpyxl`, and `scipy` are declared in grouped files so E2E/backend startup dependencies are visible.
- Heavy optional ML packages (`hmmlearn`, `optuna`, `xgboost`) live in `backend/requirements-ml.txt`. They are not installed by the default Docker runtime because current app code does not import them and `xgboost` pulls very large Linux GPU wheels.
- `pyarrow` remains in the default research requirements because the OHLCV cache cold tier writes parquet through pandas.
- Core API installs do not require every research package.

Commands:

```powershell
python -m pip install -r backend/requirements-core.txt
python -m pip install -r backend/requirements-dev.txt
python -m pip install -r backend/requirements-research.txt
python -m pip check
```
## Phase 10 Dependency Notes

Phase 10 does not introduce new external runtime dependencies. The provider hardening and Research Agent implementation use the existing FastAPI, pytest, React, Vitest, and Playwright toolchain plus Python standard-library persistence helpers.

Provider integrations continue to rely on configured optional provider clients. Missing provider secrets must degrade to deterministic local fallback behavior rather than failing the application.
## Phase 11 Dependency Notes

The automated Phase 11 implementation uses the Python and frontend dependencies already present in the repository. Real IBKR TWS/Gateway verification may require an IBKR client library or direct protocol adapter in a later hardening pass; that dependency was not added in this phase.
# Phase 12 Dependency Note

Phase 12 did not add new Python or npm dependencies. It uses existing FastAPI, SQLAlchemy, React, and test infrastructure.
