<#
.SYNOPSIS
    Safety preflight to run BEFORE any significant backend/database/trading-engine
    task. Backs up and verifies the local development database, records Git and
    migration state, and only then says it's safe to proceed.

.DESCRIPTION
    Standing procedure after the 2026-08-07 incident (see
    docs/CLAUDE_BACKEND_SAFETY.md, migration 0036_db_incident_boundary). Run this
    first, every time, before modifying backend/database/Adaptive Manager/Portfolio
    Manager/MT5/economic-intelligence/learning-engine/migration code.

.PARAMETER TaskName
    Short name for the task about to begin, e.g. "adaptive-manager-v3". Recorded in
    the task metadata sidecar next to the backup.

.EXAMPLE
    .\scripts\pre_backend_task.ps1 -TaskName "adaptive-manager-v3"

.NOTES
    Prints "SAFE TO BEGIN BACKEND TASK" ONLY if backup + verification succeeded.
    Any failure stops here with a non-zero exit code -- do not proceed with
    database/migration/backend work if this script did not print that line.
#>
param(
    [Parameter(Mandatory = $true)][string]$TaskName,
    [string]$ContainerName = "openterminalui-postgres-1",
    [string]$Database = "openterminalui",
    [string]$User = "openterminalui",
    [string]$OutDir = "C:\Users\Intel\Desktop\BensimBackups"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path

function Fail($msg) {
    Write-Error $msg
    Write-Host ""
    Write-Host "NOT SAFE TO BEGIN BACKEND TASK -- $msg"
    exit 1
}

Write-Host "=== pre_backend_task: '$TaskName' ==="

Write-Host "[1/6] Git state..."
Push-Location $repoRoot
try {
    $branch = (git rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
    $commit = (git rev-parse HEAD 2>$null | Out-String).Trim()
    $dirty = [bool](git status --porcelain 2>$null)
}
finally {
    Pop-Location
}
Write-Host "  branch=$branch commit=$commit dirty=$dirty"
if (-not $branch -or -not $commit) {
    Fail "Could not determine git branch/commit -- is this a git repository?"
}
if ($dirty) {
    Write-Warning "Working tree is dirty. Proceeding, but be aware the backup's recorded commit does not fully describe the current working tree state (this is expected mid-task; just don't assume the backup == HEAD)."
}

Write-Host "[2/6] Postgres container check..."
$containerStatus = docker ps --filter "name=$ContainerName" --format "{{.Status}}"
if (-not $containerStatus) {
    Fail "Container '$ContainerName' is not running."
}
Write-Host "  $ContainerName : $containerStatus"

Write-Host "[3/6] Current Alembic revision..."
$alembicRevision = (docker exec $ContainerName psql -U $User -d $Database -tAc "SELECT version_num FROM alembic_version;" 2>$null | Out-String).Trim()
if (-not $alembicRevision) {
    Fail "Could not read alembic_version from database '$Database'."
}
Write-Host "  alembic_revision=$alembicRevision"

Write-Host "[4/6] Running backup..."
$backupOk = $true
$backupOutput = & "$PSScriptRoot\backup_postgres.ps1" -ContainerName $ContainerName -Database $Database -User $User -OutDir $OutDir -Label "pretask-$TaskName" -Category manual 2>&1
$backupOutput | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { $backupOk = $false }

if (-not $backupOk) {
    Fail "Backup step failed. Refusing to proceed with task '$TaskName'."
}

Write-Host "[5/6] Confirming backup_verified=true..."
$verifiedLine = $backupOutput | Select-String -Pattern "^backup_verified=true$"
if (-not $verifiedLine) {
    Fail "backup_postgres.ps1 did not report backup_verified=true. Refusing to proceed."
}
$backupFileLine = $backupOutput | Select-String -Pattern "Backup created: (.+) \("
$backupFile = if ($backupFileLine) { ($backupFileLine.Matches[0].Groups[1].Value) } else { $null }

Write-Host "[6/6] Writing task metadata..."
$taskMetaPath = Join-Path $OutDir "pretask_${TaskName}_$(Get-Date -Format yyyyMMdd_HHmmss).json"
[ordered]@{
    task_name         = $TaskName
    started_at_utc    = (Get-Date).ToUniversalTime().ToString("o")
    git_branch        = $branch
    git_commit        = $commit
    git_dirty         = $dirty
    alembic_revision_before_task = $alembicRevision
    backup_file       = $backupFile
    backup_verified   = $true
} | ConvertTo-Json | Set-Content -Path $taskMetaPath -Encoding utf8
Write-Host "  task metadata: $taskMetaPath"

Write-Host ""
Write-Host "SAFE TO BEGIN BACKEND TASK"
exit 0
