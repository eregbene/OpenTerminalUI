param(
    [string]$RepoRoot = "D:\Devops\trading\OpenTerminalUI",
    [string]$ApiKey = $env:MT5_BRIDGE_API_KEY,
    [string]$EnvFile = "",
    [string[]]$AccountIds = @()
)

if (-not $EnvFile) {
    $EnvFile = Join-Path $RepoRoot ".env"
}
if (Test-Path -LiteralPath $EnvFile) {
    Get-Content -LiteralPath $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") { return }
        $name, $value = $line.Split("=", 2)
        $name = $name.Trim()
        $value = $value.Trim().Trim('"').Trim("'")
        if ($name) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

if (-not $ApiKey) {
    $ApiKey = "bensim-local-mt5-bridge"
}

function Get-EnvFirst([string[]]$Names, [string]$Default = "") {
    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { return $value }
    }
    return $Default
}

function Test-EnvEnabled([string[]]$Names, [bool]$Default = $false) {
    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($null -ne $value -and $value.Trim() -ne "") {
            return $value.Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
        }
    }
    return $Default
}

$accounts = @(
    @{ Id = "demo_10k"; Prefix = ""; Enabled = (Test-EnvEnabled @("MT5_ENABLED") $false); Port = (Get-EnvFirst @("MT5_BRIDGE_PORT") "8765"); Path = $env:MT5_PATH },
    @{ Id = "ftmo_demo_25k"; Prefix = "25K"; Enabled = (Test-EnvEnabled @("MT5_ACCOUNT_25K_ENABLED", "MT5_25K_ENABLED") $false); Port = (Get-EnvFirst @("MT5_ACCOUNT_25K_BRIDGE_PORT", "MT5_25K_BRIDGE_PORT") "8771"); Path = (Get-EnvFirst @("MT5_ACCOUNT_25K_TERMINAL_PATH", "MT5_25K_TERMINAL_PATH") "E:\MT5\Bensim-25k\terminal64.exe") },
    @{ Id = "ftmo_demo_50k"; Prefix = "50K"; Enabled = (Test-EnvEnabled @("MT5_ACCOUNT_50K_ENABLED", "MT5_50K_ENABLED") $false); Port = (Get-EnvFirst @("MT5_ACCOUNT_50K_BRIDGE_PORT", "MT5_50K_BRIDGE_PORT") "8772"); Path = (Get-EnvFirst @("MT5_ACCOUNT_50K_TERMINAL_PATH", "MT5_50K_TERMINAL_PATH") "E:\MT5\Bensim-50k\terminal64.exe") },
    @{ Id = "ftmo_demo_100k"; Prefix = "100K"; Enabled = (Test-EnvEnabled @("MT5_ACCOUNT_100K_ENABLED", "MT5_100K_ENABLED") $false); Port = (Get-EnvFirst @("MT5_ACCOUNT_100K_BRIDGE_PORT", "MT5_100K_BRIDGE_PORT") "8773"); Path = (Get-EnvFirst @("MT5_ACCOUNT_100K_TERMINAL_PATH", "MT5_100K_TERMINAL_PATH") "E:\MT5\Bensim-100k\terminal64.exe") }
)

foreach ($account in $accounts) {
    if ($AccountIds.Count -gt 0 -and $AccountIds -notcontains $account.Id) {
        continue
    }
    if (-not $account.Enabled) {
        Write-Host "[$($account.Id)] skipped: profile disabled"
        continue
    }
    if (-not $account.Path -or -not (Test-Path -LiteralPath $account.Path)) {
        Write-Warning "[$($account.Id)] terminal missing: $($account.Path)"
        continue
    }

    $envBlock = @{
        "MT5_ENABLED" = "true"
        "MT5_PATH" = $account.Path
        "MT5_ACCOUNT_ID" = $account.Id
        "MT5_BRIDGE_API_KEY" = $ApiKey
        "MT5_BRIDGE_HOST" = "127.0.0.1"
        "MT5_BRIDGE_PORT" = "$($account.Port)"
        "MT5_ACCOUNT_MODE" = "DEMO"
    }
    $assignments = ($envBlock.GetEnumerator() | ForEach-Object { "`$env:$($_.Key)='$($_.Value)'" }) -join "; "
    $command = "$assignments; python -m backend.brokers.mt5.bridge --host 127.0.0.1 --port $($account.Port) --account-id $($account.Id)"
    Start-Process -FilePath "powershell" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command) -WorkingDirectory $RepoRoot -WindowStyle Hidden
    Write-Host "[$($account.Id)] MT5 bridge started on 127.0.0.1:$($account.Port)"
}
