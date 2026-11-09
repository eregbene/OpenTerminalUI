# Connects Alpaca (PAPER account) to Claude Code. Run it yourself in PowerShell:
#   powershell -ExecutionPolicy Bypass -File D:\Devops\trading\OpenTerminalUI\bensim_github_lab\connect_alpaca_paper.ps1
# It asks for your paper key + secret (hidden input) and passes them straight to `claude mcp add`.
$key = Read-Host "Alpaca PAPER API key" -AsSecureString
$sec = Read-Host "Alpaca PAPER secret" -AsSecureString
$k = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($key))
$s = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
claude mcp remove alpaca --scope user 2>$null
claude mcp add alpaca --scope user -e "ALPACA_API_KEY=$k" -e "ALPACA_SECRET_KEY=$s" -e "ALPACA_PAPER_TRADE=true" `
  -e "ALPACA_TOOLSETS=account,trading,stock-data" '--' uvx alpaca-mcp-server
$ok = $LASTEXITCODE -eq 0
$k = $null; $s = $null
if ($ok) { Write-Host "Done. Restart Claude Code, then tell Claude: connected" -ForegroundColor Green }
else { Write-Host "claude mcp add FAILED - paste the error above to Claude" -ForegroundColor Red }
