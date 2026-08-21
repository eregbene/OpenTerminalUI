#!/bin/sh
# Runs the Phase 1 flags-ON replay across all 10 symbols, 4-at-a-time (leaves headroom on this
# container's 8 CPUs for the live MT5 autonomous trading loop that runs in the same process host).
# EURUSD/XAUUSD go in the first batch since the user flagged them as priority symbols to see first.
set -e
cd /app

run_batch() {
  for sym in "$@"; do
    python scratch_phase1_flag_ab_replay.py --symbols "$sym" --progress-every 5000 --out "/app/phase1_ab_${sym}.json" > "/app/phase1_ab_${sym}.log" 2>&1 &
  done
  wait
}

echo "BATCH 1: EURUSD XAUUSD GBPUSD USDJPY"
run_batch EURUSD XAUUSD GBPUSD USDJPY
echo "BATCH 1 DONE"

echo "BATCH 2: AUDUSD NZDUSD USDCAD USDCHF"
run_batch AUDUSD NZDUSD USDCAD USDCHF
echo "BATCH 2 DONE"

echo "BATCH 3: EURJPY GBPJPY"
run_batch EURJPY GBPJPY
echo "BATCH 3 DONE"

echo "ALL BATCHES DONE"
