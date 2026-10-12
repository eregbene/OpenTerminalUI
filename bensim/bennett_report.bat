@echo off
rem Bennett report: double-click, type the dates, the report opens in your browser.
cd /d "%~dp0.."
set /p FROM=From date (YYYY-MM-DD):
set /p TO=To date, inclusive (YYYY-MM-DD, Enter = same day):
if "%TO%"=="" set TO=%FROM%
set /p UPD=Pull latest bars from MT5 first? (y/N):
set EXTRA=
if /i "%UPD%"=="y" set EXTRA=--update
python -m bensim.bennett_report --from %FROM% --to %TO% %EXTRA%
start "" "docs\charts\report\%FROM%_%TO%\index.html"
pause
