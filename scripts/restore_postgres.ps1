<#
.SYNOPSIS
    Restore a pg_dump custom-format backup into a NEW Postgres database.

.DESCRIPTION
    Added after the 2026-08-07 incident (see migration 0036_db_incident_boundary /
    the db_incidents table). This script will NEVER restore over an existing
    database, and refuses outright to target a protected database name
    (openterminalui, production, staging, development, bensim). Always restores
    into a brand-new, empty database created by this script, so the source
    (whatever you are recovering from) is never at risk of being overwritten by a
    failed or partial restore.

    Inspect the restored data in the new database, and only point the application
    at it (docker-compose.yml) once you're satisfied it's correct.

.PARAMETER BackupFile
    Path to the pg_dump custom-format (-Fc) backup file to restore.

.PARAMETER NewDatabase
    Name of the brand-new database to create and restore into. Must not already
    exist, and must not be (or contain only) a protected name.

.PARAMETER ContainerName
    Name of the running Postgres container to restore into. Default: openterminalui-postgres-1

.PARAMETER AdminUser
    Postgres user with CREATEDB privilege, used to create $NewDatabase. Default: openterminalui

.EXAMPLE
    .\scripts\restore_postgres.ps1 -BackupFile .\backups\postgres\postgres_openterminalui_20260807_1234.dump -NewDatabase openterminalui_restored_20260807
#>
param(
    [Parameter(Mandatory = $true)][string]$BackupFile,
    [Parameter(Mandatory = $true)][string]$NewDatabase,
    [string]$ContainerName = "openterminalui-postgres-1",
    [string]$AdminUser = "openterminalui"
)

$ErrorActionPreference = "Stop"

$protectedNames = @("openterminalui", "production", "prod", "staging", "development", "dev", "bensim")
$newDbLower = $NewDatabase.ToLowerInvariant()
if ($protectedNames -contains $newDbLower) {
    throw "REFUSING TO RESTORE: '$NewDatabase' is a protected database name. Choose a distinct new name, e.g. 'openterminalui_restored_$(Get-Date -Format yyyyMMdd_HHmmss)'."
}
if ($newDbLower -notmatch "(test_|_test|restored|recovery|recovered)") {
    Write-Warning "Target database name '$NewDatabase' doesn't contain an obvious restore/test marker (restored/recovery/recovered/test_/_test). Proceeding anyway since it isn't a protected name, but consider a clearer name."
}

if (-not (Test-Path $BackupFile)) {
    throw "Backup file not found: $BackupFile"
}
$BackupFile = (Resolve-Path $BackupFile).Path

$containerStatus = docker ps --filter "name=$ContainerName" --format "{{.Status}}"
if (-not $containerStatus) {
    throw "Container '$ContainerName' is not running."
}

# Refuse if the target database already exists -- this script only ever restores
# into a database IT creates, never over something that might already hold data.
$existsCheck = docker exec $ContainerName psql -U $AdminUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$NewDatabase'"
if ($existsCheck -match "1") {
    throw "REFUSING TO RESTORE: database '$NewDatabase' already exists. Pick a new, not-yet-existing database name -- this script will not restore over an existing database."
}

Write-Host "Creating new database '$NewDatabase' on container '$ContainerName'..."
docker exec $ContainerName psql -U $AdminUser -d postgres -c "CREATE DATABASE $NewDatabase OWNER $AdminUser"
if ($LASTEXITCODE -ne 0) {
    throw "CREATE DATABASE failed with exit code $LASTEXITCODE."
}

Write-Host "Restoring $BackupFile into '$NewDatabase'..."
# PowerShell's own pipeline re-encodes bytes piped into a native command's stdin,
# which corrupts pg_restore's binary custom-format input (see the same issue fixed
# in backup_postgres.ps1). cmd.exe's redirection does not have this problem.
$escapedBackupFile = $BackupFile.Replace('"', '""')
cmd.exe /c "docker exec -i $ContainerName pg_restore -U $AdminUser -d $NewDatabase --no-owner --no-privileges < ""$escapedBackupFile"""
$restoreExitCode = $LASTEXITCODE

Write-Host "Verifying restored database..."
$tableCount = docker exec $ContainerName psql -U $AdminUser -d $NewDatabase -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
Write-Host "Restored database '$NewDatabase' now has $($tableCount.Trim()) table(s)."

if ($restoreExitCode -ne 0) {
    Write-Warning "pg_restore exited with code $restoreExitCode. This is sometimes non-fatal (e.g. harmless ownership/extension warnings) -- inspect the output above and the table count before trusting this restore."
}

Write-Host ""
Write-Host "Restore complete into a NEW, isolated database: '$NewDatabase'"
Write-Host "The original source database was never touched by this script."
Write-Host "Inspect the data before pointing docker-compose.yml at this database."
