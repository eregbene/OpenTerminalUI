<#
.SYNOPSIS
    Take a verified backup, then run `alembic upgrade head` -- never the other way
    around, and never migrate if the backup step failed.

.DESCRIPTION
    Added after the 2026-08-07 incident (see migration 0036_db_incident_boundary /
    the db_incidents table) as the standard way to run migrations against a
    database that actually matters (dev included). Sequence is fixed:
      1. pg_dump (via scripts/backup_postgres.ps1, custom format, verified with
         pg_restore --list)
      2. only if the backup step succeeded AND verified: `alembic upgrade head`
         inside the backend container

    If -RequireBackup is set (the default) and the backup step fails for any
    reason, this script exits non-zero WITHOUT attempting the migration.
    Pass -RequireBackup:$false only for a database you've already decided is
    disposable (e.g. a fresh ephemeral test DB) -- never for anything you'd be
    upset to lose.

.PARAMETER ContainerName
    Postgres container to back up. Default: openterminalui-postgres-1

.PARAMETER BackendContainerImage
    Image to run `alembic upgrade head` from. Default: bensim-trading:latest

.PARAMETER Database / User
    Passed through to backup_postgres.ps1.

.PARAMETER RequireBackup
    If $true (default), abort before migrating when the backup step fails.

.EXAMPLE
    .\scripts\pre_migration_backup.ps1
#>
param(
    [string]$ContainerName = "openterminalui-postgres-1",
    [string]$Database = "openterminalui",
    [string]$User = "openterminalui",
    [string]$BackendContainerImage = "bensim-trading:latest",
    [string]$DockerNetwork = "openterminalui_default",
    [bool]$RequireBackup = $true
)

$ErrorActionPreference = "Stop"

Write-Host "=== Step 1/2: pre-migration backup ==="
$backupSucceeded = $true
try {
    & "$PSScriptRoot\backup_postgres.ps1" -ContainerName $ContainerName -Database $Database -User $User -Label "premigration" -Category premigration
    if ($LASTEXITCODE -ne 0) { $backupSucceeded = $false }
}
catch {
    Write-Error "Backup step failed: $_"
    $backupSucceeded = $false
}

if (-not $backupSucceeded) {
    if ($RequireBackup) {
        Write-Error "REFUSING TO RUN MIGRATIONS: pre-migration backup failed and -RequireBackup is `$true. No migration was attempted."
        exit 1
    }
    else {
        Write-Warning "Backup step failed but -RequireBackup is `$false -- proceeding to migrate anyway, by explicit request."
    }
}
else {
    Write-Host "Backup step succeeded and was verified."
}

Write-Host ""
Write-Host "=== Step 2/2: alembic upgrade head ==="
docker run --rm --network $DockerNetwork -e DATABASE_URL="postgresql+asyncpg://${User}:${User}@${ContainerName}:5432/${Database}" $BackendContainerImage alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Error "alembic upgrade head failed with exit code $LASTEXITCODE. The pre-migration backup above is available to restore from if needed (scripts/restore_postgres.ps1)."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Migration complete. Backup remains available in backups\postgres\ if a rollback is ever needed."
