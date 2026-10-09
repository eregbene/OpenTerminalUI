"""Settings, read once from the environment / repo .env (no secrets in code)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dotenv() -> dict[str, str]:
    """Repo .env, last occurrence wins (same rule docker compose uses)."""
    out: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_ENV = _dotenv()


def env(name: str, default: str = "") -> str:
    return os.getenv(name) or _ENV.get(name, default)


SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "XAUUSD"]
DB_URL = env("BENSIM_DB_URL", "postgresql://openterminalui:openterminalui@localhost:5432/bensim")
BRIDGE_URL = env("BENSIM_BRIDGE_URL", f"http://127.0.0.1:{env('MT5_ACCOUNT_100K_BRIDGE_PORT', '8773')}")
BRIDGE_KEY = env("MT5_ACCOUNT_100K_BRIDGE_API_KEY") or env("MT5_BRIDGE_API_KEY")
ACCOUNT_ID = env("BENSIM_ACCOUNT_ID", "ftmo_demo_100k")
