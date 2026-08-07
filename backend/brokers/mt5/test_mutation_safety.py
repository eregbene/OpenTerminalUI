"""Canonical safety guard for tests/scripts that perform REAL broker-side MT5
mutations (order/SL/TP/partial/close) against the connected demo account.

Standing policy after the 2026-08-07 incident (see docs/CLAUDE_BACKEND_SAFETY.md):
ALL real broker-side trading development/testing uses ONLY the INTERNAL_DEMO
account. PROP_EVALUATION, PROP_FUNDED, PERSONAL_LIVE, and UNKNOWN classifications
must fail closed for test-mode mutations -- always, by querying live account
state, never by trusting an env variable alone.

Every test/script that is about to send a real order, modify SL/TP, partial-close,
or close a position for engineering-validation purposes MUST call
assert_safe_demo_mutation_test() first, and route the actual mutation through
ExecutionManager -- never call order_send() directly.
"""
from __future__ import annotations

from backend.brokers.mt5.account_registry import AccountClassification, fingerprint_account, record_sighting

# Marker written onto any order/position created purely for engineering validation
# (never onto normal autonomous trades). Excluded by default from strategy
# expectancy, policy learning, entry-quality statistics, Adaptive Manager
# promotion, the probability engine, and the strategy leaderboard -- see
# docs/CLAUDE_BACKEND_SAFETY.md.
ENGINEERING_TEST_TAG = "ENGINEERING_TEST"

TEST_MUTATION_NON_DEMO_ACCOUNT = "TEST_MUTATION_NON_DEMO_ACCOUNT"
TEST_MUTATION_ACCOUNT_FINGERPRINT_MISMATCH = "TEST_MUTATION_ACCOUNT_FINGERPRINT_MISMATCH"


class UnsafeDemoMutationError(RuntimeError):
    """Raised when a test/script attempts a real broker mutation against a
    non-demo or unexpected account. Carries a machine-readable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


async def assert_safe_demo_mutation_test(adapter, *, expected_fingerprint: str | None = None) -> None:
    """Raise UnsafeDemoMutationError unless the CURRENTLY connected account is
    genuinely the INTERNAL_DEMO account.

    Always re-queries live account state via the adapter -- never trusts a cached
    flag or an env variable by itself, per Part 14 of the recovery directive: "Do
    not allow an env variable alone to override this."
    """
    account = await adapter.mt5_account()
    fingerprint = fingerprint_account(account)
    # record_sighting is a read+upsert (bumps last_seen_at only; never grants
    # approval or changes an existing classification) -- same canonical path
    # backend/brokers/mt5/account_registry.py's own account_blockers() uses, so
    # this always reflects live, persisted account state, never a cached guess.
    classification = record_sighting(fingerprint, account_mode=getattr(adapter.config, "account_mode", "DEMO"))

    if classification != AccountClassification.INTERNAL_DEMO.value:
        raise UnsafeDemoMutationError(
            TEST_MUTATION_NON_DEMO_ACCOUNT,
            f"REFUSING test-mode broker mutation: connected account is classified "
            f"'{classification}', not INTERNAL_DEMO. Real broker-side "
            f"mutation testing is only permitted against the INTERNAL_DEMO account.",
        )

    if expected_fingerprint is not None and fingerprint.fingerprint_hash != expected_fingerprint:
        raise UnsafeDemoMutationError(
            TEST_MUTATION_ACCOUNT_FINGERPRINT_MISMATCH,
            f"REFUSING test-mode broker mutation: connected account fingerprint "
            f"{fingerprint.fingerprint_hash[:12]}... does not match the expected "
            f"registered demo account fingerprint {expected_fingerprint[:12]}....",
        )


def is_learning_eligible(trade_source: str | None) -> bool:
    """False for engineering-test trades; true for everything else (including
    normal autonomous demo trades, and legacy/unknown-tagged trades -- exclusion
    is opt-in via the explicit ENGINEERING_TEST_TAG, never opt-out by default)."""
    return trade_source != ENGINEERING_TEST_TAG
