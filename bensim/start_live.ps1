# Watchdog: keeps the MT5 bridge and the live engine running (restarts either after a crash). Detached.
# Logs: logs/bensim_live.log, logs/bensim_bridge.log. Stop: create the file bensim.STOP (no new orders) or kill this window's process.
$root = Split-Path $PSScriptRoot
$loop = @"
`$env:PYTHONPATH='$root'; Set-Location '$root'
while (`$true) {
  `$up = Get-CimInstance Win32_Process | Where-Object { `$_.Name -eq 'python.exe' -and `$_.CommandLine -like '*bensim.mt5_bridge*' }
  if (-not `$up) { & '$root\bensim\start_bridge.ps1'; Start-Sleep 20 }
  python -m bensim.live *>> '$root\logs\bensim_live.log'
  # the engine only exits after repeated failures: restart the bridge too (it may be hung, not dead)
  Get-CimInstance Win32_Process | Where-Object { `$_.Name -eq 'python.exe' -and `$_.CommandLine -like '*bensim.mt5_bridge*' } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }
  Start-Sleep 30
}
"@
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $loop
