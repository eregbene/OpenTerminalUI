from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
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
    login: Mapped[int] = mapped_column(Integer, nullable=False)
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
