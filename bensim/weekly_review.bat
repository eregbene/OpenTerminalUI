@echo off
rem WEEKLY TRADE REVIEW: every closed DEMO trade of the last finished week -- $ and R, best/worst point, exit, trend at
rem entry, news, loss tags, last week's high-impact news and the reaction, ideas to check. Observe only.
rem Writes reports\weekly\trade_review_<monday>.md (the engine also writes it every Saturday from 06:00 UTC).
rem Another week: weekly_review.bat 2026-11-02
cd /d "%~dp0.."
set PYTHONPATH=%CD%
if "%1"=="" (python -m bensim.journal) else (python -m bensim.journal --week %1)
pause
