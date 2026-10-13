@echo off
rem TODAY: both live strategies on one page -- open positions (labelled fib618 / Bennett), the last 24 h of signals
rem and what the engine did, and per pair fib618's trigger + chart next to Bennett's plan. Opens in your browser.
rem Works without Claude. Needs Docker running; pulls the newest MT5 bars first (bridge off -> uses the DB).
cd /d "%~dp0.."
python -m bensim.today
pause
