"""Phase 5 fragmentation investigation: real chronological OOS comparison between
A. exact symbol+strategy+direction (current live hard-match) and
B. strategy+direction pooled across ALL symbols (cross-symbol, no ATR/R geometry normalization
   applied beyond what build_state_fingerprint already buckets -- current_r/mfe/elapsed/regime
   buckets are already scale-relative, not raw price, so this is a real, not naive, pooling test)

Reuses adaptive_oos.run_adaptive_walk_forward unchanged (same chronological purge/embargo split,
same edge_stability classification, same calibration-error computation) -- this script only
varies which filter (symbol=X vs symbol=None) is passed in, so the comparison is apples-to-apples
against the SAME methodology already used for the per-milestone report.

Cross-symbol evidence is judged ONLY on whether OOS calibration error is measurably better than
the per-symbol baseline, per the directive's explicit "may ONLY be used if chronological OOS
tests show that it improves prediction/calibration... not enable it merely because it increases
N."
"""
from __future__ import annotations

from backend.historical_intelligence.adaptive_oos import run_adaptive_walk_forward

STRATEGY = "mtfai1"
MILESTONES = ["R_0_25", "R_0_50", "R_0_75", "R_1_00", "R_1_50", "R_2_00"]
PROB_KEYS = ["probability_continue_plus_1r", "probability_round_trip", "probability_reach_tp", "probability_reversal"]


def _avg_abs_cal_error(calibration: dict) -> float | None:
    errs = [v["abs_calibration_error"] for v in calibration.values() if v.get("abs_calibration_error") is not None]
    return sum(errs) / len(errs) if errs else None


def main() -> None:
    print(f"{'milestone':<10} {'A:symbol-exact n(tr/oos)':<26} {'A:cal_err':>10}   {'B:pooled n(tr/oos)':<22} {'B:cal_err':>10}   {'delta':>8}", flush=True)
    for milestone in MILESTONES:
        result_a = run_adaptive_walk_forward(strategy=STRATEGY, symbol="EURUSD", milestone_label=milestone)
        result_b = run_adaptive_walk_forward(strategy=STRATEGY, symbol=None, milestone_label=milestone)

        n_a = f"{result_a['train']['n']}/{result_a['oos']['n']}"
        n_b = f"{result_b['train']['n']}/{result_b['oos']['n']}"
        err_a = _avg_abs_cal_error(result_a["calibration"])
        err_b = _avg_abs_cal_error(result_b["calibration"])
        delta = (err_b - err_a) if (err_a is not None and err_b is not None) else None

        err_a_s = f"{err_a:.4f}" if err_a is not None else "n/a"
        err_b_s = f"{err_b:.4f}" if err_b is not None else "n/a"
        delta_s = f"{delta:+.4f}" if delta is not None else "n/a"
        print(f"{milestone:<10} {n_a:<26} {err_a_s:>10}   {n_b:<22} {err_b_s:>10}   {delta_s:>8}", flush=True)
        print(f"  A edge_stability={result_a['edge_stability']}  B edge_stability={result_b['edge_stability']}", flush=True)


if __name__ == "__main__":
    main()
