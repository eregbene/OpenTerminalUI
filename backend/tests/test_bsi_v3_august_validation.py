from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bsi_v3_august_validation.py"
spec = importlib.util.spec_from_file_location("bsi_v3_august_validation", MODULE_PATH)
assert spec and spec.loader
bsi_v3 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bsi_v3
spec.loader.exec_module(bsi_v3)


def _bar(minute: int, open_: float, high: float, low: float, close: float):
    return bsi_v3.Bar(
        datetime(2026, 8, 3, 9, minute, tzinfo=timezone.utc),
        open_,
        high,
        low,
        close,
    )


def test_registry_has_correct_v3_strategy_contract():
    registry = bsi_v3.strategy_registry()
    ids = {item.strategy_id for item in registry}

    assert len(registry) == 26
    assert all(item.classification == "INDEPENDENT_STRATEGY" for item in registry)
    assert all(item.strategy_id.startswith("bsi_v3_") for item in registry)
    assert "bsi_v3_smt_divergence" in ids
    assert "bsi_v3_turtle_soups_ranges" in ids
    assert "bsi_v3_mmxm" in ids
    assert "bsi_v3_4h_order_block" in ids
    assert "bsi_v3_standard_deviation_po3" in ids
    assert "bsi_v2_abc" not in ids

    smt = next(item for item in registry if item.strategy_id == "bsi_v3_smt_divergence")
    assert smt.source_rule_ids == ["FAIZ_V3_SMT_001"]
    assert "1. SMT Divergence.mp4" in smt.source_videos


def test_promoted_v3_detectors_are_strategy_specific():
    promoted = {
        "bsi_v3_order_flow",
        "bsi_v3_abc",
        "bsi_v3_abcd",
        "bsi_v3_reactionary_block",
        "bsi_v3_4h_order_block",
        "bsi_v3_mmxm_second_distribution",
        "bsi_v3_holy_grail",
        "bsi_v3_spectre",
        "bsi_v3_monday_range",
        "bsi_v3_weaver",
        "bsi_v3_standard_deviation_po3",
        "bsi_v3_ar50",
        "bsi_v3_yin_yang",
        "bsi_v3_4h_candle_ranges",
        "bsi_v3_enigma_range",
    }

    assert promoted.issubset(bsi_v3.DETECTOR_REGISTRY)
    assert len({bsi_v3.DETECTOR_REGISTRY[sid].__name__ for sid in promoted}) == len(promoted)


def test_selected_symbols_default_to_core_pairs():
    assert bsi_v3._env_list("MISSING_TEST_ENV", bsi_v3.CORE_SYMBOLS) == bsi_v3.CORE_SYMBOLS


def test_source_count_contract_matches_corrected_faiz_folder():
    source = Path(r"F:\new faiz")
    if source.exists():
        videos = [
            p
            for p in source.rglob("*")
            if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi"}
        ]
        assert len(videos) == 125

    contacts = Path("data/faiz_updated_course/visual_contact_sheets")
    if not contacts.exists():
        pytest.skip("Faiz visual artifacts are not present in this runtime image")
    assert len(list(contacts.glob("*.jpg"))) == 125

    manifest_path = Path("docs/bsi_updated_faiz/01_FAIZ_UPDATED_COURSE_VIDEO_MANIFEST.md")
    if not manifest_path.exists():
        pytest.skip("Faiz methodology docs are not present in this runtime image")
    manifest = manifest_path.read_text(encoding="utf-8")
    assert "- Total video files: `125`" in manifest
    assert "Missing numbered lessons: `none`" in manifest
    assert "| 125 " in manifest
    assert "Turtle Soups & Ranges Mastery" in manifest


def test_detect_fvgs_uses_confirmed_current_bar_only():
    bars = [
        _bar(0, 1.00, 1.02, 0.99, 1.01),
        _bar(1, 1.01, 1.03, 1.00, 1.02),
        _bar(2, 1.05, 1.07, 1.04, 1.06),
    ]

    fvgs = bsi_v3.detect_fvgs(bars)

    assert fvgs == [
        {
            "direction": "BULLISH",
            "index": 2,
            "low": 1.02,
            "high": 1.04,
            "occurred_at": bars[2].time,
            "confirmed_at": bars[2].time,
        }
    ]


def test_detect_swings_separates_occurrence_from_confirmation():
    bars = [
        _bar(0, 1.00, 1.01, 0.99, 1.00),
        _bar(1, 1.00, 1.10, 0.98, 1.08),
        _bar(2, 1.08, 1.09, 1.00, 1.01),
    ]

    swings = bsi_v3.detect_swings(bars, left=1, right=1)
    high = next(item for item in swings if item["type"] == "HIGH")

    assert high["occurred_at"] == bars[1].time
    assert high["confirmed_at"] == bars[2].time
    assert high["confirmed_at"] > high["occurred_at"]


def test_simulate_outcome_models_baseline_and_mentor_partial():
    op = bsi_v3.Opportunity(
        strategy_id="bsi_v3_test",
        symbol="EURUSD",
        direction="LONG",
        entry_time=datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc),
        entry=100.0,
        stop=99.0,
        target=102.0,
        timeframe_stack=["M1"],
        thesis_id="thesis",
        opportunity_id="opportunity",
        source_rule_ids=["rule"],
    )
    future = [
        _bar(1, 100.0, 101.1, 100.0, 101.0),
        _bar(2, 101.0, 102.1, 100.8, 102.0),
    ]

    outcome = bsi_v3.simulate_outcome(op, future)

    assert outcome.status == "tp"
    assert outcome.baseline_r == 2.0
    assert outcome.managed_r == 1.5


def test_scaffold_detector_results_are_not_classified_as_promising():
    outcome = bsi_v3.Outcome(
        opportunity_id="opportunity",
        strategy_id="bsi_v3_test",
        symbol="EURUSD",
        direction="LONG",
        entry_time=datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc).isoformat(),
        exit_time=datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc).isoformat(),
        status="tp",
        baseline_r=2.0,
        managed_r=1.5,
    )

    classification = bsi_v3.classify_initial_validation(
        [outcome],
        [],
        detector_validation_level="RAW_SCAFFOLD_NOT_STRATEGY_SPECIFIC",
        golden_example_status="IMPLEMENTATION_NOT_VALIDATED",
    )

    assert classification == "IMPLEMENTATION_NOT_VALIDATED"


class _FakeStore:
    source = "fake"

    def __init__(self, bars):
        self._bars = bars

    def candles(self, symbol, timeframe, start, end):
        return self._bars


def test_two_pair_golden_reconstruction_exports_bar_window():
    bars = [
        bsi_v3.Bar(datetime(2026, 8, 3, 9, minute, tzinfo=timezone.utc), 1.0, 1.1, 0.9, 1.0)
        for minute in range(10)
    ]
    result = {
        "methodology": bsi_v3.METHODOLOGY,
        "window": {"start": bsi_v3.START.isoformat(), "end": bsi_v3.END.isoformat()},
        "extraction": {
            "course_material_mode": "TRANSCRIPTS_PLUS_VISUAL_CONTACT_SHEETS",
            "source_video_count": 125,
            "transcripts_complete": 125,
            "visual_contact_sheets_complete": 125,
        },
        "strategy_results": {
            "bsi_v3_order_flow": {
                "spec": {
                    "strategy_id": "bsi_v3_order_flow",
                    "eligible_symbols": ["EURUSD", "XAUUSD"],
                    "source_videos": ["1. Order Flow Trading Strategy.mp4"],
                    "source_rule_ids": ["FAIZ_V3_ORDERFLOW_001"],
                },
                "opportunities": [
                    {
                        "opportunity_id": "op1",
                        "symbol": "EURUSD",
                        "entry_time": bars[5].time.isoformat(),
                        "timeframe_stack": ["M15"],
                    }
                ],
                "outcomes": [{"opportunity_id": "op1", "status": "tp"}],
            }
        },
    }

    payload = bsi_v3.build_two_pair_golden_reconstructions(
        result,
        _FakeStore(bars),
        ["EURUSD", "XAUUSD"],
        write_output=False,
    )

    artifact = payload["artifacts"]["bsi_v3_order_flow"]
    assert payload["bar_reconstruction_count"] == 1
    assert artifact["status"] == "PASS"
    assert artifact["bar_window"]["entry_bar_index"] == 5
    assert artifact["bar_window"]["bars"][5]["time"] == bars[5].time.isoformat()
