@echo off
rem TradeGuard rules app. Opens in its own window; the small minimised "TradeGuard rules" window is the server -- close it to stop.
rem Add --lan to reach it from her phone on the home Wi-Fi (PIN in logs\tradeguard_editor_pin.txt).
cd /d "%~dp0.."
start "TradeGuard rules (close to stop)" /min python -m bensim.tradeguard_editor %*
timeout /t 2 /nobreak >nul
rem own Edge profile: the main Edge sometimes runs hidden in the background and then swallows the --app window
start "" msedge --app=http://127.0.0.1:8791 --window-size=620,900 --user-data-dir="%~dp0..\logs\tradeguard_app_profile" --no-first-run
