param(
    [int]$Minutes = 480,
    [int]$IntervalSeconds = 300,
    [string]$LogPath = "logs/live-demo-monitor.log"
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null

$end = (Get-Date).AddMinutes($Minutes)

function Write-LogLine {
    param([string]$Text)
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogPath -Value "[$stamp] $Text"
}

Write-LogLine "monitor_start minutes=$Minutes interval_seconds=$IntervalSeconds"

while ((Get-Date) -lt $end) {
    try {
        $health = docker inspect --format '{{.State.Health.Status}} {{.State.Status}} {{.State.Restarting}} {{.RestartCount}}' openterminalui-backend-1 2>&1
        Write-LogLine "backend_health $health"

        $cycles = docker exec openterminalui-postgres-1 psql -U openterminalui -d openterminalui -At -F "|" -c "select coalesce(account_id,'demo_10k'), cycle_id, status, coalesce(selected_symbol,''), coalesce(trade_id,''), order_send_calls, created_at from mt5_scheduler_cycles order by created_at desc limit 12;" 2>&1
        Write-LogLine "recent_cycles_start"
        Add-Content -Path $LogPath -Value $cycles
        Write-LogLine "recent_cycles_end"

        $orders = docker exec openterminalui-postgres-1 psql -U openterminalui -d openterminalui -At -F "|" -c "select account_id, symbol, direction, status, broker_order_ticket, filled_volume, fill_price, raw_request->>'sl', raw_request->>'tp', created_at from mt5_order_records where status='ACCEPTED' order by created_at desc limit 10;" 2>&1
        Write-LogLine "accepted_orders_start"
        Add-Content -Path $LogPath -Value $orders
        Write-LogLine "accepted_orders_end"

        $adaptive = docker exec openterminalui-postgres-1 psql -U openterminalui -d openterminalui -At -F "|" -c "select account_id, action_type, status, position_id, broker_mutation_attempted, evidence->>'symbol', evidence->>'r', evidence->>'reason_code', created_at from adaptive_management_actions order by created_at desc limit 16; select account_id,state,reason,failed_actions,rejected_actions,actions_this_hour,reset_at from adaptive_circuit_breakers order by account_id; select account_id, trustworthy, finding_count, last_checked_at from mt5_account_reconciliation_status order by account_id;" 2>&1
        Write-LogLine "adaptive_start"
        Add-Content -Path $LogPath -Value $adaptive
        Write-LogLine "adaptive_end"

        $positions = docker exec openterminalui-backend-1 python -c "import asyncio; from backend.brokers.mt5.multi_account import adapter_for_account; accounts=['demo_10k','ftmo_demo_25k','ftmo_demo_50k','ftmo_demo_100k']; total=0
for a in accounts:
    try:
        ps=asyncio.run(adapter_for_account(a).mt5_positions()); total+=len(ps); print(a, 'OK', len(ps), [(p.symbol,p.type,p.ticket,p.volume,p.price_open,round(float(p.profit),2),p.sl,p.tp) for p in ps])
    except Exception as e:
        print(a, 'ERR', type(e).__name__, str(e)[:220])
print('TOTAL_OK_POSITIONS', total)" 2>&1
        Write-LogLine "positions_start"
        Add-Content -Path $LogPath -Value $positions
        Write-LogLine "positions_end"
    }
    catch {
        Write-LogLine "monitor_error $($_.Exception.Message)"
    }

    Start-Sleep -Seconds $IntervalSeconds
}

Write-LogLine "monitor_stop"
