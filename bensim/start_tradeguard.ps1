# TradeGuard watchdog: runs the guard hidden and restarts it if it ever exits. Log: logs/tradeguard.log
$root = Split-Path $PSScriptRoot
$loop = "`$env:PYTHONPATH='$root'; Set-Location '$root'; while (`$true) { python -m bensim.tradeguard live *>> '$root\logs\tradeguard.log'; Start-Sleep 10 }"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $loop
