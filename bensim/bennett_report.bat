@echo off
rem Bennett report: double-click, type a period in plain English (or "plan"), the report opens in your browser.
rem Uses the repo's current Bennett code and the candles in the DB (Docker Postgres must be running).
rem It first pulls the newest bars from MT5; if the bridge is off it says so and uses what the DB has.
cd /d "%~dp0.."
echo.
echo  Examples:  last week ^| this week ^| last friday ^| yesterday ^| 6 december
echo             last week of december 2025 ^| december 2025 ^| 2025-12-01 to 2025-12-05
echo             plan  (= what to watch next session, text only, no charts)
echo.
set WHEN=
set /p WHEN=Period (Enter = last week):
if "%WHEN%"=="" set WHEN=last week
if /i "%WHEN%"=="plan" goto plan
if /i "%WHEN%"=="tomorrow" goto plan
python -m bensim.bennett_report --when "%WHEN%" --update > "%TEMP%\bennett_report.log" 2>&1
type "%TEMP%\bennett_report.log"
for /f "tokens=1,* delims= " %%a in ('findstr /b /c:"report: " "%TEMP%\bennett_report.log"') do start "" "%%b"
pause
exit /b

:plan
python -m bensim.bennett_report --plan --update
pause
