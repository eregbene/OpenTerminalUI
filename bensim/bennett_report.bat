@echo off
rem Bennett report (the live config, its 5 pairs): double-click, type a period in plain English (or "plan").
rem Progress shows as it runs (about a minute for a week); the report opens in your browser when done.
rem Uses the repo's current code and the DB candles (Docker must be running). Pulls the newest MT5 bars first.
cd /d "%~dp0.."
echo.
echo  Examples:  last week ^| this week ^| last friday ^| yesterday ^| 6 december
echo             last week of december 2025 ^| december 2025 ^| 2025-12-01 to 2025-12-05
echo             plan  (= what to watch next, text only, no charts)
echo  For BOTH strategies' plans of today use today.bat instead.
echo.
set WHEN=
set /p WHEN=Period (Enter = last week):
if "%WHEN%"=="" set WHEN=last week
if /i "%WHEN%"=="plan" goto plan
if /i "%WHEN%"=="tomorrow" goto plan
python -m bensim.bennett_report --when "%WHEN%" --update
pause
exit /b

:plan
python -m bensim.bennett_report --plan --update
pause
