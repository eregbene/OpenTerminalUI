"""Tests for adaptive_management/service.py::_thesis_invalidation_enabled -- the 2026-08-17
DEMO-only, reversible flag gating THESIS_INVALIDATION_CLOSE (forensic audit found it net-costs
-1.82R vs not firing, across 78 real closed DEMO trades). Pure-function tests via monkeypatch,
matching v2_mode()'s own "monkeypatch.setenv works in tests" convention."""
from __future__ import annotations

from backend.adaptive_management import service
from backend.brokers.mt5.account_registry import AccountClassification


def test_defaults_to_enabled_with_no_env_var_set(monkeypatch):
    monkeypatch.delenv("ADAPTIVE_THESIS_INVALIDATION_ENABLED", raising=False)
    assert service._thesis_invalidation_enabled("any-fingerprint") is True


def test_explicit_true_stays_enabled_regardless_of_account(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_THESIS_INVALIDATION_ENABLED", "true")
    monkeypatch.setattr(service, "_account_classification", lambda fp: AccountClassification.INTERNAL_DEMO.value)
    assert service._thesis_invalidation_enabled("demo-fingerprint") is True


def test_disabled_flag_suppresses_only_for_internal_demo(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_THESIS_INVALIDATION_ENABLED", "false")
    monkeypatch.setattr(service, "_account_classification", lambda fp: AccountClassification.INTERNAL_DEMO.value)
    assert service._thesis_invalidation_enabled("demo-fingerprint") is False


def test_disabled_flag_never_suppresses_for_non_demo_accounts(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_THESIS_INVALIDATION_ENABLED", "false")
    for classification in (AccountClassification.PROP_EVALUATION.value, AccountClassification.PROP_FUNDED.value, AccountClassification.PERSONAL_LIVE.value, AccountClassification.UNKNOWN.value):
        monkeypatch.setattr(service, "_account_classification", lambda fp, c=classification: c)
        assert service._thesis_invalidation_enabled("some-fingerprint") is True, f"must stay enabled for {classification}"


def test_various_falsy_spellings_all_disable(monkeypatch):
    monkeypatch.setattr(service, "_account_classification", lambda fp: AccountClassification.INTERNAL_DEMO.value)
    for spelling in ("false", "False", "FALSE", "0", "disabled", "off"):
        monkeypatch.setenv("ADAPTIVE_THESIS_INVALIDATION_ENABLED", spelling)
        assert service._thesis_invalidation_enabled("demo-fingerprint") is False, f"'{spelling}' should disable"


def test_garbage_value_falls_back_to_enabled(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_THESIS_INVALIDATION_ENABLED", "maybe")
    monkeypatch.setattr(service, "_account_classification", lambda fp: AccountClassification.INTERNAL_DEMO.value)
    assert service._thesis_invalidation_enabled("demo-fingerprint") is True
