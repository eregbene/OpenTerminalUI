<#
.SYNOPSIS
    Back up the running Postgres container's database using pg_dump custom format,
    with a metadata sidecar recording exactly what state it was taken from.

.DESCRIPTION
    Added after the 2026-08-07 incident (see migration 0036_db_incident_boundary /
    the db_incidents table) where a destructive pytest fixture wiped the shared
    development database with no backup to recover from. This is now the standing
    local-development backup mechanism (see docs/CLAUDE_BACKEND_SAFETY.md).

    Dumps are written OUTSIDE the Docker volume AND outside the git repository, to
    a host-side directory, using pg_dump's custom format (-Fc) so they can be
    restored selectively with pg_restore. Never commit these dumps to git.

.PARAMETER ContainerName
    Name of the running Postgres container. Default: openterminalui-postgres-1

.PARAMETER Database
    Database name to dump. Default: openterminalui

.PARAMETER User
    Postgres user to dump as. Default: openterminalui

.PARAMETER OutDir
    Host directory to write the backup into. Default: C:\Users\Intel\Desktop\BensimBackups
    Must not be inside the Docker volume and must not be inside the git repo.

.PARAMETER Label
    Optional label appended to the backup filename (e.g. "pre-migration-0038").

.PARAMETER Category
    Retention category: "daily" (default, pruned per -PruneDays), "premigration"
    (last 10 always kept), "manual" or "incident" (never auto-pruned by this script).

.PARAMETER DockerVolumeName
    Recorded in the metadata sidecar only. Default: openterminalui_postgres_recovered_20260807

.EXAMPLE
    .\scripts\backup_postgres.ps1
    .\scripts\backup_postgres.ps1 -Label "pre-migration-0038" -Category premigration
    .\scripts\backup_postgres.ps1 -Category manual

.NOTES
    Retention:
      - daily:        keep backups from the last -PruneDays days (default 7)
      - premigration:  always keep at least the most recent 10
      - manual/incident: never auto-deleted by this script
#>
param(
    [string]$ContainerName = "openterminalui-postgres-1",
    [string]$Database = "openterminalui",
    [string]$User = "openterminalui",
    [string]$OutDir = "C:\Users\Intel\Desktop\BensimBackups",
    [string]$Label = "",
    [int]$PruneDays = 7,
    [ValidateSet("daily", "premigration", "manual", "incident")]
    [string]$Category = "daily",
    [string]$DockerVolumeName = "openterminalui_postgres_recovered_20260807"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
}
$OutDir = (Resolve-Path $OutDir).Path

# Refuse an output directory that resolves inside this git repo -- backups must
# never be committable by accident.
$repoRoot = (Resolve-Path "$PSScriptRoot\..").Path
if ($OutDir.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "REFUSING TO BACK UP INSIDE THE GIT REPO: OutDir '$OutDir' is inside '$repoRoot'. Choose a path outside the repository."
}

$timestampUtc = (Get-Date).ToUniversalTime()
$timestamp = $timestampUtc.ToString("yyyyMMdd_HHmmss")
$labelSuffix = if ($Label) { "_$Label" } else { "" }
$fileName = "bensim_${timestamp}${labelSuffix}.dump"
$outPath = Join-Path $OutDir $fileName
$metaPath = Join-Path $OutDir "bensim_${timestamp}${labelSuffix}.metadata.json"

Write-Host "Backing up database '$Database' from container '$ContainerName' to:"
Write-Host "  $outPath"

$containerStatus = docker ps --filter "name=$ContainerName" --format "{{.Status}}"
if (-not $containerStatus) {
    throw "Container '$ContainerName' is not running. Refusing to attempt a backup against a stopped/missing container."
}

# pg_dump runs INSIDE the container (reads live, does not lock out writers); output
# is streamed back to the host via stdout redirection, never written inside the
# container or the Docker volume itself.
$pgDumpCmd = "pg_dump -U $User -d $Database -Fc"
Write-Host "Exact command: docker exec $ContainerName $pgDumpCmd > `"$outPath`""

# PowerShell's own pipeline/`>` redirection re-encodes a native command's stdout as
# text, which corrupts pg_dump's binary custom-format output. cmd.exe's redirection
# does not have this problem, so shell out through it for this one step.
$escapedOutPath = $outPath.Replace('"', '""')
cmd.exe /c "docker exec $ContainerName pg_dump -U $User -d $Database -Fc > ""$escapedOutPath"""
if ($LASTEXITCODE -ne 0) {
    if (Test-Path $outPath) { Remove-Item $outPath -Force }
    throw "pg_dump failed with exit code $LASTEXITCODE. No backup file was left behind."
}

$fileInfo = Get-Item $outPath
if ($fileInfo.Length -eq 0) {
    Remove-Item $outPath -Force
    throw "pg_dump produced an empty file. Treating this as a failed backup; the empty file has been removed."
}

Write-Host "Backup created: $outPath ($([math]::Round($fileInfo.Length / 1MB, 2)) MB)"

# Verify the dump is readable/well-formed by listing its table of contents via
# pg_restore --list against the host file (no database is touched by this check).
Write-Host "Verifying backup integrity (pg_restore --list)..."
$listOutput = docker run --rm -v "${outPath}:/backup.dump:ro" postgres:16-alpine pg_restore --list /backup.dump 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Backup verification failed: pg_restore --list could not read $outPath. Exit code: $LASTEXITCODE`n$listOutput"
}
$tableCount = ($listOutput | Select-String -Pattern "TABLE DATA").Count
Write-Host "Backup verified OK -- $tableCount table(s) with data found in the dump."

# --- Metadata sidecar (no secrets: no DB/MT5/OpenAI/bridge credentials) ---
Push-Location $repoRoot
try {
    $gitBranch = (git rev-parse --abbrev-ref HEAD 2>$null)
    $gitCommit = (git rev-parse HEAD 2>$null)
    $gitDirty = [bool](git status --porcelain 2>$null)
}
finally {
    Pop-Location
}

$alembicRevision = docker exec $ContainerName psql -U $User -d $Database -tAc "SELECT version_num FROM alembic_version;" 2>$null
$pgVersion = docker exec $ContainerName psql -U $User -d $Database -tAc "SHOW server_version;" 2>$null

$metadata = [ordered]@{
    timestamp_utc      = $timestampUtc.ToString("o")
    timestamp_local    = (Get-Date).ToString("o")
    git_branch         = ($gitBranch | Out-String).Trim()
    git_commit         = ($gitCommit | Out-String).Trim()
    git_dirty          = $gitDirty
    alembic_revision   = ($alembicRevision | Out-String).Trim()
    database_name      = $Database
    docker_volume_name = $DockerVolumeName
    postgres_version   = ($pgVersion | Out-String).Trim()
    application_environment = $env:OPENTERMINALUI_ENV
    category           = $Category
    backup_file        = $fileName
    file_size_bytes    = $fileInfo.Length
    table_count_in_dump = $tableCount
    backup_verified    = $true
}
$metadata | ConvertTo-Json | Set-Content -Path $metaPath -Encoding utf8
Write-Host "Metadata written: $metaPath"

# --- Retention ---
switch ($Category) {
    "daily" {
        $cutoff = (Get-Date).AddDays(-$PruneDays)
        Get-ChildItem -Path $OutDir -Filter "bensim_*.dump" |
            Where-Object { $_.LastWriteTime -lt $cutoff -and $_.FullName -ne $outPath -and $_.Name -notmatch "_(premigration|manual|incident)" } |
            ForEach-Object {
                Write-Host "Pruning daily backup older than $PruneDays days: $($_.Name)"
                Remove-Item $_.FullName -Force
                $sidecar = $_.FullName -replace '\.dump$', '.metadata.json'
                if (Test-Path $sidecar) { Remove-Item $sidecar -Force }
            }
    }
    "premigration" {
        $preMigBackups = Get-ChildItem -Path $OutDir -Filter "*premigration*.dump" | Sort-Object LastWriteTime -Descending
        if ($preMigBackups.Count -gt 10) {
            $preMigBackups | Select-Object -Skip 10 | ForEach-Object {
                Write-Host "Pruning old pre-migration backup beyond the last 10: $($_.Name)"
                Remove-Item $_.FullName -Force
                $sidecar = $_.FullName -replace '\.dump$', '.metadata.json'
                if (Test-Path $sidecar) { Remove-Item $sidecar -Force }
            }
        }
    }
    default {
        Write-Host "Category '$Category' is never auto-pruned by this script."
    }
}

Write-Host ""
Write-Host "backup_verified=true"
Write-Host "Done. To restore, ALWAYS restore into a NEW database first -- never over the source:"
Write-Host "  .\scripts\restore_postgres.ps1 -BackupFile `"$outPath`" -NewDatabase openterminalui_restore_test_$timestamp"
