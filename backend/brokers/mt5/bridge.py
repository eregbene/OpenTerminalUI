from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.exceptions import MT5UnavailableError


@dataclass(frozen=True)
class MT5BridgeStatus:
    enabled: bool
    host: str
    port: int
    authenticated: bool
    public: bool
    mode: str


class MT5Bridge:
    """Narrow Windows-local bridge descriptor for Docker/Linux backend deployments."""

    def __init__(self, config: MT5Config) -> None:
        self.config = config

    def status(self) -> MT5BridgeStatus:
        host = self.config.bridge_host
        return MT5BridgeStatus(
            enabled=bool(self.config.bridge_api_key),
            host=host,
            port=self.config.bridge_port,
            authenticated=bool(self.config.bridge_api_key),
            public=host not in {"127.0.0.1", "localhost"},
            mode="READ_ONLY",
        )


class MT5BridgeClient:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 0
    COPY_TICKS_ALL = 0
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_INVALID_VOLUME = 10014
    TRADE_RETCODE_INVALID_STOPS = 10016
    TRADE_RETCODE_INVALID_FILL = 10030
    TRADE_RETCODE_NO_MONEY = 10019
    TRADE_RETCODE_MARKET_CLOSED = 10018
    TRADE_RETCODE_PRICE_CHANGED = 10020
    TRADE_RETCODE_REQUOTE = 10004
    TRADE_RETCODE_TRADE_DISABLED = 10017

    def __init__(self, config: MT5Config) -> None:
        self.config = config
        self.base_url = f"http://{config.bridge_host}:{config.bridge_port}"

    def initialize(self, **kwargs: Any) -> bool:
        return bool(self._request("POST", "/connect", kwargs).get("initialized"))

    def login(self, **kwargs: Any) -> bool:
        return bool(self._request("POST", "/login", _without_secret(kwargs)).get("login_success"))

    def shutdown(self) -> bool:
        return bool(self._request("POST", "/shutdown", {}).get("shutdown"))

    def terminal_info(self):
        return _Row(self._request("GET", "/terminal", {}))

    def version(self):
        return tuple(self._request("GET", "/version", {}).get("version") or ())

    def account_info(self):
        return _Row(self._request("GET", "/account", {}))

    def symbols_get(self, group: str | None = None):
        query = f"?group={urllib.parse.quote(group)}" if group else ""
        return [_Row(row) for row in self._request("GET", f"/symbols{query}", {}).get("items", [])]

    def symbol_info(self, symbol: str):
        data = self._request("GET", f"/symbols/{symbol}", {})
        return _Row(data) if data else None

    def symbol_select(self, symbol: str, enable: bool):
        try:
            return bool(self._request("POST", "/symbols/select", {"symbol": symbol, "enable": enable}).get("selected"))
        except Exception:
            return True

    def symbol_info_tick(self, symbol: str):
        data = self._request("GET", f"/ticks/{symbol}", {})
        return _Row(data) if data else None

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int):
        return self._request("POST", "/rates", {"symbol": symbol, "timeframe": timeframe, "start": start, "count": count}).get("items", [])

    def copy_ticks_from(self, symbol: str, from_date: datetime, count: int, flags: int):
        return self._request("POST", "/ticks", {"symbol": symbol, "from": from_date.isoformat(), "count": count, "flags": flags}).get("items", [])

    def positions_get(self):
        return [_Row(row) for row in self._request("GET", "/positions", {}).get("items", [])]

    def orders_get(self):
        return [_Row(row) for row in self._request("GET", "/orders", {}).get("items", [])]

    def history_deals_get(self, from_date: datetime, to_date: datetime):
        return [_Row(row) for row in self._request("POST", "/history/deals", {"from": from_date.isoformat(), "to": to_date.isoformat()}).get("items", [])]

    def history_orders_get(self, from_date: datetime, to_date: datetime):
        return [_Row(row) for row in self._request("POST", "/history/orders", {"from": from_date.isoformat(), "to": to_date.isoformat()}).get("items", [])]

    def order_check(self, request: dict[str, Any]):
        return _Row(self._request("POST", "/order-check", {"request": request}))

    def order_calc_margin(self, order_type: int, symbol: str, volume: float, price: float):
        return self._request("POST", "/order-calc-margin", {"order_type": order_type, "symbol": symbol, "volume": volume, "price": price}).get("value")

    def order_calc_profit(self, order_type: int, symbol: str, volume: float, price_open: float, price_close: float):
        return self._request("POST", "/order-calc-profit", {"order_type": order_type, "symbol": symbol, "volume": volume, "price_open": price_open, "price_close": price_close}).get("value")

    def order_send(self, request: dict[str, Any]):
        return _Row(self._request("POST", "/order-send", {"request": request}))

    def calendar_values(self, from_date: datetime, to_date: datetime):
        return [_Row(row) for row in self._request("POST", "/calendar/values", {"from": from_date.isoformat(), "to": to_date.isoformat()}).get("items", [])]

    def calendar_status(self):
        return self._request("GET", "/calendar/status", {})

    def last_error(self):
        return tuple(self._request("GET", "/last-error", {}).get("last_error") or ())

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = None if method == "GET" else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "X-MT5-Bridge-Key": self.config.bridge_api_key or ""},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_ms / 1000) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if method != "GET" and exc.code == 422:
                query_path = f"{path}?payload={urllib.parse.quote(json.dumps(payload))}"
                req = urllib.request.Request(
                    f"{self.base_url}{query_path}",
                    data=b"{}",
                    method=method,
                    headers={"Content-Type": "application/json", "X-MT5-Bridge-Key": self.config.bridge_api_key or ""},
                )
                with urllib.request.urlopen(req, timeout=self.config.timeout_ms / 1000) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise MT5UnavailableError(f"MT5 bridge request failed: {exc}") from exc
        except Exception as exc:
            raise MT5UnavailableError(f"MT5 bridge request failed: {exc}") from exc


class _Row(dict):
    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def _asdict(self) -> dict[str, Any]:
        return dict(self)


def create_app():
    from fastapi import Body, Depends, FastAPI, Header, HTTPException
    from backend.brokers.mt5.client import MT5Client
    from backend.brokers.mt5.config import mt5_config

    cfg = mt5_config()
    client = MT5Client(cfg)
    app = FastAPI(title="Bensim MT5 Bridge", version="1.0")

    def auth(x_mt5_bridge_key: str = Header(default="")) -> None:
        if cfg.bridge_api_key and x_mt5_bridge_key != cfg.bridge_api_key:
            raise HTTPException(status_code=403, detail="invalid bridge key")

    @app.post("/connect", dependencies=[Depends(auth)])
    def connect(payload: dict[str, Any] | None = Body(default=None)):
        return client.connect()

    @app.post("/login", dependencies=[Depends(auth)])
    def login(payload: dict[str, Any] | None = Body(default=None)):
        return {"login_success": True}

    @app.post("/shutdown", dependencies=[Depends(auth)])
    def shutdown():
        client.shutdown()
        return {"shutdown": True}

    @app.get("/terminal", dependencies=[Depends(auth)])
    def terminal():
        return client.terminal_info() or {}

    @app.get("/version", dependencies=[Depends(auth)])
    def version():
        return {"version": client.version()}

    @app.get("/account", dependencies=[Depends(auth)])
    def account():
        return client.account_info() or {}

    @app.get("/symbols", dependencies=[Depends(auth)])
    def symbols(group: str | None = None):
        mt5 = client.ensure_ready()
        rows = mt5.symbols_get(group=group) if group else mt5.symbols_get()
        return {"items": [_asdict(row) for row in _rows(rows)]}

    @app.get("/symbols/{symbol}", dependencies=[Depends(auth)])
    def symbol_info(symbol: str):
        return _asdict(client.ensure_ready().symbol_info(symbol) or {})

    @app.post("/symbols/select", dependencies=[Depends(auth)])
    def symbol_select(payload: dict[str, Any] = Body(...)):
        return {"selected": bool(client.ensure_ready().symbol_select(str(payload["symbol"]), bool(payload.get("enable", True))))}

    @app.get("/ticks/{symbol}", dependencies=[Depends(auth)])
    def tick(symbol: str):
        return _asdict(client.ensure_ready().symbol_info_tick(symbol) or {})

    @app.post("/rates", dependencies=[Depends(auth)])
    def rates(payload: dict[str, Any] = Body(...)):
        rows = client.ensure_ready().copy_rates_from_pos(
            str(payload["symbol"]),
            int(payload["timeframe"]),
            int(payload.get("start", 1)),
            int(payload.get("count", 100)),
        )
        return {"items": [_asdict(row) for row in _rows(rows)]}

    @app.get("/positions", dependencies=[Depends(auth)])
    def positions():
        return {"items": [_asdict(row) for row in _rows(client.ensure_ready().positions_get())]}

    @app.get("/orders", dependencies=[Depends(auth)])
    def orders():
        return {"items": [_asdict(row) for row in _rows(client.ensure_ready().orders_get())]}

    @app.post("/history/deals", dependencies=[Depends(auth)])
    def history_deals(payload: dict[str, Any] = Body(...)):
        start, end = _date_range(payload)
        return {"items": [_asdict(row) for row in _rows(client.ensure_ready().history_deals_get(start, end))]}

    @app.post("/history/orders", dependencies=[Depends(auth)])
    def history_orders(payload: dict[str, Any] = Body(...)):
        start, end = _date_range(payload)
        return {"items": [_asdict(row) for row in _rows(client.ensure_ready().history_orders_get(start, end))]}

    @app.post("/order-check", dependencies=[Depends(auth)])
    def order_check(payload: dict[str, Any] = Body(...)):
        return _asdict(client.ensure_ready().order_check(payload["request"]) or {})

    @app.post("/order-calc-margin", dependencies=[Depends(auth)])
    def order_calc_margin(payload: dict[str, Any] = Body(...)):
        value = client.ensure_ready().order_calc_margin(
            int(payload["order_type"]),
            str(payload["symbol"]),
            float(payload["volume"]),
            float(payload["price"]),
        )
        return {"value": _json_safe(value)}

    @app.post("/order-calc-profit", dependencies=[Depends(auth)])
    def order_calc_profit(payload: dict[str, Any] = Body(...)):
        value = client.ensure_ready().order_calc_profit(
            int(payload["order_type"]),
            str(payload["symbol"]),
            float(payload["volume"]),
            float(payload["price_open"]),
            float(payload["price_close"]),
        )
        return {"value": _json_safe(value)}

    @app.post("/order-send", dependencies=[Depends(auth)])
    def order_send(payload: dict[str, Any] = Body(...)):
        return _asdict(client.ensure_ready().order_send(payload["request"]) or {})

    @app.get("/last-error", dependencies=[Depends(auth)])
    def last_error():
        return {"last_error": client.last_error()}

    @app.get("/calendar/status", dependencies=[Depends(auth)])
    def calendar_status():
        mt5 = client.ensure_ready()
        names = [name for name in dir(mt5) if name.lower().startswith("calendar")]
        file_available = bool(cfg.calendar_export_file and Path(cfg.calendar_export_file).exists())
        return {"available": bool(names) or file_available, "methods": names, "mode": "READ_ONLY", "mql5_file_bridge": file_available}

    @app.post("/calendar/values", dependencies=[Depends(auth)])
    def calendar_values(payload: dict[str, Any] = Body(...)):
        mt5 = client.ensure_ready()
        if not hasattr(mt5, "calendar_value_history"):
            if cfg.calendar_export_file and Path(cfg.calendar_export_file).exists():
                with Path(cfg.calendar_export_file).open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return {"items": data.get("items") or []}
            raise HTTPException(status_code=501, detail={"code": "MT5_CALENDAR_UNAVAILABLE", "message": "MetaTrader5 Python package does not expose calendar_value_history"})
        start, end = _date_range(payload)
        rows = mt5.calendar_value_history(start, end)
        return {"items": [_asdict(row) for row in _rows(rows)]}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("MT5_BRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", default=int(os.getenv("MT5_BRIDGE_PORT", "8765")), type=int)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)


def _asdict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "_asdict"):
        return value._asdict()
    if isinstance(value, dict):
        return value
    if hasattr(value, "dtype") and getattr(value.dtype, "names", None):
        return {key: _json_safe(value[key]) for key in value.dtype.names}
    return dict(value)


def _rows(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value)


def _date_range(payload: dict[str, Any]) -> tuple[datetime, datetime]:
    end = datetime.fromisoformat(str(payload.get("to"))) if payload.get("to") else datetime.now(timezone.utc)
    start = datetime.fromisoformat(str(payload.get("from") or payload.get("from_"))) if (payload.get("from") or payload.get("from_")) else end.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def _json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def _without_secret(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "password"}


if __name__ == "__main__":
    main()
