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
    """2026-08-26 fix: Auth.getToken()/refreshToken() (ctrader_open_api SDK) return a plain
    dict from request.json() -- confirmed live: {'errorCode': 'ACCESS_DENIED', 'description':
    '...'} for a bad exchange. The original getattr()-based reads always silently returned None
    (dicts have no such attributes), so this could never succeed OR surface the real error --
    every exchange failed with the same generic "missing accessToken/refreshToken" message
    regardless of whether the code/credentials were actually valid. Defensively still accepts a
    real object (getattr fallback) in case a future SDK version changes the return type."""
    def _read(key: str) -> object:
        if isinstance(token, dict):
            return token.get(key)
        return getattr(token, key, None)

    error_code = _read("errorCode")
    if error_code:
        raise CTraderAuthError(f"cTrader token request failed: {error_code} -- {_read('description') or ''}")
    access_token = _read("accessToken")
    refresh_token = _read("refreshToken")
    if not access_token or not refresh_token:
        raise CTraderAuthError(f"cTrader token response missing accessToken/refreshToken (raw response: {token!r})")
    return CTraderTokenResult(
        access_token=access_token, refresh_token=refresh_token,
        expires_in=_read("expiresIn"), token_type=_read("tokenType"),
    )
