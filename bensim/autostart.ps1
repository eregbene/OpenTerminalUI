# bensim autostart / health keeper -- idempotent: run by the Windows task "bensim autostart" at logon and every 5 minutes.
# Makes sure, in order: Docker + Postgres up -> the bensim MT5 terminal running -> port 8773 free for our bridge (or held
# by it) -> the engine watchdog running (it starts the bridge + engine) -> the trading chain at High priority.
# Changes nothing that is already fine. Log: logs/autostart.log. Kill switch for trading stays bensim.STOP.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot
$log = Join-Path $root "logs\autostart.log"
$terminal = "E:\MT5\Bensim-100k\terminal64.exe"
$port = 8773
function Log($m) { Add-Content $log "$(Get-Date -Format s) $m" }

# 1. Docker Desktop (it starts Postgres itself: restart policy unless-stopped)
$dockerExe = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"
if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) -and (Test-Path $dockerExe)) {
    Start-Process $dockerExe; Log "started Docker Desktop"
}
$pg = $false
for ($i = 0; $i -lt 36; $i++) {
    docker exec openterminalui-postgres-1 pg_isready -U openterminalui -d bensim *> $null
    if ($LASTEXITCODE -eq 0) { $pg = $true; break }
    Start-Sleep 10
}
if (-not $pg) { Log "WARN Postgres not ready after 6 min (the engine retries on its own)" }

# 2. the bensim MT5 terminal (the bridge would also launch it, but starting it first avoids a slow first connect)
$term = Get-CimInstance Win32_Process -Filter "name='terminal64.exe'" | Where-Object { $_.ExecutablePath -ieq $terminal }
if (-not $term -and (Test-Path $terminal)) { Start-Process $terminal; Log "started MT5 terminal $terminal"; Start-Sleep 45 }

# 3. port 8773: reserved by Windows (Hyper-V) or held by a process that is not our bridge -> fix / report
$excluded = netsh interface ipv4 show excludedportrange protocol=tcp | ForEach-Object {
    if ($_ -match '^\s*(\d+)\s+(\d+)') { if ($port -ge [int]$Matches[1] -and $port -le [int]$Matches[2]) { $_ } } }
if ($excluded) { Log "ERROR port $port is in a Windows excluded range ($excluded) -- the bridge cannot bind; reboot or change MT5_ACCOUNT_100K_BRIDGE_PORT" }
$bridges = @(Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -like '*bensim.mt5_bridge*' })
$owner = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($owner -and -not ($bridges.ProcessId -contains $owner)) {
    $name = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
    Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; Log "freed port $port from pid $owner ($name)"
}
if ($bridges.Count -gt 1) {  # duplicates: keep the one that owns the port
    $bridges | Where-Object { $_.ProcessId -ne $owner } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Log "stopped duplicate bridge pid $($_.ProcessId)" }
}

# 4. the watchdog loop (starts the bridge when missing, runs the engine, restarts both after failures)
$wd = Get-CimInstance Win32_Process -Filter "name='powershell.exe'" | Where-Object { $_.CommandLine -like '*python -m bensim.live*' }
if (-not $wd) { & (Join-Path $root "bensim\start_live.ps1"); Log "started the engine watchdog"; Start-Sleep 60 }

# 5. priorities: live trading always beats research (MT5, bridge, engine, Docker/Postgres at High)
$high = @()
$high += Get-CimInstance Win32_Process -Filter "name='terminal64.exe'" | Where-Object { $_.ExecutablePath -ieq $terminal }
$high += Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -like '*bensim.mt5_bridge*' -or $_.CommandLine -like '*bensim.live*' }
$high += Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('vmmemWSL', 'vmmem', 'com.docker.backend.exe') }
foreach ($p in $high) {
    $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    if ($proc -and $proc.PriorityClass -ne 'High') {
        try { $proc.PriorityClass = 'High'; Log "priority High: $($proc.ProcessName) pid $($proc.Id)" } catch { }
    }
}
