"""Thin HTTP client for the MT5 bridge (backend/brokers/mt5/bridge.py, one process per account on the host).
Fails closed: no key configured -> refuse to call, never send unauthenticated requests."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from .config import ACCOUNT_ID, BRIDGE_KEY, BRIDGE_URL

TF = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 16385, "H4": 16388, "D1": 16408}


class BridgeError(RuntimeError):
    pass


def _call(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 120) -> dict[str, Any]:
    if not BRIDGE_KEY:
        raise BridgeError("MT5 bridge key not configured (MT5_ACCOUNT_100K_BRIDGE_API_KEY / MT5_BRIDGE_API_KEY)")
    body = json.dumps({"account_id": ACCOUNT_ID, **(payload or {})}).encode() if method == "POST" else None
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", data=body, method=method,
                                 headers={"X-MT5-Bridge-Key": BRIDGE_KEY, "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as exc:  # network, HTTP error, bad JSON
        raise BridgeError(f"{method} {path}: {exc}") from exc


def rates(symbol: str, tf: str, start: int, count: int) -> list[dict[str, Any]]:
    """Bars by position back from now (0 = forming bar). Times are broker SERVER time (see mt5_time)."""
    return _call("POST", "/rates", {"symbol": symbol, "timeframe": TF[tf], "start": start, "count": count}, timeout=600).get("items") or []


def account() -> dict[str, Any]:
    return _call("GET", "/account")
