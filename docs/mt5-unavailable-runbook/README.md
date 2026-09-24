# Runbook: backend says "MT5 unavailable" while the MT5 terminal is open

Symptoms in `docker logs openterminalui-backend-1`:

```
Portfolio manager refresh failed account_id=ftmo_demo_100k: MT5UnavailableError
MT5 multi-account cycle finished: ... accounts={'ftmo_demo_100k': 'TRADING_DISABLED'}
MT5 shadow outcome evaluation failed for ftmo_demo_100k:...: MT5UnavailableError
```

## How the connection works

```
MT5 terminal (Windows)  <--  Python bridge (Windows, port 8773)  <--  backend container (Docker)
terminal64.exe               python -m backend.brokers.mt5.bridge     http://host.docker.internal:8773
```

The backend runs in Linux Docker, so it can't talk to the terminal directly. It only talks to the
**Python bridge**, a separate Windows process you start yourself. An open, logged-in terminal
is not enough: if the bridge isn't running, or is on a different port from the one the
backend uses, you get `MT5UnavailableError`.

## What went wrong on 2026-09-24

1. **No bridge was running.** Only `terminal64.exe` was open.
2. **Wrong port in `.env`.** `MT5_ACCOUNT_100K_BRIDGE_PORT=22346` was a port the MT5 terminal
   itself listens on, so the bridge could never use it. Fixed by using the standard 100K port `8773`.
3. **The container still had the old port.** Changing `.env` does nothing until the container is
   recreated (`docker compose up -d backend`). A plain `docker restart` keeps the old env.
4. **A code bug (now fixed) made it look worse.** The shadow-outcome grader
   (`backend/brokers/mt5/outcome_resolver.py`) used the old global 10K connection
   (`MT5_ENABLED=false`) instead of each candidate's own account, so it logged
   `MT5UnavailableError` on every candidate even when the 100K link was fine.

## Standard ports

| Account | `account_id` | `.env` port variable | Port |
| --- | --- | --- | --- |
| 10K | `demo_10k` | `MT5_BRIDGE_PORT` | 8770 |
| 25K | `ftmo_demo_25k` | `MT5_ACCOUNT_25K_BRIDGE_PORT` | 8771 |
| 50K | `ftmo_demo_50k` | `MT5_ACCOUNT_50K_BRIDGE_PORT` | 8772 |
| 100K | `ftmo_demo_100k` | `MT5_ACCOUNT_100K_BRIDGE_PORT` | 8773 |

Only enabled accounts (`MT5_ACCOUNT_<X>_ENABLED=true`) need a bridge.

## Fix, step by step (PowerShell, in the repo root `D:\Devops\trading\OpenTerminalUI`)

### 1. Make sure the terminal is open and logged in
Open `E:\MT5\Bensim-100k\terminal64.exe`, check it's logged in to account 53007035 on
ICMarketsSC-Demo, and that **Algo Trading** is on (green button).

### 2. Check that the port is free and not taken by the terminal
```powershell
Get-NetTCPConnection -LocalPort 8773 -State Listen -ErrorAction SilentlyContinue |
  Select LocalAddress, OwningProcess,
    @{n='Process';e={(Get-Process -Id $_.OwningProcess).ProcessName}}
```
- **Nothing listed:** the port is free and the bridge is not running. Go to step 3.
- **A `python` process on `127.0.0.1`:** the bridge is already running. Skip to step 5.
- **`terminal64` or anything else:** the port is taken. Choose a free port such as 8774. Check it
  with the same command, set `MT5_ACCOUNT_100K_BRIDGE_PORT=8774` in `.env`, then continue.

### 3. Check that `.env` matches
```powershell
Select-String -Path .env -Pattern '^MT5_ACCOUNT_100K_(ENABLED|BRIDGE_PORT)|^MT5_BRIDGE_HOST|^MT5_BRIDGE_API_KEY' |
  ForEach-Object { $_.Line -replace '(API_KEY=).+','$1***' }
```
Expected: `MT5_ACCOUNT_100K_ENABLED=true`, `MT5_ACCOUNT_100K_BRIDGE_PORT=8773`,
`MT5_BRIDGE_HOST=host.docker.internal`, and an `MT5_BRIDGE_API_KEY` that is set.

### 4. Start the bridge
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-mt5-bridges.ps1 -AccountIds ftmo_demo_100k
```
It prints `[ftmo_demo_100k] MT5 bridge started on 127.0.0.1:8773` and runs in a hidden window.
Run step 2 again: you should now see a `python` process listening on 8773.

**The bridge does not survive a reboot or logoff.** Repeat this step after every restart.

### 5. Test the bridge from Windows
```powershell
$key = (Select-String .env -Pattern '^MT5_BRIDGE_API_KEY=(.+)').Matches[0].Groups[1].Value
Invoke-RestMethod http://127.0.0.1:8773/account -Headers @{ 'X-MT5-Bridge-Key' = $key } |
  Select login, server, balance, trade_allowed
```
You should see login `53007035`, server `ICMarketsSC-Demo`, and `trade_allowed = True`.
- `403 invalid bridge key`: the key in `.env` doesn't match the one the bridge started with.
  Restart the bridge (step 4).
- An `Authorization failed` error: the terminal isn't logged in. Go back to step 1.

### 6. Make sure the container uses the same port
```powershell
docker exec openterminalui-backend-1 sh -c 'echo $MT5_ACCOUNT_100K_BRIDGE_PORT'
```
If it prints anything other than the port in `.env`, recreate the container. A plain restart is
not enough:
```powershell
docker compose up -d --no-build backend
```

### 7. Test from inside the backend, the path the backend itself uses
```powershell
docker exec openterminalui-backend-1 python -c "from backend.brokers.mt5.multi_account import adapter_for_account as a; print(a('ftmo_demo_100k').client.connect()['account']['balance'])"
```
This should print the balance. If it raises `MT5UnavailableError`, go back to step 2.

### 8. Confirm in the logs
```powershell
docker logs openterminalui-backend-1 --since 10m 2>&1 | Select-String 'cycle finished|circuit breaker|MT5Unavailable'
```
Healthy means `Adaptive circuit breaker auto-recovered: broker reachable again`, and cycles no
longer showing `TRADING_DISABLED` because of the broker. Note that `TRADING_DISABLED` can also come
from the risk rules, such as the daily loss limit, not only from the connection.

### 9. After recreating the container, restart the historical watchdog
```powershell
docker exec -d openterminalui-backend-1 sh -c "cd /app && python watchdog_historical_workers.py > /data/historical_intelligence/watchdog_stdout.log 2>&1"
```

### What a healthy cycle looks like
```
MT5 multi-account cycle finished: ... accounts={'ftmo_demo_100k': 'BSI_V3_PLANNED_ENTRY_ONLY'} order_send_calls=0
```
`BSI_V3_PLANNED_ENTRY_ONLY` is normal while `BSI_V3_PLANNED_ENTRY_LIVE=true`. The M5 cycle hands
entries to the V3 fast watcher (`BSI V3 fast planned-entry watcher started`), which places the
orders. A healthy cycle takes about 2 minutes.

### If cycles take more than 5 minutes after reconnecting
After a long disconnect, a backlog of old shadow candidates can build up. Before 2026-09-24,
candidates older than about 7 days (2000 M5 bars) could never resolve, and the grader re-fetched
them through the bridge every 5 minutes, slowing down trading. They are now marked
`EXPIRED_NO_DATA`. Check the backlog with:
```powershell
docker exec openterminalui-backend-1 python -c "from backend.shared.db import SessionLocal; from backend.brokers.mt5.orm import MT5CandidateEvaluationORM as M; from sqlalchemy import func; db=SessionLocal(); print(db.query(M.outcome_status, func.count()).filter(M.outcome_type=='SHADOW').group_by(M.outcome_status).all())"
```

## Quick checklist
1. Terminal open, logged in, Algo Trading on
2. Bridge running: `python` listening on 8773
3. `.env` port = bridge port = container port
4. `Invoke-RestMethod .../account` returns your login
5. Container recreated after any `.env` change
