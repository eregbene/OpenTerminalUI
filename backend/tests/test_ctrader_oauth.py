"""2026-08-26 real bug found live: ctrader_open_api's Auth.getToken()/refreshToken() return a
plain dict (confirmed: request.json()), but _token_result_from_sdk read it with getattr(), which
silently returns None for every field on a dict -- so exchange_authorization_code/
refresh_access_token could NEVER succeed, and could never surface cTrader's real error either,
regardless of whether the auth code/credentials were actually valid."""
from __future__ import annotations

import pytest

from backend.brokers.ctrader.exceptions import CTraderAuthError
from backend.brokers.ctrader.oauth import CTraderTokenResult, _token_result_from_sdk


def test_dict_success_response_is_parsed_correctly():
    """The real, confirmed shape cTrader returns on success."""
    token = {"accessToken": "AT123", "refreshToken": "RT456", "expiresIn": 2592000, "tokenType": "bearer"}
    result = _token_result_from_sdk(token)
    assert result == CTraderTokenResult(access_token="AT123", refresh_token="RT456", expires_in=2592000, token_type="bearer")


def test_dict_error_response_raises_with_real_detail():
    """The real, confirmed shape cTrader returns for a bad/expired code -- live-verified:
    {'errorCode': 'ACCESS_DENIED', 'description': 'Access denied. Make sure the credentials are valid.'}"""
    token = {"errorCode": "ACCESS_DENIED", "description": "Access denied. Make sure the credentials are valid."}
    with pytest.raises(CTraderAuthError, match="ACCESS_DENIED"):
        _token_result_from_sdk(token)


def test_dict_missing_tokens_without_error_code_raises_with_raw_response_visible():
    """An unexpected shape (no errorCode, but also no real tokens) must still fail loudly, and
    the raised message must include the raw response so a future genuinely-different SDK shape
    is diagnosable, unlike the old generic message."""
    token = {"some_other_field": "unexpected"}
    with pytest.raises(CTraderAuthError, match="some_other_field"):
        _token_result_from_sdk(token)


def test_object_with_attributes_still_works_defensively():
    """Defensive fallback in case a future SDK version returns a real object instead of a dict."""
    class _FakeTokenObject:
        errorCode = None
        accessToken = "AT789"
        refreshToken = "RT012"
        expiresIn = 100
        tokenType = "bearer"

    result = _token_result_from_sdk(_FakeTokenObject())
    assert result.access_token == "AT789"
    assert result.refresh_token == "RT012"


def test_dict_with_falsy_error_code_does_not_raise_on_error_path():
    token = {"errorCode": "", "accessToken": "AT1", "refreshToken": "RT1"}
    result = _token_result_from_sdk(token)
    assert result.access_token == "AT1"
