from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.brokers.mt5.models import MT5Account
from backend.shared.db import Base, SessionLocal


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AccountClassification(str, Enum):
    INTERNAL_DEMO = "INTERNAL_DEMO"
    PROP_TRIAL = "PROP_TRIAL"
    PROP_EVALUATION = "PROP_EVALUATION"
    PROP_VERIFICATION = "PROP_VERIFICATION"
    PROP_FUNDED = "PROP_FUNDED"
    PERSONAL_LIVE = "PERSONAL_LIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AccountFingerprint:
    """Identity for an MT5 account, deliberately excluding the password --
    only login/server/company/currency ever feed the persisted hash."""

    fingerprint_hash: str
    login: int
    server: str
    company: str | None
    currency: str | None


@dataclass(frozen=True)
class MT5AccountProfile:
    account_id: str
    display_name: str
    terminal_path: str | None
    expected_login: int | None
    expected_server: str | None
    expected_initial_balance: Decimal
    account_mode: str
    prop_profile: str
    enabled: bool
    strategy_profile: str
    risk_profile: str
    bridge_host: str
    bridge_port: int

    def model_dump(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "display_name": self.display_name,
            "terminal_path": self.terminal_path,
            "expected_login_configured": self.expected_login is not None,
            "expected_login_masked": mask_login(self.expected_login),
            "expected_server": self.expected_server,
            "expected_initial_balance": str(self.expected_initial_balance),
            "account_mode": self.account_mode,
            "prop_profile": self.prop_profile,
            "enabled": self.enabled,
            "strategy_profile": self.strategy_profile,
            "risk_profile": self.risk_profile,
            "bridge_host": self.bridge_host,
            "bridge_port": self.bridge_port,
        }


def fingerprint_account(account: MT5Account) -> AccountFingerprint:
    server = (account.server or "").strip()
    raw = f"{account.login}:{server}".encode("utf-8")
    return AccountFingerprint(
        fingerprint_hash=hashlib.sha256(raw).hexdigest(),
        login=account.login,
        server=server,
        company=account.company,
        currency=account.currency,
    )


class MT5AccountProfileORM(Base):
    __tablename__ = "mt5_account_profiles"

    fingerprint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # BigInteger: some brokers issue demo logins above the 32-bit signed range (e.g. 5054067375),
    # so a plain Integer column would overflow on insert.
    login: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server: Mapped[str] = mapped_column(String(128), nullable=False)
    company: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default=AccountClassification.UNKNOWN.value)
    profile_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_mt5_account_profiles_login_server", "login", "server"),)


def record_sighting(fingerprint: AccountFingerprint, *, account_mode: str, profile_label: str | None = None) -> str:
    """Upsert a sighting of this account. New accounts are auto-classified
    INTERNAL_DEMO only when account_mode=='DEMO'; anything else starts
    UNKNOWN and unapproved until a human inserts an approval row directly --
    auto-registration never grants approval. Existing rows only get
    last_seen_at bumped; classification/approval are left exactly as a human
    last set them, so switching accounts never silently inherits a prior
    account's approval or risk state."""
    now = utcnow()
    with SessionLocal() as db:
        row = db.get(MT5AccountProfileORM, fingerprint.fingerprint_hash)
        if row is None:
            classification = AccountClassification.INTERNAL_DEMO.value if account_mode == "DEMO" else AccountClassification.UNKNOWN.value
            row = MT5AccountProfileORM(
                fingerprint_hash=fingerprint.fingerprint_hash,
                login=fingerprint.login,
                server=fingerprint.server,
                company=fingerprint.company,
                currency=fingerprint.currency,
                classification=classification,
                profile_label=profile_label if classification == AccountClassification.INTERNAL_DEMO.value else None,
                approved=False,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(row)
        else:
            row.last_seen_at = now
        db.commit()
        return row.classification


def register_account(*, login: int, server: str, classification: str, profile_label: str | None = None, notes: str | None = None, company: str | None = None, currency: str | None = None) -> dict[str, Any]:
    """Explicit operator-initiated registration -- the safe workflow for a
    newly-seen account. Sets classification only, NEVER approved=True:
    approval is always a separate, deliberate database action."""
    if classification not in {c.value for c in AccountClassification}:
        raise ValueError(f"invalid classification: {classification}")
    server = server.strip()
    raw = f"{login}:{server}".encode("utf-8")
    fingerprint_hash = hashlib.sha256(raw).hexdigest()
    now = utcnow()
    with SessionLocal() as db:
        row = db.get(MT5AccountProfileORM, fingerprint_hash)
        if row is None:
            row = MT5AccountProfileORM(
                fingerprint_hash=fingerprint_hash,
                login=login,
                server=server,
                company=company,
                currency=currency,
                classification=classification,
                profile_label=profile_label,
                approved=False,
                notes=notes,
                first_seen_at=now,
                last_seen_at=now,
            )
            db.add(row)
        else:
            row.classification = classification
            if profile_label is not None:
                row.profile_label = profile_label
            if notes is not None:
                row.notes = notes
            row.last_seen_at = now
        db.commit()
        return {"login": row.login, "server": row.server, "classification": row.classification, "profile_label": row.profile_label, "approved": row.approved, "notes": row.notes}


def configured_profiles() -> list[MT5AccountProfile]:
    """Canonical account registry for MT5 autonomous accounts.

    Existing 10K behavior remains wired to the current MT5_* variables. The three FTMO-style
    profiles use their own env namespace and default to disabled so they can be enabled one at
    a time after terminal/login validation.
    """
    return [
        MT5AccountProfile(
            account_id="demo_10k",
            display_name=os.getenv("MT5_10K_DISPLAY_NAME", "10K Demo"),
            terminal_path=os.getenv("MT5_PATH") or None,
            expected_login=_optional_int("MT5_LOGIN"),
            expected_server=os.getenv("MT5_SERVER") or None,
            expected_initial_balance=Decimal(os.getenv("MT5_10K_INITIAL_BALANCE", "10000")),
            account_mode=(os.getenv("MT5_ACCOUNT_MODE") or "DEMO").upper(),
            prop_profile=(os.getenv("PROP_PROFILE") or "GENERIC_PROP_CONSERVATIVE").upper(),
            enabled=_env_bool("MT5_ENABLED", False),
            strategy_profile=os.getenv("MT5_10K_STRATEGY_PROFILE", "ACTIVE_MT5"),
            risk_profile=os.getenv("MT5_10K_RISK_PROFILE", "INTERNAL_DEMO_10K"),
            bridge_host=os.getenv("MT5_BRIDGE_HOST") or "127.0.0.1",
            bridge_port=int(os.getenv("MT5_BRIDGE_PORT", "8765")),
        ),
        _ftmo_profile("ftmo_demo_25k", "25K FTMO Demo", "25K", Decimal("25000"), r"E:\MT5\Bensim-25k\terminal64.exe"),
        _ftmo_profile("ftmo_demo_50k", "50K FTMO Demo", "50K", Decimal("50000"), r"E:\MT5\Bensim-50k\terminal64.exe"),
        _ftmo_profile("ftmo_demo_100k", "100K FTMO Demo", "100K", Decimal("100000"), r"E:\MT5\Bensim-100k\terminal64.exe"),
    ]


def profile_by_id(account_id: str) -> MT5AccountProfile | None:
    return next((profile for profile in configured_profiles() if profile.account_id == account_id), None)


def profile_health(profile: MT5AccountProfile) -> dict[str, Any]:
    host_windows_path = _looks_like_windows_path(profile.terminal_path)
    exists = bool(profile.terminal_path and (Path(profile.terminal_path).exists() or (os.name != "nt" and host_windows_path)))
    blockers: list[str] = []
    warnings: list[str] = []
    if profile.enabled and not exists:
        blockers.append("TERMINAL_PATH_MISSING")
    if profile.enabled and profile.account_id != "demo_10k" and profile.expected_login is None:
        blockers.append("EXPECTED_LOGIN_NOT_CONFIGURED")
    if profile.enabled and profile.account_id != "demo_10k" and not profile.expected_server:
        blockers.append("EXPECTED_SERVER_NOT_CONFIGURED")
    if not profile.enabled:
        warnings.append("ACCOUNT_PROFILE_DISABLED")
    return {
        **profile.model_dump(),
        "terminal_path_exists": exists,
        "terminal_path_host_only": bool(os.name != "nt" and host_windows_path),
        "health": "BLOCKED" if blockers else "OK",
        "blockers": blockers,
        "warnings": warnings,
    }


def mask_login(login: int | str | None) -> str | None:
    if login is None:
        return None
    raw = str(login)
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{raw[:2]}***{raw[-2:]}"


def registry_health() -> dict[str, Any]:
    return {
        "architecture": "ONE_BACKEND_WITH_ONE_MT5_WORKER_PROCESS_PER_ACCOUNT_REQUIRED",
        "mt5_python_process_isolation_required": True,
        "reason": "MetaTrader5.initialize(path=...) is process-global; terminal switching before order_send is unsafe.",
        "items": [profile_health(profile) for profile in configured_profiles()],
    }


def validate_profile_account(profile: MT5AccountProfile, account: MT5Account) -> list[str]:
    blockers: list[str] = []
    if profile.account_mode != "DEMO":
        blockers.append("PROFILE_ACCOUNT_MODE_NOT_DEMO")
    if account.trade_mode not in {0, None}:
        blockers.append("ACTUAL_ACCOUNT_NOT_DEMO")
    if profile.expected_login is None:
        if profile.account_id != "demo_10k":
            blockers.append("EXPECTED_LOGIN_NOT_CONFIGURED")
    elif int(account.login) != int(profile.expected_login):
        blockers.append("ACCOUNT_EXECUTION_CONTEXT_MISMATCH")
    if profile.expected_server and str(account.server or "").strip().casefold() != profile.expected_server.strip().casefold():
        blockers.append("ACCOUNT_SERVER_MISMATCH")
    return blockers


def _ftmo_profile(account_id: str, display_name: str, prefix: str, initial_balance: Decimal, default_path: str) -> MT5AccountProfile:
    return MT5AccountProfile(
        account_id=account_id,
        display_name=_account_env(prefix, "DISPLAY_NAME", display_name),
        terminal_path=_account_env(prefix, "TERMINAL_PATH", default_path),
        expected_login=_optional_int_alias(f"MT5_ACCOUNT_{prefix}_LOGIN", f"MT5_{prefix}_LOGIN"),
        expected_server=_account_env(prefix, "SERVER", None),
        expected_initial_balance=Decimal(_account_env(prefix, "INITIAL_BALANCE", str(initial_balance)) or str(initial_balance)),
        account_mode=(_account_env(prefix, "ACCOUNT_MODE", "DEMO") or "DEMO").upper(),
        prop_profile=(_account_env(prefix, "PROP_PROFILE", "FTMO_2_STEP") or "FTMO_2_STEP").upper(),
        enabled=_env_bool_alias(f"MT5_ACCOUNT_{prefix}_ENABLED", f"MT5_{prefix}_ENABLED", default=False),
        strategy_profile=_account_env(prefix, "STRATEGY_PROFILE", "ACTIVE_MT5") or "ACTIVE_MT5",
        risk_profile=_account_env(prefix, "RISK_PROFILE", "FTMO_2STEP_DEMO") or "FTMO_2STEP_DEMO",
        bridge_host=_account_env(prefix, "BRIDGE_HOST", os.getenv("MT5_BRIDGE_HOST") or "127.0.0.1") or "127.0.0.1",
        bridge_port=int(_account_env(prefix, "BRIDGE_PORT", _default_bridge_port(prefix)) or _default_bridge_port(prefix)),
    )


def _default_bridge_port(prefix: str) -> str:
    return {"25K": "8771", "50K": "8772", "100K": "8773"}.get(prefix, "8765")


def _optional_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    return int(raw) if raw.isdigit() else None


def _optional_int_alias(*names: str) -> int | None:
    for name in names:
        value = _optional_int(name)
        if value is not None:
            return value
    return None


def _account_env(prefix: str, suffix: str, default: str | None) -> str | None:
    return os.getenv(f"MT5_ACCOUNT_{prefix}_{suffix}") or os.getenv(f"MT5_{prefix}_{suffix}") or default


def _looks_like_windows_path(path: str | None) -> bool:
    return bool(path and len(path) > 2 and path[1:3] == ":\\")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_bool_alias(*names: str, default: bool) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            return raw.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _env_true(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


_PROP_CLASSIFICATIONS = {
    AccountClassification.PROP_TRIAL.value,
    AccountClassification.PROP_EVALUATION.value,
    AccountClassification.PROP_VERIFICATION.value,
    AccountClassification.PROP_FUNDED.value,
}


def account_blockers(account: MT5Account, *, account_mode: str) -> list[str]:
    """Independent, always-active safety check. INTERNAL_DEMO accounts
    produce zero blockers here. Any other classification requires TWO
    independent locks: a generic env feature gate (LIVE_TRADING_ENABLED /
    PROP_TRADING_ENABLED, both default false) AND a persisted, manually-set
    approved=True row -- so a misconfigured .env alone still can't activate
    real trading."""
    fingerprint = fingerprint_account(account)
    classification = record_sighting(fingerprint, account_mode=account_mode)
    if classification == AccountClassification.INTERNAL_DEMO.value:
        return []
    blockers: list[str] = []
    if classification == AccountClassification.PERSONAL_LIVE.value and not _env_true("LIVE_TRADING_ENABLED"):
        blockers.append("LIVE_TRADING_ENV_GATE_DISABLED")
    if classification in _PROP_CLASSIFICATIONS and not _env_true("PROP_TRADING_ENABLED"):
        blockers.append("PROP_TRADING_ENV_GATE_DISABLED")
    with SessionLocal() as db:
        row = db.get(MT5AccountProfileORM, fingerprint.fingerprint_hash)
        approved = bool(row and row.approved)
    if not approved:
        blockers.append("ACCOUNT_NOT_APPROVED")
    return blockers


def get_profile(fingerprint_hash: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(MT5AccountProfileORM, fingerprint_hash)
        if not row:
            return None
        return {
            "login": row.login,
            "server": row.server,
            "company": row.company,
            "currency": row.currency,
            "classification": row.classification,
            "profile_label": row.profile_label,
            "approved": row.approved,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "first_seen_at": row.first_seen_at.isoformat(),
            "last_seen_at": row.last_seen_at.isoformat(),
        }


def list_known_accounts() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(MT5AccountProfileORM).order_by(MT5AccountProfileORM.last_seen_at.desc()).all()
        return [
            {
                "login": row.login,
                "server": row.server,
                "company": row.company,
                "currency": row.currency,
                "classification": row.classification,
                "profile_label": row.profile_label,
                "approved": row.approved,
                "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                "first_seen_at": row.first_seen_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
            }
            for row in rows
        ]
