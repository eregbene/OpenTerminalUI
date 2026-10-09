"""Minimal MT5 bridge for bensim: one process on the Windows host, one MT5 terminal, DEMO accounts only.

    powershell -File bensim/start_bridge.ps1       # or: python -m bensim.mt5_bridge (MT5_PATH, key and port from .env)

Speaks the same API bensim.bridge already calls (same paths, payloads and row fields as the old backend bridge):
  GET  /account                                   -> account_info fields
  POST /rates {symbol, timeframe, start, count}  -> {"items": [{time, open, high, low, close, tick_volume, spread, real_volume}]}
Safety: binds 127.0.0.1 only; every request needs X-MT5-Bridge-Key (constant-time compare) and, when sent, the
configured account_id; refuses to start unless the logged-in account is a demo account. Single-threaded on
purpose -- the MetaTrader5 package is not thread-safe. Order endpoints get added with the live engine.
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
