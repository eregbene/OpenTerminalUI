# Starts the bensim MT5 bridge detached (survives closing the terminal). Log: logs/bensim_bridge.log
$root = Split-Path $PSScriptRoot
$cmd = "`$env:PYTHONPATH='$root'; `$env:MT5_PATH='E:\MT5\Bensim-100k\terminal64.exe'; Set-Location '$root'; python -m bensim.mt5_bridge *>> '$root\logs\bensim_bridge.log'"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $cmd
