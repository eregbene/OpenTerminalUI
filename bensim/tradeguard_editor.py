"""TradeGuard rules page: a tiny web form to edit logs/tradeguard.json (no Docker, Python stdlib only, ~20 MB RAM).

    bensim\\tradeguard_rules.bat                      # opens http://127.0.0.1:8791 ; close the window = stopped
    python -m bensim.tradeguard_editor --lan          # also reachable from her phone/laptop on the home Wi-Fi (PIN needed)
    python -m bensim.tradeguard_editor --selfcheck

Saved rules apply from next Monday 00:00 (server time) -- the guard reads the file only at the start of a week
(or when the guard restarts). Every save keeps the old file in logs/tradeguard_backups/.
PIN for --lan: first line of logs/tradeguard_editor_pin.txt (the page refuses to save over the LAN without it).
"""
from __future__ import annotations

import hmac
import html
import ipaddress
import json
import math
import os
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .tradeguard import CFG, DAYS, LOGS

PORT = 8791
RULES = (("day_loss", "Max loss per day $"), ("day_losses", "Max losing trades added up per day $"), ("week_loss", "Max loss per week $"),
         ("day_profit", "Stop after day profit $"), ("week_profit", "Stop after week profit $"),
         ("trade_loss", "Max loss per trade $"), ("trade_profit", "Close a trade at profit $"))
PIN_FILE = LOGS / "tradeguard_editor_pin.txt"


def money(form: dict, k: str, label: str) -> float:
    v = form.get(k, [""])[0].strip().replace(",", "") or "0"
    try:
        x = float(v)
    except ValueError:
        raise ValueError(f"{label}: '{v}' is not a number")
    if not math.isfinite(x) or x < 0:
        raise ValueError(f"{label}: must be 0 or more (0 = rule off)")
    return int(x) if x.is_integer() else x


def windows(s: str, day: str) -> list[list[str]]:
    out = []
    for part in filter(None, (p.strip() for p in s.split(","))):
        try:
            a, b = (time.strftime("%H:%M", time.strptime(x.strip(), "%H:%M")) for x in part.split("-"))
        except ValueError:
            raise ValueError(f"{day}: '{part}' must look like 09:30-12:30")
        if a >= b:
            raise ValueError(f"{day}: '{part}' ends before it starts")
        out.append([a, b])
    return out


def apply(cfg: dict, form: dict) -> dict:
    """New config from the form; raises ValueError with a plain message. Terminal/login/timezone are never touched."""
    new = json.loads(json.dumps(cfg))
    r = new.setdefault("rules", {})
    for k, label in RULES:
        r[k] = money(form, k, label)
    by_day = {d: money(form, "tp_" + d, f"Trade profit {d}") for d in DAYS[:5] if form.get("tp_" + d, [""])[0].strip()}
    if by_day:
        r["trade_profit_by_day"] = by_day
    else:
        r.pop("trade_profit_by_day", None)
    new["floating"] = "floating" in form
    th = new.get("trading_hours")
    if th is None:
        th = new["trading_hours"] = {"tz": "Europe/Bucharest", "effective_from_utc": int(time.time())}
    th["days"] = {d: windows(form.get("h_" + d, [""])[0], d) for d in DAYS}
    th["close_at_window_end"] = "close_at_window_end" in form
    th["delete_pending_outside"] = "delete_pending_outside" in form
    return new


def save(new: dict) -> None:
    bak = CFG.parent / "tradeguard_backups"
    bak.mkdir(exist_ok=True)
    shutil.copy2(CFG, bak / time.strftime("tradeguard_%Y-%m-%d_%H%M%S.json"))
    tmp = CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, indent=1))
    os.replace(tmp, CFG)


def last_status() -> str:
    try:
        raw = (LOGS / "tradeguard.log").read_bytes()[-4000:]
        lines = [x for x in raw.decode("utf-16" if raw[:2] == b"\xff\xfe" else "utf-8", "replace").replace("\0", "").splitlines() if x[:2] == "20"]
        return lines[-1] if lines else "no log yet"
    except OSError:
        return "no log yet"


def page(cfg: dict, msg: str = "", ok: bool = True, lan: bool = False) -> str:
    r, th = cfg.get("rules", {}), cfg.get("trading_hours") or {}
    by_day = r.get("trade_profit_by_day", {})
    e = lambda v: html.escape(str(v), quote=True)
    rules = "".join(f'<label>{lbl}<input name="{k}" inputmode="decimal" value="{e(r.get(k, 0))}"></label>' for k, lbl in RULES)
    tp = "".join(f'<label>{d.title()}<input name="tp_{d}" inputmode="decimal" placeholder="same" value="{e(by_day.get(d, ""))}"></label>' for d in DAYS[:5])
    hours = "".join(f'<label>{d.title()}<input name="h_{d}" placeholder="no trading" value="{e(", ".join(f"{a}-{b}" for a, b in th.get("days", {}).get(d, [])))}"></label>' for d in DAYS)
    chk = lambda name, on, lbl: f'<label class="c"><input type="checkbox" name="{name}"{" checked" if on else ""}> {lbl}</label>'
    note = f'<p class="{"ok" if ok else "bad"}">{e(msg)}</p>' if msg else ""
    pin = '<label>PIN<input name="pin" type="password" inputmode="numeric"></label>' if lan else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradeGuard Rules</title><style>
:root{{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--mute:#667;--line:#d7dbe0;--acc:#2f6fdf;--ok:#1d7a3a;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#15181c;--card:#1e2227;--fg:#e8eaed;--mute:#9aa;--line:#353b42;--acc:#6ea0ff;--ok:#5fcf7f;--bad:#ff8a80}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}}
main{{max-width:560px;margin:0 auto;padding:16px}}h1{{font-size:20px;margin:4px 0}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}}
h2{{font-size:15px;margin:0 0 8px}}small,.m{{color:var(--mute)}}
label{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:6px 0}}
input:not([type=checkbox]){{width:11em;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}}
label.c{{justify-content:flex-start}}button{{width:100%;padding:11px;border:0;border-radius:8px;background:var(--acc);color:#fff;font:600 16px system-ui}}
.ok{{color:var(--ok);font-weight:600}}.bad{{color:var(--bad);font-weight:600}}</style></head><body><main>
<h1>TradeGuard rules</h1><small class="m">Account {e(cfg.get("login", ""))} &middot; now: {e(last_status())}</small>{note}
<form method="post">
<section><h2>Limits</h2><small>0 = rule off</small>{rules}{chk("floating", cfg.get("floating", True), "Count open trades too (floating P/L)")}</section>
<section><h2>Trade profit by day</h2><small>empty = use "Close a trade at profit" above</small>{tp}</section>
<section><h2>Trading hours ({e(th.get("tz", "Europe/Bucharest"))})</h2><small>like 09:30-12:30, 15:00-18:40 &middot; empty = no trading that day</small>{hours}
{chk("delete_pending_outside", th.get("delete_pending_outside", True), "Delete pending orders outside the hours")}
{chk("close_at_window_end", th.get("close_at_window_end", False), "Close open trades when a window ends")}</section>
{pin}<button>Save</button><p class="m">New rules start next Monday 00:00. The old file is kept as a backup.</p>
</form></main></body></html>"""


class H(BaseHTTPRequestHandler):
    lan = False

    def _send(self, body: str, code: int = 200) -> None:
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def host_ok(self) -> bool:
        """Only requests addressed to an IP or localhost (blocks DNS rebinding: a site's own domain name in Host),
        and for POST an Origin that is that same host (blocks other websites posting here)."""
        host = self.headers.get("Host") or ""
        name = urlparse("//" + host).hostname or ""
        try:
            ipaddress.ip_address(name)
        except ValueError:
            if name != "localhost":
                return False
        origin = self.headers.get("Origin")
        return self.command == "GET" or (origin is not None and urlparse(origin).netloc == host)

    def do_GET(self) -> None:
        if not self.host_ok():
            return self._send("forbidden", 403)
        self._send(page(json.loads(CFG.read_text()), lan=self.lan))

    def do_POST(self) -> None:
        if not self.host_ok():
            return self._send("forbidden", 403)
        form = parse_qs(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 20_000)).decode())
        cfg = json.loads(CFG.read_text())
        if self.lan and self.client_address[0] != "127.0.0.1":
            want = PIN_FILE.read_text().strip() if PIN_FILE.exists() else ""
            if not want or not hmac.compare_digest(form.get("pin", [""])[0].encode(), want.encode()):
                return self._send(page(cfg, "Wrong PIN -- nothing saved.", False, self.lan))
        try:
            new = apply(cfg, form)
        except ValueError as exc:
            return self._send(page(cfg, f"Not saved: {exc}", False, self.lan))
        save(new)
        with open(CFG.parent / "tradeguard.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" RULES EDITED from {self.client_address[0]} (apply next week): {new['rules']}\n")
        self._send(page(new, "Saved. These rules start next Monday 00:00.", True, self.lan))

    def log_message(self, *a) -> None:  # quiet
        pass


def _selfcheck() -> None:
    cfg = {"login": 1, "rules": {"day_loss": 1}, "trading_hours": {"tz": "Europe/Bucharest", "effective_from_utc": 5, "days": {}}}
    f = {"day_loss": ["2,000"], "week_loss": ["7000"], "tp_wed": ["2100"], "h_mon": ["9:30-12:30, 15:00-18:40"], "floating": ["on"]}
    n = apply(cfg, f)
    assert n["rules"]["day_loss"] == 2000 and n["rules"]["day_profit"] == 0 and n["rules"]["trade_profit_by_day"] == {"wed": 2100}
    assert n["trading_hours"]["days"]["mon"] == [["09:30", "12:30"], ["15:00", "18:40"]] and n["trading_hours"]["days"]["sat"] == []
    assert n["floating"] and not n["trading_hours"]["delete_pending_outside"] and n["trading_hours"]["effective_from_utc"] == 5
    assert n["login"] == 1 and cfg["rules"] == {"day_loss": 1}  # original untouched
    for bad in ({"day_loss": ["-5"]}, {"day_loss": ["abc"]}, {"day_loss": ["nan"]}, {"h_mon": ["12:30-09:30"]}, {"h_tue": ["9.30-12"]}):
        try:
            apply(cfg, bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    print("selfcheck OK")


def main() -> None:
    if "--selfcheck" in sys.argv:
        return _selfcheck()
    H.lan = "--lan" in sys.argv
    host = "0.0.0.0" if H.lan else "127.0.0.1"
    print(f"TradeGuard rules page on http://{'<this PC IP>' if H.lan else '127.0.0.1'}:{PORT}  (close this window to stop)")
    ThreadingHTTPServer((host, PORT), H).serve_forever()


if __name__ == "__main__":
    main()
