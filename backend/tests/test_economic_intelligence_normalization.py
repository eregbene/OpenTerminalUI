from __future__ import annotations

from datetime import timezone

import pytest

from backend.economic_intelligence.normalization import missing_fields, normalize_timestamp_to_utc, parse_numeric_value


@pytest.mark.parametrize(
    "raw,expected_value,expected_unit",
    [
        ("3.5%", 3.5, "percent"),
        ("-0.2%", -0.2, "percent"),
        ("120K", 120_000.0, "K"),
        ("1.2M", 1_200_000.0, "M"),
        ("2.5B", 2_500_000_000.0, "B"),
        ("+250K", 250_000.0, "K"),
        ("-15K", -15_000.0, "K"),
        ("1,234.5", 1234.5, None),
        ("42", 42.0, None),
    ],
)
def test_parse_numeric_value_common_formats(raw, expected_value, expected_unit):
    result = parse_numeric_value(raw)
    assert result.value == pytest.approx(expected_value)
    assert result.unit == expected_unit
    assert result.failure_reason is None


@pytest.mark.parametrize("raw", [None, "", "Tentative", "All Day", "tentative", "n/a", "-", "--"])
def test_parse_numeric_value_placeholders_return_none_not_zero(raw):
    result = parse_numeric_value(raw)
    assert result.value is None
    assert result.confidence == 0.0
    assert result.failure_reason is not None


def test_parse_numeric_value_unparseable_text_is_safe():
    result = parse_numeric_value("Revised from 3.2%")
    assert result.value is None
    assert result.failure_reason == "unparseable_text"


def test_parse_numeric_value_never_raises():
    for raw in (object(), [], {}, float("nan")):
        result = parse_numeric_value(raw)
        assert result.failure_reason is not None or result.value is not None


def test_normalize_timestamp_to_utc_from_iso_string():
    dt, tz_label = normalize_timestamp_to_utc("2026-08-05T12:30:00+02:00", source_timezone="Europe/Berlin")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 10
    assert tz_label == "Europe/Berlin"


def test_normalize_timestamp_to_utc_from_epoch():
    dt, _ = normalize_timestamp_to_utc(1754390400)
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_normalize_timestamp_to_utc_missing_returns_none():
    dt, tz_label = normalize_timestamp_to_utc(None, source_timezone="forex_factory_feed")
    assert dt is None
    assert tz_label == "forex_factory_feed"


def test_normalize_timestamp_to_utc_garbage_never_raises():
    dt, _ = normalize_timestamp_to_utc("not-a-date")
    assert dt is None


def test_missing_fields_detects_empty_and_none():
    row = {"title": "NFP", "country": "USD", "impact": "", "date": None}
    assert set(missing_fields(row, ("title", "country", "impact", "date"))) == {"impact", "date"}
