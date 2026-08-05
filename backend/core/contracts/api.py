from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class APIErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTHORIZATION_FAILED = "AUTHORIZATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class APIErrorPayload(BaseModel):
    code: APIErrorCode | str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class APIErrorEnvelope(BaseModel):
    error: APIErrorPayload


class BensimAPIError(Exception):
    def __init__(
        self,
        code: APIErrorCode | str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
