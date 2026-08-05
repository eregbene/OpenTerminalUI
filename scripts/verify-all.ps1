param(
  [switch]$SkipE2E,
  [switch]$SkipDockerBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Run-Step {
  param(
    [string]$Name,
    [scriptblock]$Command
  )
  Write-Host "==> $Name"
  & $Command
}

Push-Location $root
try {
  Run-Step "Frontend unit tests" { Push-Location frontend; npm.cmd test; Pop-Location }
  Run-Step "Frontend build" { Push-Location frontend; npm.cmd run build; Pop-Location }
  if (-not $SkipDockerBuild) {
    Run-Step "Docker build" { docker compose build }
  }
  Run-Step "Docker startup" { docker compose up -d }
  Run-Step "Docker status" { docker compose ps }
  Run-Step "Backend dependency check" { docker compose exec -T backend python -m pip check }
  Run-Step "Backend tests" { docker compose exec -T backend python -m pytest --disable-warnings --tb=short }
  Run-Step "Redis connectivity" { docker compose exec -T redis redis-cli ping }
  Run-Step "Liveness" { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/livez | Select-Object -ExpandProperty StatusCode }
  Run-Step "Readiness" { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/readyz | Select-Object -ExpandProperty StatusCode }
  Run-Step "API documentation" { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/docs | Select-Object -ExpandProperty StatusCode }
  if (-not $SkipE2E) {
    Run-Step "Playwright E2E" { Push-Location frontend; npm.cmd run test:e2e; Pop-Location }
  }
}
finally {
  Pop-Location
}
