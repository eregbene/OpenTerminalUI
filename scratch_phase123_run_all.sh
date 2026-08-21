#!/bin/sh
# Runs the Phase 1-3 multi-variant replay across all 10 symbols, 4-at-a-time (leaves headroom on
# this container's 8 CPUs for the live MT5 autonomous trading loop). Output goes to
# /research/phase123 (bind-mounted to E:\BensimResearch\phase123 on the host) with crash-safe
# incremental writes and coarse per-symbol resume -- safe to re-run this whole script after any
# interruption (Docker Desktop hang, container restart) without losing more than one flush
# interval's worth of work on whichever symbol was mid-walk, and without re-walking any symbol
# whose .done marker already exists.
set -e
cd /app
OUT_DIR=/research/phase123
mkdir -p "$OUT_DIR"

run_batch() {
  for sym in "$@"; do
    python scratch_phase123_variant_replay.py --symbols "$sym" --progress-every 5000 --out-dir "$OUT_DIR" > "$OUT_DIR/${sym}.log" 2>&1 &
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
