"""Minimal MT5 bridge for bensim: one process on the Windows host, one MT5 terminal, DEMO accounts only.

    powershell -File bensim/start_bridge.ps1       # or: python -m bensim.mt5_bridge (MT5_PATH, key and port from .env)

Speaks the same API bensim.bridge already calls (same paths, payloads and row fields as the old backend bridge):
  GET  /account                                   -> account_info fields
  POST /rates {symbol, timeframe, start, count}  -> {"items": [{time, open, high, low, close, tick_volume, spread, real_volume}]}
  POST /tick {symbol}                           -> bid/ask/time + the symbol specs sizing needs (tick value/size, volume step...)
  GET  /positions                               -> {"items": [open positions]}
  POST /order {symbol, side, volume, sl, tp, magic, comment}  -> market order (DEMO only), MT5 order_send result
  POST /close {ticket}                          -> closes that position at market (DEMO only)
  POST /deals {start, end}                      -> {"items": [deals in that UTC-epoch window]}
Safety: binds 127.0.0.1 only; every request needs X-MT5-Bridge-Key (constant-time compare) and, when sent, the
configured account_id; refuses to start unless the logged-in account is a demo account. Single-threaded on
purpose -- the MetaTrader5 package is not thread-safe. Trading endpoints re-check on every call that the account is still DEMO.
"""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from urllib.parse import urlparse

from .config import ACCOUNT_ID, BRIDGE_KEY, BRIDGE_URL, env

MAX_BODY = 64 * 1024


def _rows(rows: Any) -> list[dict[str, Any]]:
    if rows is None:
        return []
    names = rows.dtype.names
    return [{k: (v.item() if hasattr(v, "item") else v) for k, v in zip(names, r)} for r in rows]


def _nt(x: Any) -> Any:
    """MT5 namedtuples (also nested, e.g. order_send result.request) -> plain dicts."""
    if hasattr(x, "_asdict"):
        return {k: _nt(v) for k, v in x._asdict().items()}
    return x.item() if hasattr(x, "item") else x


def _filling(mt5: Any, info: Any) -> int:
    fm = int(getattr(info, "filling_mode", 0))
    return mt5.ORDER_FILLING_FOK if fm & 1 else (mt5.ORDER_FILLING_IOC if fm & 2 else mt5.ORDER_FILLING_RETURN)


def _market(mt5: Any, symbol: str, side: str, volume: float, **extra: Any) -> dict[str, Any]:
    """Market order with deviation 20 points; `extra` = sl/tp/magic/comment or position (to close)."""
    acct = mt5.account_info()
    if acct is None or acct.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        return {"ok": False, "detail": "refused: account is not DEMO"}
    info = mt5.symbol_info(symbol)
    if info is None or not mt5.symbol_select(symbol, True):
        return {"ok": False, "detail": f"unknown symbol {symbol}"}
    tick = mt5.symbol_info_tick(symbol)
    buy = side == "BUY"
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(volume),
           "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, "price": tick.ask if buy else tick.bid,
           "deviation": 20, "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(mt5, info), **extra}
    res = mt5.order_send(req)
    if res is None:
        return {"ok": False, "detail": f"order_send returned None: {mt5.last_error()}"}
    out = _nt(res)
    out.pop("request", None)
    return {"ok": res.retcode == mt5.TRADE_RETCODE_DONE, **out}


def make_handler(mt5: Any, key: str, account_id: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self, payload: dict[str, Any]) -> bool:
            if not hmac.compare_digest(self.headers.get("X-MT5-Bridge-Key", ""), key):
                self._send(403, {"detail": "invalid bridge key"})
                return False
            requested = payload.get("account_id") or self.headers.get("X-MT5-Account-ID") or account_id
            if requested != account_id:
                self._send(409, {"detail": {"code": "ACCOUNT_EXECUTION_CONTEXT_MISMATCH", "worker_account_id": account_id}})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized({}):
                return
            if self.path == "/account":
                info = mt5.account_info()
                return self._send(200, {"account_id": account_id, **(info._asdict() if info else {})}) if info else self._send(503, {"detail": str(mt5.last_error())})
            if self.path == "/positions":
                return self._send(200, {"account_id": account_id, "items": [_nt(p) for p in (mt5.positions_get() or ())]})
            self._send(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"detail": "body too large"})
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"detail": "bad json"})
            if not self._authorized(payload):
                return
            if self.path == "/rates":
                try:
                    rows = mt5.copy_rates_from_pos(str(payload["symbol"]), int(payload["timeframe"]),
                                                   int(payload.get("start", 1)), min(int(payload.get("count", 100)), 100_000))
                except (KeyError, ValueError, TypeError) as exc:
                    return self._send(400, {"detail": f"bad request: {exc}"})
                return self._send(200, {"account_id": account_id, "items": _rows(rows)})
            try:
                if self.path == "/tick":
                    sym = str(payload["symbol"])
                    mt5.symbol_select(sym, True)
                    info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
                    if info is None or tick is None:
                        return self._send(404, {"detail": f"no quote for {sym}: {mt5.last_error()}"})
                    keep = ("digits", "point", "trade_tick_value", "trade_tick_size", "volume_min", "volume_max", "volume_step",
                            "trade_stops_level", "trade_mode", "spread")
                    return self._send(200, {"symbol": sym, "bid": tick.bid, "ask": tick.ask, "time": tick.time, **{k: _nt(getattr(info, k)) for k in keep}})
                if self.path == "/order":
                    side = str(payload["side"])
                    if side not in ("BUY", "SELL") or float(payload["volume"]) <= 0:
                        return self._send(400, {"detail": "bad side/volume"})
                    res = _market(mt5, str(payload["symbol"]), side, float(payload["volume"]), sl=float(payload["sl"]), tp=float(payload["tp"]),
                                  magic=int(payload.get("magic", 0)), comment=str(payload.get("comment", ""))[:31])
                    return self._send(200, res)
                if self.path == "/close":
                    pos = mt5.positions_get(ticket=int(payload["ticket"]))
                    if not pos:
                        return self._send(404, {"detail": "position not found"})
                    p = pos[0]
                    side = "SELL" if p.type == mt5.POSITION_TYPE_BUY else "BUY"
                    return self._send(200, _market(mt5, p.symbol, side, p.volume, position=p.ticket, magic=p.magic, comment=str(payload.get("comment", "close"))[:31]))
                if self.path == "/deals":
                    from datetime import datetime, timezone
                    a, b = (datetime.fromtimestamp(int(payload[k]), timezone.utc) for k in ("start", "end"))
                    return self._send(200, {"account_id": account_id, "items": [_nt(d) for d in (mt5.history_deals_get(a, b) or ())]})
            except (KeyError, ValueError, TypeError) as exc:
                return self._send(400, {"detail": f"bad request: {exc}"})
            self._send(404, {"detail": "not found"})

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet; errors still raise
            pass

    return Handler


def main() -> None:
    import MetaTrader5 as mt5  # host-only dependency

    if not BRIDGE_KEY:
        raise SystemExit("refusing to start: no bridge key (MT5_ACCOUNT_100K_BRIDGE_API_KEY / MT5_BRIDGE_API_KEY)")
    path = env("MT5_PATH")
    if not (mt5.initialize(path=path) if path else mt5.initialize()):
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    info = mt5.account_info()
    if info is None or info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        mt5.shutdown()
        raise SystemExit(f"refusing to start: account is not DEMO ({getattr(info, 'trade_mode', None)})")
    port = urlparse(BRIDGE_URL).port or 8773  # the port bensim.bridge calls, by construction
    print(f"bensim MT5 bridge: account {ACCOUNT_ID} (login {info.login}, {info.server}) on 127.0.0.1:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), make_handler(mt5, BRIDGE_KEY, ACCOUNT_ID)).serve_forever()


if __name__ == "__main__":
    main()
