"""One-time browser authorization + token refresh, using the official SDK's own Auth class
(ctrader_open_api.Auth) rather than reimplementing the OAuth2 HTTP calls.

cTrader's Open API access token grants either "trading" (full access) or "accounts" (read-only)
scope. Phase 2 is explicitly read-only, so build_authorization_url() below defaults to
scope="accounts" -- an additional, OAuth-level safety layer beyond this adapter's own
CTraderReadOnlyViolation guards: even a bug that somehow tried to submit an order would be
rejected by cTrader's own server, not just by Bensim's code.

Refresh tokens are single-use/rotating (cTrader's own documented behavior) -- refresh_access_token
returns the NEW refresh token alongside the new access token; callers MUST persist the new one
(the old one stops working after one refresh) or discard the config's old value.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.brokers.ctrader.exceptions import CTraderAuthError


@dataclass(frozen=True)
class CTraderTokenResult:
    access_token: str
    refresh_token: str
    expires_in: int | None
    token_type: str | None


def build_authorization_url(*, client_id: str, client_secret: str, redirect_uri: str, scope: str = "accounts") -> str:
    """scope: "accounts" (read-only, default here) or "trading" (full access -- do not use for
    this phase). The user opens this URL in a browser, logs into their cTrader account, and
    approves access; cTrader then redirects to redirect_uri with `?code=...`."""
    from ctrader_open_api import Auth

    auth = Auth(client_id, client_secret, redirect_uri)
    return auth.getAuthUri(scope=scope)


def exchange_authorization_code(*, client_id: str, client_secret: str, redirect_uri: str, auth_code: str) -> CTraderTokenResult:
    from ctrader_open_api import Auth

    auth = Auth(client_id, client_secret, redirect_uri)
    token = auth.getToken(auth_code)
    return _token_result_from_sdk(token)


def refresh_access_token(*, client_id: str, client_secret: str, redirect_uri: str, refresh_token: str) -> CTraderTokenResult:
    from ctrader_open_api import Auth

    auth = Auth(client_id, client_secret, redirect_uri)
    token = auth.refreshToken(refresh_token)
    return _token_result_from_sdk(token)


def _token_result_from_sdk(token: object) -> CTraderTokenResult:
    error_code = getattr(token, "errorCode", None)
    if error_code:
        raise CTraderAuthError(f"cTrader token request failed: {error_code} -- {getattr(token, 'description', '')}")
    access_token = getattr(token, "accessToken", None)
    refresh_token = getattr(token, "refreshToken", None)
    if not access_token or not refresh_token:
        raise CTraderAuthError("cTrader token response missing accessToken/refreshToken")
    return CTraderTokenResult(
        access_token=access_token, refresh_token=refresh_token,
        expires_in=getattr(token, "expiresIn", None), token_type=getattr(token, "tokenType", None),
    )
