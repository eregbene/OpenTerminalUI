# Weekly backup of the bensim database (candles, trades, ...) from the Docker Postgres container.
# Writes D:\Backups\bensim\bensim_<date>.dump (pg_dump custom format) and keeps the newest 8 (about 2 months).
# Restore:  docker cp <file> openterminalui-postgres-1:/tmp/r.dump
#           docker exec openterminalui-postgres-1 pg_restore -U openterminalui -d bensim --clean --if-exists /tmp/r.dump
# Scheduled weekly by the Windows task "bensim DB backup" (Sunday 12:00; docs/justin_bennett_smc.md 7.5).
param([string]$Dir = "D:\Backups\bensim", [int]$Keep = 8, [string]$Container = "openterminalui-postgres-1")
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Dir | Out-Null
$log = Join-Path $Dir "backup.log"
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$file = Join-Path $Dir "bensim_$stamp.dump"
try {
    # dump inside the container, then copy the file out (piping binary through PowerShell 5 corrupts it)
    docker exec $Container pg_dump -U openterminalui -d bensim -Fc -Z 6 -f /tmp/bensim_backup.dump
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed ($LASTEXITCODE)" }
    docker cp "${Container}:/tmp/bensim_backup.dump" $file
    if ($LASTEXITCODE -ne 0) { throw "docker cp failed ($LASTEXITCODE)" }
    docker exec $Container rm -f /tmp/bensim_backup.dump | Out-Null
    $mb = [math]::Round((Get-Item $file).Length / 1MB, 1)
    Get-ChildItem $Dir -Filter "bensim_*.dump" | Sort-Object Name -Descending | Select-Object -Skip $Keep | Remove-Item -Force
    Add-Content $log "$(Get-Date -Format s) OK $file ($mb MB)"
    Write-Output "backup OK: $file ($mb MB)"
} catch {
    Add-Content $log "$(Get-Date -Format s) FAILED: $_"
    Write-Output "backup FAILED: $_"
    exit 1
}
