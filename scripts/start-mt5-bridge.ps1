$env:MT5_ENABLED = "true"
$env:MT5_ACCOUNT_MODE = "DEMO"
$env:MT5_SERVER = "MetaQuotes-Demo"
$env:MT5_PATH = "C:\Program Files\MetaTrader 5\terminal64.exe"
$env:MT5_TIMEOUT_MS = "60000"
$env:MT5_BRIDGE_API_KEY = "bensim-local-mt5-bridge"
Start-Process -FilePath python -ArgumentList @("-m", "backend.brokers.mt5.bridge", "--host", "127.0.0.1", "--port", "8770") -WorkingDirectory "C:\Users\Intel\Desktop\Devops\trading\OpenTerminalUI" -WindowStyle Hidden
